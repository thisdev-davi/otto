"""Foto de perfil do contato: quando buscar, o que aceitar, quem pode ver.

Três coisas se travam aqui, e cada uma corresponde a uma forma conhecida de errar:

1. **Quando buscar.** Sem a expiração preguiçosa, ou a foto nunca atualiza, ou o worker
   vai à rede a cada mensagem de todo contato que esconde a dela — que é a maioria.
2. **O que aceitar.** O `content-type` vem do outro lado da rede (`CLAUDE.md` regra 6):
   é a allow-list que decide, nunca o remetente.
3. **Quem pode ver.** A URL é assinada porque `<img src>` não manda header — e aceitar
   credencial pela URL não pode virar "a rota ficou aberta".
"""

import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import httpx
import pytest_asyncio
from sqlalchemy import text

from app.channels.whatsapp import EvolutionProvider
from app.core.database import AsyncSessionLocal
from app.main import app
from app.services import avatars, media_links

# Sem `pytestmark` global: metade dos testes daqui é síncrona (a decisão de buscar e a
# assinatura são funções puras), e `asyncio_mode = auto` já cuida das outras.
SENHA = "s3cr3tpassword"


# ─────────────────────────── quando buscar ───────────────────────────


class TestQuandoBuscar:
    def test_contato_sem_avatar_nenhum(self) -> None:
        assert avatars.desatualizado(None) is True
        assert avatars.desatualizado({}) is True

    def test_recem_buscado_nao_repete(self) -> None:
        agora = datetime.now(timezone.utc).isoformat()
        meta = {"avatar": {"status": "stored", "fetched_at": agora}}
        assert avatars.desatualizado(meta) is False

    def test_sem_foto_visivel_tambem_conta_como_buscado(self) -> None:
        """O contato que esconde a foto é o caso COMUM.

        Sem registrar a tentativa, cada mensagem dele dispararia uma ida ao Evolution e
        outra ao CDN — para receber o mesmo "não tem" de sempre.
        """
        agora = datetime.now(timezone.utc).isoformat()
        meta = {"avatar": {"status": "unavailable", "fetched_at": agora}}
        assert avatars.desatualizado(meta) is False

    def test_foto_velha_volta_a_ser_buscada(self) -> None:
        antigo = datetime.now(timezone.utc) - timedelta(days=avatars.VALIDADE_DIAS + 1)
        meta = {"avatar": {"status": "stored", "fetched_at": antigo.isoformat()}}
        assert avatars.desatualizado(meta) is True

    def test_carimbo_ilegivel_nao_trava_a_busca(self) -> None:
        """Data corrompida não pode significar 'nunca mais busque'."""
        meta = {"avatar": {"status": "stored", "fetched_at": "ontem"}}
        assert avatars.desatualizado(meta) is True


class TestUrlAssinada:
    def test_sem_foto_guardada_nao_gera_url(self) -> None:
        tenant, contato = uuid.uuid4(), uuid.uuid4()
        assert avatars.url_de(tenant, contato, None) is None
        assert (
            avatars.url_de(
                tenant, contato, {"avatar": {"status": avatars.STATUS_UNAVAILABLE}}
            )
            is None
        )

    def test_com_foto_a_url_vem_assinada(self) -> None:
        """Nunca `?token=`: o que vai na query é assinatura de um recurso só."""
        tenant, contato = uuid.uuid4(), uuid.uuid4()
        url = avatars.url_de(
            tenant, contato, {"avatar": {"status": avatars.STATUS_STORED}}
        )
        assert url is not None
        assert url.startswith(f"/contacts/{contato}/avatar?")
        assert "token=" not in url
        assert "sig=" in url and "exp=" in url

    def test_url_fica_igual_dentro_da_janela(self, monkeypatch) -> None:
        """A lista da inbox re-assina a cada evento de WebSocket.

        Com `exp` novo a cada chamada a URL mudava a cada segundo, o `Cache-Control` nunca
        acertava e cada mensagem na conta rebaixava todas as fotos da lista.
        """
        tenant, contato = uuid.uuid4(), uuid.uuid4()
        meta = {"avatar": {"status": avatars.STATUS_STORED}}
        inicio = 1_800_000_000 - 1_800_000_000 % avatars.CACHE_SEGUNDOS
        monkeypatch.setattr(media_links.time, "time", lambda: inicio + 10)
        antes = avatars.url_de(tenant, contato, meta)
        monkeypatch.setattr(
            media_links.time, "time", lambda: inicio + avatars.CACHE_SEGUNDOS - 10
        )
        depois = avatars.url_de(tenant, contato, meta)
        assert antes == depois

    def test_alinhar_so_estica_a_validade(self, monkeypatch) -> None:
        """Arredondar o vencimento não pode entregar URL que vence antes do TTL."""
        tenant, contato = uuid.uuid4(), uuid.uuid4()
        agora = 1_800_000_123
        monkeypatch.setattr(media_links.time, "time", lambda: agora)
        par = media_links.assinar(
            avatars.TIPO_LINK, contato, tenant, janela=avatars.CACHE_SEGUNDOS
        )
        campos = dict(p.split("=", 1) for p in par.split("&"))
        assert int(campos["exp"]) >= agora + media_links.TTL_PADRAO
        assert media_links.validar(
            avatars.TIPO_LINK, contato, tenant, int(campos["exp"]), campos["sig"]
        )


# ─────────────────────────── o que aceitar ───────────────────────────


class _RespostaFalsa:
    def __init__(self, status_code=200, payload=None, content=b"", headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.content = content
        self.headers = headers or {}

    def json(self):
        return self._payload


def _cliente_falso(monkeypatch, url_resp, imagem_resp) -> None:
    """Substitui o httpx do provider: uma resposta para o POST, outra para o GET."""

    class ClienteFalso:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *args, **kwargs):
            return url_resp

        async def get(self, *args, **kwargs):
            return imagem_resp

    monkeypatch.setattr(
        "app.channels.whatsapp.httpx.AsyncClient", lambda *a, **kw: ClienteFalso()
    )


class TestDownloadDoProvider:
    async def test_imagem_valida_desce(self, monkeypatch) -> None:
        _cliente_falso(
            monkeypatch,
            _RespostaFalsa(payload={"profilePictureUrl": "https://cdn/x.jpg"}),
            _RespostaFalsa(
                content=b"\xff\xd8jpeg", headers={"content-type": "image/jpeg"}
            ),
        )
        resultado = await EvolutionProvider().fetch_avatar({"instance": "i"}, "555@lid")
        assert resultado == (b"\xff\xd8jpeg", "image/jpeg")

    async def test_contato_sem_foto_visivel_devolve_none(self, monkeypatch) -> None:
        """Resposta sem `profilePictureUrl` é resposta, não erro."""
        _cliente_falso(monkeypatch, _RespostaFalsa(payload={}), _RespostaFalsa())
        assert (
            await EvolutionProvider().fetch_avatar({"instance": "i"}, "5@lid") is None
        )

    async def test_content_type_fora_da_allow_list_e_recusado(
        self, monkeypatch
    ) -> None:
        """A regra 6 no ponto onde ela nasceu: o tipo vem do outro lado da rede.

        Sem esta trava, um `text/html` devolvido pelo CDN viraria arquivo servido na
        nossa origem — exatamente o buraco que a mídia de conversa já fechou.
        """
        _cliente_falso(
            monkeypatch,
            _RespostaFalsa(payload={"profilePictureUrl": "https://cdn/x"}),
            _RespostaFalsa(
                content=b"<script>",
                headers={"content-type": "text/html; charset=utf-8"},
            ),
        )
        assert (
            await EvolutionProvider().fetch_avatar({"instance": "i"}, "5@lid") is None
        )

    async def test_imagem_acima_do_teto_e_recusada(self, monkeypatch) -> None:
        _cliente_falso(
            monkeypatch,
            _RespostaFalsa(payload={"profilePictureUrl": "https://cdn/x"}),
            _RespostaFalsa(
                content=b"x" * (6 * 1024 * 1024), headers={"content-type": "image/png"}
            ),
        )
        assert (
            await EvolutionProvider().fetch_avatar({"instance": "i"}, "5@lid") is None
        )

    async def test_sem_instancia_nao_vai_a_rede(self, monkeypatch) -> None:
        def explode(*a, **kw):
            raise AssertionError("não podia ter ido à rede")

        monkeypatch.setattr("app.channels.whatsapp.httpx.AsyncClient", explode)
        assert await EvolutionProvider().fetch_avatar({}, "5@lid") is None


# ─────────────────────────── quem pode ver ───────────────────────────


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


async def _tenant_com_avatar(db, tmp_path, nome: str) -> dict:
    tenant_id, contact_id = uuid.uuid4(), uuid.uuid4()
    email = f"user_{contact_id.hex[:8]}@example.com"
    arquivo = tmp_path / f"{contact_id}.jpg"
    arquivo.write_bytes(b"\xff\xd8\xff-conteudo-de-imagem-falso")

    await db.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, :nome)"),
        {"id": str(tenant_id), "nome": nome},
    )
    await db.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, hashed_password, role, is_active) "
            "VALUES (gen_random_uuid(), :tid, :email, :hashed, 'AGENT', true)"
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
            "VALUES (:id, :tid, 'WHATSAPP', :ext, 'Cliente com Foto')"
        ),
        {"id": str(contact_id), "tid": str(tenant_id), "ext": str(uuid.uuid4())},
    )
    await avatars.registrar(
        db,
        tenant_id,
        contact_id,
        {
            "status": avatars.STATUS_STORED,
            "path": str(arquivo),
            "mimetype": "image/jpeg",
        },
    )
    await db.commit()
    return {"tenant_id": tenant_id, "contact_id": contact_id, "email": email}


async def _limpa(db, tenant_id) -> None:
    for stmt in (
        "DELETE FROM contacts WHERE tenant_id = :tid",
        "DELETE FROM users WHERE tenant_id = :tid",
        "DELETE FROM tenants WHERE id = :tid",
    ):
        await db.execute(text(stmt), {"tid": str(tenant_id)})
    await db.commit()


@pytest_asyncio.fixture
async def dono(db_session, tmp_path):
    dados = await _tenant_com_avatar(db_session, tmp_path, "Tenant Avatar")
    yield dados
    await _limpa(db_session, dados["tenant_id"])


@pytest_asyncio.fixture
async def vizinho(db_session, tmp_path):
    dados = await _tenant_com_avatar(db_session, tmp_path, "Tenant Vizinho")
    yield dados
    await _limpa(db_session, dados["tenant_id"])


async def _login(client: httpx.AsyncClient, email: str) -> str:
    resp = await client.post("/auth/token", data={"username": email, "password": SENHA})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


class TestAcessoAoAvatar:
    async def test_com_assinatura(self, client, dono) -> None:
        assinatura = media_links.assinar(
            avatars.TIPO_LINK, dono["contact_id"], dono["tenant_id"]
        )
        resp = await client.get(f"/contacts/{dono['contact_id']}/avatar?{assinatura}")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/jpeg")
        assert resp.headers["x-content-type-options"] == "nosniff"

    async def test_sem_credencial_nenhuma(self, client, dono) -> None:
        """Aceitar assinatura na URL não pode ter aberto a rota."""
        resp = await client.get(f"/contacts/{dono['contact_id']}/avatar")
        assert resp.status_code == 401

    async def test_assinatura_de_outro_tenant_nao_vale(
        self, client, dono, vizinho
    ) -> None:
        """A assinatura é sobre (contato, tenant): trocar o dono invalida."""
        assinatura = media_links.assinar(
            avatars.TIPO_LINK, dono["contact_id"], vizinho["tenant_id"]
        )
        resp = await client.get(f"/contacts/{dono['contact_id']}/avatar?{assinatura}")
        assert resp.status_code == 401

    async def test_bearer_do_vizinho_nao_ve_foto_alheia(
        self, client, dono, vizinho
    ) -> None:
        token = await _login(client, vizinho["email"])
        resp = await client.get(
            f"/contacts/{dono['contact_id']}/avatar",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401

    async def test_contato_sem_foto_da_404(self, client, dono, db_session) -> None:
        await avatars.registrar(
            db_session,
            dono["tenant_id"],
            dono["contact_id"],
            {"status": avatars.STATUS_UNAVAILABLE},
        )
        await db_session.commit()
        assinatura = media_links.assinar(
            avatars.TIPO_LINK, dono["contact_id"], dono["tenant_id"]
        )
        resp = await client.get(f"/contacts/{dono['contact_id']}/avatar?{assinatura}")
        assert resp.status_code == 404
