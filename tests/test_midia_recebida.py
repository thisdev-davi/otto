"""Excluir mídia recebida: o arquivo sai, a mensagem fica marcada, só admin pode.

O que este arquivo trava:
- o arquivo é apagado do disco e a thread mostra "removida", não um ícone quebrado;
- excluir é condicional no banco — duas requisições juntas apagam uma vez só;
- mídia ENVIADA (que a galeria reaproveita entre conversas) nunca é apagada por aqui;
- atendente não exclui, e outro tenant nem descobre que a mensagem existe.
"""

import asyncio
import json
import uuid

import bcrypt
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.main import app
from app.services import inbox_queries, midia_recebida

pytestmark = pytest.mark.asyncio

SENHA = "s3cr3tpassword"


@pytest_asyncio.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest_asyncio.fixture
async def cenario(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "MEDIA_DIR", str(tmp_path))
    tenant_id, conv_id, contact_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    admin_id, agente_id = uuid.uuid4(), uuid.uuid4()
    sufixo = tenant_id.hex[:8]
    hashed = bcrypt.hashpw(SENHA.encode(), bcrypt.gensalt()).decode()
    mensagens = {}
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO tenants (id, name) VALUES (:id, 'Tenant Banco de Mídia')"
            ),
            {"id": str(tenant_id)},
        )
        for uid, papel in ((admin_id, "ADMIN"), (agente_id, "AGENT")):
            await db.execute(
                text(
                    "INSERT INTO users (id, tenant_id, email, hashed_password, full_name, "
                    "role, is_active) VALUES (:id, :tid, :email, :h, :nome, :papel, true)"
                ),
                {
                    "id": str(uid),
                    "tid": str(tenant_id),
                    "email": f"{papel.lower()}_{sufixo}@example.com",
                    "h": hashed,
                    "nome": "Dona Ana" if papel == "ADMIN" else "Atendente",
                    "papel": papel,
                },
            )
        await db.execute(
            text(
                "INSERT INTO contacts (id, tenant_id, channel, external_id, name) "
                "VALUES (:id, :tid, 'WHATSAPP', '5511933333', 'Cliente')"
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
        for nome, direcao in (("recebida", "INBOUND"), ("enviada", "OUTBOUND")):
            msg_id = uuid.uuid4()
            arquivo = tmp_path / f"{msg_id}.jpg"
            arquivo.write_bytes(b"\xff\xd8jpeg")
            meta = {
                "media": {
                    "has_media": True,
                    "status": "stored",
                    "path": str(arquivo),
                    "mimetype": "image/jpeg",
                }
            }
            await db.execute(
                text(
                    "INSERT INTO messages (id, conversation_id, tenant_id, direction, "
                    "content_type, channel, external_id, channel_metadata) VALUES "
                    "(:id, :cid, :tid, :dir, 'IMAGE', 'WHATSAPP', :ext, CAST(:meta AS jsonb))"
                ),
                {
                    "id": str(msg_id),
                    "cid": str(conv_id),
                    "tid": str(tenant_id),
                    "dir": direcao,
                    "ext": str(uuid.uuid4()),
                    "meta": json.dumps(meta),
                },
            )
            mensagens[nome] = {"id": msg_id, "path": arquivo}
        await db.commit()

    yield {
        "tenant_id": tenant_id,
        "conv_id": conv_id,
        "admin_id": admin_id,
        "admin_email": f"admin_{sufixo}@example.com",
        "agente_email": f"agent_{sufixo}@example.com",
        **mensagens,
    }

    async with AsyncSessionLocal() as db:
        for tabela in ("messages", "conversations", "contacts", "users"):
            await db.execute(
                text(f"DELETE FROM {tabela} WHERE tenant_id = :t"),
                {"t": str(tenant_id)},
            )
        await db.execute(
            text("DELETE FROM tenants WHERE id = :t"), {"t": str(tenant_id)}
        )
        await db.commit()


async def _token(client, email: str) -> dict:
    r = await client.post("/auth/token", data={"username": email, "password": SENHA})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def test_admin_exclui_e_a_thread_mostra_removida(client, cenario) -> None:
    auth = await _token(client, cenario["admin_email"])
    alvo = cenario["recebida"]

    r = await client.delete(f"/media/{alvo['id']}", headers=auth)
    assert r.status_code == 204
    assert not alvo["path"].exists()

    async with AsyncSessionLocal() as db:
        msgs = await inbox_queries.get_conversation_messages(
            db, cenario["tenant_id"], cenario["conv_id"]
        )
        acao = await db.scalar(
            text(
                "SELECT count(*) FROM audit_events WHERE tenant_id = :t "
                "AND action = 'media.delete'"
            ),
            {"t": str(cenario["tenant_id"])},
        )
    linha = next(m for m in msgs if m["id"] == str(alvo["id"]))
    assert linha["media_url"] is None
    assert linha["media_removed_at"] is not None
    assert linha["media_removed_by"] == "Dona Ana"
    assert acao == 1

    # A mídia some também da rota que serve o arquivo.
    assert (await client.get(f"/media/{alvo['id']}", headers=auth)).status_code == 404
    # Excluir de novo não tem o que fazer.
    assert (
        await client.delete(f"/media/{alvo['id']}", headers=auth)
    ).status_code == 404


async def test_midia_enviada_nao_e_apagada(client, cenario) -> None:
    """A galeria reaproveita o arquivo entre conversas: apagar por aqui furaria todas."""
    auth = await _token(client, cenario["admin_email"])
    r = await client.delete(f"/media/{cenario['enviada']['id']}", headers=auth)
    assert r.status_code == 404
    assert cenario["enviada"]["path"].exists()


async def test_atendente_nao_exclui(client, cenario) -> None:
    auth = await _token(client, cenario["agente_email"])
    r = await client.delete(f"/media/{cenario['recebida']['id']}", headers=auth)
    assert r.status_code == 403
    assert cenario["recebida"]["path"].exists()


async def test_exclusao_concorrente_apaga_uma_vez(cenario) -> None:
    async def tentar() -> bool:
        async with AsyncSessionLocal() as db:
            return await midia_recebida.remover(
                db, cenario["tenant_id"], cenario["recebida"]["id"], cenario["admin_id"]
            )

    resultados = await asyncio.gather(tentar(), tentar())
    assert sorted(resultados) == [False, True]


async def test_outro_tenant_nao_alcanca(cenario) -> None:
    async with AsyncSessionLocal() as db:
        assert not await midia_recebida.remover(
            db, uuid.uuid4(), cenario["recebida"]["id"], cenario["admin_id"]
        )
    assert cenario["recebida"]["path"].exists()
