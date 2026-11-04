import redis.asyncio as aioredis
from sqlalchemy import text

from app.channels.factory import get_provider
from app.channels.types import NormalizedStatusUpdate
from app.core.config import settings
from app.core.database import WorkerSessionLocal
from app.models.enums import Channel
from app.services import events
from app.services.bot_orchestrator import handle_inbound
from app.services.ingestion import ingest
from app.tasks.runner import run_task
from app.worker import celery_app

# Precedência dos status: um ACK atrasado nunca regride o status já alcançado.
_STATUS_RANK = {"SENT": 1, "DELIVERED": 2, "READ": 3, "FAILED": 4}


@celery_app.task(name="tasks.ingest_webhook")
def ingest_webhook(channel_str: str, raw_body: str, connection_id: str) -> None:
    """Processa um webhook já autenticado pela rota.

    Recebe `connection_id` (resolvido pelo `webhook_id` da URL) em vez dos headers da
    requisição. Os headers saíram por dois motivos: nenhum `_parse_*` os lê, e mandá-los
    escrevia a `apikey` do Evolution em texto claro na fila do Redis, onde ela ficava
    visível a qualquer `redis-cli MONITOR` e a qualquer traceback que ecoe os argumentos
    da task.
    """
    channel = Channel(channel_str)
    provider = get_provider(channel)
    events_parsed = provider.parse_webhook({}, raw_body.encode())
    # UM run_task para o payload inteiro: o laço vai dentro da corrotina, no mesmo loop.
    run_task(lambda: _handle_events(events_parsed, connection_id))


async def _handle_events(events_parsed: list, connection_id: str) -> None:
    for normalized in events_parsed:
        if isinstance(normalized, NormalizedStatusUpdate):
            await _apply_status(normalized, connection_id)
        else:
            await _ingest_one(normalized, connection_id)


async def _ingest_one(normalized, connection_id: str) -> None:
    redis_client = aioredis.from_url(settings.REDIS_URL)
    try:
        async with WorkerSessionLocal() as db:
            result = await ingest(db, redis_client, normalized, connection_id)
            if result is None:
                return

            # Mídia inbound → baixa (e transcreve áudio) num worker separado.
            if (normalized.channel_metadata or {}).get("media", {}).get("has_media"):
                from app.tasks.media import process_inbound_media

                process_inbound_media.delay(
                    str(result["message_id"]), str(result["tenant_id"])
                )

            # Foto de perfil do contato: contato novo, ou foto vista pela última vez há
            # mais de uma semana. Duas idas à rede (Evolution + CDN do WhatsApp) que não
            # podem atrasar a resposta ao cliente — por isso worker, como a mídia.
            if result.get("avatar_desatualizado"):
                from app.tasks.media import fetch_contact_avatar

                fetch_contact_avatar.delay(
                    str(result["contact_id"]), str(result["tenant_id"])
                )

            # Mensagem nova persistida → dá a vez ao agente de IA (best-effort:
            # qualquer falha é engolida no orquestrador, nunca quebra a ingestão).
            # message_ts = timestamp ORIGINAL → a guarda anti-flood corta o backlog
            # que o WhatsApp entrega ao (re)conectar.
            await handle_inbound(
                db,
                redis_client,
                result["tenant_id"],
                result["conversation_id"],
                normalized.content_type.name,
                message_ts=normalized.timestamp,
            )
    finally:
        await redis_client.aclose()


async def _apply_status(update: NormalizedStatusUpdate, connection_id: str) -> None:
    """Atualiza o status de entrega/leitura, sem regredir (precedência no WHERE).

    O `tenant_id` entra no WHERE via `connection_id` (defesa em profundidade, CLAUDE.md).
    Sem ele, o alvo do UPDATE era só `(channel, external_id)` — um par que vem do corpo do
    webhook. A constraint única segura hoje, mas o predicado de tenant é o que garante que
    um evento entregue numa conexão não toque mensagem de outro salão.
    """
    new_rank = _STATUS_RANK.get(update.status.name, 0)
    escopo = (
        " AND tenant_id = (SELECT tenant_id FROM channel_connections WHERE id = :cid)"
    )
    redis_client = aioredis.from_url(settings.REDIS_URL)
    try:
        async with WorkerSessionLocal() as db:
            params = {
                "status": update.status.name,
                "channel": update.channel.name,
                "external_id": update.external_id,
                "new_rank": new_rank,
                "cid": connection_id,
            }
            row = await db.execute(
                text(
                    "UPDATE messages SET status = :status "
                    "WHERE channel = :channel AND external_id = :external_id "
                    "  AND COALESCE("
                    "    CASE status "
                    "      WHEN 'SENT' THEN 1 WHEN 'DELIVERED' THEN 2 "
                    "      WHEN 'READ' THEN 3 WHEN 'FAILED' THEN 4 ELSE 0 END, 0) < :new_rank"
                    + escopo
                    + " RETURNING id, tenant_id, conversation_id"
                ),
                params,
            )
            r = row.fetchone()
            if r is None:
                return  # mensagem desconhecida ou status não avança
            await db.commit()
            await events.publish(
                redis_client,
                r.tenant_id,
                events.MESSAGE_STATUS,
                {
                    "conversation_id": str(r.conversation_id),
                    "message_id": str(r.id),
                    "status": update.status.value,
                },
            )
    finally:
        await redis_client.aclose()
