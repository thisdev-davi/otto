"""Ciclo de vida da sessão: logout, troca de senha e revogação de refresh token.

Antes disto não havia como invalidar nada. O refresh token valia 30 dias, se rotacionava
sozinho a cada uso, e nem logout nem troca de senha existiam no sistema — quem
suspeitasse que a senha vazou não tinha nenhuma ação disponível. Um refresh copiado do
localStorage era acesso por um mês.

`test_refresh_antigo_morre_apos_logout` é o teste que trava a correção: rotacionar não é
revogar.
"""

import uuid

import bcrypt
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.main import app

pytestmark = pytest.mark.asyncio

SENHA = "s3cr3tpassword"


@pytest_asyncio.fixture
async def db_session():
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest_asyncio.fixture
async def conta(db_session):
    tenant_id = uuid.uuid4()
    email = f"sess_{tenant_id.hex[:8]}@example.com"
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, 'Tenant Sessão')"),
        {"id": str(tenant_id)},
    )
    await db_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, hashed_password, full_name, role, is_active) "
            "VALUES (gen_random_uuid(), :tid, :email, :hashed, 'Dono', 'ADMIN', true)"
        ),
        {
            "tid": str(tenant_id),
            "email": email,
            "hashed": bcrypt.hashpw(SENHA.encode(), bcrypt.gensalt()).decode(),
        },
    )
    await db_session.commit()
    yield {"tenant_id": tenant_id, "email": email}
    await db_session.execute(
        text("DELETE FROM users WHERE tenant_id = :t"), {"t": str(tenant_id)}
    )
    await db_session.execute(
        text("DELETE FROM tenants WHERE id = :t"), {"t": str(tenant_id)}
    )
    await db_session.commit()


async def _login(client, email, senha=SENHA) -> dict:
    resp = await client.post("/auth/token", data={"username": email, "password": senha})
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestLogout:
    async def test_refresh_antigo_morre_apos_logout(self, client, conta):
        par = await _login(client, conta["email"])

        # Antes do logout o refresh funciona.
        ok = await client.post(
            "/auth/refresh", json={"refresh_token": par["refresh_token"]}
        )
        assert ok.status_code == 200

        saiu = await client.post(
            "/auth/logout", headers={"Authorization": f"Bearer {par['access_token']}"}
        )
        assert saiu.status_code == 204

        # O refresh original — e o rotacionado — deixam de valer.
        for token in (par["refresh_token"], ok.json()["refresh_token"]):
            morto = await client.post("/auth/refresh", json={"refresh_token": token})
            assert morto.status_code == 401

    async def test_login_novo_funciona_depois_do_logout(self, client, conta):
        par = await _login(client, conta["email"])
        await client.post(
            "/auth/logout", headers={"Authorization": f"Bearer {par['access_token']}"}
        )
        novo = await _login(client, conta["email"])
        resp = await client.post(
            "/auth/refresh", json={"refresh_token": novo["refresh_token"]}
        )
        assert resp.status_code == 200

    async def test_logout_exige_autenticacao(self, client):
        assert (await client.post("/auth/logout")).status_code == 401


class TestTrocaDeSenha:
    async def test_troca_e_derruba_as_outras_sessoes(self, client, conta):
        par = await _login(client, conta["email"])
        nova = "outrasenhaforte1"

        resp = await client.post(
            "/auth/password",
            headers={"Authorization": f"Bearer {par['access_token']}"},
            json={"senha_atual": SENHA, "nova_senha": nova},
        )
        assert resp.status_code == 204

        # Senha antiga não entra mais; a nova entra.
        antiga = await client.post(
            "/auth/token", data={"username": conta["email"], "password": SENHA}
        )
        assert antiga.status_code == 401
        await _login(client, conta["email"], nova)

        # E o refresh emitido antes da troca morreu.
        morto = await client.post(
            "/auth/refresh", json={"refresh_token": par["refresh_token"]}
        )
        assert morto.status_code == 401

    async def test_senha_atual_errada(self, client, conta):
        par = await _login(client, conta["email"])
        resp = await client.post(
            "/auth/password",
            headers={"Authorization": f"Bearer {par['access_token']}"},
            json={"senha_atual": "nao-e-essa", "nova_senha": "outrasenhaforte1"},
        )
        assert resp.status_code == 401

    async def test_senha_nova_curta(self, client, conta):
        par = await _login(client, conta["email"])
        resp = await client.post(
            "/auth/password",
            headers={"Authorization": f"Bearer {par['access_token']}"},
            json={"senha_atual": SENHA, "nova_senha": "curta"},
        )
        assert resp.status_code == 422
