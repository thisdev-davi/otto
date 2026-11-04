"""Banco de mídia: o que os clientes mandaram (fotos, figurinhas, vídeos, documentos, áudios).

**Só mídia RECEBIDA.** A enviada pela galeria do espaço reaproveita o mesmo arquivo em
toda mensagem (`salao_fotos.enviar_galeria`); apagar a partir de uma delas furaria o
histórico de todas as outras conversas. A recebida tem um arquivo por mensagem.

**Excluir apaga o arquivo, não a mensagem.** O histórico da conversa é append-only
(`OTTO_PROJETO.md` §6.3): a linha fica, marcada como removida, com quem e quando — senão
o "como na foto" do cliente perde a referência e some a prova de que algo foi apagado.
"""

import json
import logging
from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import audit, media_links, storage

log = logging.getLogger("uvicorn.error")


async def remover(
    db: AsyncSession, tenant_id: UUID, message_id: UUID, user_id: UUID
) -> bool:
    """Marca a mídia como removida e apaga o arquivo. False se não havia o que remover.

    O UPDATE é condicional (`status = 'stored'`), mesma regra da captura: dois cliques
    simultâneos não apagam duas vezes nem registram duas auditorias. O arquivo só sai
    DEPOIS do commit — com o banco ainda aberto, uma falha no commit deixaria a mensagem
    apontando para um arquivo que já não existe.
    """
    marca = json.dumps(
        {
            "status": "removed",
            "removed_at": datetime.now(timezone.utc).isoformat(),
            "removed_by": str(user_id),
        }
    )
    row = await db.execute(
        text(
            "UPDATE messages m SET channel_metadata = jsonb_set("
            "  m.channel_metadata, '{media}',"
            "  (m.channel_metadata->'media') - 'path' || CAST(:marca AS jsonb)) "
            "FROM (SELECT id, channel_metadata->'media'->>'path' AS path FROM messages "
            "      WHERE id = :mid AND tenant_id = :tid FOR UPDATE) antes "
            "WHERE m.id = antes.id AND m.tenant_id = :tid AND m.direction = 'INBOUND' "
            "  AND m.channel_metadata->'media'->>'status' = 'stored' "
            "RETURNING antes.path"
        ),
        {"mid": str(message_id), "tid": str(tenant_id), "marca": marca},
    )
    alvo = row.fetchone()
    if alvo is None:
        return False
    await audit.record(
        db,
        tenant_id,
        "media.delete",
        "media",
        message_id,
        actor_type="human",
        actor_user_id=user_id,
    )
    await db.commit()

    if alvo.path:
        try:
            await storage.delete(alvo.path)
        except Exception:
            # ponytail: o arquivo fica órfão e inalcançável (o caminho saiu do banco).
            # Uma varredura de órfãos por prefixo do tenant resolve se um dia importar.
            log.exception(
                "midia_recebida: arquivo da mensagem %s não foi apagado", message_id
            )
    return True


# Tipos que entram no banco de mídia, no vocabulário do enum (`ContentType.name`).
TIPOS = ("IMAGE", "STICKER", "VIDEO", "DOCUMENT", "AUDIO")


async def listar(
    db: AsyncSession,
    tenant_id: UUID,
    tipo: str = "IMAGE",
    busca: str = "",
    de: date | None = None,
    ate: date | None = None,
    limite: int = 60,
    offset: int = 0,
) -> list[dict]:
    """Mídias recebidas e ainda guardadas, da mais nova para a mais antiga.

    `busca` casa com o nome ou o telefone do contato. Só `status = 'stored'`: pendente
    ainda não tem arquivo, indisponível nunca terá, e removida já foi.
    """
    # ponytail: varre as mensagens do tenant filtrando o JSONB. Um índice parcial
    # (tenant_id, created_at) WHERE direction='INBOUND' resolve quando o volume pedir.
    rows = await db.execute(
        text(
            "SELECT m.id, m.conversation_id, m.content_type, m.content, m.created_at, "
            "  m.channel_metadata->'media'->>'mimetype' AS mimetype, "
            "  ct.name AS contato, ct.phone AS telefone "
            "FROM messages m "
            "JOIN conversations c ON c.id = m.conversation_id AND c.tenant_id = m.tenant_id "
            "JOIN contacts ct ON ct.id = c.contact_id AND ct.tenant_id = m.tenant_id "
            "WHERE m.tenant_id = :tid AND m.direction = 'INBOUND' "
            "  AND m.content_type = CAST(:tipo AS contenttype_enum) "
            "  AND m.channel_metadata->'media'->>'status' = 'stored' "
            "  AND (:busca = '' OR ct.name ILIKE :padrao OR ct.phone ILIKE :padrao) "
            "  AND (CAST(:de AS date) IS NULL OR m.created_at >= CAST(:de AS date)) "
            "  AND (CAST(:ate AS date) IS NULL "
            "       OR m.created_at < CAST(:ate AS date) + interval '1 day') "
            "ORDER BY m.created_at DESC, m.id DESC "
            "LIMIT :limite OFFSET :offset"
        ),
        {
            "tid": str(tenant_id),
            "tipo": tipo,
            "busca": busca,
            # `%` e `_` digitados casam literalmente, não como curinga.
            "padrao": "%"
            + busca.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            + "%",
            "de": de,
            "ate": ate,
            "limite": limite,
            "offset": offset,
        },
    )
    return [
        {
            "message_id": r.id,
            "conversation_id": r.conversation_id,
            "content_type": r.content_type.lower(),
            "legenda": r.content,
            "mimetype": r.mimetype,
            "contato": r.contato,
            "telefone": r.telefone,
            "created_at": r.created_at,
            "media_url": f"/media/{r.id}?{media_links.assinar('media', r.id, tenant_id)}",
        }
        for r in rows.fetchall()
    ]
