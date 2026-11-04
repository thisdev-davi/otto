"""Envio de anexo pelo atendente: POST /conversations/{id}/messages/media.

O provider é trocado no REGISTRO do factory, não com mock no método. É o teste da
regra de isolamento de canal se pagando: a rota nunca soube que existe Evolution, então
o teste também não precisa saber — e se alguém importar o provider concreto dentro de
`app/api`, estes testes passam a falhar, que é exatamente o alarme desejado.
"""

import os
import uuid

import bcrypt
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from app.channels import factory
from app.channels.base import ChannelProvider
from app.channels.types import OutboundMessage, SendResult
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.main import app
from app.models.enums import Channel

pytestmark = pytest.mark.asyncio

SENHA = "s3cr3tpassword"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64

enviadas: list[OutboundMessage] = []


class _FakeProvider(ChannelProvider):
    falhar = False

    def verify_webhook(self, headers: dict, body: bytes) -> bool:
        return True

    def parse_webhook(self, headers: dict, body: bytes) -> list:
        return []

    async def send(self, message: OutboundMessage) -> SendResult:
        enviadas.append(message)
        if _FakeProvider.falhar:
            return SendResult(success=False, error="provider fora do ar")
        return SendResult(success=True, external_id="FAKE-1")


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


@pytest_asyncio.fixture(autouse=True)
def provider_fake(monkeypatch, tmp_path):
    enviadas.clear()
    _FakeProvider.falhar = False
    monkeypatch.setitem(factory._registry, Channel.WHATSAPP, _FakeProvider)
    # MEDIA_DIR isolado: sem isto os testes sujam data/media do ambiente real.
    monkeypatch.setattr(settings, "MEDIA_DIR", str(tmp_path))
    yield


@pytest_asyncio.fixture
async def conversa(db_session):
    tenant_id = uuid.uuid4()
    contact_id = uuid.uuid4()
    conv_id = uuid.uuid4()
    conn_id = uuid.uuid4()
    email = f"user_{conv_id.hex[:8]}@example.com"

    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, 'Tenant Anexo')"),
        {"id": str(tenant_id)},
    )
    await db_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, hashed_password, full_name, role, is_active) "
            "VALUES (gen_random_uuid(), :tid, :email, :hashed, 'Atendente', 'AGENT', true)"
        ),
        {
            "tid": str(tenant_id),
            "email": email,
            "hashed": bcrypt.hashpw(SENHA.encode(), bcrypt.gensalt()).decode(),
        },
    )
    await db_session.execute(
        text(
            "INSERT INTO channel_connections (id, tenant_id, channel, credentials, is_active) "
            "VALUES (:id, :tid, 'WHATSAPP', CAST('{\"instance\": \"otto-test\"}' AS jsonb), true)"
        ),
        {"id": str(conn_id), "tid": str(tenant_id)},
    )
    await db_session.execute(
        text(
            "INSERT INTO contacts (id, tenant_id, channel, external_id, name) "
            "VALUES (:id, :tid, 'WHATSAPP', '5511933333@s.whatsapp.net', 'Cliente Anexo')"
        ),
        {"id": str(contact_id), "tid": str(tenant_id)},
    )
    await db_session.execute(
        text(
            "INSERT INTO conversations (id, tenant_id, contact_id, channel_connection_id, "
            "status, unread_count) VALUES (:id, :tid, :cid, :conn, 'OPEN', 0)"
        ),
        {
            "id": str(conv_id),
            "tid": str(tenant_id),
            "cid": str(contact_id),
            "conn": str(conn_id),
        },
    )
    await db_session.commit()

    yield {"tenant_id": tenant_id, "conv_id": conv_id, "email": email}

    for stmt in (
        "DELETE FROM messages WHERE tenant_id = :tid",
        "DELETE FROM conversations WHERE tenant_id = :tid",
        "DELETE FROM contacts WHERE tenant_id = :tid",
        "DELETE FROM channel_connections WHERE tenant_id = :tid",
        "DELETE FROM users WHERE tenant_id = :tid",
        "DELETE FROM tenants WHERE id = :tid",
    ):
        await db_session.execute(text(stmt), {"tid": str(tenant_id)})
    await db_session.commit()


async def _login(client: httpx.AsyncClient, email: str) -> str:
    resp = await client.post("/auth/token", data={"username": email, "password": SENHA})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _envia(client, conversa, arquivo, caption="", token=None):
    token = token or await _login(client, conversa["email"])
    return await client.post(
        f"/conversations/{conversa['conv_id']}/messages/media",
        headers=_auth(token),
        files={"file": arquivo},
        data={"caption": caption},
    )


class TestEnvioDeAnexo:
    async def test_imagem_com_legenda(self, client, conversa, db_session):
        resp = await _envia(
            client, conversa, ("foto.png", PNG, "image/png"), caption="olha isso"
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["content_type"] == "image"
        assert body["content"] == "olha isso"
        assert body["status"] == "sent"
        # media_url na resposta: a thread faz append deste objeto e a bolha precisa
        # renderizar na hora, não só depois do refresh do WebSocket. Vem ASSINADA — o
        # front não monta mais URL de mídia com o JWT do usuário.
        assert body["media_url"].startswith(f"/media/{body['id']}?")
        assert "sig=" in body["media_url"] and "exp=" in body["media_url"]

        assert len(enviadas) == 1
        assert enviadas[0].media.mimetype == "image/png"
        assert enviadas[0].media.data == PNG
        assert enviadas[0].content == "olha isso"

        row = await db_session.execute(
            text(
                "SELECT content_type, channel_metadata->'media'->>'path' AS path "
                "FROM messages WHERE id = :id"
            ),
            {"id": body["id"]},
        )
        r = row.fetchone()
        assert r.content_type == "IMAGE"
        assert os.path.exists(r.path)

    async def test_sem_legenda_usa_rotulo_do_tipo_no_preview(
        self, client, conversa, db_session
    ):
        resp = await _envia(client, conversa, ("foto.png", PNG, "image/png"))
        assert resp.status_code == 201
        assert resp.json()["content"] is None

        row = await db_session.execute(
            text("SELECT last_message_preview AS p FROM conversations WHERE id = :id"),
            {"id": str(conversa["conv_id"])},
        )
        # O mesmo rótulo que uma imagem RECEBIDA produz.
        assert row.fetchone().p == "Imagem"

    async def test_legenda_vira_preview(self, client, conversa, db_session):
        await _envia(
            client, conversa, ("foto.png", PNG, "image/png"), caption="tabela nova"
        )
        row = await db_session.execute(
            text("SELECT last_message_preview AS p FROM conversations WHERE id = :id"),
            {"id": str(conversa["conv_id"])},
        )
        assert row.fetchone().p == "tabela nova"

    async def test_tipo_derivado_do_mimetype_nao_do_nome(self, client, conversa):
        """PDF com nome enganoso continua sendo DOCUMENT."""
        resp = await _envia(
            client, conversa, ("parece-imagem.png", b"%PDF-1.4 x", "application/pdf")
        )
        assert resp.status_code == 201
        assert resp.json()["content_type"] == "document"
        assert enviadas[0].content_type.name == "DOCUMENT"

    async def test_mimetype_proibido_e_415(self, client, conversa):
        resp = await _envia(
            client, conversa, ("virus.exe", b"MZ", "application/x-msdownload")
        )
        assert resp.status_code == 415
        assert enviadas == []

    async def test_acima_do_teto_e_413(self, client, conversa):
        grande = b"0" * (6 * 1024 * 1024)  # teto de imagem é 5 MB
        resp = await _envia(client, conversa, ("grande.png", grande, "image/png"))
        assert resp.status_code == 413
        assert enviadas == []

    async def test_falha_do_provider_grava_failed_mas_mantem_o_arquivo(
        self, client, conversa, db_session
    ):
        _FakeProvider.falhar = True
        resp = await _envia(client, conversa, ("foto.png", PNG, "image/png"))
        assert resp.status_code == 201
        assert resp.json()["status"] == "failed"

        row = await db_session.execute(
            text(
                "SELECT status, channel_metadata->'media'->>'path' AS path "
                "FROM messages WHERE id = :id"
            ),
            {"id": resp.json()["id"]},
        )
        r = row.fetchone()
        assert r.status == "FAILED"
        # O arquivo sobrevive à falha de envio — é o que permite reenviar.
        assert os.path.exists(r.path)

    async def test_conversa_de_outro_tenant_e_404(self, client, conversa):
        outro = uuid.uuid4()
        token = await _login(client, conversa["email"])
        resp = await client.post(
            f"/conversations/{outro}/messages/media",
            headers=_auth(token),
            files={"file": ("foto.png", PNG, "image/png")},
        )
        assert resp.status_code == 404

    async def test_sem_autenticacao(self, client, conversa):
        resp = await client.post(
            f"/conversations/{conversa['conv_id']}/messages/media",
            files={"file": ("foto.png", PNG, "image/png")},
        )
        assert resp.status_code in (401, 403)


class TestRotaDeTextoRecusaMidia:
    async def test_content_type_nao_texto_e_422(self, client, conversa):
        """O bug silencioso: gravava IMAGE e mandava o conteúdo pelo /sendText."""
        token = await _login(client, conversa["email"])
        resp = await client.post(
            f"/conversations/{conversa['conv_id']}/messages",
            headers=_auth(token),
            json={"content": "foto.png", "content_type": "image"},
        )
        assert resp.status_code == 422
        assert enviadas == []

    async def test_texto_continua_funcionando(self, client, conversa):
        token = await _login(client, conversa["email"])
        resp = await client.post(
            f"/conversations/{conversa['conv_id']}/messages",
            headers=_auth(token),
            json={"content": "oi", "content_type": "text"},
        )
        assert resp.status_code == 201
        assert enviadas[0].media is None
