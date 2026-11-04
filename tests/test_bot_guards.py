"""Guards do orquestrador: cada silêncio do bot tem um motivo registrado.

Antes disso as seis saídas antecipadas de `_handle` eram `return` nu. O sintoma era
sempre o mesmo — "liguei o toggle e o bot não respondeu" — sem nenhuma forma de saber
qual guard disparou. Estes testes travam o vocabulário de motivos no lugar.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.models.enums import ContentType
from app.services import bot_orchestrator, midia
from app.services.bot_orchestrator import SkipReason, handle_inbound
from app.services.bot_settings import (
    DEFAULT_TRANSFER_MESSAGE,
    MODO_AGENTE,
    BotConfig,
)

pytestmark = pytest.mark.asyncio


class _FakeRedis:
    """O publish é best-effort e irrelevante aqui; só não pode explodir."""

    async def publish(self, *args, **kwargs) -> None:
        return None


@pytest_asyncio.fixture
async def base_data() -> dict:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    contact_id = uuid.uuid4()
    conn_id = uuid.uuid4()
    conv_id = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        await db.execute(
            text("INSERT INTO tenants (id, name) VALUES (:id, 'Test Bot Guards')"),
            {"id": str(tenant_id)},
        )
        await db.execute(
            text(
                "INSERT INTO users (id, tenant_id, email, hashed_password, is_active) "
                "VALUES (:id, :tid, 'guard@bot.com', 'x', true)"
            ),
            {"id": str(user_id), "tid": str(tenant_id)},
        )
        await db.execute(
            text(
                "INSERT INTO channel_connections (id, tenant_id, channel, credentials, is_active) "
                "VALUES (:id, :tid, 'WHATSAPP', '{\"instance\": \"guard-test\"}'::jsonb, true)"
            ),
            {"id": str(conn_id), "tid": str(tenant_id)},
        )
        await db.execute(
            text(
                "INSERT INTO contacts (id, tenant_id, channel, external_id, name) "
                "VALUES (:id, :tid, 'WHATSAPP', '5511977777', 'Guard')"
            ),
            {"id": str(contact_id), "tid": str(tenant_id)},
        )
        await db.execute(
            text(
                "INSERT INTO conversations "
                "(id, tenant_id, contact_id, channel_connection_id, status, unread_count) "
                "VALUES (:id, :tid, :cid, :conn, 'OPEN', 0)"
            ),
            {
                "id": str(conv_id),
                "tid": str(tenant_id),
                "cid": str(contact_id),
                "conn": str(conn_id),
            },
        )
        await db.commit()

    yield {"tenant_id": tenant_id, "user_id": user_id, "conv_id": conv_id}

    async with AsyncSessionLocal() as db:
        for table in (
            "bot_sessions",
            "messages",
            "conversation_events",
            "conversations",
            "contacts",
            "channel_connections",
            "users",
        ):
            await db.execute(
                text(f"DELETE FROM {table} WHERE tenant_id = :tid"),
                {"tid": str(tenant_id)},
            )
        await db.execute(
            text("DELETE FROM tenants WHERE id = :id"), {"id": str(tenant_id)}
        )
        await db.commit()


async def _skip_reason(conv_id: uuid.UUID) -> str | None:
    async with AsyncSessionLocal() as db:
        row = await db.execute(
            text("SELECT skip_reason FROM bot_sessions WHERE conversation_id = :cid"),
            {"cid": str(conv_id)},
        )
        return row.scalar_one_or_none()


def _enable_ai(monkeypatch) -> None:
    """Liga a IA sem depender de linha em bot_settings nem de rede."""

    async def fake_load(db, tenant_id):
        return BotConfig(ai_enabled=True, modo=MODO_AGENTE, provider="stub")

    monkeypatch.setattr(bot_orchestrator, "load_bot_config", fake_load)


async def test_conversa_capturada_registra_motivo(base_data: dict, monkeypatch) -> None:
    """Humano no comando: o bot cala a boca — e agora diz por quê."""
    _enable_ai(monkeypatch)
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO bot_sessions (conversation_id, tenant_id, variables) "
                "VALUES (:cid, :tid, '{}'::jsonb)"
            ),
            {"cid": str(base_data["conv_id"]), "tid": str(base_data["tenant_id"])},
        )
        await db.execute(
            text("UPDATE conversations SET assigned_user_id = :uid WHERE id = :cid"),
            {"uid": str(base_data["user_id"]), "cid": str(base_data["conv_id"])},
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        await handle_inbound(
            db,
            _FakeRedis(),
            base_data["tenant_id"],
            base_data["conv_id"],
            ContentType.TEXT.name,
            message_ts=datetime.now(timezone.utc),
        )

    assert await _skip_reason(base_data["conv_id"]) == SkipReason.CLAIMED


async def test_sessao_desativada_registra_motivo(base_data: dict, monkeypatch) -> None:
    _enable_ai(monkeypatch)
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO bot_sessions "
                "(conversation_id, tenant_id, variables, bot_disabled) "
                "VALUES (:cid, :tid, '{}'::jsonb, true)"
            ),
            {"cid": str(base_data["conv_id"]), "tid": str(base_data["tenant_id"])},
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        await handle_inbound(
            db,
            _FakeRedis(),
            base_data["tenant_id"],
            base_data["conv_id"],
            ContentType.TEXT.name,
            message_ts=datetime.now(timezone.utc),
        )

    assert await _skip_reason(base_data["conv_id"]) == SkipReason.SESSION_OFF


async def test_guards_pre_sessao_nao_criam_linha(base_data: dict, monkeypatch) -> None:
    """Não-texto, IA desligada e mensagem antiga não sujam bot_sessions.

    Criar a linha ali marcaria como "bot pulou" conversas em que o bot nunca teve vez —
    e a coluna vira ruído em vez de diagnóstico.
    """
    # IA desligada: o load_bot_config real devolve BotConfig() sem linha em bot_settings.
    async with AsyncSessionLocal() as db:
        await handle_inbound(
            db,
            _FakeRedis(),
            base_data["tenant_id"],
            base_data["conv_id"],
            ContentType.TEXT.name,
            message_ts=datetime.now(timezone.utc),
        )
    assert await _skip_reason(base_data["conv_id"]) is None

    # Não-texto: sai antes até de carregar a config.
    async with AsyncSessionLocal() as db:
        await handle_inbound(
            db,
            _FakeRedis(),
            base_data["tenant_id"],
            base_data["conv_id"],
            ContentType.IMAGE.name,
            message_ts=datetime.now(timezone.utc),
        )
    assert await _skip_reason(base_data["conv_id"]) is None

    # Mensagem antiga (backlog do WhatsApp ao reconectar).
    _enable_ai(monkeypatch)
    async with AsyncSessionLocal() as db:
        await handle_inbound(
            db,
            _FakeRedis(),
            base_data["tenant_id"],
            base_data["conv_id"],
            ContentType.TEXT.name,
            message_ts=datetime.now(timezone.utc) - timedelta(days=1),
        )
    assert await _skip_reason(base_data["conv_id"]) is None


async def test_resposta_enviada_marca_ok(base_data: dict, monkeypatch) -> None:
    """Caminho feliz: com o provider stub o bot responde e o motivo vira 'ok'."""
    _enable_ai(monkeypatch)

    enviadas: list[str] = []

    async def fake_send(db, tenant_id, conversation_id, conv, content, delay_ms=0):
        enviadas.append(content)
        return True

    monkeypatch.setattr(bot_orchestrator, "_send_and_persist", fake_send)

    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO messages "
                "(id, conversation_id, tenant_id, direction, content_type, content, "
                " channel, created_at) "
                "VALUES (gen_random_uuid(), :cid, :tid, 'INBOUND', 'TEXT', "
                " 'quero saber os precos', 'WHATSAPP', now())"
            ),
            {"cid": str(base_data["conv_id"]), "tid": str(base_data["tenant_id"])},
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        await handle_inbound(
            db,
            _FakeRedis(),
            base_data["tenant_id"],
            base_data["conv_id"],
            ContentType.TEXT.name,
            message_ts=datetime.now(timezone.utc),
        )

    assert enviadas, "o stub deveria ter produzido uma resposta"
    assert await _skip_reason(base_data["conv_id"]) == SkipReason.OK


async def test_falha_da_ia_avisa_e_agenda_retentativa(
    base_data: dict, monkeypatch
) -> None:
    """1ª falha: avisa UMA vez e agenda a re-tentativa.

    Antes, o aviso "Só um segundinho, já te respondo" era uma promessa que o sistema não
    tinha como cumprir: a re-tentativa dependia de o CLIENTE mandar outra mensagem, e a
    falha seguinte não enviava nada. Em teste real o bot avisou e emudeceu.
    """
    _enable_ai(monkeypatch)

    enviadas: list[str] = []
    agendadas: list[tuple] = []

    async def fake_send(db, tenant_id, conversation_id, conv, content, delay_ms=0):
        enviadas.append(content)
        return True

    async def llm_fora_do_ar(**kwargs):
        return None

    monkeypatch.setattr(bot_orchestrator, "_send_and_persist", fake_send)
    monkeypatch.setattr(bot_orchestrator.llm, "agent_turn", llm_fora_do_ar)
    monkeypatch.setattr(
        bot_orchestrator, "_schedule_retry", lambda t, c: agendadas.append((t, c))
    )

    async with AsyncSessionLocal() as db:
        await handle_inbound(
            db,
            _FakeRedis(),
            base_data["tenant_id"],
            base_data["conv_id"],
            ContentType.TEXT.name,
            message_ts=datetime.now(timezone.utc),
        )

    assert enviadas == [bot_orchestrator.WAIT_MESSAGE]
    assert len(agendadas) == 1, "a re-tentativa tem que ser agendada"
    assert await _skip_reason(base_data["conv_id"]) == SkipReason.LLM_DOWN


async def test_segunda_falha_encaminha_em_vez_de_emudecer(
    base_data: dict, monkeypatch
) -> None:
    """A 2ª falha encaminha na hora. Antes havia um estado do meio que não enviava nada."""
    _enable_ai(monkeypatch)

    enviadas: list[str] = []

    async def fake_send(db, tenant_id, conversation_id, conv, content, delay_ms=0):
        enviadas.append(content)
        return True

    async def llm_fora_do_ar(**kwargs):
        return None

    monkeypatch.setattr(bot_orchestrator, "_send_and_persist", fake_send)
    monkeypatch.setattr(bot_orchestrator.llm, "agent_turn", llm_fora_do_ar)
    monkeypatch.setattr(bot_orchestrator, "_schedule_retry", lambda t, c: None)

    for _ in range(2):
        async with AsyncSessionLocal() as db:
            await handle_inbound(
                db,
                _FakeRedis(),
                base_data["tenant_id"],
                base_data["conv_id"],
                ContentType.TEXT.name,
                message_ts=datetime.now(timezone.utc),
            )

    # Aviso na 1ª, transferência na 2ª — nunca silêncio.
    assert len(enviadas) == 2
    assert enviadas[0] == bot_orchestrator.WAIT_MESSAGE
    assert enviadas[1] != bot_orchestrator.WAIT_MESSAGE

    async with AsyncSessionLocal() as db:
        row = await db.execute(
            text(
                "SELECT handoff, bot_disabled FROM bot_sessions WHERE conversation_id = :c"
            ),
            {"c": str(base_data["conv_id"])},
        )
        s = row.fetchone()
    assert s.handoff is True
    assert s.bot_disabled is True


async def test_midia_avisa_e_na_segunda_chama_gente(
    base_data: dict, monkeypatch
) -> None:
    """Foto gerava silêncio; depois passou a gerar UMA frase e silêncio para sempre.

    Nenhum dos dois servia: quem manda foto duas vezes é justamente quem não consegue
    escrever o que quer, e era exatamente ele que ficava sem ninguém. Agora a primeira
    avisa, a segunda encaminha para a equipe, e da terceira em diante o bot cala porque
    já tem gente na conversa.
    """
    _enable_ai(monkeypatch)

    enviadas: list[str] = []

    async def fake_send(db, tenant_id, conversation_id, conv, content, delay_ms=0):
        enviadas.append(content)
        return True

    monkeypatch.setattr(bot_orchestrator, "_send_and_persist", fake_send)

    for _ in range(3):
        async with AsyncSessionLocal() as db:
            await handle_inbound(
                db,
                _FakeRedis(),
                base_data["tenant_id"],
                base_data["conv_id"],
                ContentType.IMAGE.name,
                message_ts=datetime.now(timezone.utc),
            )

    assert len(enviadas) == 2
    assert enviadas[0] == midia.texto("IMAGE") + bot_orchestrator.MEDIA_CONVITE
    assert enviadas[1] == DEFAULT_TRANSFER_MESSAGE

    async with AsyncSessionLocal() as db:
        row = await db.execute(
            text("SELECT handoff FROM bot_sessions WHERE conversation_id = :c"),
            {"c": str(base_data["conv_id"])},
        )
    assert row.fetchone().handoff is True


async def test_texto_zera_o_contador_de_midias(base_data: dict, monkeypatch) -> None:
    """Foto, texto, foto não pode encaminhar: o cliente voltou a conversar no meio.

    Sem zerar, duas fotos separadas por uma conversa inteira somavam e mandavam para a
    fila alguém que estava sendo atendido normalmente.
    """
    _enable_ai(monkeypatch)

    async def fake_send(db, tenant_id, conversation_id, conv, content, delay_ms=0):
        return True

    async def fake_turn(**kwargs):
        return {
            "reply": "claro!",
            "captured": {},
            "status": "gathering",
            "summary": "",
            "priority": "normal",
            "sentiment": "neutral",
        }

    monkeypatch.setattr(bot_orchestrator, "_send_and_persist", fake_send)
    monkeypatch.setattr(bot_orchestrator.llm, "agent_turn", fake_turn)

    for tipo in (ContentType.IMAGE, ContentType.TEXT, ContentType.IMAGE):
        async with AsyncSessionLocal() as db:
            await handle_inbound(
                db,
                _FakeRedis(),
                base_data["tenant_id"],
                base_data["conv_id"],
                tipo.name,
                message_ts=datetime.now(timezone.utc),
            )

    async with AsyncSessionLocal() as db:
        row = await db.execute(
            text("SELECT handoff FROM bot_sessions WHERE conversation_id = :c"),
            {"c": str(base_data["conv_id"])},
        )
    assert row.fetchone().handoff is False


async def test_audio_nao_recebe_a_frase_de_midia(base_data: dict, monkeypatch) -> None:
    """Nota de voz não se responde na ingestão — espera a transcrição.

    O bot mandava "me conta por texto" e, segundos depois, a resposta de verdade ao que
    o cliente tinha falado. Pior: queimava o aviso de mídia, então a foto seguinte da
    mesma conversa caía no silêncio.
    """
    _enable_ai(monkeypatch)

    enviadas: list[str] = []

    async def fake_send(db, tenant_id, conversation_id, conv, content, delay_ms=0):
        enviadas.append(content)
        return True

    monkeypatch.setattr(bot_orchestrator, "_send_and_persist", fake_send)

    async with AsyncSessionLocal() as db:
        await handle_inbound(
            db,
            _FakeRedis(),
            base_data["tenant_id"],
            base_data["conv_id"],
            ContentType.AUDIO.name,
            message_ts=datetime.now(timezone.utc),
        )

    assert enviadas == []


async def test_audio_que_nao_virou_texto_nao_fica_mudo(
    base_data: dict, monkeypatch
) -> None:
    """Transcrição vazia era `return` nu — o cliente falava e não recebia nada."""
    _enable_ai(monkeypatch)

    enviadas: list[str] = []

    async def fake_send(db, tenant_id, conversation_id, conv, content, delay_ms=0):
        enviadas.append(content)
        return True

    monkeypatch.setattr(bot_orchestrator, "_send_and_persist", fake_send)

    async with AsyncSessionLocal() as db:
        await handle_inbound(
            db,
            _FakeRedis(),
            base_data["tenant_id"],
            base_data["conv_id"],
            ContentType.AUDIO.name,
            message_ts=datetime.now(timezone.utc),
            audio_ilegivel=True,
        )

    assert enviadas == [midia.texto("AUDIO") + bot_orchestrator.MEDIA_CONVITE]


async def test_midia_antiga_nao_dispara_resposta(base_data: dict, monkeypatch) -> None:
    """A guarda anti-flood vem ANTES do ramo de mídia. Regressão de incidente real.

    O ramo de mídia ficava acima do `_is_stale`, então ao reconectar o WhatsApp
    entregava o backlog de figurinhas e imagens antigas e CADA uma disparava um
    "Recebi seu arquivo!". O bot escreveu do nada para vários contatos em segundos —
    exatamente o flood que a guarda existe para impedir.
    """
    _enable_ai(monkeypatch)

    enviadas: list[str] = []

    async def fake_send(db, tenant_id, conversation_id, conv, content, delay_ms=0):
        enviadas.append(content)
        return True

    monkeypatch.setattr(bot_orchestrator, "_send_and_persist", fake_send)

    async with AsyncSessionLocal() as db:
        await handle_inbound(
            db,
            _FakeRedis(),
            base_data["tenant_id"],
            base_data["conv_id"],
            ContentType.IMAGE.name,
            message_ts=datetime.now(timezone.utc) - timedelta(days=2),
        )

    assert enviadas == [], "mídia do backlog não pode gerar mensagem"


async def test_midia_recente_ainda_responde(base_data: dict, monkeypatch) -> None:
    """O conserto do flood não pode ter matado o caso legítimo."""
    _enable_ai(monkeypatch)

    enviadas: list[str] = []

    async def fake_send(db, tenant_id, conversation_id, conv, content, delay_ms=0):
        enviadas.append(content)
        return True

    monkeypatch.setattr(bot_orchestrator, "_send_and_persist", fake_send)

    async with AsyncSessionLocal() as db:
        await handle_inbound(
            db,
            _FakeRedis(),
            base_data["tenant_id"],
            base_data["conv_id"],
            ContentType.IMAGE.name,
            message_ts=datetime.now(timezone.utc),
        )

    assert enviadas == [midia.texto("IMAGE") + bot_orchestrator.MEDIA_CONVITE]


async def test_conversa_sem_conexao_ainda_encontra_o_canal(
    base_data: dict, monkeypatch
) -> None:
    """Reconectar o WhatsApp deixa a conversa órfã — e ela não pode emudecer por isso.

    A FK `conversations.channel_connection_id` é ON DELETE SET NULL, então apagar a
    conexão (que é o que "reconectar" faz) zera a coluna de TODA conversa que já existia.
    Com o JOIN direto que havia antes, `_load_conversation` devolvia None e o bot desistia
    sem sequer registrar um motivo — e o atendente levava 404 ao tentar responder.
    """
    _enable_ai(monkeypatch)
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("UPDATE conversations SET channel_connection_id = NULL WHERE id = :c"),
            {"c": str(base_data["conv_id"])},
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        conv = await bot_orchestrator._load_conversation(
            db, base_data["tenant_id"], base_data["conv_id"]
        )

    assert conv is not None, "órfã não pode sumir do radar do bot"
    assert conv.channel_connection_id is not None, (
        "o plano B tem que achar a conexão ativa"
    )
    assert conv.instance_key == "guard-test"


async def test_religar_conversas_orfas_conserta_de_vez(base_data: dict) -> None:
    """Criar a conexão nova religa o que ficou para trás — sem depender do plano B."""
    from app.models.enums import Channel
    from app.services import channels as channels_service

    async with AsyncSessionLocal() as db:
        await db.execute(
            text("UPDATE conversations SET channel_connection_id = NULL WHERE id = :c"),
            {"c": str(base_data["conv_id"])},
        )
        await db.commit()

        nova = (
            await db.execute(
                text("SELECT id FROM channel_connections WHERE tenant_id = :t LIMIT 1"),
                {"t": str(base_data["tenant_id"])},
            )
        ).scalar_one()
        religadas = await channels_service.religar_conversas_orfas(
            db, base_data["tenant_id"], Channel.WHATSAPP, nova
        )
        await db.commit()

    assert religadas == 1
    async with AsyncSessionLocal() as db:
        atual = (
            await db.execute(
                text("SELECT channel_connection_id FROM conversations WHERE id = :c"),
                {"c": str(base_data["conv_id"])},
            )
        ).scalar_one()
    assert atual == nova


# ─────────────────── o silêncio que não era guard: envio que falhou ───────────────────


class _ConvFake:
    """O mínimo que `_send_and_persist` lê de uma conversa."""

    channel = "WHATSAPP"
    contact_external_id = "5511977777"
    contact_phone = "5511977777"
    instance_key = "guard-test"

    def __init__(self, channel_connection_id) -> None:
        self.channel_connection_id = channel_connection_id


async def _conexao_de(tenant_id) -> uuid.UUID:
    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                text("SELECT id FROM channel_connections WHERE tenant_id = :t"),
                {"t": str(tenant_id)},
            )
        ).fetchone()
    return row.id


class _ProviderQueCai:
    """Evolution com a sessão do WhatsApp derrubada: HTTP 500, sem exceção."""

    async def send(self, outbound):
        from app.channels.types import SendResult

        return SendResult(success=False, error='{"message":"Connection Closed"}')


async def test_envio_que_falha_guarda_o_motivo_na_mensagem(
    base_data: dict, monkeypatch
) -> None:
    """Mesma doutrina dos guards: silêncio sem motivo registrado é o bug.

    O provider já devolvia o erro em `SendResult.error` e ele era descartado — resposta
    não-2xx não levanta exceção, então nem o log pegava. A bolha aparecia na inbox como
    um "falhou" mudo e a causa (sessão do WhatsApp caída) só saía reproduzindo o envio
    na mão.
    """
    monkeypatch.setattr(
        bot_orchestrator, "get_provider", lambda canal: _ProviderQueCai()
    )

    conv = _ConvFake(await _conexao_de(base_data["tenant_id"]))
    async with AsyncSessionLocal() as db:
        await bot_orchestrator._send_and_persist(
            db, base_data["tenant_id"], base_data["conv_id"], conv, "Oi!"
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                text(
                    "SELECT status, channel_metadata FROM messages "
                    "WHERE tenant_id = :t AND direction = 'OUTBOUND'"
                ),
                {"t": str(base_data["tenant_id"])},
            )
        ).fetchone()

    assert row.status == "FAILED"
    assert "Connection Closed" in row.channel_metadata["erro"]
    assert row.channel_metadata["bot"] is True


async def test_envio_bem_sucedido_nao_guarda_erro(base_data: dict, monkeypatch) -> None:
    """`erro` presente significa falha — não pode virar chave sempre-presente e vazia."""

    class _ProviderOk:
        async def send(self, outbound):
            from app.channels.types import SendResult

            return SendResult(success=True, external_id="ABC123")

    monkeypatch.setattr(bot_orchestrator, "get_provider", lambda canal: _ProviderOk())

    conv = _ConvFake(await _conexao_de(base_data["tenant_id"]))
    async with AsyncSessionLocal() as db:
        await bot_orchestrator._send_and_persist(
            db, base_data["tenant_id"], base_data["conv_id"], conv, "Oi!"
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        row = (
            await db.execute(
                text(
                    "SELECT status, external_id, channel_metadata FROM messages "
                    "WHERE tenant_id = :t AND direction = 'OUTBOUND'"
                ),
                {"t": str(base_data["tenant_id"])},
            )
        ).fetchone()

    assert row.status == "SENT"
    assert row.external_id == "ABC123"
    assert "erro" not in row.channel_metadata
