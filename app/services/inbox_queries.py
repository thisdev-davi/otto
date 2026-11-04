from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import avatars, media_links


_CONVERSATION_COLUMNS = """
    c.id,
    c.tenant_id,
    c.contact_id,
    ct.name            AS contact_name,
    ct.external_id     AS contact_external_id,
    ct.phone           AS contact_phone,
    ct.channel         AS contact_channel,
    ct.channel_metadata AS contact_channel_metadata,
    c.status,
    c.assigned_user_id,
    c.priority,
    c.tags,
    c.last_message_at,
    c.last_message_preview,
    c.unread_count,
    c.created_at
"""

_CONTACT_JOIN = (
    "JOIN contacts ct ON ct.id = c.contact_id AND ct.tenant_id = c.tenant_id"
)

# Onde a busca procura. Nome e preview cobrem "achar o fulano" e "aquela do orçamento",
# que é o uso real; o telefone entra porque muita conversa não tem nome nenhum.
_SEARCH_CLAUSE = (
    " AND (ct.name ILIKE :search "
    "OR c.last_message_preview ILIKE :search "
    "OR ct.external_id ILIKE :phone "
    # `ct.phone` além do `external_id`: quando o remetente chega como `@lid`, o endereço
    # não contém dígito nenhum do telefone, e procurar pelo número do cliente não achava
    # a conversa dele.
    "OR ct.phone ILIKE :phone)"
)


def _search_sql(search: str | None) -> tuple[str, dict]:
    """Cláusula e parâmetros da busca. ('', {}) quando não há termo.

    Devolver vazio importa: fila sem busca não deve pagar três ILIKE por linha.
    """
    q = (search or "").strip()
    if not q:
        return "", {}

    # `%` e `_` são curingas do LIKE. Sem escapar, buscar "50%" casaria com TODA
    # conversa do tenant — um filtro que finge filtrar é pior que nenhum.
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    # external_id é "5511999999999@s.whatsapp.net". Quem digita "(11) 99999" não acha
    # nada comparando o texto cru, então o ramo do telefone usa só os dígitos.
    digitos = "".join(c for c in q if c.isdigit())

    return _SEARCH_CLAUSE, {
        "search": f"%{escaped}%",
        "phone": f"%{digitos}%" if digitos else f"%{escaped}%",
    }


def _row_to_dict(row) -> dict:
    assigned = row.assigned_user_id
    last_msg = row.last_message_at
    created = row.created_at
    return {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "contact_id": str(row.contact_id),
        "contact_name": row.contact_name,
        "contact_external_id": row.contact_external_id,
        "contact_phone": row.contact_phone,
        "contact_channel": row.contact_channel.lower() if row.contact_channel else None,
        # Já assinada, como `media_url` da mensagem: `<img src>` não manda header, e o
        # que vai na query é assinatura de um recurso só (`CLAUDE.md` regra 6). `None`
        # quando não há foto guardada — a maioria dos contatos esconde a dela, e o front
        # cai nas iniciais.
        "contact_avatar_url": avatars.url_de(
            row.tenant_id, row.contact_id, row.contact_channel_metadata
        ),
        "status": row.status.lower() if row.status else None,
        "assigned_user_id": str(assigned) if assigned is not None else None,
        "priority": row.priority or "normal",
        "tags": row.tags or [],
        "last_message_at": last_msg.isoformat() if last_msg is not None else None,
        "last_message_preview": row.last_message_preview,
        "unread_count": row.unread_count,
        "created_at": created.isoformat() if created is not None else None,
    }


async def get_unclaimed(
    db: AsyncSession,
    tenant_id: UUID,
    limit: int = 20,
    offset: int = 0,
    search: str | None = None,
) -> list[dict]:
    clause, params = _search_sql(search)
    result = await db.execute(
        text(
            f"SELECT {_CONVERSATION_COLUMNS} "
            f"FROM conversations c {_CONTACT_JOIN} "
            "WHERE c.tenant_id = :tenant_id "
            "AND c.assigned_user_id IS NULL "
            "AND c.status != 'RESOLVED' "
            f"{clause} "
            "ORDER BY (c.priority = 'high') DESC, c.last_message_at DESC NULLS LAST "
            "LIMIT :limit OFFSET :offset"
        ),
        {"tenant_id": str(tenant_id), "limit": limit, "offset": offset, **params},
    )
    return [_row_to_dict(row) for row in result.fetchall()]


async def get_assigned_to(
    db: AsyncSession,
    tenant_id: UUID,
    user_id: UUID,
    limit: int = 20,
    offset: int = 0,
    search: str | None = None,
) -> list[dict]:
    clause, params = _search_sql(search)
    result = await db.execute(
        text(
            f"SELECT {_CONVERSATION_COLUMNS} "
            f"FROM conversations c {_CONTACT_JOIN} "
            "WHERE c.tenant_id = :tenant_id "
            "AND c.assigned_user_id = :user_id "
            "AND c.status != 'RESOLVED' "
            f"{clause} "
            "ORDER BY c.last_message_at DESC NULLS LAST "
            "LIMIT :limit OFFSET :offset"
        ),
        {
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
            "limit": limit,
            "offset": offset,
            **params,
        },
    )
    return [_row_to_dict(row) for row in result.fetchall()]


async def get_all(
    db: AsyncSession,
    tenant_id: UUID,
    limit: int = 20,
    offset: int = 0,
    search: str | None = None,
) -> list[dict]:
    clause, params = _search_sql(search)
    result = await db.execute(
        text(
            f"SELECT {_CONVERSATION_COLUMNS} "
            f"FROM conversations c {_CONTACT_JOIN} "
            "WHERE c.tenant_id = :tenant_id "
            f"{clause} "
            "ORDER BY c.last_message_at DESC NULLS LAST "
            "LIMIT :limit OFFSET :offset"
        ),
        {"tenant_id": str(tenant_id), "limit": limit, "offset": offset, **params},
    )
    return [_row_to_dict(row) for row in result.fetchall()]


async def get_conversation_by_id(
    db: AsyncSession,
    tenant_id: UUID,
    conversation_id: UUID,
) -> dict | None:
    # LEFT JOIN: conversa sem sessão de bot é o caso normal (bot desligado, ou o bot
    # nunca teve vez) e não pode sumir do detalhe por causa disso.
    result = await db.execute(
        text(
            f"SELECT {_CONVERSATION_COLUMNS}, bs.skip_reason AS bot_skip_reason, "
            "ct.profile AS contact_profile "
            f"FROM conversations c {_CONTACT_JOIN} "
            "LEFT JOIN bot_sessions bs ON bs.conversation_id = c.id "
            "WHERE c.id = :conversation_id AND c.tenant_id = :tenant_id"
        ),
        {"conversation_id": str(conversation_id), "tenant_id": str(tenant_id)},
    )
    row = result.fetchone()
    if row is None:
        return None
    return {
        **_row_to_dict(row),
        "bot_skip_reason": row.bot_skip_reason,
        # O que a IA sabe deste cliente de atendimentos anteriores. O atendente que assume
        # deixa de começar do zero — é o mesmo ganho, para quem fecha a venda.
        "contact_profile": dict(row.contact_profile or {}),
    }


async def get_conversation_messages(
    db: AsyncSession,
    tenant_id: UUID,
    conversation_id: UUID,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """As `limit` mensagens MAIS RECENTES, devolvidas em ordem cronológica.

    O DESC na subquery é o ponto todo. Com `ORDER BY created_at ASC LIMIT 50` direto, uma
    conversa com mais de 50 mensagens congelava nas 50 PRIMEIRAS e a mensagem nova nunca
    aparecia na thread — em silêncio, e justamente nas conversas mais longas, que são as
    que importam. Mesma forma já usada em ai_context.load_history (era por isso que a IA
    enxergava o contexto recente enquanto o atendente via história antiga).

    Com DESC, `offset` significa "N mensagens mais para trás" — a semântica de carregar
    histórico antigo, que é como a thread pagina.
    """
    result = await db.execute(
        text(
            "SELECT id, direction, content_type, content, status, created_at, "
            "is_internal, is_bot, has_media, transcript_status FROM ("
            "  SELECT id, direction, content_type, content, status, created_at, "
            "  COALESCE(channel_metadata->>'internal', 'false') = 'true' AS is_internal, "
            "  COALESCE(channel_metadata->>'bot', 'false') = 'true' AS is_bot, "
            "  channel_metadata->'media'->>'has_media' AS has_media, "
            "  channel_metadata->'media'->>'transcript_status' AS transcript_status "
            "  FROM messages "
            "  WHERE conversation_id = :conversation_id AND tenant_id = :tenant_id "
            # `id` como desempate: sem ele, mensagens de mesmo instante saíam em
            # ordem arbitrária E instável — a mesma conversa recarregada duas vezes
            # mostrava arranjos diferentes. As bolhas novas já nascem com instantes
            # distintos (clock_timestamp), mas o histórico gravado antes disso está
            # empatado para sempre, e estável é melhor que embaralhado.
            "  ORDER BY created_at DESC, id DESC "
            "  LIMIT :limit OFFSET :offset"
            ") sub ORDER BY created_at ASC, id ASC"
        ),
        {
            "conversation_id": str(conversation_id),
            "tenant_id": str(tenant_id),
            "limit": limit,
            "offset": offset,
        },
    )
    rows = result.fetchall()
    return [
        {
            "id": str(r.id),
            "direction": r.direction.lower() if r.direction else None,
            "content_type": r.content_type.lower() if r.content_type else None,
            "content": r.content,
            "status": r.status.lower() if r.status else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "is_internal": r.is_internal,
            "is_bot": r.is_bot,
            # URL já assinada: `<img src>` não manda header, e a alternativa antiga era
            # pendurar o JWT do atendente na query string. Ver services/media_links.py.
            "media_url": (
                f"/media/{r.id}?{media_links.assinar('media', r.id, tenant_id)}"
                if r.has_media == "true"
                else None
            ),
            "transcript_status": r.transcript_status,
        }
        for r in rows
    ]
