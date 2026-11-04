"""Ingestão de eventos de canal.

**Duas barreiras, de propósito.** A `apikey` compartilhada (`provider.verify_webhook`)
prova que o evento veio do nosso Evolution. O `webhook_id` no caminho da URL prova de
QUAL conexão ele é — e é dele que sai o tenant.

Antes existia só a primeira. Como o tenant era resolvido pelo campo `instance` do corpo
(`services/ingestion.py`), quem tivesse a chave compartilhada escolhia em qual tenant a
mensagem forjada caía: dava para injetar conversa, gastar a chave de LLM do salão e fazer
o bot responder para números arbitrários, em qualquer cliente da plataforma. O segredo por
conexão fecha isso — vazar o de um salão não alcança o vizinho.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated

from app.channels.factory import get_provider
from app.core import ratelimit
from app.core.config import settings
from app.core.database import get_session
from app.models.enums import Channel
from app.services import channels as channels_service
from app.tasks.ingest import ingest_webhook

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Teto do corpo do webhook. Uma mensagem do WhatsApp é JSON com metadados — nunca chega
# perto disso (a mídia vem depois, por download, não no corpo). O cap existe porque a
# leitura acontece ANTES de qualquer autenticação: sem ele, um POST sem credencial
# nenhuma segura memória do processo pelo tempo que quiser.
_MAX_BODY = 1 * 1024 * 1024


async def _ler_corpo(request: Request) -> bytes:
    """Lê o corpo em streaming, abortando acima de `_MAX_BODY`."""
    partes: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > _MAX_BODY:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Payload muito grande",
            )
        partes.append(chunk)
    return b"".join(partes)


def _provider_ou_404(channel: Channel):
    """Canal do enum mas sem provider registrado (instagram, email) → 404, não 500.

    `Channel` ainda tem os canais removidos, então `/webhooks/instagram` passa pela
    validação do path e explodia em ValueError dentro da factory — 500 não autenticado,
    de graça, para qualquer um.
    """
    try:
        return get_provider(channel)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Canal não disponível"
        )


@router.post("/{channel}/{webhook_id}", status_code=200)
async def receive_webhook(
    channel: Channel,
    webhook_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    # Limite por IP ANTES de ler o corpo e de tocar no banco: quem apanha aqui não custa
    # nem I/O. O limite por conexão vem depois, quando se sabe de quem é o evento.
    await ratelimit.exigir(
        f"webhook:ip:{ratelimit.ip_do_cliente(request)}",
        settings.RATE_LIMIT_WEBHOOK,
        settings.RATE_LIMIT_WEBHOOK_JANELA,
    )

    body = await _ler_corpo(request)
    provider = _provider_ou_404(channel)
    if not provider.verify_webhook(dict(request.headers), body):
        raise HTTPException(status_code=401, detail="Webhook inválido")

    conexao = await channels_service.resolve_by_webhook_id(session, channel, webhook_id)
    if conexao is None:
        # Mesma resposta do segredo errado: não confirmar que o webhook_id existe.
        raise HTTPException(status_code=401, detail="Webhook inválido")

    # Um salão com o WhatsApp sequestrado não pode consumir a fila dos outros.
    await ratelimit.exigir(
        f"webhook:conn:{conexao['id']}",
        settings.RATE_LIMIT_WEBHOOK,
        settings.RATE_LIMIT_WEBHOOK_JANELA,
    )

    ingest_webhook.delay(channel.value, body.decode(), str(conexao["id"]))
    return {"status": "accepted"}
