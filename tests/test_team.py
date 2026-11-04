"""Convites de equipe e administração de membros (integração: ASGI + banco real).

O que estes testes protegem: o link de convite é a única porta de entrada para um
atendente novo, e é uma credencial que trafega por WhatsApp. Precisa ser de uso único,
expirável e revogável — e o tenant nunca pode ficar sem administrador.
"""

import uuid

import bcrypt
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.main import app
from app.services import team

pytestmark = pytest.mark.asyncio

SENHA = "s3cr3tpassword"
SENHA_NOVA = "outrasenhaforte"


@pytest_asyncio.fixture
async def db_session():
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def equipe(db_session):
    tenant_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, :name)"),
        {"id": str(tenant_id), "name": "Salão da Ana"},
    )
    ids = {}
    for role in ("ADMIN", "AGENT"):
        uid = uuid.uuid4()
        ids[role] = {"id": uid, "email": f"{role.lower()}_{uid.hex[:8]}@example.com"}
        await db_session.execute(
            text(
                "INSERT INTO users (id, tenant_id, email, hashed_password, role, is_active) "
                "VALUES (:id, :t, :e, :p, :r, true)"
            ),
            {
                "id": str(uid),
                "t": str(tenant_id),
                "e": ids[role]["email"],
                "p": bcrypt.hashpw(SENHA.encode(), bcrypt.gensalt()).decode(),
                "r": role,
            },
        )
    await db_session.commit()

    yield {"tenant_id": tenant_id, "admin": ids["ADMIN"], "agent": ids["AGENT"]}

    await db_session.execute(
        text("DELETE FROM user_invites WHERE tenant_id = :t"), {"t": str(tenant_id)}
    )
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


async def _login(client, email: str, senha: str = SENHA) -> str:
    resp = await client.post("/auth/token", data={"username": email, "password": senha})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _convida(client, admin_token: str, email: str, role: str = "AGENT"):
    resp = await client.post(
        "/users/invites",
        json={"email": email, "full_name": "Pessoa Nova", "role": role},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestCriacaoDeConvite:
    async def test_agent_nao_convida(self, client, equipe):
        token = await _login(client, equipe["agent"]["email"])
        resp = await client.post(
            "/users/invites", json={"email": "x@ex.com"}, headers=_auth(token)
        )
        assert resp.status_code == 403

    async def test_token_nao_fica_em_claro_no_banco(self, client, equipe, db_session):
        """O banco guarda só o hash: vazar a tabela não entrega acesso."""
        admin = await _login(client, equipe["admin"]["email"])
        criado = await _convida(client, admin, "nova@example.com")

        raw = criado["token"]
        row = await db_session.execute(
            text("SELECT token_hash FROM user_invites WHERE id = :id"),
            {"id": criado["invite"]["id"]},
        )
        guardado = row.scalar_one()
        assert guardado != raw
        assert guardado == team.hash_token(raw)

    async def test_email_invalido_recusado(self, client, equipe):
        admin = await _login(client, equipe["admin"]["email"])
        resp = await client.post(
            "/users/invites", json={"email": "sem-arroba"}, headers=_auth(admin)
        )
        assert resp.status_code == 400

    async def test_nao_convida_quem_ja_e_membro(self, client, equipe):
        admin = await _login(client, equipe["admin"]["email"])
        resp = await client.post(
            "/users/invites",
            json={"email": equipe["agent"]["email"]},
            headers=_auth(admin),
        )
        assert resp.status_code == 409

    async def test_reconvite_substitui_o_pendente(self, client, equipe):
        """Dois links válidos para a mesma pessoa é superfície de ataque à toa."""
        admin = await _login(client, equipe["admin"]["email"])
        primeiro = await _convida(client, admin, "dupla@example.com")
        segundo = await _convida(client, admin, "dupla@example.com")

        assert (await client.get(f"/invites/{primeiro['token']}")).status_code == 404
        assert (await client.get(f"/invites/{segundo['token']}")).status_code == 200

        lista = await client.get("/users/invites", headers=_auth(admin))
        pendentes = [
            i for i in lista.json()["invites"] if i["email"] == "dupla@example.com"
        ]
        assert len(pendentes) == 1


class TestAceite:
    async def test_fluxo_completo(self, client, equipe):
        admin = await _login(client, equipe["admin"]["email"])
        criado = await _convida(client, admin, "entrando@example.com")
        token = criado["token"]

        # a tela pública diz onde a pessoa está entrando, e nada além disso
        espiada = await client.get(f"/invites/{token}")
        assert espiada.status_code == 200
        assert espiada.json()["tenant_name"] == "Salão da Ana"
        assert espiada.json()["email"] == "entrando@example.com"

        aceite = await client.post(
            f"/invites/{token}/accept",
            json={"password": SENHA_NOVA, "full_name": "Maria"},
        )
        assert aceite.status_code == 200
        # já entra logado, sem passar pela tela de login
        novo_token = aceite.json()["access_token"]
        eu = await client.get("/users/me", headers=_auth(novo_token))
        assert eu.json()["email"] == "entrando@example.com"
        assert eu.json()["full_name"] == "Maria"
        assert eu.json()["role"] == "AGENT"

        # e a senha definida funciona no login normal
        await _login(client, "entrando@example.com", SENHA_NOVA)

    async def test_convite_e_de_uso_unico(self, client, equipe):
        admin = await _login(client, equipe["admin"]["email"])
        token = (await _convida(client, admin, "umavez@example.com"))["token"]

        primeiro = await client.post(
            f"/invites/{token}/accept", json={"password": SENHA_NOVA}
        )
        assert primeiro.status_code == 200

        segundo = await client.post(
            f"/invites/{token}/accept", json={"password": SENHA_NOVA}
        )
        assert segundo.status_code == 404

    async def test_token_inexistente(self, client):
        assert (await client.get("/invites/token-inventado")).status_code == 404
        resp = await client.post(
            "/invites/token-inventado/accept", json={"password": SENHA_NOVA}
        )
        assert resp.status_code == 404

    async def test_convite_expirado_nao_vale(self, client, equipe, db_session):
        admin = await _login(client, equipe["admin"]["email"])
        criado = await _convida(client, admin, "tarde@example.com")

        await db_session.execute(
            text(
                "UPDATE user_invites SET expires_at = now() - interval '1 day' WHERE id = :id"
            ),
            {"id": criado["invite"]["id"]},
        )
        await db_session.commit()

        assert (await client.get(f"/invites/{criado['token']}")).status_code == 404

    async def test_senha_curta_recusada(self, client, equipe):
        admin = await _login(client, equipe["admin"]["email"])
        token = (await _convida(client, admin, "fraca@example.com"))["token"]

        resp = await client.post(f"/invites/{token}/accept", json={"password": "123"})
        assert resp.status_code == 400

    async def test_revogado_nao_pode_ser_aceito(self, client, equipe):
        admin = await _login(client, equipe["admin"]["email"])
        criado = await _convida(client, admin, "revogado@example.com")

        apagou = await client.delete(
            f"/users/invites/{criado['invite']['id']}", headers=_auth(admin)
        )
        assert apagou.status_code == 204

        resp = await client.post(
            f"/invites/{criado['token']}/accept", json={"password": SENHA_NOVA}
        )
        assert resp.status_code == 404

    async def test_admin_convidado_entra_como_admin(self, client, equipe):
        admin = await _login(client, equipe["admin"]["email"])
        token = (await _convida(client, admin, "socio@example.com", role="ADMIN"))[
            "token"
        ]

        aceite = await client.post(
            f"/invites/{token}/accept", json={"password": SENHA_NOVA}
        )
        novo = aceite.json()["access_token"]
        assert (
            await client.get("/bot/settings", headers=_auth(novo))
        ).status_code == 200


class TestMembros:
    async def test_admin_lista_inclusive_inativos(self, client, equipe, db_session):
        admin = await _login(client, equipe["admin"]["email"])
        await db_session.execute(
            text("UPDATE users SET is_active = false WHERE id = :id"),
            {"id": str(equipe["agent"]["id"])},
        )
        await db_session.commit()

        resp = await client.get("/users/members", headers=_auth(admin))
        emails = {m["email"] for m in resp.json()["members"]}
        assert equipe["agent"]["email"] in emails  # /users esconderia; /members não

    async def test_promove_e_rebaixa(self, client, equipe):
        admin = await _login(client, equipe["admin"]["email"])
        agent_id = equipe["agent"]["id"]

        promovido = await client.patch(
            f"/users/members/{agent_id}", json={"role": "ADMIN"}, headers=_auth(admin)
        )
        assert promovido.status_code == 200
        assert promovido.json()["role"] == "ADMIN"

        # o recém-promovido já entra nas rotas de admin
        token_dele = await _login(client, equipe["agent"]["email"])
        assert (
            await client.get("/reports/overview", headers=_auth(token_dele))
        ).status_code == 200

    async def test_ultimo_admin_nao_pode_ser_rebaixado(self, client, equipe):
        """Sem esta trava o negócio se tranca fora da própria conta."""
        admin = await _login(client, equipe["admin"]["email"])

        resp = await client.patch(
            f"/users/members/{equipe['admin']['id']}",
            json={"role": "AGENT"},
            headers=_auth(admin),
        )
        assert resp.status_code == 409

    async def test_ultimo_admin_nao_pode_ser_desativado(self, client, equipe):
        admin = await _login(client, equipe["admin"]["email"])
        resp = await client.patch(
            f"/users/members/{equipe['admin']['id']}",
            json={"is_active": False},
            headers=_auth(admin),
        )
        assert resp.status_code == 409

    async def test_rebaixa_admin_quando_ha_outro(self, client, equipe):
        admin = await _login(client, equipe["admin"]["email"])
        # promove o agent → agora existem dois admins
        await client.patch(
            f"/users/members/{equipe['agent']['id']}",
            json={"role": "ADMIN"},
            headers=_auth(admin),
        )
        # agora o primeiro pode se rebaixar
        resp = await client.patch(
            f"/users/members/{equipe['admin']['id']}",
            json={"role": "AGENT"},
            headers=_auth(admin),
        )
        assert resp.status_code == 200

    async def test_agent_nao_administra(self, client, equipe):
        token = await _login(client, equipe["agent"]["email"])
        assert (
            await client.get("/users/members", headers=_auth(token))
        ).status_code == 403
        resp = await client.patch(
            f"/users/members/{equipe['agent']['id']}",
            json={"role": "ADMIN"},
            headers=_auth(token),
        )
        assert resp.status_code == 403
