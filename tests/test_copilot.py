"""Testes do copilot de sugestão de resposta (provider stub, sem rede)."""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.services import copilot

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def base_data(db_session: AsyncSession) -> dict:
    tenant_id = uuid.uuid4()
    contact_id = uuid.uuid4()
    conv_id = uuid.uuid4()

    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, 'Tenant Copilot')"),
        {"id": str(tenant_id)},
    )
    await db_session.execute(
        text(
            "INSERT INTO contacts (id, tenant_id, channel, external_id, name) "
            "VALUES (:id, :tid, 'WHATSAPP', '5511911111', 'Cliente Copilot')"
        ),
        {"id": str(contact_id), "tid": str(tenant_id)},
    )
    await db_session.execute(
        text(
            "INSERT INTO conversations (id, tenant_id, contact_id, status, unread_count) "
            "VALUES (:id, :tid, :cid, 'OPEN', 0)"
        ),
        {"id": str(conv_id), "tid": str(tenant_id), "cid": str(contact_id)},
    )
    await db_session.commit()

    yield {"tenant_id": tenant_id, "conv_id": conv_id}

    for stmt in (
        "DELETE FROM messages WHERE tenant_id = :tid",
        "DELETE FROM conversations WHERE tenant_id = :tid",
        "DELETE FROM contacts WHERE tenant_id = :tid",
        "DELETE FROM bot_settings WHERE tenant_id = :tid",
        "DELETE FROM tenants WHERE id = :tid",
    ):
        await db_session.execute(text(stmt), {"tid": str(tenant_id)})
    await db_session.commit()


async def _insert_inbound(db: AsyncSession, tenant_id, conv_id, content: str) -> None:
    await db.execute(
        text(
            "INSERT INTO messages (id, conversation_id, tenant_id, direction, content_type, "
            "content, channel, external_id) VALUES (gen_random_uuid(), :cid, :tid, 'INBOUND', "
            "'TEXT', :content, 'WHATSAPP', :ext)"
        ),
        {
            "cid": str(conv_id),
            "tid": str(tenant_id),
            "content": content,
            "ext": str(uuid.uuid4()),
        },
    )
    await db.commit()


async def test_sugestao_com_historico(db_session, base_data):
    """Provider stub (default sem bot_settings) devolve sugestão determinística."""
    await _insert_inbound(
        db_session, base_data["tenant_id"], base_data["conv_id"], "quero um orçamento"
    )
    suggestion = await copilot.suggest(
        db_session, base_data["tenant_id"], base_data["conv_id"]
    )
    assert suggestion  # stub sempre sugere quando há histórico


async def test_conversa_sem_historico_nao_sugere(db_session, base_data):
    suggestion = await copilot.suggest(
        db_session, base_data["tenant_id"], base_data["conv_id"]
    )
    assert suggestion is None


async def test_conversa_inexistente(db_session, base_data):
    suggestion = await copilot.suggest(db_session, base_data["tenant_id"], uuid.uuid4())
    assert suggestion is None


async def test_conversa_de_outro_tenant_nao_vaza(db_session, base_data):
    """Defesa multi-tenant: conversa existe, mas pertence a outro tenant."""
    await _insert_inbound(
        db_session, base_data["tenant_id"], base_data["conv_id"], "oi"
    )
    outro_tenant = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, 'Outro Tenant')"),
        {"id": str(outro_tenant)},
    )
    await db_session.commit()
    try:
        suggestion = await copilot.suggest(
            db_session, outro_tenant, base_data["conv_id"]
        )
        assert suggestion is None
    finally:
        await db_session.execute(
            text("DELETE FROM tenants WHERE id = :id"), {"id": str(outro_tenant)}
        )
        await db_session.commit()
