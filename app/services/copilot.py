"""Copilot do atendente: sugere a próxima resposta na thread, sob demanda.

Funciona mesmo com o bot automático desligado (ai_enabled só governa o bot);
usa a mesma config do tenant (persona, conhecimento, provider, RAG).
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import llm
from app.services.ai_context import build_knowledge, load_history
from app.services.bot_settings import load_bot_config


async def suggest(
    db: AsyncSession, tenant_id: UUID, conversation_id: UUID
) -> str | None:
    """Sugestão de resposta para a conversa. None quando a IA está indisponível."""
    bot = await load_bot_config(db, tenant_id)

    row = await db.execute(
        text(
            "SELECT ct.name AS contact_name, ct.id AS contact_id, "
            "ct.phone AS contact_phone FROM conversations c "
            "JOIN contacts ct ON ct.id = c.contact_id "
            "WHERE c.id = :conversation_id AND c.tenant_id = :tenant_id"
        ),
        {"conversation_id": str(conversation_id), "tenant_id": str(tenant_id)},
    )
    conv = row.fetchone()
    if conv is None:
        return None

    history = await load_history(db, tenant_id, conversation_id)
    if not history:
        return None
    # Mesma reserva que o agente enxerga: o atendente não pode receber uma sugestão que
    # contradiz o que o bot diria ao mesmo cliente.
    knowledge = await build_knowledge(
        db, tenant_id, bot, history, conv.contact_id, conv.contact_phone
    )

    return await llm.suggest_reply(
        persona=bot.persona,
        knowledge=knowledge,
        history=history,
        contact_name=conv.contact_name or "",
        provider=bot.provider,
        model=bot.model,
        api_key=bot.api_key,
    )
