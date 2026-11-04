"""Publicação de eventos tipados no canal Redis do tenant (consumidos pelo WebSocket).

Envelope único: {"type": <tipo>, ...payload}. O front reage por tipo (append da
mensagem certa, atualização de status/atribuição) em vez de refazer fetch de tudo.
"""

import json
import logging
from uuid import UUID

log = logging.getLogger("uvicorn.error")

# Tipos de evento (contrato com o front).
MESSAGE_INBOUND = "message.inbound"
MESSAGE_OUTBOUND = "message.outbound"
MESSAGE_STATUS = "message.status"
# Mensagem apagada ou editada (pelo cliente ou pelo atendente): a thread recarrega.
MESSAGE_CHANGED = "message.changed"
CONVERSATION_ASSIGNED = "conversation.assigned"
CONVERSATION_STATUS = "conversation.status"
# O bot desistiu e a conversa virou tarefa de gente. É o ÚNICO evento que pede ação
# humana, e por isso é o único além de `message.inbound` que o front transforma em aviso:
# antes dele, a conversa que mais precisava de alguém era a mais silenciosa do sistema —
# escalação só publicava `message.outbound`, que é justamente o tipo que não avisa.
CONVERSATION_HANDOFF = "conversation.handoff"


def channel(tenant_id: UUID | str) -> str:
    return f"otto:{tenant_id}"


async def publish(
    redis_client, tenant_id: UUID | str, event_type: str, payload: dict
) -> None:
    """Publica um evento no canal do tenant. Best-effort: falha de Redis nunca propaga."""
    try:
        body = json.dumps({"type": event_type, **payload}, ensure_ascii=False)
        await redis_client.publish(channel(tenant_id), body)
    except Exception as exc:
        log.warning("events.publish (%s) falhou: %s", event_type, exc)


async def publish_once(tenant_id: UUID | str, event_type: str, payload: dict) -> None:
    """Como publish, mas abre e fecha a própria conexão Redis. Para endpoints HTTP
    (que não têm um redis_client em mãos). Best-effort."""
    import redis.asyncio as aioredis

    from app.core.config import settings

    try:
        redis_client = aioredis.from_url(settings.REDIS_URL)
        try:
            await publish(redis_client, tenant_id, event_type, payload)
        finally:
            await redis_client.aclose()
    except Exception as exc:
        log.warning("events.publish_once (%s) falhou: %s", event_type, exc)
