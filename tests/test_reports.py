"""Testes das agregações de relatório (banco real, isolamento de tenant)."""

import uuid
from datetime import date, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.services import reports

pytestmark = pytest.mark.asyncio

TODAY = date.today()
FROM = TODAY - timedelta(days=7)
TO = TODAY + timedelta(days=1)


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def seeded(db_session: AsyncSession) -> dict:
    tid = uuid.uuid4()
    other_tid = uuid.uuid4()
    user_id = uuid.uuid4()
    contact_id = uuid.uuid4()
    resolved_conv = uuid.uuid4()
    open_conv = uuid.uuid4()

    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:i, 'Rep'), (:o, 'Outro Rep')"),
        {"i": str(tid), "o": str(other_tid)},
    )
    await db_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, hashed_password, full_name, is_active) "
            "VALUES (:u, :t, 'ana@rep.com', 'x', 'Ana', true)"
        ),
        {"u": str(user_id), "t": str(tid)},
    )
    await db_session.execute(
        text(
            "INSERT INTO contacts (id, tenant_id, channel, external_id, name) "
            "VALUES (:c, :t, 'WHATSAPP', '551190000', 'Cliente')"
        ),
        {"c": str(contact_id), "t": str(tid)},
    )
    # conversa resolvida: created hoje, first_response +2min, resolved +1h
    await db_session.execute(
        text(
            "INSERT INTO conversations (id, tenant_id, contact_id, status, unread_count, "
            "created_at, first_response_at, resolved_at, assigned_user_id) "
            "VALUES (:id, :t, :c, 'RESOLVED', 0, now(), now() + interval '2 minutes', "
            "now() + interval '1 hour', :u)"
        ),
        {
            "id": str(resolved_conv),
            "t": str(tid),
            "c": str(contact_id),
            "u": str(user_id),
        },
    )
    # conversa aberta e não capturada
    await db_session.execute(
        text(
            "INSERT INTO conversations (id, tenant_id, contact_id, status, unread_count, created_at) "
            "VALUES (:id, :t, :c, 'OPEN', 1, now())"
        ),
        {"id": str(open_conv), "t": str(tid), "c": str(contact_id)},
    )
    # eventos e mensagens do atendente
    for action in ("CLAIMED", "RESOLVED"):
        await db_session.execute(
            text(
                "INSERT INTO conversation_events (id, conversation_id, tenant_id, action, actor_user_id, created_at) "
                "VALUES (gen_random_uuid(), :conv, :t, :a, :u, now())"
            ),
            {"conv": str(resolved_conv), "t": str(tid), "a": action, "u": str(user_id)},
        )
    for direction, sender in (("INBOUND", None), ("OUTBOUND", user_id)):
        await db_session.execute(
            text(
                "INSERT INTO messages (id, conversation_id, tenant_id, direction, content_type, "
                "content, channel, external_id, sender_user_id, created_at) "
                "VALUES (gen_random_uuid(), :conv, :t, :d, 'TEXT', 'oi', 'WHATSAPP', :ext, :s, now())"
            ),
            {
                "conv": str(resolved_conv),
                "t": str(tid),
                "d": direction,
                "ext": str(uuid.uuid4()),
                "s": str(sender) if sender else None,
            },
        )
    # bot_session com handoff
    await db_session.execute(
        text(
            "INSERT INTO bot_sessions (conversation_id, tenant_id, variables, handoff, updated_at) "
            "VALUES (:conv, :t, '{}'::jsonb, true, now())"
        ),
        {"conv": str(resolved_conv), "t": str(tid)},
    )
    await db_session.commit()

    yield {"tenant_id": tid, "other_tenant_id": other_tid, "user_id": user_id}

    for t in (tid, other_tid):
        for tb in (
            "messages",
            "bot_sessions",
            "conversation_events",
            "conversations",
            "contacts",
            "users",
        ):
            await db_session.execute(
                text(f"DELETE FROM {tb} WHERE tenant_id = :t"), {"t": str(t)}
            )
        await db_session.execute(
            text("DELETE FROM tenants WHERE id = :t"), {"t": str(t)}
        )
    await db_session.commit()


async def test_overview(db_session, seeded):
    o = await reports.overview(db_session, seeded["tenant_id"], FROM, TO)
    assert o["open_count"] == 1
    assert o["unclaimed_count"] == 1
    assert o["resolved_count"] == 1
    assert o["avg_resolution_seconds"] == 3600  # 1h
    assert o["avg_first_response_seconds"] == 120  # 2min


async def test_volume(db_session, seeded):
    pts = await reports.volume(db_session, seeded["tenant_id"], FROM, TO)
    total = sum(p["total"] for p in pts)
    assert total == 2  # 1 inbound + 1 outbound
    assert {p["direction"] for p in pts} == {"inbound", "outbound"}


async def test_agents(db_session, seeded):
    ags = await reports.agents(db_session, seeded["tenant_id"], FROM, TO)
    ana = next(a for a in ags if a["name"] == "Ana")
    assert ana["claimed"] == 1
    assert ana["resolved"] == 1
    assert ana["messages_sent"] == 1


async def test_bot(db_session, seeded):
    b = await reports.bot(db_session, seeded["tenant_id"], FROM, TO)
    assert b["triaged"] == 1
    assert b["handoffs"] == 1
    assert b["failures"] == 0


async def test_isolamento_de_tenant(db_session, seeded):
    """O outro tenant não enxerga nada do tenant semeado."""
    o = await reports.overview(db_session, seeded["other_tenant_id"], FROM, TO)
    assert o["open_count"] == 0
    assert o["resolved_count"] == 0
    ags = await reports.agents(db_session, seeded["other_tenant_id"], FROM, TO)
    assert ags == []


async def test_inactivity_is_not_bot_success_and_legacy_stays_unknown(
    db_session, seeded
):
    tid = str(seeded["tenant_id"])
    await db_session.execute(
        text(
            "UPDATE conversations SET closure_reason='inactivity',assigned_user_id=NULL WHERE tenant_id=:tid AND status='RESOLVED'"
        ),
        {"tid": tid},
    )
    await db_session.commit()
    result = await reports.bot(db_session, seeded["tenant_id"], FROM, TO)
    assert result["closed_by_inactivity"] == 1
    assert result["resolved_by_bot"] == 0
    await db_session.execute(
        text(
            "UPDATE conversations SET closure_reason='legacy_unknown' WHERE tenant_id=:tid AND status='RESOLVED'"
        ),
        {"tid": tid},
    )
    await db_session.commit()
    result = await reports.bot(db_session, seeded["tenant_id"], FROM, TO)
    assert result["closed_by_inactivity"] == result["resolved_by_bot"] == 0
