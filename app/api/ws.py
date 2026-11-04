import asyncio
import contextlib
import logging
from uuid import uuid4

import redis.asyncio as aioredis
from fastapi import APIRouter
from fastapi.websockets import WebSocket, WebSocketDisconnect
from sqlalchemy import text

from app.api.auth import decode_token
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.services import audit
from app.services.events import channel

router = APIRouter(tags=["websocket"])

log = logging.getLogger("uvicorn.error")


async def _pump(websocket: WebSocket, pubsub) -> None:
    """Repassa os eventos do canal do tenant para o navegador."""
    async for message in pubsub.listen():
        if message["type"] == "message":
            await websocket.send_text(message["data"].decode())


async def _wait_disconnect(websocket: WebSocket) -> None:
    """Escuta o cliente só para NOTAR quando ele sumir.

    O front nunca envia nada por este socket, e é justamente aí que morava o
    vazamento: sem ninguém em `receive()`, a saída do cliente só era percebida na
    próxima tentativa de `send_text` — ou seja, nunca, em tenant sem tráfego. O
    handler ficava estacionado para sempre em `pubsub.listen()`, segurando o socket
    (CLOSE-WAIT) e uma conexão Redis. Com o front reconectando a cada 3s
    (`useWebSocket.ts`), cada recarga de aba deixava mais um fantasma para trás.
    """
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Stream de eventos do tenant. Autenticado por JWT em ?token= (o tenant vem
    do token, não da URL — antes disso qualquer um escutava qualquer tenant)."""
    token = websocket.query_params.get("token", "")
    token_data = decode_token(token)
    if token_data is None:
        await websocket.close(code=4401)  # unauthorized
        return

    correlation_id = uuid4()
    ip_network = audit.mask_ip(websocket.client.host if websocket.client else None)
    async with AsyncSessionLocal() as session:
        version = await session.scalar(
            text(
                "SELECT token_version FROM users WHERE id=:uid AND tenant_id=:tid AND is_active=true"
            ),
            {"uid": token_data.user_id, "tid": token_data.tenant_id},
        )
        if version is None or version != token_data.token_version:
            await websocket.close(code=4401)
            return
        await audit.record(
            session,
            token_data.tenant_id,
            "ws.connect",
            "events",
            actor_type="human",
            actor_user_id=token_data.user_id,
            correlation_id=correlation_id,
            ip_network=ip_network,
        )
        await session.commit()

    await websocket.accept()
    ch = channel(token_data.tenant_id)
    redis_client = aioredis.from_url(settings.REDIS_URL)
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(ch)

    # As duas pontas correm juntas e a primeira que terminar encerra a conexão:
    # o cliente saiu, ou o repasse morreu. Sem a corrida, só a segunda era vigiada.
    pump = asyncio.create_task(_pump(websocket, pubsub))
    watch = asyncio.create_task(_wait_disconnect(websocket))
    try:
        done, pending = await asyncio.wait(
            {pump, watch}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        # Espera o cancelamento de fato acontecer: sem isto voltaríamos ao mesmo
        # problema — a task seguiria viva segurando o Redis depois do finally.
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            with contextlib.suppress(
                asyncio.CancelledError, WebSocketDisconnect, RuntimeError
            ):
                task.result()
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("ws: falha no stream do tenant %s", token_data.tenant_id)
    finally:
        # Best-effort: a conexão já pode ter caído do outro lado, e uma falha na
        # limpeza não pode impedir a liberação do que vem depois.
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(ch)
        with contextlib.suppress(Exception):
            await pubsub.aclose()
        with contextlib.suppress(Exception):
            await redis_client.aclose()
        # Do not persist event payloads or JWTs. The handshake proves the identity.
        try:
            async with AsyncSessionLocal() as session:
                await audit.record(
                    session,
                    token_data.tenant_id,
                    "ws.disconnect",
                    "events",
                    actor_type="human",
                    actor_user_id=token_data.user_id,
                    correlation_id=correlation_id,
                    ip_network=ip_network,
                )
                await session.commit()
        except Exception:
            log.error("WebSocket disconnect audit failed")
