import asyncio
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.services.assignment import (
    claim_conversation,
    release_conversation,
    transfer_conversation,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def base_data(db_session: AsyncSession) -> dict:
    tenant_id = uuid.uuid4()
    user1_id = uuid.uuid4()
    user2_id = uuid.uuid4()
    contact_id = uuid.uuid4()
    conv_id = uuid.uuid4()

    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, :name)"),
        {"id": str(tenant_id), "name": "Test Tenant Assignment"},
    )
    for uid, email in [(user1_id, "u1@assign.com"), (user2_id, "u2@assign.com")]:
        await db_session.execute(
            text(
                "INSERT INTO users (id, tenant_id, email, hashed_password, is_active) "
                "VALUES (:id, :tenant_id, :email, 'x', true)"
            ),
            {"id": str(uid), "tenant_id": str(tenant_id), "email": email},
        )
    await db_session.execute(
        text(
            "INSERT INTO contacts (id, tenant_id, channel, external_id, name) "
            "VALUES (:id, :tenant_id, 'WHATSAPP', :ext_id, 'Test')"
        ),
        {"id": str(contact_id), "tenant_id": str(tenant_id), "ext_id": "5511900000"},
    )
    await db_session.execute(
        text(
            "INSERT INTO conversations (id, tenant_id, contact_id, status, unread_count) "
            "VALUES (:id, :tenant_id, :contact_id, 'OPEN', 0)"
        ),
        {
            "id": str(conv_id),
            "tenant_id": str(tenant_id),
            "contact_id": str(contact_id),
        },
    )
    await db_session.commit()

    yield {
        "tenant_id": tenant_id,
        "user1_id": user1_id,
        "user2_id": user2_id,
        "conv_id": conv_id,
    }

    await db_session.execute(
        text("DELETE FROM bot_sessions WHERE tenant_id = :tid"), {"tid": str(tenant_id)}
    )
    await db_session.execute(
        text("DELETE FROM conversation_events WHERE tenant_id = :tid"),
        {"tid": str(tenant_id)},
    )
    await db_session.execute(
        text("DELETE FROM conversations WHERE tenant_id = :tid"),
        {"tid": str(tenant_id)},
    )
    await db_session.execute(
        text("DELETE FROM contacts WHERE tenant_id = :tid"), {"tid": str(tenant_id)}
    )
    await db_session.execute(
        text("DELETE FROM users WHERE tenant_id = :tid"), {"tid": str(tenant_id)}
    )
    await db_session.execute(
        text("DELETE FROM tenants WHERE id = :id"), {"id": str(tenant_id)}
    )
    await db_session.commit()


async def _get_assigned_user(db: AsyncSession, conv_id: uuid.UUID) -> uuid.UUID | None:
    row = await db.execute(
        text("SELECT assigned_user_id FROM conversations WHERE id = :id"),
        {"id": str(conv_id)},
    )
    val = row.scalar_one_or_none()
    return val


async def test_claim_success(base_data: dict) -> None:
    async with AsyncSessionLocal() as db:
        result = await claim_conversation(
            db, base_data["conv_id"], base_data["user1_id"], base_data["tenant_id"]
        )
    assert result is True
    async with AsyncSessionLocal() as db:
        assigned = await _get_assigned_user(db, base_data["conv_id"])
    assert str(assigned) == str(base_data["user1_id"])


async def test_claim_race_condition(base_data: dict) -> None:
    async def try_claim(user_id: uuid.UUID) -> bool:
        async with AsyncSessionLocal() as db:
            return await claim_conversation(
                db, base_data["conv_id"], user_id, base_data["tenant_id"]
            )

    result1, result2 = await asyncio.gather(
        try_claim(base_data["user1_id"]),
        try_claim(base_data["user2_id"]),
    )

    assert sum([result1, result2]) == 1

    async with AsyncSessionLocal() as db:
        assigned = await _get_assigned_user(db, base_data["conv_id"])
    assert assigned is not None
    assert str(assigned) in [str(base_data["user1_id"]), str(base_data["user2_id"])]


async def test_claim_already_assigned(base_data: dict) -> None:
    async with AsyncSessionLocal() as db:
        await claim_conversation(
            db, base_data["conv_id"], base_data["user1_id"], base_data["tenant_id"]
        )
    async with AsyncSessionLocal() as db:
        result = await claim_conversation(
            db, base_data["conv_id"], base_data["user2_id"], base_data["tenant_id"]
        )
    assert result is False


async def test_release_success(base_data: dict) -> None:
    async with AsyncSessionLocal() as db:
        await claim_conversation(
            db, base_data["conv_id"], base_data["user1_id"], base_data["tenant_id"]
        )
    async with AsyncSessionLocal() as db:
        result = await release_conversation(
            db, base_data["conv_id"], base_data["user1_id"], base_data["tenant_id"]
        )
    assert result is True
    async with AsyncSessionLocal() as db:
        assigned = await _get_assigned_user(db, base_data["conv_id"])
    assert assigned is None


async def test_release_wrong_user(base_data: dict) -> None:
    async with AsyncSessionLocal() as db:
        await claim_conversation(
            db, base_data["conv_id"], base_data["user1_id"], base_data["tenant_id"]
        )
    async with AsyncSessionLocal() as db:
        result = await release_conversation(
            db, base_data["conv_id"], base_data["user2_id"], base_data["tenant_id"]
        )
    assert result is False
    async with AsyncSessionLocal() as db:
        assigned = await _get_assigned_user(db, base_data["conv_id"])
    assert str(assigned) == str(base_data["user1_id"])


async def test_transfer_success(base_data: dict) -> None:
    async with AsyncSessionLocal() as db:
        await claim_conversation(
            db, base_data["conv_id"], base_data["user1_id"], base_data["tenant_id"]
        )
    async with AsyncSessionLocal() as db:
        result = await transfer_conversation(
            db,
            base_data["conv_id"],
            base_data["user1_id"],
            base_data["user2_id"],
            base_data["tenant_id"],
        )
    assert result is True
    async with AsyncSessionLocal() as db:
        assigned = await _get_assigned_user(db, base_data["conv_id"])
    assert str(assigned) == str(base_data["user2_id"])


async def test_release_reativa_o_bot(base_data: dict) -> None:
    """Liberar destrava a sessão do bot — senão a conversa fica muda para sempre.

    bot_disabled e handoff só eram LIGADOS (3 falhas seguidas da IA ou teto pós-handoff)
    e nada os desligava: nem release, nem o toggle do painel. Uma conversa que falhasse
    uma vez nunca mais era atendida pela IA.
    """
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO bot_sessions "
                "(conversation_id, tenant_id, variables, ai_failures, post_handoff_turns, "
                " handoff, bot_disabled, skip_reason) "
                'VALUES (:cid, :tid, \'{"intake": {"nome": "Ana"}}\'::jsonb, 3, 7, '
                " true, true, 'sessao_desativada')"
            ),
            {"cid": str(base_data["conv_id"]), "tid": str(base_data["tenant_id"])},
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        await claim_conversation(
            db, base_data["conv_id"], base_data["user1_id"], base_data["tenant_id"]
        )
    async with AsyncSessionLocal() as db:
        assert (
            await release_conversation(
                db, base_data["conv_id"], base_data["user1_id"], base_data["tenant_id"]
            )
            is True
        )

    async with AsyncSessionLocal() as db:
        row = await db.execute(
            text(
                "SELECT bot_disabled, handoff, post_handoff_turns, ai_failures, "
                "skip_reason, variables FROM bot_sessions WHERE conversation_id = :cid"
            ),
            {"cid": str(base_data["conv_id"])},
        )
        s = row.fetchone()

    assert s.bot_disabled is False
    assert s.handoff is False
    assert s.post_handoff_turns == 0
    assert s.ai_failures == 0
    assert s.skip_reason is None
    # Os dados já coletados do cliente sobrevivem: só o estado de desistência é zerado.
    assert s.variables == {"intake": {"nome": "Ana"}}


async def test_release_sem_sessao_de_bot(base_data: dict) -> None:
    """Conversa que nunca teve bot não pode quebrar o release (o UPDATE casa 0 linhas)."""
    async with AsyncSessionLocal() as db:
        await claim_conversation(
            db, base_data["conv_id"], base_data["user1_id"], base_data["tenant_id"]
        )
    async with AsyncSessionLocal() as db:
        assert (
            await release_conversation(
                db, base_data["conv_id"], base_data["user1_id"], base_data["tenant_id"]
            )
            is True
        )


async def test_transfer_wrong_owner(base_data: dict) -> None:
    async with AsyncSessionLocal() as db:
        await claim_conversation(
            db, base_data["conv_id"], base_data["user1_id"], base_data["tenant_id"]
        )
    async with AsyncSessionLocal() as db:
        result = await transfer_conversation(
            db,
            base_data["conv_id"],
            base_data["user2_id"],
            base_data["user1_id"],
            base_data["tenant_id"],
        )
    assert result is False


async def test_transfer_para_usuario_de_outro_tenant(base_data: dict) -> None:
    """Destino tem que ser do mesmo tenant.

    `to_user_id` vinha do corpo do request direto para o SET, e só a CONVERSA era
    validada por tenant. Transferir para alguém de outro salão passava — e a conversa
    sumia de todas as filas legítimas: a de não-atribuídas exige `assigned_user_id IS
    NULL`, e a de atribuídas filtra por tenant. Atendimento enterrado sem deixar rastro.
    """
    outro_tenant = uuid.uuid4()
    estranho = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("INSERT INTO tenants (id, name) VALUES (:id, 'Outro Salão')"),
            {"id": str(outro_tenant)},
        )
        await db.execute(
            text(
                "INSERT INTO users (id, tenant_id, email, hashed_password, is_active) "
                "VALUES (:id, :t, 'intruso@outro.com', 'x', true)"
            ),
            {"id": str(estranho), "t": str(outro_tenant)},
        )
        await db.commit()

    try:
        async with AsyncSessionLocal() as db:
            await claim_conversation(
                db, base_data["conv_id"], base_data["user1_id"], base_data["tenant_id"]
            )
        async with AsyncSessionLocal() as db:
            result = await transfer_conversation(
                db,
                base_data["conv_id"],
                base_data["user1_id"],
                estranho,
                base_data["tenant_id"],
            )
        assert result is False

        # E a conversa continua com o dono original, não órfã.
        async with AsyncSessionLocal() as db:
            dono = await db.execute(
                text("SELECT assigned_user_id FROM conversations WHERE id = :c"),
                {"c": str(base_data["conv_id"])},
            )
            assert dono.scalar_one() == base_data["user1_id"]
    finally:
        async with AsyncSessionLocal() as db:
            await db.execute(
                text("DELETE FROM users WHERE tenant_id = :t"),
                {"t": str(outro_tenant)},
            )
            await db.execute(
                text("DELETE FROM tenants WHERE id = :t"), {"t": str(outro_tenant)}
            )
            await db.commit()


async def test_transfer_para_usuario_inativo(base_data: dict) -> None:
    """Fila de um desativado é fila que ninguém olha."""
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("UPDATE users SET is_active = false WHERE id = :u"),
            {"u": str(base_data["user2_id"])},
        )
        await db.commit()
        await claim_conversation(
            db, base_data["conv_id"], base_data["user1_id"], base_data["tenant_id"]
        )
    async with AsyncSessionLocal() as db:
        result = await transfer_conversation(
            db,
            base_data["conv_id"],
            base_data["user1_id"],
            base_data["user2_id"],
            base_data["tenant_id"],
        )
    assert result is False


async def test_only_owner_can_resolve_and_repetition_is_noop(db_session, base_data):
    from app.services.conversation_state import transition, StateConflict

    d = base_data
    await claim_conversation(db_session, d["conv_id"], d["user1_id"], d["tenant_id"])
    with pytest.raises(StateConflict):
        await transition(
            db_session, d["tenant_id"], d["conv_id"], d["user2_id"], "RESOLVED"
        )
    assert await transition(
        db_session, d["tenant_id"], d["conv_id"], d["user1_id"], "RESOLVED"
    )
    await db_session.commit()
    first = await db_session.scalar(
        text("SELECT resolved_at FROM conversations WHERE id=:id"),
        {"id": str(d["conv_id"])},
    )
    assert not await transition(
        db_session, d["tenant_id"], d["conv_id"], d["user1_id"], "RESOLVED"
    )
    await db_session.commit()
    assert first == await db_session.scalar(
        text("SELECT resolved_at FROM conversations WHERE id=:id"),
        {"id": str(d["conv_id"])},
    )
    count = await db_session.scalar(
        text(
            "SELECT count(*) FROM conversation_events WHERE conversation_id=:id AND action='RESOLVED'"
        ),
        {"id": str(d["conv_id"])},
    )
    assert count == 1


async def test_release_and_transfer_cannot_revive_closed_conversation(
    db_session, base_data
):
    from app.services.conversation_state import transition

    d = base_data
    await claim_conversation(db_session, d["conv_id"], d["user1_id"], d["tenant_id"])
    await transition(
        db_session, d["tenant_id"], d["conv_id"], d["user1_id"], "RESOLVED"
    )
    await db_session.commit()
    assert not await release_conversation(
        db_session, d["conv_id"], d["user1_id"], d["tenant_id"]
    )
    assert not await transfer_conversation(
        db_session, d["conv_id"], d["user1_id"], d["user2_id"], d["tenant_id"]
    )
    row = (
        await db_session.execute(
            text("SELECT status,resolved_at FROM conversations WHERE id=:id"),
            {"id": str(d["conv_id"])},
        )
    ).one()
    assert row.status == "RESOLVED" and row.resolved_at is not None


async def test_teammate_reopens_and_clears_closure(db_session, base_data):
    from app.services.conversation_state import transition, ConversationNotFound

    d = base_data
    await claim_conversation(db_session, d["conv_id"], d["user1_id"], d["tenant_id"])
    await transition(
        db_session, d["tenant_id"], d["conv_id"], d["user1_id"], "RESOLVED"
    )
    await db_session.commit()
    with pytest.raises(ConversationNotFound):
        await transition(db_session, uuid.uuid4(), d["conv_id"], d["user1_id"], "OPEN")
    assert await transition(
        db_session, d["tenant_id"], d["conv_id"], d["user2_id"], "OPEN"
    )
    await db_session.commit()
    row = (
        await db_session.execute(
            text(
                "SELECT status,resolved_at,closure_reason,assigned_user_id FROM conversations WHERE id=:id"
            ),
            {"id": str(d["conv_id"])},
        )
    ).one()
    assert row.status == "OPEN"
    assert (
        row.resolved_at is None
        and row.closure_reason is None
        and row.assigned_user_id is None
    )


async def test_concurrent_closure_has_one_winner(db_session, base_data):
    from app.services.conversation_state import transition

    d = base_data
    await claim_conversation(db_session, d["conv_id"], d["user1_id"], d["tenant_id"])

    async def close():
        async with AsyncSessionLocal() as session:
            changed = await transition(
                session, d["tenant_id"], d["conv_id"], d["user1_id"], "RESOLVED"
            )
            await session.commit()
            return changed

    assert sorted(await asyncio.gather(close(), close())) == [False, True]
