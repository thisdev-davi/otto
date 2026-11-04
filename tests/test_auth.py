import uuid

import bcrypt
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.main import app

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def db_session():
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def test_user(db_session):
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    email = f"test_{user_id.hex[:8]}@example.com"
    password = "s3cr3tpassword"
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, :name)"),
        {"id": str(tenant_id), "name": "Test Tenant"},
    )
    await db_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, hashed_password, is_active)"
            " VALUES (:id, :tenant_id, :email, :hashed_password, true)"
        ),
        {
            "id": str(user_id),
            "tenant_id": str(tenant_id),
            "email": email,
            "hashed_password": hashed,
        },
    )
    await db_session.commit()

    yield {"id": user_id, "tenant_id": tenant_id, "email": email, "password": password}

    await db_session.execute(
        text("DELETE FROM users WHERE id = :id"), {"id": str(user_id)}
    )
    await db_session.execute(
        text("DELETE FROM tenants WHERE id = :id"), {"id": str(tenant_id)}
    )
    await db_session.commit()


@pytest_asyncio.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


async def test_login_success(client: httpx.AsyncClient, test_user: dict) -> None:
    response = await client.post(
        "/auth/token",
        data={"username": test_user["email"], "password": test_user["password"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert "access_token" in body
    assert "refresh_token" in body  # login devolve o par
    assert body["token_type"] == "bearer"


async def test_login_wrong_password(client: httpx.AsyncClient, test_user: dict) -> None:
    response = await client.post(
        "/auth/token",
        data={"username": test_user["email"], "password": "wrongpassword"},
    )
    assert response.status_code == 401


async def test_login_unknown_email(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/auth/token",
        data={"username": "nonexistent@example.com", "password": "anypassword"},
    )
    assert response.status_code == 401


async def test_refresh_flow(client: httpx.AsyncClient, test_user: dict) -> None:
    login = await client.post(
        "/auth/token",
        data={"username": test_user["email"], "password": test_user["password"]},
    )
    refresh_token = login.json()["refresh_token"]

    resp = await client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"] and body["refresh_token"]

    # o novo access token é aceito num endpoint protegido
    me = await client.get(
        "/users", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.status_code == 200


async def test_refresh_rejects_access_token(
    client: httpx.AsyncClient, test_user: dict
) -> None:
    """Um access token não pode ser usado no /auth/refresh (type != refresh)."""
    login = await client.post(
        "/auth/token",
        data={"username": test_user["email"], "password": test_user["password"]},
    )
    access = login.json()["access_token"]
    resp = await client.post("/auth/refresh", json={"refresh_token": access})
    assert resp.status_code == 401


async def test_refresh_token_rejected_as_access(
    client: httpx.AsyncClient, test_user: dict
) -> None:
    """Um refresh token não autentica em endpoint protegido (type != access)."""
    login = await client.post(
        "/auth/token",
        data={"username": test_user["email"], "password": test_user["password"]},
    )
    refresh = login.json()["refresh_token"]
    resp = await client.get("/users", headers={"Authorization": f"Bearer {refresh}"})
    assert resp.status_code == 401
