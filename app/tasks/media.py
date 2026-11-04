"""Tasks Celery de mídia inbound: download e transcrição de áudio.

Fora do request do webhook (download + Whisper podem demorar). Idempotente:
no-op quando a mídia já está num estado final.
"""

import json
import logging
import mimetypes
from uuid import UUID

import redis.asyncio as aioredis

from app.ai import transcription
from app.channels.factory import get_provider
from app.core.config import settings
from app.core.database import WorkerSessionLocal
from app.models.enums import Channel, ContentType
from app.services import avatars, bot_settings, storage
from app.services.previews import preview_transcricao
from app.services.channels import JOIN_CONEXAO
from app.tasks.runner import run_task
from app.worker import celery_app

log = logging.getLogger("uvicorn.error")


@celery_app.task(name="tasks.process_inbound_media", max_retries=2)
def process_inbound_media(message_id: str, tenant_id: str) -> None:
    run_task(lambda: _process(UUID(message_id), UUID(tenant_id)))


@celery_app.task(name="tasks.transcribe_audio", max_retries=2)
def transcribe_audio(message_id: str, tenant_id: str) -> None:
    run_task(lambda: _transcribe(UUID(message_id), UUID(tenant_id)))


@celery_app.task(name="tasks.fetch_contact_avatar", max_retries=1)
def fetch_contact_avatar(contact_id: str, tenant_id: str) -> None:
    run_task(lambda: _fetch_avatar(UUID(contact_id), UUID(tenant_id)))


# ─────────────────────────── download ───────────────────────────


async def _load_message(db, message_id: UUID, tenant_id: UUID):
    from sqlalchemy import text

    row = await db.execute(
        text(
            "SELECT m.content_type, m.channel, m.channel_metadata, "
            "COALESCE(cc.credentials, fb.credentials) AS credentials, m.conversation_id, m.created_at "
            "FROM messages m "
            "JOIN conversations c ON c.id = m.conversation_id AND c.tenant_id = m.tenant_id "
            "JOIN contacts ct ON ct.id = c.contact_id AND ct.tenant_id = m.tenant_id "
            + JOIN_CONEXAO
            + "WHERE m.id = :mid AND m.tenant_id = :tid"
        ),
        {"mid": str(message_id), "tid": str(tenant_id)},
    )
    return row.fetchone()


async def _process(message_id: UUID, tenant_id: UUID) -> None:
    async with WorkerSessionLocal() as db:
        from sqlalchemy import text

        msg = await _load_message(db, message_id, tenant_id)
        if msg is None:
            return
        meta = dict(msg.channel_metadata or {})
        media = meta.get("media") or {}
        if not media.get("has_media") or media.get("status") in (
            "stored",
            "unavailable",
        ):
            return  # idempotência

        provider = get_provider(Channel(msg.channel.lower()))
        result = await provider.fetch_media(dict(msg.credentials or {}), meta)
        if result is None:
            media["status"] = "unavailable"
        else:
            data, mimetype = result
            ext = mimetypes.guess_extension(mimetype.split(";")[0]) or ".bin"
            path = await storage.put(
                f"{tenant_id}/{message_id}{ext}", data, mimetype, audit_session=db
            )
            media.update({"status": "stored", "path": path, "mimetype": mimetype})
            if msg.content_type == ContentType.AUDIO.name:
                media["transcript_status"] = "pending"

        meta["media"] = media
        await db.execute(
            text(
                "UPDATE messages SET channel_metadata = CAST(:meta AS jsonb) "
                "WHERE id = :mid AND tenant_id = :tid"
            ),
            {"meta": json.dumps(meta), "mid": str(message_id), "tid": str(tenant_id)},
        )
        await db.commit()

    if msg.content_type != ContentType.AUDIO.name:
        return
    if media.get("status") == "stored":
        transcribe_audio.delay(str(message_id), str(tenant_id))
        return
    # O áudio não desceu do WhatsApp. Não há o que transcrever e o orquestrador está
    # esperando por um texto que nunca vem — avisa o cliente em vez de deixá-lo no vácuo.
    await _dar_a_vez_ao_bot(
        tenant_id,
        msg.conversation_id,
        ContentType.AUDIO.name,
        msg.created_at,
        audio_ilegivel=True,
    )


# ─────────────────────────── foto de perfil ───────────────────────────


async def _fetch_avatar(contact_id: UUID, tenant_id: UUID) -> None:
    """Baixa (ou re-baixa) a foto de perfil do contato. Ver `services/avatars.py`.

    Fora do request do webhook porque são duas idas à rede, e uma delas ao CDN do
    WhatsApp — nada disso pode atrasar a gravação da mensagem que chegou.
    """
    from sqlalchemy import text

    async with WorkerSessionLocal() as db:
        row = await db.execute(
            text(
                "SELECT ct.external_id, ct.channel, ct.channel_metadata, ("
                "  SELECT f.credentials FROM channel_connections f "
                "   WHERE f.tenant_id = ct.tenant_id AND f.channel = ct.channel "
                "     AND f.is_active = true ORDER BY f.created_at DESC LIMIT 1"
                ") AS credentials "
                "FROM contacts ct WHERE ct.id = :cid AND ct.tenant_id = :tid"
            ),
            {"cid": str(contact_id), "tid": str(tenant_id)},
        )
        contato = row.fetchone()
        if contato is None or not contato.credentials:
            return
        # Re-checagem: duas mensagens seguidas do mesmo cliente enfileiram duas buscas, e
        # a segunda não precisa acontecer. Mesma idempotência do download de mídia.
        if not avatars.desatualizado(dict(contato.channel_metadata or {})):
            return

        provider = get_provider(Channel(contato.channel.lower()))
        resultado = await provider.fetch_avatar(
            dict(contato.credentials or {}), contato.external_id
        )
        if resultado is None:
            # Registrado, e não ignorado: sem isto a gente perguntaria de novo a cada
            # mensagem de todo contato que esconde a foto — que é a maioria.
            await avatars.registrar(
                db, tenant_id, contact_id, {"status": avatars.STATUS_UNAVAILABLE}
            )
        else:
            dados, mimetype = resultado
            ext = mimetypes.guess_extension(mimetype) or ".jpg"
            caminho = await storage.put(
                f"{tenant_id}/avatars/{contact_id}{ext}",
                dados,
                mimetype,
                audit_session=db,
            )
            await avatars.registrar(
                db,
                tenant_id,
                contact_id,
                {
                    "status": avatars.STATUS_STORED,
                    "path": caminho,
                    "mimetype": mimetype,
                },
            )
        await db.commit()


async def _dar_a_vez_ao_bot(
    tenant_id: UUID,
    conversation_id: UUID,
    content_type: str,
    message_ts,
    audio_ilegivel: bool = False,
) -> None:
    """Devolve o turno ao orquestrador depois que a mídia terminou de ser processada.

    Áudio é o único tipo que o bot não responde na ingestão: ele espera o texto. Por isso
    TODO desfecho de uma nota de voz precisa passar por aqui — inclusive os ruins. Sem
    isso, download que falha e transcrição que volta vazia eram silêncio absoluto: o
    cliente mandou áudio e nunca recebeu resposta nenhuma, sem erro em lugar nenhum.

    `message_ts` é o `created_at` ORIGINAL do áudio, então a guarda anti-flood continua
    valendo mesmo com o atraso do download e da transcrição.
    """
    from app.services.bot_orchestrator import handle_inbound

    redis_client = aioredis.from_url(settings.REDIS_URL)
    try:
        async with WorkerSessionLocal() as db:
            await handle_inbound(
                db,
                redis_client,
                tenant_id,
                conversation_id,
                content_type,
                message_ts=message_ts,
                audio_ilegivel=audio_ilegivel,
            )
    finally:
        await redis_client.aclose()


# ─────────────────────────── transcrição ───────────────────────────


async def _transcribe(message_id: UUID, tenant_id: UUID) -> None:
    from sqlalchemy import text

    async with WorkerSessionLocal() as db:
        msg = await _load_message(db, message_id, tenant_id)
        if msg is None:
            return
        meta = dict(msg.channel_metadata or {})
        media = meta.get("media") or {}
        path = media.get("path")
        if (
            media.get("transcript_status") == "done"
            or not path
            or not await storage.exists(path)
        ):
            return

        audio = await storage.read(path)
        # A chave do painel do tenant vem primeiro: o objetivo é que quem já configurou
        # o Gemini para o agente não precise de uma segunda conta de IA só para o áudio.
        # Vazio, `transcribe` cai no `.env` sozinho.
        cfg = await bot_settings.load_bot_config(db, tenant_id)
        provider, api_key = bot_settings.resolve_transcription(cfg)
        transcript = await transcription.transcribe(
            audio,
            media.get("mimetype", ""),
            api_key=api_key,
            provider=provider,
            model=cfg.model,
        )

        if transcript:
            media["transcript_status"] = "done"
            meta["media"] = media
            await db.execute(
                text(
                    "UPDATE messages SET content = :content, "
                    "channel_metadata = CAST(:meta AS jsonb) "
                    "WHERE id = :mid AND tenant_id = :tid"
                ),
                {
                    "content": transcript,
                    "meta": json.dumps(meta),
                    "mid": str(message_id),
                    "tid": str(tenant_id),
                },
            )
            await db.execute(
                text(
                    "UPDATE conversations SET last_message_preview = :preview "
                    "WHERE id = :cid AND tenant_id = :tid"
                ),
                {
                    "preview": preview_transcricao(transcript),
                    "cid": str(msg.conversation_id),
                    "tid": str(tenant_id),
                },
            )
            await db.commit()
        else:
            media["transcript_status"] = "failed"
            meta["media"] = media
            await db.execute(
                text(
                    "UPDATE messages SET channel_metadata = CAST(:meta AS jsonb) "
                    "WHERE id = :mid AND tenant_id = :tid"
                ),
                {
                    "meta": json.dumps(meta),
                    "mid": str(message_id),
                    "tid": str(tenant_id),
                },
            )
            await db.commit()

    if not transcript:
        # Transcrição vazia era `return` nu, e o cliente que mandou nota de voz não
        # recebia absolutamente nada. Agora o bot diz que não entendeu o áudio e o
        # roteiro segue de onde parou — na segunda vez, chama gente.
        await _dar_a_vez_ao_bot(
            tenant_id,
            msg.conversation_id,
            ContentType.AUDIO.name,
            msg.created_at,
            audio_ilegivel=True,
        )
        return

    # áudio transcrito vira texto no contexto → o bot pode responder
    await _dar_a_vez_ao_bot(
        tenant_id, msg.conversation_id, ContentType.TEXT.name, msg.created_at
    )
