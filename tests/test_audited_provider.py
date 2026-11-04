"""External-effect audit must precede dispatch and must never retry ambiguity."""

from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.channels.audited import AuditedProvider
from app.channels.base import ChannelProvider
from app.channels.types import OutboundMessage, SendResult
from app.core.config import settings
from app.core.database import AsyncSessionLocal, WorkerSessionLocal
from app.models.enums import Channel, ContentType
from app.services import audit


@pytest_asyncio.fixture
async def audited_connection():
    assert (
        settings.ENV == "test"
    )  # DSN safety is enforced centrally by pytest_sessionstart.
    tenant_id, connection_id = uuid4(), uuid4()
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("INSERT INTO tenants(id,name) VALUES(:id,'External audit fixture')"),
            {"id": tenant_id},
        )
        await session.execute(
            text(
                "INSERT INTO channel_connections(id,tenant_id,channel,credentials,is_active) VALUES(:id,:tid,'WHATSAPP','{}',true)"
            ),
            {"id": connection_id, "tid": tenant_id},
        )
        await session.commit()
    yield tenant_id, connection_id
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("DELETE FROM tenants WHERE id=:id"), {"id": tenant_id}
        )
        await session.commit()


class FakeProvider(ChannelProvider):
    def __init__(self, tenant_id, *, fail=False):
        self.tenant_id = tenant_id
        self.fail = fail
        self.calls = 0
        self.intent_visible = False

    def verify_webhook(self, headers, body):
        return True

    def parse_webhook(self, headers, body):
        return []

    async def send(self, message):
        self.calls += 1
        async with WorkerSessionLocal() as session:
            self.intent_visible = bool(
                await session.scalar(
                    text(
                        "SELECT count(*) FROM audit_events WHERE tenant_id=:tid AND action='channel.send' AND result='pending'"
                    ),
                    {"tid": self.tenant_id},
                )
            )
        if self.fail:
            raise TimeoutError("provider may have accepted private-message-content")
        return SendResult(success=True, external_id="private-provider-id")


def message(connection_id):
    return OutboundMessage(
        channel=Channel.WHATSAPP,
        channel_connection_id=connection_id,
        to_external_id="private-destination",
        content_type=ContentType.TEXT,
        content="private-message-content",
        channel_metadata={"token": "private-secret"},
    )


@pytest.mark.asyncio
async def test_intent_committed_before_send_and_no_payload(audited_connection):
    tenant_id, connection_id = audited_connection
    fake = FakeProvider(tenant_id)
    result = await AuditedProvider(fake).send(message(connection_id))
    assert result.success and fake.calls == 1 and fake.intent_visible
    async with AsyncSessionLocal() as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM audit_events WHERE tenant_id=:tid AND action='channel.send' ORDER BY created_at"
                    ),
                    {"tid": tenant_id},
                )
            )
            .mappings()
            .all()
        )
    assert [row["result"] for row in rows] == ["pending", "accepted"]
    assert rows[0]["metadata"]["operation_id"] == rows[1]["metadata"]["operation_id"]
    assert rows[0]["correlation_id"] == rows[1]["correlation_id"]
    for private in (
        "private-message-content",
        "private-destination",
        "private-secret",
        "private-provider-id",
    ):
        assert private not in str(rows)


@pytest.mark.asyncio
async def test_timeout_unknown_and_never_retried(audited_connection):
    tenant_id, connection_id = audited_connection
    fake = FakeProvider(tenant_id, fail=True)
    with pytest.raises(TimeoutError):
        await AuditedProvider(fake).send(message(connection_id))
    assert fake.calls == 1 and fake.intent_visible
    async with AsyncSessionLocal() as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM audit_events WHERE tenant_id=:tid AND action='channel.send' ORDER BY created_at"
                    ),
                    {"tid": tenant_id},
                )
            )
            .mappings()
            .all()
        )
    assert [row["result"] for row in rows] == ["pending", "unknown"]
    assert rows[-1]["reason"] == "provider_exception"
    assert "private-message-content" not in str(rows)


@pytest.mark.asyncio
async def test_missing_tenant_and_failed_intent_prevent_dispatch(
    audited_connection, monkeypatch
):
    tenant_id, connection_id = audited_connection
    fake = FakeProvider(tenant_id)
    with pytest.raises(ValueError):
        await AuditedProvider(fake).send(message(uuid4()))
    assert fake.calls == 0

    async def reject(*args, **kwargs):
        raise RuntimeError("audit_unavailable")

    monkeypatch.setattr(audit, "record", reject)
    with pytest.raises(RuntimeError):
        await AuditedProvider(fake).send(message(connection_id))
    assert fake.calls == 0


@pytest.mark.asyncio
async def test_result_audit_failure_preserves_known_remote_acceptance(
    audited_connection, monkeypatch
):
    tenant_id, connection_id = audited_connection
    fake = FakeProvider(tenant_id)
    original = audit.record

    async def reject_result(*args, **kwargs):
        if kwargs.get("result") == "accepted":
            raise RuntimeError("audit_unavailable_after_dispatch")
        return await original(*args, **kwargs)

    monkeypatch.setattr(audit, "record", reject_result)
    result = await AuditedProvider(fake).send(message(connection_id))
    assert result.success and fake.calls == 1


@pytest.mark.asyncio
async def test_bot_turn_correlation_reaches_trigger_and_external_send(
    audited_connection,
):
    tenant_id, connection_id = audited_connection
    fake = FakeProvider(tenant_id)
    with audit.actor_context(tenant_id, actor_type="bot"):
        correlation_id = audit.get_context()["correlation_id"]
        async with WorkerSessionLocal() as session:
            await session.execute(
                text("UPDATE channel_connections SET is_active=false WHERE id=:id"),
                {"id": connection_id},
            )
            await audit.record(
                session, tenant_id, "bot.decision", "channel", connection_id
            )
            await session.commit()
        await AuditedProvider(fake).send(message(connection_id))
        with pytest.raises(ValueError):
            async with WorkerSessionLocal() as session:
                await audit.record(
                    session, uuid4(), "bot.decision", "channel", connection_id
                )
    assert audit.get_context()["tenant_id"] is None
    async with AsyncSessionLocal() as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT actor_type,actor_user_id,correlation_id FROM audit_events WHERE tenant_id=:tid AND (action='db.update' OR action='bot.decision' OR action='channel.send')"
                    ),
                    {"tid": tenant_id},
                )
            )
            .mappings()
            .all()
        )
        assert len(rows) == 4
        assert all(
            row["actor_type"] == "bot"
            and row["actor_user_id"] is None
            and row["correlation_id"] == correlation_id
            for row in rows
        )
