"""Atendente edita ou apaga para todos o que ELE enviou, dentro das janelas do WhatsApp.

O que trava:
- só a própria mensagem: nem a do colega, nem a do bot;
- fora da janela (15 min para editar, 48 h para apagar) o servidor recusa;
- o canal primeiro, o banco depois: se o WhatsApp recusar, nada muda no Otto;
- a versão anterior fica guardada, e a bolha diz quem apagou.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import httpx
import pytest_asyncio
from sqlalchemy import text

from app.channels.types import OutboundMessage, SendResult
from app.channels.whatsapp import EvolutionProvider
from app.core.database import AsyncSessionLocal
from app.main import app
from app.models.enums import Channel, ContentType
from app.services import inbox_queries

SENHA = "s3cr3tpassword"


class _Canal:
    """Provider falso: registra o que pediram e responde o que o teste mandar."""

    def __init__(self) -> None:
        self.chamadas: list[tuple[str, str, str]] = []
        self.sucesso = True

    async def edit_message(self, message, external_id):
        self.chamadas.append(("editar", external_id, message.content))
        return SendResult(success=self.sucesso, error=None if self.sucesso else "x")

    async def delete_message(self, message, external_id):
        self.chamadas.append(("apagar", external_id, ""))
        return SendResult(success=self.sucesso, error=None if self.sucesso else "x")


@pytest_asyncio.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest_asyncio.fixture
async def canal(monkeypatch):
    falso = _Canal()
    monkeypatch.setattr("app.api.inbox.get_provider", lambda _canal: falso)
    return falso


@pytest_asyncio.fixture
async def cenario():
    tid, conn, contato, conv = (uuid.uuid4() for _ in range(4))
    autor, colega = uuid.uuid4(), uuid.uuid4()
    sufixo = tid.hex[:8]
    hashed = bcrypt.hashpw(SENHA.encode(), bcrypt.gensalt()).decode()
    agora = datetime.now(timezone.utc)
    msgs = {}
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO tenants (id, name) VALUES (:id, 'Tenant Revisão Equipe')"
            ),
            {"id": str(tid)},
        )
        for uid, nome in ((autor, "Bruno"), (colega, "Carla")):
            await db.execute(
                text(
                    "INSERT INTO users (id, tenant_id, email, hashed_password, full_name, "
                    "role, is_active) VALUES (:id, :t, :e, :h, :n, 'AGENT', true)"
                ),
                {
                    "id": str(uid),
                    "t": str(tid),
                    "e": f"{nome.lower()}_{sufixo}@example.com",
                    "h": hashed,
                    "n": nome,
                },
            )
        await db.execute(
            text(
                "INSERT INTO channel_connections (id, tenant_id, channel, credentials, "
                "is_active) VALUES (:id, :t, 'WHATSAPP', CAST(:c AS jsonb), true)"
            ),
            {"id": str(conn), "t": str(tid), "c": json.dumps({"instance": "inst"})},
        )
        await db.execute(
            text(
                "INSERT INTO contacts (id, tenant_id, channel, external_id, name) "
                "VALUES (:id, :t, 'WHATSAPP', '5511955555@s.whatsapp.net', 'Cliente')"
            ),
            {"id": str(contato), "t": str(tid)},
        )
        await db.execute(
            text(
                "INSERT INTO conversations (id, tenant_id, contact_id, status, "
                "unread_count, channel_connection_id, assigned_user_id) "
                "VALUES (:id, :t, :c, 'OPEN', 0, :conn, :autor)"
            ),
            {
                "id": str(conv),
                "t": str(tid),
                "c": str(contato),
                "conn": str(conn),
                "autor": str(autor),
            },
        )
        casos = {
            "recente": (autor, "TEXT", agora - timedelta(minutes=2), True),
            "de_ontem": (autor, "TEXT", agora - timedelta(days=1), True),
            "antiga": (autor, "TEXT", agora - timedelta(days=3), True),
            "do_colega": (colega, "TEXT", agora - timedelta(minutes=2), True),
            "foto": (autor, "IMAGE", agora - timedelta(minutes=2), True),
            "nao_saiu": (autor, "TEXT", agora - timedelta(minutes=2), False),
        }
        for nome, (quem, tipo, quando, saiu) in casos.items():
            mid = uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO messages (id, conversation_id, tenant_id, direction, "
                    "content_type, content, channel, external_id, sender_user_id, "
                    "created_at) VALUES (:id, :c, :t, 'OUTBOUND', :tipo, 'dia 12, R$ 0', "
                    "'WHATSAPP', :ext, :quem, :quando)"
                ),
                {
                    "id": str(mid),
                    "c": str(conv),
                    "t": str(tid),
                    "tipo": tipo,
                    "ext": f"EXT-{mid.hex[:10]}" if saiu else None,
                    "quem": str(quem),
                    "quando": quando,
                },
            )
            msgs[nome] = mid
        await db.commit()

    yield {
        "tid": tid,
        "conv": conv,
        "autor_email": f"bruno_{sufixo}@example.com",
        "colega_email": f"carla_{sufixo}@example.com",
        **msgs,
    }

    async with AsyncSessionLocal() as db:
        for tabela in (
            "messages",
            "conversations",
            "contacts",
            "channel_connections",
            "users",
        ):
            await db.execute(
                text(f"DELETE FROM {tabela} WHERE tenant_id = :t"), {"t": str(tid)}
            )
        await db.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": str(tid)})
        await db.commit()


async def _auth(client, email: str) -> dict:
    r = await client.post("/auth/token", data={"username": email, "password": SENHA})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _url(cenario, nome: str) -> str:
    return f"/conversations/{cenario['conv']}/messages/{cenario[nome]}"


async def _linha(cenario, nome: str) -> dict:
    async with AsyncSessionLocal() as db:
        msgs = await inbox_queries.get_conversation_messages(
            db, cenario["tid"], cenario["conv"]
        )
    return next(m for m in msgs if m["id"] == str(cenario[nome]))


async def test_editar_a_propria_guarda_o_original(client, canal, cenario) -> None:
    auth = await _auth(client, cenario["autor_email"])
    r = await client.patch(
        _url(cenario, "recente"), json={"content": "dia 13"}, headers=auth
    )
    assert r.status_code == 204, r.text
    assert canal.chamadas[0][0] == "editar" and canal.chamadas[0][2] == "dia 13"
    linha = await _linha(cenario, "recente")
    assert linha["content"] == "dia 13"
    assert linha["original_content"] == "dia 12, R$ 0"
    assert linha["edited_at"] is not None


async def test_apagar_marca_quem_apagou(client, canal, cenario) -> None:
    auth = await _auth(client, cenario["autor_email"])
    assert (
        await client.delete(_url(cenario, "de_ontem"), headers=auth)
    ).status_code == 204
    linha = await _linha(cenario, "de_ontem")
    assert linha["deleted_by"] == "Bruno"
    assert linha["content"] == "dia 12, R$ 0"
    # Apagar de novo não vai ao WhatsApp.
    assert (
        await client.delete(_url(cenario, "de_ontem"), headers=auth)
    ).status_code == 409
    assert len(canal.chamadas) == 1


async def test_mensagem_do_colega_nao_e_sua(client, canal, cenario) -> None:
    auth = await _auth(client, cenario["autor_email"])
    r = await client.delete(_url(cenario, "do_colega"), headers=auth)
    assert r.status_code == 404
    assert canal.chamadas == []


async def test_janelas_do_whatsapp(client, canal, cenario) -> None:
    auth = await _auth(client, cenario["autor_email"])
    editar_ontem = await client.patch(
        _url(cenario, "de_ontem"), json={"content": "x"}, headers=auth
    )
    assert editar_ontem.status_code == 409
    assert (
        await client.delete(_url(cenario, "antiga"), headers=auth)
    ).status_code == 409
    assert canal.chamadas == []


async def test_so_texto_edita_e_so_o_que_saiu(client, canal, cenario) -> None:
    auth = await _auth(client, cenario["autor_email"])
    foto = await client.patch(
        _url(cenario, "foto"), json={"content": "x"}, headers=auth
    )
    assert foto.status_code == 422
    nao_saiu = await client.delete(_url(cenario, "nao_saiu"), headers=auth)
    assert nao_saiu.status_code == 409


async def test_canal_recusou_nada_muda(client, canal, cenario) -> None:
    canal.sucesso = False
    auth = await _auth(client, cenario["autor_email"])
    r = await client.patch(
        _url(cenario, "recente"), json={"content": "x"}, headers=auth
    )
    assert r.status_code == 502
    linha = await _linha(cenario, "recente")
    assert linha["content"] == "dia 12, R$ 0"
    assert linha["edited_at"] is None


async def test_evolution_recebe_a_key_da_original(monkeypatch) -> None:
    """O WhatsApp aplica a edição ao par (remoteJid, id) — o JID tem de ser o do envio."""
    pedidos = []

    class Cliente:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, metodo, url, json=None, **kw):
            pedidos.append((metodo, url, json))
            return httpx.Response(200, json={})

    monkeypatch.setattr(
        "app.channels.whatsapp.httpx.AsyncClient", lambda *a, **k: Cliente()
    )
    msg = OutboundMessage(
        channel=Channel.WHATSAPP,
        channel_connection_id=uuid.uuid4(),
        to_external_id="999@lid",
        to_phone="5511955555",
        content_type=ContentType.TEXT,
        content="novo",
        channel_metadata={"instance_key": "inst"},
    )
    provider = EvolutionProvider()
    assert (await provider.edit_message(msg, "ABC")).success
    assert (await provider.delete_message(msg, "ABC")).success
    (m1, u1, c1), (m2, u2, c2) = pedidos
    assert m1 == "POST" and u1.endswith("/chat/updateMessage/inst")
    assert c1["key"] == {
        "id": "ABC",
        "remoteJid": "5511955555@s.whatsapp.net",
        "fromMe": True,
    }
    assert m2 == "DELETE" and u2.endswith("/chat/deleteMessageForEveryone/inst")
    assert c2["remoteJid"] == "5511955555@s.whatsapp.net"
