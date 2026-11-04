"""Escalação do agente: acontece UMA vez, mesmo com dois turnos na mesma conversa.

Vem de bug em teste real. O LLM falhou numa pergunta, o bot mandou "Só um segundinho" e
agendou a re-tentativa para 8 s depois; o cliente mandou outra mensagem nesse intervalo. Os
dois turnos rodaram em paralelo, os dois leram `bot_sessions.handoff = false` e o cliente
recebeu DUAS mensagens de encaminhamento seguidas, com duas notas de triagem — a nota
duplicada é a prova, porque ela só é inserida dentro do ramo de escalação.

O conserto tem duas camadas, e cada teste aqui trava uma:
- `_claim_handoff`: UPDATE condicional, só um turno ganha (como a captura de conversa);
- `from_retry`: a re-tentativa se descarta quando outro turno já respondeu.
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.models.enums import ContentType
from app.services import bot_orchestrator
from app.services.bot_orchestrator import SkipReason, handle_inbound
from app.services.bot_settings import MODO_AGENTE, BotConfig

pytestmark = pytest.mark.asyncio


class _FakeRedis:
    """Guarda o que foi publicado.

    O aviso à equipe é parte do contrato da escalação, não enfeite: `conversation.handoff`
    é o único evento do sistema que pede ação humana, e ele nasce exatamente onde a nota de
    triagem duplicou no bug real. Quem trava a duplicação da nota tem que travar a do aviso.
    """

    def __init__(self) -> None:
        self.publicados: list[dict] = []

    async def publish(self, *args, **kwargs) -> None:
        self.publicados.append(json.loads(args[-1]))

    def do_tipo(self, tipo: str) -> list[dict]:
        return [e for e in self.publicados if e.get("type") == tipo]


def _decisao(reply: str, status: str = "ready_to_handoff") -> dict:
    """Decisão já normalizada, no formato que `llm.agent_turn` devolve."""
    return {
        "reply": reply,
        "captured": {"nome": "Davi Oliveira"},
        "status": status,
        "summary": "Cliente perguntou por data sem cadastro na agenda.",
        "priority": "normal",
        "sentiment": "neutral",
    }


@pytest_asyncio.fixture
async def base_data() -> dict:
    tenant_id = uuid.uuid4()
    contact_id = uuid.uuid4()
    conn_id = uuid.uuid4()
    conv_id = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        await db.execute(
            text("INSERT INTO tenants (id, name) VALUES (:id, 'Test Bot Handoff')"),
            {"id": str(tenant_id)},
        )
        await db.execute(
            text(
                "INSERT INTO channel_connections (id, tenant_id, channel, credentials, is_active) "
                "VALUES (:id, :tid, 'WHATSAPP', '{\"instance\": \"handoff-test\"}'::jsonb, true)"
            ),
            {"id": str(conn_id), "tid": str(tenant_id)},
        )
        await db.execute(
            text(
                "INSERT INTO contacts (id, tenant_id, channel, external_id, name) "
                "VALUES (:id, :tid, 'WHATSAPP', '5511966666', 'Davi')"
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

    yield {"tenant_id": tenant_id, "conv_id": conv_id}

    async with AsyncSessionLocal() as db:
        for table in (
            "bot_sessions",
            "messages",
            "conversation_events",
            "conversations",
            "contacts",
            "channel_connections",
        ):
            await db.execute(
                text(f"DELETE FROM {table} WHERE tenant_id = :tid"),
                {"tid": str(tenant_id)},
            )
        await db.execute(
            text("DELETE FROM tenants WHERE id = :id"), {"id": str(tenant_id)}
        )
        await db.commit()


def _enable_ai(monkeypatch) -> None:
    async def fake_load(db, tenant_id):
        return BotConfig(ai_enabled=True, modo=MODO_AGENTE, provider="stub")

    monkeypatch.setattr(bot_orchestrator, "load_bot_config", fake_load)


async def _criar_sessao(base_data: dict, **colunas) -> None:
    """Cria a linha de bot_sessions ANTES do turno.

    Não é conveniência: com a linha ausente, dois turnos concorrentes disputam o
    `INSERT ... ON CONFLICT DO NOTHING` de `_load_or_create_session` e o segundo fica preso
    até o primeiro commitar — o teste travaria na barreira em vez de exercitar a corrida.
    Na conversa real a linha também já existe (foi o turno que falhou que a criou).
    """
    extra = "".join(f", {c} = :{c}" for c in colunas)
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO bot_sessions (conversation_id, tenant_id, variables) "
                "VALUES (:cid, :tid, '{}'::jsonb)"
            ),
            {"cid": str(base_data["conv_id"]), "tid": str(base_data["tenant_id"])},
        )
        if colunas:
            await db.execute(
                text(
                    f"UPDATE bot_sessions SET updated_at = now(){extra} "
                    "WHERE conversation_id = :cid"
                ),
                {"cid": str(base_data["conv_id"]), **colunas},
            )
        await db.commit()


async def _notas_internas(tenant_id: uuid.UUID) -> list[str]:
    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            text(
                "SELECT content FROM messages WHERE tenant_id = :tid "
                "AND channel_metadata->>'internal' = 'true' ORDER BY created_at"
            ),
            {"tid": str(tenant_id)},
        )
        return [r.content for r in rows.fetchall()]


async def _sessao(conv_id: uuid.UUID):
    async with AsyncSessionLocal() as db:
        row = await db.execute(
            text(
                "SELECT handoff, bot_disabled, skip_reason, post_handoff_turns "
                "FROM bot_sessions WHERE conversation_id = :cid"
            ),
            {"cid": str(conv_id)},
        )
        return row.fetchone()


async def test_dois_turnos_concorrentes_escalam_uma_vez(
    base_data: dict, monkeypatch
) -> None:
    """A corrida do bug real: dois turnos, uma escalação.

    Sem `_claim_handoff` este teste falha com 2 notas e 2 mensagens — exatamente o que
    apareceu no print do atendimento.
    """
    _enable_ai(monkeypatch)
    await _criar_sessao(base_data)

    enviadas: list[str] = []

    async def fake_send(db, tenant_id, conversation_id, conv, content, delay_ms=0):
        enviadas.append(content)
        return True

    # Barreira: os dois turnos só passam do LLM depois de AMBOS terem lido a sessão com
    # handoff = false. É o que torna a corrida determinística em vez de sorte de scheduler.
    liberado = asyncio.Event()
    chegaram = 0
    respostas = iter(
        [
            "Vou te passar para um de nossos atendentes agora mesmo!",
            "Já estou encaminhando seu contato para nossa equipe comercial.",
        ]
    )

    async def fake_turn(**kwargs):
        nonlocal chegaram
        chegaram += 1
        if chegaram == 2:
            liberado.set()
        await asyncio.wait_for(liberado.wait(), timeout=5)
        return _decisao(next(respostas))

    monkeypatch.setattr(bot_orchestrator, "_send_and_persist", fake_send)
    monkeypatch.setattr(bot_orchestrator.llm, "agent_turn", fake_turn)

    redis = _FakeRedis()

    async def turno():
        async with AsyncSessionLocal() as db:
            await handle_inbound(
                db,
                redis,
                base_data["tenant_id"],
                base_data["conv_id"],
                ContentType.TEXT.name,
                message_ts=datetime.now(timezone.utc),
            )

    await asyncio.gather(turno(), turno())

    assert chegaram == 2, "os dois turnos precisam ter chamado o LLM"
    assert len(enviadas) == 1, f"o cliente recebeu {len(enviadas)} avisos: {enviadas}"

    notas = await _notas_internas(base_data["tenant_id"])
    triagens = [n for n in notas if n.startswith("Triagem por IA")]
    assert len(triagens) == 1, f"{len(triagens)} notas de triagem: {triagens}"

    s = await _sessao(base_data["conv_id"])
    assert s.handoff is True

    avisos = redis.do_tipo("conversation.handoff")
    assert len(avisos) == 1, f"a equipe foi avisada {len(avisos)} vezes: {avisos}"
    assert avisos[0]["contact_name"] == "Davi"
    assert avisos[0]["resumo"], "aviso sem motivo não diz ao atendente o que o espera"


async def test_turno_perdedor_registra_motivo_do_silencio(
    base_data: dict, monkeypatch
) -> None:
    """O ramo do perdedor, isolado: claim recusado ⇒ resposta descartada.

    A sessão começa com handoff = false (é o que o turno lê) e o `_claim_handoff` recusa,
    como quando outro turno escala no meio da chamada ao LLM. Não vale montar este caso com
    handoff = true na sessão: aí o turno cai no caminho normal de pós-handoff e DEVE
    responder — quem já foi encaminhado continua sendo atendido.
    """
    _enable_ai(monkeypatch)
    await _criar_sessao(base_data)

    enviadas: list[str] = []

    async def fake_send(db, tenant_id, conversation_id, conv, content, delay_ms=0):
        enviadas.append(content)
        return True

    async def fake_turn(**kwargs):
        return _decisao("Já estou encaminhando seu contato para a equipe!")

    async def claim_recusado(db, tenant_id, conversation_id):
        return False

    monkeypatch.setattr(bot_orchestrator, "_send_and_persist", fake_send)
    monkeypatch.setattr(bot_orchestrator.llm, "agent_turn", fake_turn)
    monkeypatch.setattr(bot_orchestrator, "_claim_handoff", claim_recusado)

    redis = _FakeRedis()
    async with AsyncSessionLocal() as db:
        await handle_inbound(
            db,
            redis,
            base_data["tenant_id"],
            base_data["conv_id"],
            ContentType.TEXT.name,
            message_ts=datetime.now(timezone.utc),
        )

    assert enviadas == [], "o aviso de encaminhamento não pode sair duas vezes"
    assert redis.do_tipo("conversation.handoff") == [], (
        "quem perdeu a corrida não escalou nada — avisar aqui faria a equipe receber "
        "dois alertas da mesma conversa"
    )
    assert await _notas_internas(base_data["tenant_id"]) == []
    s = await _sessao(base_data["conv_id"])
    assert s.skip_reason == SkipReason.DUPLICATE_HANDOFF
    assert s.post_handoff_turns == 1


async def test_retentativa_obsoleta_e_descartada(base_data: dict, monkeypatch) -> None:
    """`ai_failures = 0` significa que outro turno já respondeu: a re-tentativa sai fora.

    É a guarda que evita a concorrência na origem — sem ela, a re-tentativa agendada e a
    mensagem nova do cliente rodam juntas na mesma conversa.
    """
    _enable_ai(monkeypatch)
    await _criar_sessao(base_data)  # ai_failures = 0 por default

    chamou_llm = False

    async def fake_turn(**kwargs):
        nonlocal chamou_llm
        chamou_llm = True
        return _decisao("resposta que não deveria existir")

    monkeypatch.setattr(bot_orchestrator.llm, "agent_turn", fake_turn)

    async with AsyncSessionLocal() as db:
        await handle_inbound(
            db,
            _FakeRedis(),
            base_data["tenant_id"],
            base_data["conv_id"],
            ContentType.TEXT.name,
            from_retry=True,
        )

    assert chamou_llm is False, "a re-tentativa obsoleta não deve gastar chamada de IA"
    s = await _sessao(base_data["conv_id"])
    assert s.skip_reason == SkipReason.RETRY_OBSOLETE


async def test_retentativa_ainda_pendente_responde(
    base_data: dict, monkeypatch
) -> None:
    """A guarda acima não pode matar a re-tentativa legítima.

    Com `ai_failures = 1` nenhum turno respondeu depois da falha: é o caso em que o
    "Só um segundinho, já te respondo" precisa ser cumprido.
    """
    _enable_ai(monkeypatch)
    await _criar_sessao(base_data, ai_failures=1)

    enviadas: list[str] = []

    async def fake_send(db, tenant_id, conversation_id, conv, content, delay_ms=0):
        enviadas.append(content)
        return True

    async def fake_turn(**kwargs):
        return _decisao(
            "Sobre o dia 06/08, deixa eu confirmar com a equipe!", "gathering"
        )

    monkeypatch.setattr(bot_orchestrator, "_send_and_persist", fake_send)
    monkeypatch.setattr(bot_orchestrator.llm, "agent_turn", fake_turn)

    async with AsyncSessionLocal() as db:
        await handle_inbound(
            db,
            _FakeRedis(),
            base_data["tenant_id"],
            base_data["conv_id"],
            ContentType.TEXT.name,
            from_retry=True,
        )

    assert len(enviadas) == 1
    s = await _sessao(base_data["conv_id"])
    assert s.skip_reason == SkipReason.OK


async def test_ia_fora_do_ar_tambem_avisa_a_equipe(
    base_data: dict, monkeypatch
) -> None:
    """A escalação mais silenciosa do sistema: ninguém decidiu escalar, o provedor caiu.

    Ela acontece com frequência dentro de uma re-tentativa agendada — ou seja, sem nenhuma
    mensagem nova do cliente por perto. Antes do evento próprio, o único sinal publicado era
    `message.outbound`, que o front ignora de propósito: a conversa entrava na fila e a tela
    não fazia barulho nenhum. Quem só olha a Inbox de vez em quando descobria horas depois.
    """
    _enable_ai(monkeypatch)
    await _criar_sessao(base_data, ai_failures=bot_orchestrator.AI_MAX_FAILURES - 1)

    async def llm_fora_do_ar(**kwargs):
        return None

    async def fake_send(db, tenant_id, conversation_id, conv, content, delay_ms=0):
        return True

    monkeypatch.setattr(bot_orchestrator.llm, "agent_turn", llm_fora_do_ar)
    monkeypatch.setattr(bot_orchestrator, "_send_and_persist", fake_send)
    monkeypatch.setattr(bot_orchestrator, "_schedule_retry", lambda *a, **kw: None)

    redis = _FakeRedis()
    async with AsyncSessionLocal() as db:
        await handle_inbound(
            db,
            redis,
            base_data["tenant_id"],
            base_data["conv_id"],
            ContentType.TEXT.name,
            from_retry=True,
        )

    s = await _sessao(base_data["conv_id"])
    assert s.handoff is True
    avisos = redis.do_tipo("conversation.handoff")
    assert len(avisos) == 1
    assert avisos[0]["resumo"] == bot_orchestrator.MOTIVO_IA_INDISPONIVEL

    # E não avisa de novo no turno seguinte: a conversa já está na fila desde o anterior.
    redis.publicados.clear()
    async with AsyncSessionLocal() as db:
        await handle_inbound(
            db,
            redis,
            base_data["tenant_id"],
            base_data["conv_id"],
            ContentType.TEXT.name,
            message_ts=datetime.now(timezone.utc),
        )
    assert redis.do_tipo("conversation.handoff") == []
