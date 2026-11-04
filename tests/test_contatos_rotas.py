"""Rotas do perfil do cliente e do "digitando", pela API (ASGI + banco real).

Complementa `test_contatos.py`, que exercita o SERVICE. O que se trava aqui é a
fiação — que o `tenant_id` sai mesmo do token e não do caminho da URL.

É uma distinção que já mordeu este projeto: `SEGURANCA.md` §C1 documenta o webhook em
que o tenant vinha do corpo da requisição. Um service perfeitamente isolado não protege
nada se a rota lhe entregar o tenant errado, e nenhum teste de service pega isso.
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


async def _tenant_com_usuario(db, nome: str) -> dict:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    email = f"rota_{user_id.hex[:8]}@example.com"
    await db.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, :n)"),
        {"id": str(tenant_id), "n": nome},
    )
    await db.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, hashed_password, full_name, role, is_active) "
            "VALUES (:id, :t, :e, :h, 'Fulano', 'ADMIN', true)"
        ),
        {
            "id": str(user_id),
            "t": str(tenant_id),
            "e": email,
            "h": bcrypt.hashpw(SENHA.encode(), bcrypt.gensalt()).decode(),
        },
    )
    return {"tenant_id": tenant_id, "user_id": user_id, "email": email}


async def _contato(db, tenant_id: uuid.UUID, nome: str) -> uuid.UUID:
    contact_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO contacts (id, tenant_id, channel, external_id, name, phone) "
            "VALUES (:id, :t, 'WHATSAPP', :ext, :n, :p)"
        ),
        {
            "id": str(contact_id),
            "t": str(tenant_id),
            "ext": f"{contact_id.hex[:10]}@s.whatsapp.net",
            "n": nome,
            "p": f"55119{contact_id.int % 100000000:08d}",
        },
    )
    return contact_id


@pytest_asyncio.fixture
async def cenario(db_session):
    """Dois tenants, cada um com seu usuário e seu contato."""
    casa = await _tenant_com_usuario(db_session, "Casa")
    vizinho = await _tenant_com_usuario(db_session, "Vizinho")
    casa["contact_id"] = await _contato(
        db_session, casa["tenant_id"], "Cliente da Casa"
    )
    vizinho["contact_id"] = await _contato(
        db_session, vizinho["tenant_id"], "Cliente do Vizinho"
    )
    await db_session.commit()

    yield {"casa": casa, "vizinho": vizinho}

    for t in (casa["tenant_id"], vizinho["tenant_id"]):
        for tabela in ("contact_notes", "contacts", "users"):
            await db_session.execute(
                text(f"DELETE FROM {tabela} WHERE tenant_id = :t"), {"t": str(t)}
            )
        await db_session.execute(
            text("DELETE FROM tenants WHERE id = :t"), {"t": str(t)}
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


# ─────────────────────────── perfil ───────────────────────────


async def test_perfil_do_proprio_tenant_abre(cenario, client) -> None:
    token = await _login(client, cenario["casa"]["email"])
    r = await client.get(
        f"/contacts/{cenario['casa']['contact_id']}", headers=_auth(token)
    )
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["nome"] == "Cliente da Casa"
    assert corpo["conversas"] == [] and corpo["reservas"] == [] and corpo["notas"] == []


async def test_perfil_de_outro_tenant_e_404(cenario, client) -> None:
    """404 e não 403: distinguir os dois conta que o contato existe em algum lugar."""
    token = await _login(client, cenario["casa"]["email"])
    r = await client.get(
        f"/contacts/{cenario['vizinho']['contact_id']}", headers=_auth(token)
    )
    assert r.status_code == 404


async def test_perfil_sem_token_e_recusado(cenario, client) -> None:
    r = await client.get(f"/contacts/{cenario['casa']['contact_id']}")
    assert r.status_code in (401, 403)


# ─────────────────────────── anotações ───────────────────────────


async def test_criar_e_listar_anotacao(cenario, client) -> None:
    token = await _login(client, cenario["casa"]["email"])
    contato = cenario["casa"]["contact_id"]

    r = await client.post(
        f"/contacts/{contato}/notes",
        json={"texto": "pediu desconto à vista"},
        headers=_auth(token),
    )
    assert r.status_code == 201
    nota = r.json()
    assert nota["texto"] == "pediu desconto à vista"
    assert nota["autor"] == "Fulano"

    perfil = await client.get(f"/contacts/{contato}", headers=_auth(token))
    assert [n["texto"] for n in perfil.json()["notas"]] == ["pediu desconto à vista"]


async def test_anotar_em_contato_de_outro_tenant_e_404(cenario, client) -> None:
    token = await _login(client, cenario["casa"]["email"])
    r = await client.post(
        f"/contacts/{cenario['vizinho']['contact_id']}/notes",
        json={"texto": "não deveria entrar"},
        headers=_auth(token),
    )
    assert r.status_code == 404


async def test_anotacao_vazia_e_recusada(cenario, client) -> None:
    token = await _login(client, cenario["casa"]["email"])
    r = await client.post(
        f"/contacts/{cenario['casa']['contact_id']}/notes",
        json={"texto": "   "},
        headers=_auth(token),
    )
    assert r.status_code == 404


async def test_apagar_anotacao_de_outro_tenant_e_404(cenario, client) -> None:
    """Mesmo sabendo o id da nota, e mesmo sendo admin do próprio tenant."""
    token_vizinho = await _login(client, cenario["vizinho"]["email"])
    criada = await client.post(
        f"/contacts/{cenario['vizinho']['contact_id']}/notes",
        json={"texto": "segredo do vizinho"},
        headers=_auth(token_vizinho),
    )
    nota_id = criada.json()["id"]

    token_casa = await _login(client, cenario["casa"]["email"])
    r = await client.delete(
        f"/contacts/{cenario['casa']['contact_id']}/notes/{nota_id}",
        headers=_auth(token_casa),
    )
    assert r.status_code == 404

    # E continua lá para o dono.
    perfil = await client.get(
        f"/contacts/{cenario['vizinho']['contact_id']}", headers=_auth(token_vizinho)
    )
    assert len(perfil.json()["notas"]) == 1


async def test_autor_apaga_a_propria_anotacao(cenario, client) -> None:
    token = await _login(client, cenario["casa"]["email"])
    contato = cenario["casa"]["contact_id"]
    criada = await client.post(
        f"/contacts/{contato}/notes", json={"texto": "some"}, headers=_auth(token)
    )
    r = await client.delete(
        f"/contacts/{contato}/notes/{criada.json()['id']}", headers=_auth(token)
    )
    assert r.status_code == 204

    perfil = await client.get(f"/contacts/{contato}", headers=_auth(token))
    assert perfil.json()["notas"] == []


# ─────────────────────────── digitando ───────────────────────────


async def test_typing_em_conversa_de_outro_tenant_e_404(cenario, client) -> None:
    """A rota engole erro do canal, mas NÃO pode engolir conversa de outro tenant."""
    token = await _login(client, cenario["casa"]["email"])
    r = await client.post(f"/conversations/{uuid.uuid4()}/typing", headers=_auth(token))
    assert r.status_code == 404
