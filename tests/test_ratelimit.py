"""Rate limiting do login e do webhook.

Não existia limite nenhum antes. `POST /auth/token` era adivinhação de senha ilimitada e,
como cada tentativa queima um bcrypt de custo 12 no event loop, também era negação de
serviço barata: não é preciso acertar a senha para ocupar a API.

O teste que trava a correção é `test_conta_por_email_tambem`: limitar só por IP deixa de
fora o ataque distribuído contra uma conta específica, que é o caso que realmente derruba
a senha de alguém.
"""

import asyncio
import uuid

import bcrypt
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core import ratelimit
from app.core.config import settings
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
async def usuario(db_session):
    tenant_id = uuid.uuid4()
    email = f"rl_{tenant_id.hex[:8]}@example.com"
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, 'Tenant RL')"),
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
        text("DELETE FROM users WHERE tenant_id = :tid"), {"tid": str(tenant_id)}
    )
    await db_session.execute(
        text("DELETE FROM tenants WHERE id = :tid"), {"tid": str(tenant_id)}
    )
    await db_session.commit()


class TestLimiteDeLogin:
    async def test_senha_errada_em_rajada_vira_429(self, client, usuario):
        vistos = []
        for _ in range(settings.RATE_LIMIT_LOGIN + 3):
            resp = await client.post(
                "/auth/token",
                data={"username": usuario["email"], "password": "errada"},
            )
            vistos.append(resp.status_code)

        assert 401 in vistos, "as primeiras tentativas têm que ser avaliadas de verdade"
        assert 429 in vistos, "a rajada tem que ser cortada"
        assert vistos[-1] == 429

    async def test_conta_por_email_tambem(self, client, usuario):
        """Limitar só por IP não cobre o ataque distribuído contra uma conta."""
        for _ in range(settings.RATE_LIMIT_LOGIN + 1):
            await client.post(
                "/auth/token",
                data={"username": usuario["email"], "password": "errada"},
            )
        # Outro IP, mesma conta: o balde por e-mail continua estourado.
        resp = await client.post(
            "/auth/token",
            data={"username": usuario["email"], "password": SENHA},
            headers={"X-Forwarded-For": "203.0.113.9"},
        )
        assert resp.status_code == 429

    async def test_login_valido_passa(self, client, usuario):
        resp = await client.post(
            "/auth/token", data={"username": usuario["email"], "password": SENHA}
        )
        assert resp.status_code == 200
        assert resp.json()["access_token"]


class TestIpDoCliente:
    """`X-Forwarded-For` só vale com proxy declarado — senão qualquer um troca de IP."""

    def _request(self, headers: dict) -> httpx.Request:
        from starlette.requests import Request

        escopo = {
            "type": "http",
            "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
            "client": ("10.0.0.1", 1234),
        }
        return Request(escopo)

    async def test_ignora_xff_sem_proxy_confiavel(self, monkeypatch):
        monkeypatch.setattr(settings, "TRUSTED_PROXY", False)
        req = self._request({"X-Forwarded-For": "1.2.3.4"})
        assert ratelimit.ip_do_cliente(req) == "10.0.0.1"

    async def test_usa_xff_com_proxy_confiavel(self, monkeypatch):
        monkeypatch.setattr(settings, "TRUSTED_PROXY", True)
        monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 1)
        req = self._request({"X-Forwarded-For": "10.0.0.9"})
        assert ratelimit.ip_do_cliente(req) == "10.0.0.9"

    async def test_item_forjado_pelo_cliente_nao_vale(self, monkeypatch):
        """O cliente manda `1.2.3.4`; o proxy acrescenta o IP real à direita."""
        monkeypatch.setattr(settings, "TRUSTED_PROXY", True)
        monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 1)
        req = self._request({"X-Forwarded-For": "1.2.3.4, 198.51.100.7"})
        assert ratelimit.ip_do_cliente(req) == "198.51.100.7"

    async def test_dois_proxies(self, monkeypatch):
        monkeypatch.setattr(settings, "TRUSTED_PROXY", True)
        monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 2)
        req = self._request({"X-Forwarded-For": "1.2.3.4, 198.51.100.7, 10.0.0.9"})
        assert ratelimit.ip_do_cliente(req) == "198.51.100.7"


class TestContador:
    async def test_libera_ate_o_limite_e_corta_depois(self):
        chave = f"teste:{uuid.uuid4()}"
        assert [await ratelimit.consumir(chave, 3, 60) for _ in range(5)] == [
            True,
            True,
            True,
            False,
            False,
        ]

    async def test_falha_aberta_sem_redis(self, monkeypatch):
        """Redis fora do ar não pode trancar o dono para fora do painel."""

        def explode():
            raise RuntimeError("redis fora do ar")

        monkeypatch.setattr(ratelimit, "_cliente", explode)
        assert await ratelimit.consumir("qualquer", 1, 60) is True


class TestClientePorEventLoop:
    """Uma task Celery é um `asyncio.run` — logo, um event loop novo (`tasks/runner.py`).

    O cliente do redis guardado num global sobrevivia ao loop que o criou, e a task
    seguinte levantava `got Future attached to a different loop`. O efeito não era um erro
    visível: como `consumir` falha ABERTA, o teto diário de turnos do agente simplesmente
    parava de contar — `AI_MAX_TURNS_DIA` desligado de fato no único caminho que gasta LLM.

    Este teste roda numa thread porque `asyncio.run` não pode ser chamado de dentro de um
    loop já rodando, e o que precisa ser exercitado é justamente o `run_task` de verdade.
    """

    async def test_duas_tasks_seguidas_continuam_contando(self):
        from app.tasks.runner import run_task

        chave = f"teste:loop:{uuid.uuid4()}"

        def duas_tasks() -> list[bool]:
            # Limite 1: a segunda task TEM que ser recusada. Com o cliente preso ao loop
            # da primeira, ela levantaria, cairia na falha aberta e devolveria True.
            return [
                run_task(lambda: ratelimit.consumir(chave, 1, 60)),
                run_task(lambda: ratelimit.consumir(chave, 1, 60)),
            ]

        assert await asyncio.to_thread(duas_tasks) == [True, False]
