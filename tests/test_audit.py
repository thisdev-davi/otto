"""Isolated-PostgreSQL evidence for audit privacy, isolation and atomicity."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import bcrypt
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.api import audit as audit_api
from app.api import auth
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.services import audit

app = FastAPI()
app.include_router(auth.router, prefix="/auth")
app.include_router(audit_api.router)


@pytest_asyncio.fixture
async def audit_accounts():
    # Avoid any accidental execution against the user's development database.
    assert (
        settings.ENV == "test"
    )  # DSN safety is enforced centrally by pytest_sessionstart.
    accounts = []
    async with AsyncSessionLocal() as session:
        for role in ("ADMIN", "ADMIN", "AGENT"):
            tenant_id, user_id = uuid4(), uuid4()
            await session.execute(
                text("INSERT INTO tenants(id,name) VALUES(:id,'Audit fixture')"),
                {"id": tenant_id},
            )
            await session.execute(
                text(
                    "INSERT INTO users(id,tenant_id,email,hashed_password,role,is_active) VALUES(:id,:tid,:email,:password,:role,true)"
                ),
                {
                    "id": user_id,
                    "tid": tenant_id,
                    "email": f"{user_id}@example.test",
                    "password": bcrypt.hashpw(
                        b"test-pass", bcrypt.gensalt(rounds=4)
                    ).decode(),
                    "role": role,
                },
            )
            accounts.append((tenant_id, user_id))
        await session.commit()
    yield accounts
    async with AsyncSessionLocal() as session:
        for tenant_id, _ in accounts:
            await session.execute(
                text("DELETE FROM tenants WHERE id=:id"), {"id": tenant_id}
            )
        await session.commit()


def headers(account):
    return {"Authorization": "Bearer " + auth.create_access_token(*account)}


def test_masking_metadata_cursor_and_csv():
    assert audit.mask_ip("203.0.113.92") == "203.0.113.0/24"
    assert audit.mask_ip("2001:db8:abcd:12::1234") == "2001:db8:abcd::/48"
    assert audit.mask_ip("untrusted-header") is None
    assert audit.safe_metadata(
        {
            "prompt": "private",
            "token": "secret",
            "count": 3,
            "status": "accepted",
            "error_type": "API failed with secret key",
            "endpoint": "a@example.test",
        }
    ) == {"count": 3, "status": "accepted"}
    now, event_id = datetime.now(timezone.utc), uuid4()
    assert audit.decode_cursor(audit.encode_cursor(now, event_id)) == (now, event_id)
    for value in ("broken", "W10", "WyJub25zZW5zZSIsICJ4Il0"):
        with pytest.raises(ValueError):
            audit.decode_cursor(value)
    for value in ("=1+1", " +SUM(A1)", "\t@SUM(A1)", "-1"):
        assert audit.csv_value(value).startswith("'")
    assert audit.csv_value("status=success") == "status=success"


@pytest.mark.asyncio
async def test_tenant_isolation_permissions_filters_pagination_export(audit_accounts):
    first, second, agent = audit_accounts
    async with AsyncSessionLocal() as session:
        for i in range(4):
            await audit.record(
                session,
                first[0],
                "test.event",
                "test",
                str(i),
                metadata={"count": i, "content": "never-store-this"},
            )
        await audit.record(session, second[0], "other.private", "test")
        await session.commit()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        forbidden = await client.get("/audit", headers=headers(agent))
        assert forbidden.status_code == 403
        response = await client.get(
            "/audit",
            params={"action": "test.event", "limit": 2},
            headers=headers(first),
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["items"]) == 2 and body["next_cursor"]
        assert all(
            item["action"] == "test.event" and "content" not in item["metadata"]
            for item in body["items"]
        )
        next_page = await client.get(
            "/audit",
            params={"action": "test.event", "limit": 2, "cursor": body["next_cursor"]},
            headers=headers(first),
        )
        assert {item["id"] for item in body["items"]}.isdisjoint(
            item["id"] for item in next_page.json()["items"]
        )
        assert next_page.json()["next_cursor"] is None
        assert (
            await client.get(
                "/audit", params={"cursor": "broken"}, headers=headers(first)
            )
        ).status_code == 422
        assert (
            await client.get(
                "/audit",
                params={"date_from": "2026-01-01T00:00:00"},
                headers=headers(first),
            )
        ).status_code == 422
        assert (
            await client.get(
                "/audit", params={"action": "other.private"}, headers=headers(first)
            )
        ).json()["items"] == []
        exported = await client.get(
            "/audit/export", params={"action": "test.event"}, headers=headers(first)
        )
        assert exported.status_code == 200
        assert "text/csv" in exported.headers["content-type"]
        assert "test.event" in exported.text and "other.private" not in exported.text
        assert "never-store-this" not in exported.text
    async with AsyncSessionLocal() as session:
        actions = (
            (
                await session.execute(
                    text("SELECT action FROM audit_events WHERE tenant_id=:tid"),
                    {"tid": first[0]},
                )
            )
            .scalars()
            .all()
        )
        assert (
            "audit.read" in actions
            and "audit.export" in actions
            and "audit.export.completed" in actions
        )


@pytest.mark.asyncio
async def test_trigger_atomicity_append_only_and_retention(audit_accounts):
    tenant_id, user_id = audit_accounts[0]
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("UPDATE users SET full_name='Private Name' WHERE id=:id"),
            {"id": user_id},
        )
        await session.commit()
        row = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM audit_events WHERE tenant_id=:tid AND action='db.update' ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"tid": tenant_id},
                )
            )
            .mappings()
            .one()
        )
        assert row["resource_id"] == str(user_id)
        assert row["actor_type"] == "system" and row["actor_user_id"] is None
        assert "full_name" in row["metadata"]["changed_fields"]
        assert "Private Name" not in str(row)
        await session.rollback()
        # A database-side audit write failure must abort the actual mutation.
        await session.execute(
            text(
                "CREATE FUNCTION audit_test_reject() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'audit unavailable'; END $$"
            )
        )
        await session.execute(
            text(
                "CREATE TRIGGER audit_test_reject BEFORE INSERT ON audit_events FOR EACH ROW EXECUTE FUNCTION audit_test_reject()"
            )
        )
        with pytest.raises(DBAPIError):
            await session.execute(
                text("UPDATE users SET full_name='Must rollback' WHERE id=:id"),
                {"id": user_id},
            )
        await session.rollback()  # also rolls back temporary failure trigger/function
        assert (
            await session.scalar(
                text("SELECT full_name FROM users WHERE id=:id"), {"id": user_id}
            )
            == "Private Name"
        )
        await session.rollback()
        with pytest.raises(DBAPIError):
            await session.execute(
                text("UPDATE audit_events SET reason='rewritten' WHERE id=:id"),
                {"id": row["id"]},
            )
        await session.rollback()
        with pytest.raises(DBAPIError):
            await session.execute(
                text("DELETE FROM audit_events WHERE id=:id"), {"id": row["id"]}
            )
        await session.rollback()
        old_id, recent_id = uuid4(), uuid4()
        for event_id, age in ((old_id, 181), (recent_id, 179)):
            await session.execute(
                text(
                    "INSERT INTO audit_events(id,tenant_id,created_at,actor_type,action,resource_type,result,correlation_id,metadata) VALUES(:id,:tid,:created,'system','test.retention','test','success',:cid,'{}')"
                ),
                {
                    "id": event_id,
                    "tid": tenant_id,
                    "created": datetime.now(timezone.utc) - timedelta(days=age),
                    "cid": uuid4(),
                },
            )
        await session.commit()
    await audit.cleanup_expired()
    async with AsyncSessionLocal() as session:
        assert (
            await session.scalar(
                text("SELECT count(*) FROM audit_events WHERE id=:id"), {"id": old_id}
            )
            == 0
        )
        assert (
            await session.scalar(
                text("SELECT count(*) FROM audit_events WHERE id=:id"),
                {"id": recent_id},
            )
            == 1
        )


@pytest.mark.asyncio
async def test_verified_actor_login_and_revoked_access(audit_accounts):
    account = audit_accounts[0]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("203.0.113.92", 123)),
        base_url="http://test",
    ) as client:
        failed = await client.post(
            "/auth/token",
            data={"username": f"{account[1]}@example.test", "password": "wrong-pass"},
        )
        assert failed.status_code == 401
        login = await client.post(
            "/auth/token",
            data={"username": f"{account[1]}@example.test", "password": "test-pass"},
        )
        assert login.status_code == 200
        authorization = {"Authorization": "Bearer " + login.json()["access_token"]}
        assert (await client.get("/audit", headers=authorization)).status_code == 200
        assert (
            await client.post("/auth/logout", headers=authorization)
        ).status_code == 204
        assert (await client.get("/audit", headers=authorization)).status_code == 401
    async with AsyncSessionLocal() as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM audit_events WHERE tenant_id=:tid AND action='auth.login'"
                    ),
                    {"tid": account[0]},
                )
            )
            .mappings()
            .all()
        )
        assert {row["result"] for row in rows} == {"success", "failure"}
        failed = next(row for row in rows if row["result"] == "failure")
        assert failed["actor_type"] == "anonymous" and failed["actor_user_id"] is None
        success = next(row for row in rows if row["result"] == "success")
        assert (
            success["actor_user_id"] == account[1]
            and success["ip_network"] == "203.0.113.0/24"
        )
        mutation = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM audit_events WHERE tenant_id=:tid AND action='db.update' AND resource_type='users' ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"tid": account[0]},
                )
            )
            .mappings()
            .one()
        )
        assert (
            mutation["actor_user_id"] == account[1]
            and mutation["actor_type"] == "human"
        )


@pytest.mark.asyncio
async def test_websocket_verifies_membership_and_audits_session(
    audit_accounts, monkeypatch
):
    import asyncio
    from app.api import ws

    account = audit_accounts[0]

    class FakeSocket:
        def __init__(self, token):
            self.query_params = {"token": token}
            self.client = type("Peer", (), {"host": "203.0.113.92"})()
            self.closed = None
            self.accepted = False

        async def close(self, code):
            self.closed = code

        async def accept(self):
            self.accepted = True

        async def receive(self):
            return {"type": "websocket.disconnect"}

    class FakePubsub:
        async def subscribe(self, channel):
            return None

        async def unsubscribe(self, channel):
            return None

        async def aclose(self):
            return None

        async def listen(self):
            await asyncio.Future()
            yield {}

    class FakeRedis:
        def pubsub(self):
            return FakePubsub()

        async def aclose(self):
            return None

    monkeypatch.setattr(ws.aioredis, "from_url", lambda url: FakeRedis())
    socket = FakeSocket(auth.create_access_token(*account))
    await ws.websocket_endpoint(socket)
    assert socket.accepted
    async with AsyncSessionLocal() as session:
        actions = (
            (
                await session.execute(
                    text(
                        "SELECT action FROM audit_events WHERE tenant_id=:tid AND action LIKE 'ws.%'"
                    ),
                    {"tid": account[0]},
                )
            )
            .scalars()
            .all()
        )
        assert set(actions) == {"ws.connect", "ws.disconnect"}
        await session.execute(
            text("UPDATE users SET token_version=token_version+1 WHERE id=:uid"),
            {"uid": account[1]},
        )
        await session.commit()
    revoked = FakeSocket(auth.create_access_token(*account))
    await ws.websocket_endpoint(revoked)
    assert revoked.closed == 4401 and not revoked.accepted
    wrong_membership = FakeSocket(
        auth.create_access_token(audit_accounts[1][0], account[1])
    )
    await ws.websocket_endpoint(wrong_membership)
    assert wrong_membership.closed == 4401 and not wrong_membership.accepted
