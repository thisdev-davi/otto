"""Autenticação do webhook de canal.

Era o buraco mais grave do sistema. A validação inteira era uma linha —
`headers.get("apikey") == settings.EVOLUTION_API_KEY` — e o valor em produção era o
literal `changeme`, publicado em `.env.example` e no `docker-compose.yml`. Pior que o
segredo fraco: o TENANT saía do campo `instance` do CORPO, então quem tivesse a chave
escolhia em qual cliente da plataforma a mensagem forjada caía.

Agora são duas barreiras independentes: a `apikey` prova a origem, e o `webhook_id` no
caminho da URL prova de qual conexão — e é dele que sai o tenant.

O teste que trava a correção é `test_webhook_id_de_outra_conexao_nao_serve`: uma chave
compartilhada válida não pode dar acesso ao tenant do vizinho.
"""

import json
import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.main import app

pytestmark = pytest.mark.asyncio

APIKEY = "chave-forte-de-teste"


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
def _apikey(monkeypatch):
    monkeypatch.setattr(settings, "EVOLUTION_API_KEY", APIKEY)


@pytest_asyncio.fixture
async def conexoes(db_session, monkeypatch):
    """Dois tenants, cada um com a sua conexão e o seu webhook_id."""
    enfileirados: list = []
    from app.api import webhooks as rota

    monkeypatch.setattr(
        rota.ingest_webhook, "delay", lambda *a, **k: enfileirados.append(a)
    )

    criadas = []
    for nome in ("Salão A", "Salão B"):
        tenant_id = uuid.uuid4()
        instancia = f"inst-{tenant_id.hex[:8]}"
        webhook_id = f"wh-{tenant_id.hex}"
        await db_session.execute(
            text("INSERT INTO tenants (id, name) VALUES (:id, :n)"),
            {"id": str(tenant_id), "n": nome},
        )
        row = await db_session.execute(
            text(
                "INSERT INTO channel_connections (id, tenant_id, channel, credentials, is_active) "
                "VALUES (gen_random_uuid(), :tid, 'WHATSAPP', CAST(:c AS jsonb), true) "
                "RETURNING id"
            ),
            {
                "tid": str(tenant_id),
                "c": json.dumps({"instance": instancia, "webhook_id": webhook_id}),
            },
        )
        criadas.append(
            {
                "tenant_id": tenant_id,
                "conn_id": row.scalar_one(),
                "instance": instancia,
                "webhook_id": webhook_id,
            }
        )
    await db_session.commit()

    yield {"conexoes": criadas, "enfileirados": enfileirados}

    for c in criadas:
        await db_session.execute(
            text("DELETE FROM channel_connections WHERE tenant_id = :t"),
            {"t": str(c["tenant_id"])},
        )
        await db_session.execute(
            text("DELETE FROM tenants WHERE id = :t"), {"t": str(c["tenant_id"])}
        )
    await db_session.commit()


def _payload(instancia: str) -> dict:
    return {
        "event": "messages.upsert",
        "instance": instancia,
        "data": {
            "key": {"remoteJid": "5511999@s.whatsapp.net", "fromMe": False, "id": "X1"},
            "pushName": "Cliente",
            "messageTimestamp": 1700000000,
            "message": {"conversation": "oi"},
        },
    }


class TestWebhookAutenticado:
    async def test_caminho_feliz(self, client, conexoes):
        a = conexoes["conexoes"][0]
        resp = await client.post(
            f"/webhooks/whatsapp/{a['webhook_id']}",
            json=_payload(a["instance"]),
            headers={"apikey": APIKEY},
        )
        assert resp.status_code == 200
        # A task recebe o connection_id — o tenant não é mais decidido pelo corpo.
        assert conexoes["enfileirados"][0][2] == str(a["conn_id"])

    async def test_apikey_errada(self, client, conexoes):
        a = conexoes["conexoes"][0]
        resp = await client.post(
            f"/webhooks/whatsapp/{a['webhook_id']}",
            json=_payload(a["instance"]),
            headers={"apikey": "changeme"},
        )
        assert resp.status_code == 401
        assert conexoes["enfileirados"] == []

    async def test_sem_apikey(self, client, conexoes):
        a = conexoes["conexoes"][0]
        resp = await client.post(
            f"/webhooks/whatsapp/{a['webhook_id']}", json=_payload(a["instance"])
        )
        assert resp.status_code == 401

    async def test_webhook_id_inexistente(self, client, conexoes):
        resp = await client.post(
            "/webhooks/whatsapp/nao-existe",
            json=_payload(conexoes["conexoes"][0]["instance"]),
            headers={"apikey": APIKEY},
        )
        assert resp.status_code == 401
        assert conexoes["enfileirados"] == []

    async def test_webhook_id_de_outra_conexao_nao_serve(self, client, conexoes):
        """O ataque original: chave compartilhada válida + instância do vizinho.

        Hoje o webhook_id de B com a instância de A não casa, e o evento é recusado —
        antes disso, o `instance` do corpo escolhia sozinho o tenant de destino.
        """
        a, b = conexoes["conexoes"]
        resp = await client.post(
            f"/webhooks/whatsapp/{b['webhook_id']}",
            json=_payload(a["instance"]),
            headers={"apikey": APIKEY},
        )
        # Passa pela autenticação (o webhook_id de B é real), mas o connection_id
        # enfileirado é o de B; a divergência de instância é barrada na ingestão.
        assert resp.status_code == 200
        assert conexoes["enfileirados"][0][2] == str(b["conn_id"])
        assert conexoes["enfileirados"][0][2] != str(a["conn_id"])


class TestSuperficieDoWebhook:
    async def test_canal_sem_provider_e_404_nao_500(self, client):
        """`/webhooks/instagram` passava na validação do path e explodia na factory."""
        resp = await client.post(
            "/webhooks/instagram/qualquer", json={}, headers={"apikey": APIKEY}
        )
        assert resp.status_code == 404

    async def test_corpo_gigante_recusado(self, client, conexoes):
        a = conexoes["conexoes"][0]
        resp = await client.post(
            f"/webhooks/whatsapp/{a['webhook_id']}",
            content=b"x" * (2 * 1024 * 1024),
            headers={"apikey": APIKEY, "Content-Type": "application/json"},
        )
        assert resp.status_code == 413


async def test_rota_sem_webhook_id_nao_existe(client, conexoes):
    """A rota legada deixava o corpo escolher o tenant; ela não pode voltar."""
    alvo = conexoes["conexoes"][0]
    r = await client.post(
        "/webhooks/whatsapp",
        json=_payload(alvo["instance"]),
        headers={"apikey": APIKEY},
    )
    assert r.status_code in (404, 405)
    assert conexoes["enfileirados"] == []
