"""Testes do CRUD de respostas rápidas (isolamento de tenant)."""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.services import quick_replies

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def tenant(db_session: AsyncSession) -> dict:
    tid = uuid.uuid4()
    uid = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:t, 'QR')"), {"t": str(tid)}
    )
    await db_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, hashed_password, is_active) "
            "VALUES (:u, :t, 'qr@x.com', 'x', true)"
        ),
        {"u": str(uid), "t": str(tid)},
    )
    await db_session.commit()
    yield {"tenant_id": tid, "user_id": uid}
    await db_session.execute(
        text("DELETE FROM quick_replies WHERE tenant_id = :t"), {"t": str(tid)}
    )
    await db_session.execute(
        text("DELETE FROM users WHERE tenant_id = :t"), {"t": str(tid)}
    )
    await db_session.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": str(tid)})
    await db_session.commit()


async def test_create_e_list(db_session, tenant):
    created = await quick_replies.create(
        db_session,
        tenant["tenant_id"],
        tenant["user_id"],
        "/Horário",
        "Horário",
        "9h às 18h",
    )
    assert created is not None
    assert (
        created["shortcut"] == "horário"
    )  # normalizado: sem /, minúsculo (acento preservado)
    rows = await quick_replies.list_all(db_session, tenant["tenant_id"])
    assert len(rows) == 1
    assert rows[0]["content"] == "9h às 18h"


async def test_atalho_duplicado_conflita(db_session, tenant):
    await quick_replies.create(
        db_session, tenant["tenant_id"], tenant["user_id"], "oi", "Oi", "Olá!"
    )
    dup = await quick_replies.create(
        db_session, tenant["tenant_id"], tenant["user_id"], "oi", "Oi2", "Oi de novo"
    )
    assert dup is None


async def test_update(db_session, tenant):
    created = await quick_replies.create(
        db_session, tenant["tenant_id"], tenant["user_id"], "x", "X", "antigo"
    )
    updated = await quick_replies.update(
        db_session, tenant["tenant_id"], uuid.UUID(created["id"]), "x", "X", "novo"
    )
    assert updated["content"] == "novo"


async def test_delete(db_session, tenant):
    created = await quick_replies.create(
        db_session, tenant["tenant_id"], tenant["user_id"], "d", "D", "del"
    )
    assert (
        await quick_replies.delete(
            db_session, tenant["tenant_id"], uuid.UUID(created["id"])
        )
        is True
    )
    assert await quick_replies.list_all(db_session, tenant["tenant_id"]) == []


async def test_isolamento_de_tenant(db_session, tenant):
    created = await quick_replies.create(
        db_session, tenant["tenant_id"], tenant["user_id"], "s", "S", "segredo"
    )
    outro = uuid.uuid4()
    # outro tenant não vê nem consegue apagar
    assert await quick_replies.list_all(db_session, outro) == []
    assert (
        await quick_replies.delete(db_session, outro, uuid.UUID(created["id"])) is False
    )
