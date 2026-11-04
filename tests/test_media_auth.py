"""Autenticação da rota de mídia.

Existe por causa de um bug em produção: `GET /media/{id}` exigia o JWT no header
`Authorization`, mas quem busca a URL é o navegador — `<img src>` e `<audio src>` não
mandam header nenhum. Resultado: toda imagem e todo áudio recebido aparecia quebrado na
thread, apesar de ter sido baixado e gravado corretamente pelo worker. O sintoma não
apontava para auth: a mídia existia em disco e o registro estava certo no banco.

A primeira correção foi aceitar `?token=<JWT>`. Funcionava, e criava um problema pior: o
access token do atendente passava a circular em query string, e esta rota podia devolver
HTML escolhido por quem mandou a mensagem — a página do atacante lia o próprio
`location.search` e ficava com a sessão. Hoje a URL vem ASSINADA (`services/media_links`)
e o `Content-Type` é decidido pelo servidor (`attachments.mimetype_para_servir`).

O par que trava tudo isso é `test_com_assinatura` + `test_sem_credencial_nenhuma`: aceitar
credencial pela URL não pode virar "a rota ficou aberta".
"""

import os
import uuid

import bcrypt
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.main import app
from app.services import media_links

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


async def _cria_tenant_com_midia(db, tmp_path, nome: str) -> dict:
    """Um tenant com usuário, conversa e uma mensagem de áudio gravada em disco."""
    tenant_id = uuid.uuid4()
    contact_id = uuid.uuid4()
    conv_id = uuid.uuid4()
    msg_id = uuid.uuid4()
    email = f"user_{msg_id.hex[:8]}@example.com"

    arquivo = tmp_path / f"{msg_id}.ogg"
    arquivo.write_bytes(b"OggS-conteudo-de-audio-falso")

    await db.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, :nome)"),
        {"id": str(tenant_id), "nome": nome},
    )
    await db.execute(
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
    await db.execute(
        text(
            "INSERT INTO contacts (id, tenant_id, channel, external_id, name) "
            "VALUES (:id, :tid, 'WHATSAPP', '5511922222', 'Cliente Mídia')"
        ),
        {"id": str(contact_id), "tid": str(tenant_id)},
    )
    await db.execute(
        text(
            "INSERT INTO conversations (id, tenant_id, contact_id, status, unread_count) "
            "VALUES (:id, :tid, :cid, 'OPEN', 0)"
        ),
        {"id": str(conv_id), "tid": str(tenant_id), "cid": str(contact_id)},
    )
    await db.execute(
        text(
            "INSERT INTO messages (id, conversation_id, tenant_id, direction, content_type, "
            "content, channel, external_id, channel_metadata) "
            "VALUES (:id, :cid, :tid, 'INBOUND', 'AUDIO', NULL, 'WHATSAPP', :ext, "
            "CAST(:meta AS jsonb))"
        ),
        {
            "id": str(msg_id),
            "cid": str(conv_id),
            "tid": str(tenant_id),
            "ext": str(uuid.uuid4()),
            "meta": (
                '{"media": {"has_media": true, "status": "stored", '
                f'"path": "{arquivo}", "mimetype": "audio/ogg; codecs=opus"}}}}'
            ),
        },
    )
    await db.commit()
    return {"tenant_id": tenant_id, "msg_id": msg_id, "email": email, "path": arquivo}


async def _limpa(db, tenant_id) -> None:
    for stmt in (
        "DELETE FROM messages WHERE tenant_id = :tid",
        "DELETE FROM conversations WHERE tenant_id = :tid",
        "DELETE FROM contacts WHERE tenant_id = :tid",
        "DELETE FROM users WHERE tenant_id = :tid",
        "DELETE FROM tenants WHERE id = :tid",
    ):
        await db.execute(text(stmt), {"tid": str(tenant_id)})
    await db.commit()


@pytest_asyncio.fixture
async def midia(db_session, tmp_path):
    dados = await _cria_tenant_com_midia(db_session, tmp_path, "Tenant Mídia")
    yield dados
    await _limpa(db_session, dados["tenant_id"])


async def _login(client: httpx.AsyncClient, email: str) -> str:
    resp = await client.post("/auth/token", data={"username": email, "password": SENHA})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


class TestAutenticacaoDaMidia:
    async def test_com_header_authorization(self, client, midia):
        token = await _login(client, midia["email"])
        resp = await client.get(
            f"/media/{midia['msg_id']}", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200
        assert resp.content == b"OggS-conteudo-de-audio-falso"

    async def test_com_assinatura(self, client, midia):
        """O caminho que o <audio src> usa — é o que estava quebrado."""
        assinatura = media_links.assinar("media", midia["msg_id"], midia["tenant_id"])
        resp = await client.get(f"/media/{midia['msg_id']}?{assinatura}")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("audio/ogg")
        assert resp.headers["x-content-type-options"] == "nosniff"

    async def test_jwt_na_query_nao_vale_mais(self, client, midia):
        """O `?token=<JWT>` foi retirado de propósito; não pode voltar por acidente."""
        token = await _login(client, midia["email"])
        resp = await client.get(f"/media/{midia['msg_id']}?token={token}")
        assert resp.status_code == 401

    async def test_sem_credencial_nenhuma(self, client, midia):
        resp = await client.get(f"/media/{midia['msg_id']}")
        assert resp.status_code == 401

    async def test_assinatura_forjada(self, client, midia):
        resp = await client.get(f"/media/{midia['msg_id']}?exp=99999999999&sig=forjada")
        assert resp.status_code == 404

    async def test_assinatura_expirada(self, client, midia):
        vencida = media_links.assinar(
            "media", midia["msg_id"], midia["tenant_id"], ttl=-10
        )
        resp = await client.get(f"/media/{midia['msg_id']}?{vencida}")
        assert resp.status_code == 404

    async def test_assinatura_de_outro_recurso_nao_serve(self, client, midia):
        """Assinatura é por (tipo, item, tenant) — trocar o item invalida."""
        outra = media_links.assinar("media", uuid.uuid4(), midia["tenant_id"])
        resp = await client.get(f"/media/{midia['msg_id']}?{outra}")
        assert resp.status_code == 404

    async def test_token_de_outro_tenant_nao_ve(self, client, db_session, tmp_path):
        """Isolamento por tenant, agora com o JWT no header."""
        dono = await _cria_tenant_com_midia(db_session, tmp_path, "Tenant Dono")
        intruso = await _cria_tenant_com_midia(db_session, tmp_path, "Tenant Intruso")
        try:
            token = await _login(client, intruso["email"])
            resp = await client.get(
                f"/media/{dono['msg_id']}", headers={"Authorization": f"Bearer {token}"}
            )
            assert resp.status_code == 404
        finally:
            await _limpa(db_session, dono["tenant_id"])
            await _limpa(db_session, intruso["tenant_id"])

    async def test_arquivo_sumido_do_disco_e_404(self, client, midia):
        os.remove(midia["path"])
        token = await _login(client, midia["email"])
        resp = await client.get(
            f"/media/{midia['msg_id']}", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 404


class TestContentTypeNaoEDoRemetente:
    """C2: o mimetype gravado vem do payload do canal — quem manda a mensagem escolhe.

    Servir isso de volta cru deixava um estranho gravar bytes de HTML anunciados como
    `text/html`; o atendente abria o anexo e a página do atacante executava na origem da
    API. `nosniff` não cobre: o tipo declarado já era o do atacante.
    """

    async def test_html_forjado_nao_e_servido_como_html(
        self, client, db_session, tmp_path
    ):
        dados = await _cria_tenant_com_midia(db_session, tmp_path, "Tenant XSS")
        try:
            await db_session.execute(
                text(
                    "UPDATE messages SET content_type = 'DOCUMENT', "
                    "channel_metadata = jsonb_set(channel_metadata, "
                    "'{media,mimetype}', '\"text/html\"') WHERE id = :id"
                ),
                {"id": str(dados["msg_id"])},
            )
            await db_session.commit()

            assinatura = media_links.assinar(
                "media", dados["msg_id"], dados["tenant_id"]
            )
            resp = await client.get(f"/media/{dados['msg_id']}?{assinatura}")

            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("application/octet-stream")
            assert resp.headers["content-disposition"].startswith("attachment")
            assert "sandbox" in resp.headers["content-security-policy"]
        finally:
            await _limpa(db_session, dados["tenant_id"])

    async def test_mimetype_incoerente_com_o_content_type(
        self, client, db_session, tmp_path
    ):
        """`image/png` numa Message AUDIO é sinal de metadata adulterada."""
        dados = await _cria_tenant_com_midia(db_session, tmp_path, "Tenant Incoerente")
        try:
            await db_session.execute(
                text(
                    "UPDATE messages SET channel_metadata = jsonb_set("
                    "channel_metadata, '{media,mimetype}', '\"image/png\"') "
                    "WHERE id = :id"
                ),
                {"id": str(dados["msg_id"])},
            )
            await db_session.commit()

            assinatura = media_links.assinar(
                "media", dados["msg_id"], dados["tenant_id"]
            )
            resp = await client.get(f"/media/{dados['msg_id']}?{assinatura}")
            assert resp.headers["content-type"].startswith("application/octet-stream")
        finally:
            await _limpa(db_session, dados["tenant_id"])
