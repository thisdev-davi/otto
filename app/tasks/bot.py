"""Task Celery do agente de IA: re-tentativa do turno.

A ingestão da base de conhecimento morava aqui, servindo à rota `POST /bot/kb/documents`.
As duas saíram junto: os documentos vêm de `conteudo/base/*.md` e
`scripts/sincronizar_conteudo.py` chama `kb.ingest_document` direto, sem fila — o script é
síncrono e mostra o resultado no terminal, então a fila não tinha o que resolver.
"""

from uuid import UUID

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.database import WorkerSessionLocal
from app.models.enums import ContentType
from app.tasks.runner import run_task
from app.worker import celery_app

# Espera antes de tentar o turno de novo depois de uma falha do provedor de IA. Curto o
# bastante para o cliente ainda estar na conversa, longo o bastante para um blip de rede
# ou um rate limit passar.
RETRY_DELAY_SECONDS = 8


@celery_app.task(name="tasks.retry_bot_turn")
def retry_bot_turn(tenant_id: str, conversation_id: str) -> None:
    """Tenta o turno do bot de novo depois de uma falha do provedor de IA.

    Existe porque o "Só um segundinho, já te respondo" era uma promessa que o sistema não
    tinha como cumprir: a re-tentativa só acontecia quando o CLIENTE mandava outra
    mensagem, e a falha seguinte não enviava nada. Na prática o bot avisava e emudecia.

    Agendada com countdown; entra pelo caminho normal do orquestrador, então herda todas
    as guardas (conversa capturada, sessão desativada, anti-flood).
    """
    run_task(lambda: _retry_turn(UUID(tenant_id), UUID(conversation_id)))


async def _retry_turn(tenant_id: UUID, conversation_id: UUID) -> None:
    # Import tardio: bot_orchestrator importa deste módulo para agendar a task, e no topo
    # isto seria import circular.
    from app.services.bot_orchestrator import handle_inbound

    redis_client = aioredis.from_url(settings.REDIS_URL)
    try:
        async with WorkerSessionLocal() as db:
            # message_ts=None desliga a guarda anti-flood de propósito: a mensagem
            # original pode ter sido recebida há mais tempo que o limite por causa da
            # própria espera, e descartá-la aqui recriaria o silêncio que a task conserta.
            # from_retry=True liga a guarda que descarta a re-tentativa quando outro turno
            # já respondeu no intervalo — sem ela os dois rodam juntos na mesma conversa.
            await handle_inbound(
                db,
                redis_client,
                tenant_id,
                conversation_id,
                ContentType.TEXT.name,
                from_retry=True,
            )
    finally:
        await redis_client.aclose()
