from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def claim_conversation(
    db: AsyncSession, conversation_id: UUID, user_id: UUID, tenant_id: UUID
) -> bool:
    result = await db.execute(
        text(
            "UPDATE conversations "
            "SET assigned_user_id = :user_id, "
            "    assigned_at = now(), "
            "    status = 'PENDING' "
            "WHERE id = :conversation_id "
            "  AND tenant_id = :tenant_id "
            "  AND assigned_user_id IS NULL "
            "  AND status != 'RESOLVED' "
            "RETURNING id"
        ),
        {
            "user_id": str(user_id),
            "conversation_id": str(conversation_id),
            "tenant_id": str(tenant_id),
        },
    )
    if result.fetchone() is None:
        return False

    await db.execute(
        text(
            "INSERT INTO conversation_events "
            "(id, conversation_id, tenant_id, action, actor_user_id, target_user_id, created_at) "
            "VALUES (gen_random_uuid(), :conversation_id, :tenant_id, :action, :actor_user_id, :target_user_id, now())"
        ),
        {
            "conversation_id": str(conversation_id),
            "tenant_id": str(tenant_id),
            "action": "CLAIMED",
            "actor_user_id": str(user_id),
            "target_user_id": None,
        },
    )
    await db.commit()
    return True


async def release_conversation(
    db: AsyncSession, conversation_id: UUID, user_id: UUID, tenant_id: UUID
) -> bool:
    result = await db.execute(
        text(
            "UPDATE conversations "
            "SET assigned_user_id = NULL, "
            "    assigned_at = NULL, "
            "    status = 'OPEN' "
            "WHERE id = :conversation_id "
            "  AND tenant_id = :tenant_id "
            "  AND assigned_user_id = :user_id "
            "  AND status != 'RESOLVED' "
            "RETURNING id"
        ),
        {
            "conversation_id": str(conversation_id),
            "tenant_id": str(tenant_id),
            "user_id": str(user_id),
        },
    )
    if result.fetchone() is None:
        return False

    # Liberar = devolver a conversa para a fila E para o bot. Sem isto, `bot_disabled` e
    # `handoff` (ligados por falhas seguidas da IA ou pelo teto pós-handoff) nunca voltam
    # a false: a conversa fica muda para sempre e religar o toggle do painel não muda nada.
    # `variables` fica intacto de propósito — os dados já coletados do cliente continuam
    # valendo, o que se perde é só o estado de desistência.
    await db.execute(
        text(
            "UPDATE bot_sessions SET bot_disabled = false, bot_disabled_until = NULL, "
            "handoff = false, post_handoff_turns = 0, ai_failures = 0, "
            "skip_reason = NULL, updated_at = now() "
            "WHERE conversation_id = :conversation_id AND tenant_id = :tenant_id"
        ),
        {"conversation_id": str(conversation_id), "tenant_id": str(tenant_id)},
    )

    await db.execute(
        text(
            "INSERT INTO conversation_events "
            "(id, conversation_id, tenant_id, action, actor_user_id, target_user_id, created_at) "
            "VALUES (gen_random_uuid(), :conversation_id, :tenant_id, :action, :actor_user_id, :target_user_id, now())"
        ),
        {
            "conversation_id": str(conversation_id),
            "tenant_id": str(tenant_id),
            "action": "RELEASED",
            "actor_user_id": str(user_id),
            "target_user_id": None,
        },
    )
    await db.commit()
    return True


async def transfer_conversation(
    db: AsyncSession,
    conversation_id: UUID,
    from_user_id: UUID,
    to_user_id: UUID,
    tenant_id: UUID,
) -> bool:
    """Transfere a conversa para outro atendente DO MESMO TENANT.

    O destino é conferido no próprio WHERE, junto com a condição de posse — mesma
    disciplina do resto do módulo. Sem essa checagem, `to_user_id` ia direto do corpo do
    request para o SET: só a conversa era validada por tenant, o usuário de destino não.
    Transferir para um usuário de outro salão passava, e a conversa sumia de todas as
    filas legítimas (a fila de não-atribuídas exige `assigned_user_id IS NULL`, e a de
    atribuídas filtra por tenant) — um jeito silencioso de enterrar atendimento.
    """
    result = await db.execute(
        text(
            "UPDATE conversations "
            "SET assigned_user_id = :to_user_id, "
            "    assigned_at = now() "
            "WHERE id = :conversation_id "
            "  AND tenant_id = :tenant_id "
            "  AND assigned_user_id = :from_user_id "
            "  AND status != 'RESOLVED' "
            "  AND EXISTS ("
            "    SELECT 1 FROM users u "
            "    WHERE u.id = :to_user_id AND u.tenant_id = :tenant_id "
            "      AND u.is_active = true"
            "  ) "
            "RETURNING id"
        ),
        {
            "to_user_id": str(to_user_id),
            "conversation_id": str(conversation_id),
            "tenant_id": str(tenant_id),
            "from_user_id": str(from_user_id),
        },
    )
    if result.fetchone() is None:
        return False

    await db.execute(
        text(
            "INSERT INTO conversation_events "
            "(id, conversation_id, tenant_id, action, actor_user_id, target_user_id, created_at) "
            "VALUES (gen_random_uuid(), :conversation_id, :tenant_id, :action, :actor_user_id, :target_user_id, now())"
        ),
        {
            "conversation_id": str(conversation_id),
            "tenant_id": str(tenant_id),
            "action": "TRANSFERRED",
            "actor_user_id": str(from_user_id),
            "target_user_id": str(to_user_id),
        },
    )
    await db.commit()
    return True
