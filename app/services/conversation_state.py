"""Transições de encerramento disputadas no banco, com evento na mesma transação."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class StateConflict(Exception):
    pass


class ConversationNotFound(Exception):
    pass


async def transition(
    db: AsyncSession, tenant_id: UUID, conversation_id: UUID, user_id: UUID, target: str
) -> bool:
    if target not in {"OPEN", "RESOLVED"}:
        raise StateConflict("Use captura para colocar uma conversa em atendimento.")
    closing = target == "RESOLVED"
    changes = (
        "status = 'RESOLVED', resolved_at = now(), closure_reason = 'human'"
        if closing
        else "status = 'OPEN', resolved_at = NULL, closure_reason = NULL, "
        "assigned_user_id = NULL, assigned_at = NULL"
    )
    condition = (
        "status != 'RESOLVED' AND assigned_user_id = :uid"
        if closing
        else "status = 'RESOLVED'"
    )
    params = {"tid": str(tenant_id), "cid": str(conversation_id), "uid": str(user_id)}
    result = await db.execute(
        text(
            f"UPDATE conversations SET {changes} "
            f"WHERE id = :cid AND tenant_id = :tid AND {condition} RETURNING id"
        ),
        params,
    )
    if result.first() is None:
        current = (
            await db.execute(
                text(
                    "SELECT status, assigned_user_id FROM conversations WHERE id=:cid AND tenant_id=:tid"
                ),
                params,
            )
        ).first()
        if current is None:
            raise ConversationNotFound
        if current.status == target and (
            not closing or current.assigned_user_id == user_id
        ):
            return False
        raise StateConflict(
            "A conversa mudou ou pertence a outro atendente. Atualize a lista."
        )
    await db.execute(
        text(
            "INSERT INTO conversation_events "
            "(id, conversation_id, tenant_id, action, actor_user_id, created_at) "
            "VALUES (gen_random_uuid(), :cid, :tid, :action, :uid, now())"
        ),
        {**params, "action": "RESOLVED" if closing else "REOPENED"},
    )
    if not closing:
        await db.execute(
            text(
                "UPDATE bot_sessions SET bot_disabled=false, bot_disabled_until=NULL, "
                "handoff=false, post_handoff_turns=0, ai_failures=0, skip_reason=NULL "
                "WHERE conversation_id=:cid AND tenant_id=:tid"
            ),
            params,
        )
    return True
