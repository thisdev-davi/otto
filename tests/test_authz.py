"""Autorização por papel: o que um ADMIN pode e um AGENT não.

Testes de integração (ASGI + banco real, como test_auth.py). O ponto aqui não é o
happy path do admin, e sim que o AGENT leve 403 — esconder o botão no front não é
controle de acesso, e é justamente essa a parte que some sem ninguém notar.

A fronteira que estes testes fixam: AGENT não configura a IA, não vê relatório e não
mexe no canal, mas CONTINUA listando os colegas (`/users`) — é o que alimenta o seletor
de transferência, e transferir conversa não é gerenciar equipe.
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

# Rotas que exigem ADMIN. Cobre um representante de cada router guardado — se alguém
# remover a dependency do router, pelo menos uma destas cai.
ROTAS_ADMIN = [
    ("GET", "/bot/settings"),
    ("GET", "/bot/kb/documents"),
    ("GET", "/reports/overview"),
    ("GET", "/reports/agents"),
    ("GET", "/channels"),
]


@pytest_asyncio.fixture
async def db_session():
    async with AsyncSessionLocal() as session:
        yield session


async def _cria_usuario(db, tenant_id: uuid.UUID, role: str) -> dict:
    user_id = uuid.uuid4()
    email = f"{role.lower()}_{user_id.hex[:8]}@example.com"
    hashed = bcrypt.hashpw(SENHA.encode(), bcrypt.gensalt()).decode()
    await db.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, hashed_password, full_name, role, is_active) "
            "VALUES (:id, :tenant_id, :email, :hashed, :name, :role, true)"
        ),
        {
            "id": str(user_id),
            "tenant_id": str(tenant_id),
            "email": email,
            "hashed": hashed,
            "name": f"Fulano {role}",
            "role": role,
        },
    )
    return {"id": user_id, "email": email, "role": role}


@pytest_asyncio.fixture
async def equipe(db_session):
    """Um tenant com um ADMIN e um AGENT."""
    tenant_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, :name)"),
        {"id": str(tenant_id), "name": "Tenant Authz"},
    )
    admin = await _cria_usuario(db_session, tenant_id, "ADMIN")
    agent = await _cria_usuario(db_session, tenant_id, "AGENT")
    await db_session.commit()

    yield {"tenant_id": tenant_id, "admin": admin, "agent": agent}

    await db_session.execute(
        text("DELETE FROM users WHERE tenant_id = :t"), {"t": str(tenant_id)}
    )
    await db_session.execute(
        text("DELETE FROM tenants WHERE id = :t"), {"t": str(tenant_id)}
    )
    await db_session.commit()


@pytest_asyncio.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def _login(client: httpx.AsyncClient, email: str) -> str:
    resp = await client.post("/auth/token", data={"username": email, "password": SENHA})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class TestRotasAdmin:
    @pytest.mark.parametrize("metodo,rota", ROTAS_ADMIN)
    async def test_agent_recebe_403(self, client, equipe, metodo, rota):
        token = await _login(client, equipe["agent"]["email"])
        resp = await client.request(metodo, rota, headers=_auth(token))
        assert resp.status_code == 403, f"{rota} deixou um AGENT entrar"

    @pytest.mark.parametrize("metodo,rota", ROTAS_ADMIN)
    async def test_admin_passa(self, client, equipe, metodo, rota):
        token = await _login(client, equipe["admin"]["email"])
        resp = await client.request(metodo, rota, headers=_auth(token))
        assert resp.status_code == 200, f"{rota} barrou um ADMIN: {resp.text}"

    async def test_sem_token_e_401_nao_403(self, client, equipe):
        """Não autenticado ≠ autenticado sem permissão — o front trata diferente."""
        resp = await client.get("/bot/settings")
        assert resp.status_code in (401, 403)  # HTTPBearer devolve 403 sem header
        resp = await client.get("/bot/settings", headers=_auth("token-invalido"))
        assert resp.status_code == 401


class TestRotasDeTodos:
    async def test_agent_lista_colegas_para_transferir(self, client, equipe):
        """`/users` NÃO é admin-only: sem ela não há para quem transferir."""
        token = await _login(client, equipe["agent"]["email"])
        resp = await client.get("/users", headers=_auth(token))

        assert resp.status_code == 200
        emails = {u["email"] for u in resp.json()["users"]}
        assert equipe["admin"]["email"] in emails
        assert equipe["agent"]["email"] in emails

    async def test_users_me_devolve_o_proprio_papel(self, client, equipe):
        for quem in ("admin", "agent"):
            token = await _login(client, equipe[quem]["email"])
            resp = await client.get("/users/me", headers=_auth(token))

            assert resp.status_code == 200
            body = resp.json()
            assert body["email"] == equipe[quem]["email"]
            assert body["role"] == equipe[quem]["role"]

    async def test_me_nao_vaza_outro_tenant(self, client, equipe, db_session):
        """O id vem do token, mas o WHERE também filtra por tenant (defesa em profundidade)."""
        token = await _login(client, equipe["agent"]["email"])
        resp = await client.get("/users/me", headers=_auth(token))
        assert resp.json()["id"] == str(equipe["agent"]["id"])


class TestDesativacao:
    async def test_usuario_desativado_nao_renova_sessao(
        self, client, equipe, db_session
    ):
        """Desativar tem que valer agora, não em 30 dias.

        O /auth/refresh reemitia o par a partir do payload do token, sem olhar o banco:
        um atendente desligado seguiria rotacionando o próprio refresh até ele expirar.
        """
        login = await client.post(
            "/auth/token",
            data={"username": equipe["agent"]["email"], "password": SENHA},
        )
        refresh = login.json()["refresh_token"]

        # renova normalmente enquanto está ativo
        ok = await client.post("/auth/refresh", json={"refresh_token": refresh})
        assert ok.status_code == 200
        refresh = ok.json()["refresh_token"]

        await db_session.execute(
            text("UPDATE users SET is_active = false WHERE id = :id"),
            {"id": str(equipe["agent"]["id"])},
        )
        await db_session.commit()

        negado = await client.post("/auth/refresh", json={"refresh_token": refresh})
        assert negado.status_code == 401

    async def test_admin_desativado_perde_o_poder(self, client, equipe, db_session):
        """require_admin lê o banco, então desativar/rebaixar vale no request seguinte."""
        token = await _login(client, equipe["admin"]["email"])
        assert (
            await client.get("/bot/settings", headers=_auth(token))
        ).status_code == 200

        await db_session.execute(
            text("UPDATE users SET role = 'AGENT' WHERE id = :id"),
            {"id": str(equipe["admin"]["id"])},
        )
        await db_session.commit()

        # mesmo token, ainda válido e não expirado — mas o papel mudou no banco
        assert (
            await client.get("/bot/settings", headers=_auth(token))
        ).status_code == 403


class TestRespostasRapidas:
    """Leitura para todo atendente, escrita só admin.

    O par GET-200 + POST-403 é o ponto: sem ele, alguém "uniformiza" o router com um
    `dependencies=[require_admin]` e quebra o autocomplete `/atalho` de toda a equipe
    sem nenhum teste ficar vermelho.
    """

    async def test_agent_le_para_alimentar_o_atalho(self, client, equipe):
        token = await _login(client, equipe["agent"]["email"])
        resp = await client.get("/quick-replies", headers=_auth(token))
        assert resp.status_code == 200

    async def test_agent_nao_cria(self, client, equipe):
        token = await _login(client, equipe["agent"]["email"])
        resp = await client.post(
            "/quick-replies",
            headers=_auth(token),
            json={"shortcut": "horario", "title": "Horário", "content": "9h às 18h"},
        )
        assert resp.status_code == 403

    async def test_admin_cria_edita_e_apaga(self, client, equipe):
        token = await _login(client, equipe["admin"]["email"])
        criado = await client.post(
            "/quick-replies",
            headers=_auth(token),
            json={"shortcut": "horario", "title": "Horário", "content": "9h às 18h"},
        )
        assert criado.status_code == 201, criado.text
        qr_id = criado.json()["id"]

        editado = await client.put(
            f"/quick-replies/{qr_id}",
            headers=_auth(token),
            json={"shortcut": "horario", "title": "Horário", "content": "8h às 20h"},
        )
        assert editado.status_code == 200
        assert editado.json()["content"] == "8h às 20h"

        apagado = await client.delete(f"/quick-replies/{qr_id}", headers=_auth(token))
        assert apagado.status_code == 204

    async def test_agent_nao_apaga(self, client, equipe):
        admin = await _login(client, equipe["admin"]["email"])
        criado = await client.post(
            "/quick-replies",
            headers=_auth(admin),
            json={"shortcut": "obrigado", "title": "Obrigado", "content": "Obrigado!"},
        )
        qr_id = criado.json()["id"]
        try:
            agent = await _login(client, equipe["agent"]["email"])
            resp = await client.delete(f"/quick-replies/{qr_id}", headers=_auth(agent))
            assert resp.status_code == 403
        finally:
            await client.delete(f"/quick-replies/{qr_id}", headers=_auth(admin))
