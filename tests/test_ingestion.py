"""Ingestão de mensagem inbound: idempotência, não-lidas e falha informativa.

O `unread_count` só era incrementado aqui e nunca zerado em lugar nenhum do sistema —
o badge da lista crescia para sempre. O contrato agora tem as duas pontas: a ingestão
soma, `POST /conversations/{id}/read` zera.
"""

import json
import uuid
from datetime import datetime, timezone

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from app.channels.types import NormalizedInbound
from app.core.database import AsyncSessionLocal
from app.main import app
from app.models.enums import Channel, ContentType
from app.services import avatars
from app.services.ingestion import ingest

pytestmark = pytest.mark.asyncio

INSTANCIA = "otto-ingest-test"


class _FakeRedis:
    async def publish(self, *args, **kwargs) -> None:
        return None


def _inbound(
    external_id: str,
    instance: str = INSTANCIA,
    *,
    contact_external_id: str = "5511900000999@s.whatsapp.net",
    contact_name: str | None = "Cliente Teste",
    contact_phone: str | None = None,
) -> NormalizedInbound:
    return NormalizedInbound(
        channel=Channel.WHATSAPP,
        instance_key=instance,
        external_id=external_id,
        contact_external_id=contact_external_id,
        contact_name=contact_name,
        contact_phone=contact_phone,
        content_type=ContentType.TEXT,
        content="oi",
        channel_metadata={},
        timestamp=datetime.now(timezone.utc),
    )


async def _phone_do_contato(tenant_id: uuid.UUID, external_id: str) -> str | None:
    async with AsyncSessionLocal() as db:
        row = await db.execute(
            text(
                "SELECT phone FROM contacts "
                "WHERE tenant_id = :tid AND external_id = :ext"
            ),
            {"tid": str(tenant_id), "ext": external_id},
        )
        return row.scalar_one_or_none()


@pytest_asyncio.fixture
async def conexao():
    tenant_id = uuid.uuid4()
    conn_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("INSERT INTO tenants (id, name) VALUES (:id, 'Tenant Ingest')"),
            {"id": str(tenant_id)},
        )
        await db.execute(
            text(
                "INSERT INTO channel_connections (id, tenant_id, channel, credentials, is_active) "
                "VALUES (:id, :tid, 'WHATSAPP', CAST(:cred AS jsonb), true)"
            ),
            {
                "id": str(conn_id),
                "tid": str(tenant_id),
                "cred": '{"instance": "%s"}' % INSTANCIA,
            },
        )
        await db.commit()

    yield {"tenant_id": tenant_id}

    async with AsyncSessionLocal() as db:
        for tabela in (
            "bot_sessions",
            "messages",
            "conversation_events",
            "conversations",
            "contacts",
            "channel_connections",
            "users",
        ):
            await db.execute(
                text(f"DELETE FROM {tabela} WHERE tenant_id = :t"),
                {"t": str(tenant_id)},
            )
        await db.execute(
            text("DELETE FROM tenants WHERE id = :t"), {"t": str(tenant_id)}
        )
        await db.commit()


async def test_sem_conexao_ativa_e_no_op_e_nao_levanta(conexao) -> None:
    """Antes isto era `raise ValueError`, que só trocava um log legível por traceback.

    A mensagem se perde nos dois casos — o que muda é o operador conseguir descobrir
    por quê. O no-op também evita a task morrer no meio de um lote de eventos.
    """
    async with AsyncSessionLocal() as db:
        resultado = await ingest(
            db, _FakeRedis(), _inbound("MSG-SEM-CONEXAO", "instancia-que-nao-existe")
        )
    assert resultado is None


async def test_ingest_incrementa_nao_lidas(conexao) -> None:
    async with AsyncSessionLocal() as db:
        primeira = await ingest(db, _FakeRedis(), _inbound("MSG-UNREAD-1"))
        await ingest(db, _FakeRedis(), _inbound("MSG-UNREAD-2"))
    assert primeira is not None

    async with AsyncSessionLocal() as db:
        row = await db.execute(
            text("SELECT unread_count FROM conversations WHERE id = :c"),
            {"c": str(primeira["conversation_id"])},
        )
        assert row.scalar_one() == 2


async def test_contato_novo_pede_a_foto_de_perfil_uma_vez(conexao) -> None:
    """Quem decide buscar a foto é a ingestão; quem enfileira é `tasks/ingest.py`.

    A segunda mensagem não pode pedir de novo: sem isto, cada mensagem de cada cliente
    viraria duas idas à rede (Evolution + CDN do WhatsApp) para rebaixar a mesma imagem.
    """
    async with AsyncSessionLocal() as db:
        primeira = await ingest(db, _FakeRedis(), _inbound("MSG-AVATAR-1"))
        assert primeira is not None
        assert primeira["avatar_desatualizado"] is True

        # O worker teria gravado isto ao terminar. Daqui em diante, silêncio por 7 dias.
        await avatars.registrar(
            db,
            primeira["tenant_id"],
            primeira["contact_id"],
            {"status": avatars.STATUS_UNAVAILABLE},
        )
        await db.commit()

        segunda = await ingest(db, _FakeRedis(), _inbound("MSG-AVATAR-2"))
    assert segunda is not None
    assert segunda["avatar_desatualizado"] is False


async def test_evento_duplicado_e_no_op(conexao) -> None:
    """UniqueConstraint(channel, external_id): reentrega de webhook não pode duplicar."""
    async with AsyncSessionLocal() as db:
        primeira = await ingest(db, _FakeRedis(), _inbound("MSG-DUPLICADA"))
        repetida = await ingest(db, _FakeRedis(), _inbound("MSG-DUPLICADA"))
    assert primeira is not None
    assert repetida is None


async def test_read_zera_nao_lidas_e_respeita_tenant(conexao) -> None:
    """Zerar é a ponta que faltava — e não pode zerar conversa de outro tenant."""
    async with AsyncSessionLocal() as db:
        r = await ingest(db, _FakeRedis(), _inbound("MSG-READ-1"))
    assert r is not None
    conv_id, tenant_id = r["conversation_id"], r["tenant_id"]

    # Usuário do MESMO tenant zera.
    import bcrypt

    from app.api.auth import create_access_token

    user_id = uuid.uuid4()
    outro_tenant = uuid.uuid4()
    outro_user = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO users (id, tenant_id, email, hashed_password, role, is_active) "
                "VALUES (:id, :t, :e, :h, 'ADMIN', true)"
            ),
            {
                "id": str(user_id),
                "t": str(tenant_id),
                "e": f"read_{user_id.hex[:8]}@ex.com",
                "h": bcrypt.hashpw(b"x", bcrypt.gensalt()).decode(),
            },
        )
        await db.execute(
            text("INSERT INTO tenants (id, name) VALUES (:id, 'Tenant Ingest Outro')"),
            {"id": str(outro_tenant)},
        )
        await db.execute(
            text(
                "INSERT INTO users (id, tenant_id, email, hashed_password, role, is_active) "
                "VALUES (:id, :t, :e, :h, 'ADMIN', true)"
            ),
            {
                "id": str(outro_user),
                "t": str(outro_tenant),
                "e": f"read_{outro_user.hex[:8]}@ex.com",
                "h": bcrypt.hashpw(b"x", bcrypt.gensalt()).decode(),
            },
        )
        await db.commit()

    token_dono = create_access_token(tenant_id, user_id)
    token_intruso = create_access_token(outro_tenant, outro_user)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        # Tenant errado não enxerga a conversa: 404, e o contador continua de pé.
        r_intruso = await c.post(
            f"/conversations/{conv_id}/read",
            headers={"Authorization": f"Bearer {token_intruso}"},
        )
        assert r_intruso.status_code == 404

        r_dono = await c.post(
            f"/conversations/{conv_id}/read",
            headers={"Authorization": f"Bearer {token_dono}"},
        )
        assert r_dono.status_code == 200
        assert r_dono.json()["unread_count"] == 0

    async with AsyncSessionLocal() as db:
        row = await db.execute(
            text("SELECT unread_count FROM conversations WHERE id = :c"),
            {"c": str(conv_id)},
        )
        assert row.scalar_one() == 0
        await db.execute(
            text("DELETE FROM users WHERE tenant_id = :t"), {"t": str(outro_tenant)}
        )
        await db.execute(
            text("DELETE FROM tenants WHERE id = :t"), {"t": str(outro_tenant)}
        )
        await db.commit()


class TestTelefoneDoContato:
    """O telefone chega pelo canal e tem que ser GRAVADO — antes era simplesmente perdido.

    `contacts.phone` existia no modelo desde sempre e nenhuma linha do app escrevia nele.
    Quem precisava de telefone pegava o `external_id`, que com remetente `@lid` não é
    telefone nenhum: foi assim que uma reserva confirmada ficou sem como ligar ao cliente.
    """

    async def test_grava_o_telefone_no_contato_novo(self, conexao) -> None:
        async with AsyncSessionLocal() as db:
            await ingest(
                db, _FakeRedis(), _inbound("MSG-TEL-1", contact_phone="5511000000001")
            )
        assert (
            await _phone_do_contato(
                conexao["tenant_id"], "5511900000999@s.whatsapp.net"
            )
            == "5511000000001"
        )

    async def test_mensagem_sem_telefone_nao_apaga_o_conhecido(self, conexao) -> None:
        async with AsyncSessionLocal() as db:
            await ingest(
                db, _FakeRedis(), _inbound("MSG-TEL-2", contact_phone="5511000000001")
            )
            # "Não sei agora" não é "não tem": o canal nem sempre informa.
            await ingest(db, _FakeRedis(), _inbound("MSG-TEL-3", contact_phone=None))
        assert (
            await _phone_do_contato(
                conexao["tenant_id"], "5511900000999@s.whatsapp.net"
            )
            == "5511000000001"
        )

    async def test_nome_igual_ainda_grava_o_telefone(self, conexao) -> None:
        """A armadilha do `WHERE` do upsert.

        O `ON CONFLICT ... WHERE` só comparava o NOME. Como o nome quase nunca muda,
        nenhum contato que já existia ganharia telefone — e o teste do contato NOVO
        passaria assim mesmo, porque lá quem resolve é o INSERT.
        """
        async with AsyncSessionLocal() as db:
            await ingest(db, _FakeRedis(), _inbound("MSG-TEL-4", contact_phone=None))
        assert (
            await _phone_do_contato(
                conexao["tenant_id"], "5511900000999@s.whatsapp.net"
            )
            is None
        )

        async with AsyncSessionLocal() as db:
            # Mesmo nome de sempre, telefone novo.
            await ingest(
                db, _FakeRedis(), _inbound("MSG-TEL-5", contact_phone="5511000000001")
            )
        assert (
            await _phone_do_contato(
                conexao["tenant_id"], "5511900000999@s.whatsapp.net"
            )
            == "5511000000001"
        )


async def test_mesma_pessoa_em_dois_contatos_e_denunciada(conexao, caplog) -> None:
    """Instrumentação: dois endereços, um telefone só — alguém precisa saber.

    Não muda comportamento nenhum. Existe porque unificar identidade por telefone é
    mudança arriscada (mexe no endereço de envio) e hoje ninguém sabe se o cenário
    acontece de verdade. Este log é o que responde essa pergunta.
    """
    import logging

    async with AsyncSessionLocal() as db:
        await ingest(
            db,
            _FakeRedis(),
            _inbound(
                "MSG-DUP-1",
                contact_external_id="5511000000001@s.whatsapp.net",
                contact_phone="5511000000001",
            ),
        )

    with caplog.at_level(logging.ERROR, logger="uvicorn.error"):
        async with AsyncSessionLocal() as db:
            # Mesmo telefone, endereço novo — é assim que o relink parte o contato em dois.
            await ingest(
                db,
                _FakeRedis(),
                _inbound(
                    "MSG-DUP-2",
                    contact_external_id="21711730823330@lid",
                    contact_phone="5511000000001",
                ),
            )

    assert any("Contato duplicado" in r.message for r in caplog.records), (
        "sem este log ninguém descobre que a mesma pessoa virou dois contatos"
    )


async def test_evento_inbound_carrega_nome_para_a_notificacao(conexao) -> None:
    """O cartão do navegador precisa dizer DE QUEM é a mensagem.

    Sem `contact_name` no evento, a notificação do sistema diz só "Nova mensagem" e o
    atendente tem de voltar à aba justamente para descobrir se valia a pena voltar.
    """
    publicados: list[tuple] = []

    class _RedisQueGrava:
        async def publish(self, canal, corpo) -> None:
            publicados.append((canal, json.loads(corpo)))

    async with AsyncSessionLocal() as db:
        await ingest(
            db, _RedisQueGrava(), _inbound("MSG-NOTIF", contact_name="Carlos Souza")
        )

    inbound = [p for _, p in publicados if p.get("type") == "message.inbound"]
    assert len(inbound) == 1
    assert inbound[0]["contact_name"] == "Carlos Souza"
    assert inbound[0]["preview"]


async def test_sem_nome_o_evento_cai_no_telefone(conexao) -> None:
    """Contato novo chega sem pushName; o número é melhor que "Nova mensagem"."""
    publicados: list[dict] = []

    class _RedisQueGrava:
        async def publish(self, canal, corpo) -> None:
            publicados.append(json.loads(corpo))

    async with AsyncSessionLocal() as db:
        await ingest(
            db,
            _RedisQueGrava(),
            _inbound("MSG-SEM-NOME", contact_name=None, contact_phone="5511988887777"),
        )
    inbound = [p for p in publicados if p.get("type") == "message.inbound"]
    assert inbound[0]["contact_name"] == "5511988887777"


# ─────────────── conversa parada é encerrada, e a próxima começa do zero ───────────────
#
# O caso real: o cliente parou de responder em "para qual data você está pensando?" e
# voltou horas depois com "oi". A conversa continuava aberta naquela pergunta, "oi" não
# é data, e ele levava "Desculpa, não entendi" — na segunda tentativa, fila da equipe.


async def _envelhecer(conversation_id, segundos: int) -> None:
    """Empurra a última atividade da conversa para trás. É o relógio do teste."""
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "UPDATE conversations "
                "SET last_message_at = now() - make_interval(secs => :s) "
                "WHERE id = :c"
            ),
            {"c": str(conversation_id), "s": float(segundos)},
        )
        await db.commit()


async def _status(conversation_id) -> str:
    async with AsyncSessionLocal() as db:
        row = await db.execute(
            text("SELECT status FROM conversations WHERE id = :c"),
            {"c": str(conversation_id)},
        )
        return row.scalar_one()


async def test_conversa_parada_encerra_e_a_proxima_mensagem_abre_outra(conexao) -> None:
    async with AsyncSessionLocal() as db:
        primeira = await ingest(db, _FakeRedis(), _inbound("MSG-INAT-1"))
    assert primeira is not None
    await _envelhecer(primeira["conversation_id"], 7200)

    async with AsyncSessionLocal() as db:
        segunda = await ingest(db, _FakeRedis(), _inbound("MSG-INAT-2"))

    assert segunda is not None
    assert segunda["conversation_id"] != primeira["conversation_id"], (
        "a mensagem nova tem de abrir conversa nova, senão o roteiro continua parado "
        "na pergunta antiga"
    )
    assert await _status(primeira["conversation_id"]) == "RESOLVED"


async def test_conversa_recente_continua_a_mesma(conexao) -> None:
    """O encerramento não pode partir em duas uma conversa que está acontecendo."""
    async with AsyncSessionLocal() as db:
        primeira = await ingest(db, _FakeRedis(), _inbound("MSG-INAT-3"))
    await _envelhecer(primeira["conversation_id"], 120)

    async with AsyncSessionLocal() as db:
        segunda = await ingest(db, _FakeRedis(), _inbound("MSG-INAT-4"))

    assert segunda["conversation_id"] == primeira["conversation_id"]
    assert await _status(primeira["conversation_id"]) != "RESOLVED"


async def test_conversa_de_atendente_nao_e_encerrada(conexao) -> None:
    """Atendente não perde a conversa dele porque o cliente demorou para responder."""
    async with AsyncSessionLocal() as db:
        primeira = await ingest(db, _FakeRedis(), _inbound("MSG-INAT-5"))
        user_id = uuid.uuid4()
        await db.execute(
            text(
                "INSERT INTO users (id, tenant_id, email, hashed_password, is_active) "
                "VALUES (:id, :t, 'dono@inat.com', 'x', true)"
            ),
            {"id": str(user_id), "t": str(conexao["tenant_id"])},
        )
        await db.execute(
            text("UPDATE conversations SET assigned_user_id = :u WHERE id = :c"),
            {"u": str(user_id), "c": str(primeira["conversation_id"])},
        )
        await db.commit()
    await _envelhecer(primeira["conversation_id"], 7200)

    async with AsyncSessionLocal() as db:
        segunda = await ingest(db, _FakeRedis(), _inbound("MSG-INAT-6"))

    assert segunda["conversation_id"] == primeira["conversation_id"]
    assert await _status(primeira["conversation_id"]) != "RESOLVED"


async def test_conversa_encaminhada_a_equipe_nao_e_encerrada(conexao) -> None:
    """Conversa na fila pertence à equipe. Encerrá-la sumiria com o cliente da fila —
    o oposto exato do que o encerramento existe para evitar."""
    async with AsyncSessionLocal() as db:
        primeira = await ingest(db, _FakeRedis(), _inbound("MSG-INAT-7"))
        await db.execute(
            text(
                "INSERT INTO bot_sessions (conversation_id, tenant_id, variables, handoff) "
                "VALUES (:c, :t, '{}'::jsonb, true)"
            ),
            {"c": str(primeira["conversation_id"]), "t": str(conexao["tenant_id"])},
        )
        await db.commit()
    await _envelhecer(primeira["conversation_id"], 7200)

    async with AsyncSessionLocal() as db:
        segunda = await ingest(db, _FakeRedis(), _inbound("MSG-INAT-8"))

    assert segunda["conversation_id"] == primeira["conversation_id"]
    assert await _status(primeira["conversation_id"]) != "RESOLVED"


async def test_encerramento_desligado_por_configuracao(conexao, monkeypatch) -> None:
    from app.services import ingestion as ingestion_mod

    monkeypatch.setattr(ingestion_mod.settings, "CONVERSA_INATIVA_SEGUNDOS", 0)
    async with AsyncSessionLocal() as db:
        primeira = await ingest(db, _FakeRedis(), _inbound("MSG-INAT-9"))
    await _envelhecer(primeira["conversation_id"], 7200)

    async with AsyncSessionLocal() as db:
        segunda = await ingest(db, _FakeRedis(), _inbound("MSG-INAT-10"))

    assert segunda["conversation_id"] == primeira["conversation_id"]


async def test_reentrega_antiga_nao_encerra_nem_abre_conversa(conexao) -> None:
    """Evento duplicado é no-op mesmo com a conversa parada há horas.

    O caso real é o backlog que o WhatsApp devolve ao reconectar: a mensagem já foi
    ingerida, volta horas depois, e o encerramento por inatividade disparava ANTES do
    INSERT perceber a duplicata. O commit do caminho duplicado gravava a conversa do
    cliente como resolvida e uma conversa nova, vazia, na fila.
    """
    publicados: list[dict] = []

    class _RedisQueGrava:
        async def publish(self, canal, corpo) -> None:
            publicados.append(json.loads(corpo))

    async with AsyncSessionLocal() as db:
        primeira = await ingest(db, _FakeRedis(), _inbound("MSG-REENTREGA"))
    assert primeira is not None
    await _envelhecer(primeira["conversation_id"], 7200)

    async with AsyncSessionLocal() as db:
        repetida = await ingest(db, _RedisQueGrava(), _inbound("MSG-REENTREGA"))
        row = await db.execute(
            text("SELECT count(*) FROM conversations WHERE contact_id = :c"),
            {"c": str(primeira["contact_id"])},
        )
        conversas = row.scalar_one()

    assert repetida is None
    assert await _status(primeira["conversation_id"]) != "RESOLVED"
    assert conversas == 1
    assert publicados == []


async def test_encerramento_avisa_o_front_depois_do_commit(conexao) -> None:
    """O aviso saiu de dentro da transação, mas não pode ter sumido."""
    publicados: list[dict] = []

    class _RedisQueGrava:
        async def publish(self, canal, corpo) -> None:
            publicados.append(json.loads(corpo))

    async with AsyncSessionLocal() as db:
        primeira = await ingest(db, _FakeRedis(), _inbound("MSG-INAT-AVISO-1"))
    await _envelhecer(primeira["conversation_id"], 7200)

    async with AsyncSessionLocal() as db:
        await ingest(db, _RedisQueGrava(), _inbound("MSG-INAT-AVISO-2"))

    status = [p for p in publicados if p.get("type") == "conversation.status"]
    assert len(status) == 1
    assert status[0]["conversation_id"] == str(primeira["conversation_id"])


async def test_encerramento_deixa_evento_de_sistema(conexao) -> None:
    """Sem ator: a convenção do sistema para "ninguém fez isso, o sistema fez"."""
    async with AsyncSessionLocal() as db:
        primeira = await ingest(db, _FakeRedis(), _inbound("MSG-INAT-11"))
    await _envelhecer(primeira["conversation_id"], 7200)

    async with AsyncSessionLocal() as db:
        await ingest(db, _FakeRedis(), _inbound("MSG-INAT-12"))
        row = await db.execute(
            text(
                "SELECT action, actor_user_id FROM conversation_events "
                "WHERE conversation_id = :c"
            ),
            {"c": str(primeira["conversation_id"])},
        )
        eventos = row.fetchall()

    assert [(e.action, e.actor_user_id) for e in eventos] == [("RESOLVED", None)]
