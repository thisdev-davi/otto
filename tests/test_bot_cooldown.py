"""O bot desativado por falha de IA volta sozinho; o desativado por decisão, não.

Incidente que originou estes testes (31/07): o modelo devolveu um JSON cortado no teto de
tokens duas vezes seguidas, o orquestrador contou como provedor indisponível e gravou
`bot_disabled = true`. Era definitivo. O provedor estava de pé o tempo todo, e o cliente
— que tinha acabado de perguntar se a reserva dele fora confirmada — nunca mais recebeu
resposta do agente.

O teste que trava a correção é `test_bot_volta_sozinho_quando_o_prazo_vence`. O que
impede a correção de ir longe demais é `test_teto_pos_handoff_continua_permanente`: as
duas causas usam o mesmo `bot_disabled`, e se voltarem a ser indistinguíveis o bug volta
em silêncio.
"""

import asyncio
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
    async def publish(self, *args, **kwargs) -> None:
        return None


@pytest_asyncio.fixture
async def base_data() -> dict:
    tenant_id = uuid.uuid4()
    contact_id = uuid.uuid4()
    conn_id = uuid.uuid4()
    conv_id = uuid.uuid4()

    async with AsyncSessionLocal() as db:
        await db.execute(
            text("INSERT INTO tenants (id, name) VALUES (:id, 'Test Cooldown')"),
            {"id": str(tenant_id)},
        )
        await db.execute(
            text(
                "INSERT INTO channel_connections (id, tenant_id, channel, credentials, is_active) "
                "VALUES (:id, :tid, 'WHATSAPP', '{\"instance\": \"cooldown-test\"}'::jsonb, true)"
            ),
            {"id": str(conn_id), "tid": str(tenant_id)},
        )
        await db.execute(
            text(
                "INSERT INTO contacts (id, tenant_id, channel, external_id, name) "
                "VALUES (:id, :tid, 'WHATSAPP', '5511966666', 'Cooldown')"
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


async def _sessao(conv_id: uuid.UUID) -> dict:
    async with AsyncSessionLocal() as db:
        row = await db.execute(
            text(
                "SELECT bot_disabled, bot_disabled_until, ai_failures, handoff, "
                "skip_reason FROM bot_sessions WHERE conversation_id = :cid"
            ),
            {"cid": str(conv_id)},
        )
        r = row.fetchone()
        return {
            "bot_disabled": r.bot_disabled,
            "bot_disabled_until": r.bot_disabled_until,
            "ai_failures": r.ai_failures,
            "handoff": r.handoff,
            "skip_reason": r.skip_reason,
        }


def _prepara(monkeypatch, *, turno, enviadas: list[str]) -> None:
    async def fake_load(db, tenant_id):
        return BotConfig(ai_enabled=True, modo=MODO_AGENTE, provider="stub")

    async def fake_send(db, tenant_id, conversation_id, conv, content, delay_ms=0):
        enviadas.append(content)
        return True

    monkeypatch.setattr(bot_orchestrator, "load_bot_config", fake_load)
    monkeypatch.setattr(bot_orchestrator, "_send_and_persist", fake_send)
    monkeypatch.setattr(bot_orchestrator.llm, "agent_turn", turno)
    # A re-tentativa agendada é uma task Celery; aqui ela só não pode disparar de verdade.
    monkeypatch.setattr(bot_orchestrator, "_schedule_retry", lambda *a, **kw: None)


async def _turno(base_data: dict, from_retry: bool = False) -> None:
    async with AsyncSessionLocal() as db:
        await handle_inbound(
            db,
            _FakeRedis(),
            base_data["tenant_id"],
            base_data["conv_id"],
            ContentType.TEXT.name,
            message_ts=datetime.now(timezone.utc),
            from_retry=from_retry,
        )


def _decisao(reply: str) -> dict:
    return {
        "reply": reply,
        "captured": {},
        "status": "gathering",
        "sentiment": "neutral",
        "priority": "normal",
    }


async def _notas(conv_id: uuid.UUID) -> list[str]:
    async with AsyncSessionLocal() as db:
        row = await db.execute(
            text(
                "SELECT content FROM messages WHERE conversation_id = :cid "
                "AND channel_metadata->>'internal' = 'true'"
            ),
            {"cid": str(conv_id)},
        )
        return [r.content for r in row]


async def _falha_ate_desativar(base_data: dict, monkeypatch) -> list[str]:
    """Roda turnos com o LLM falhando até estourar AI_MAX_FAILURES."""
    enviadas: list[str] = []

    async def turno_que_falha(**kwargs):
        return None

    _prepara(monkeypatch, turno=turno_que_falha, enviadas=enviadas)
    for _ in range(bot_orchestrator.AI_MAX_FAILURES):
        await _turno(base_data)
    return enviadas


async def test_falhas_seguidas_desativam_com_prazo(base_data, monkeypatch) -> None:
    await _falha_ate_desativar(base_data, monkeypatch)

    s = await _sessao(base_data["conv_id"])
    assert s["bot_disabled"] is True
    assert s["bot_disabled_until"] is not None, (
        "falha de IA é acidente: tem que ter prazo, senão a conversa fica sem agente "
        "para sempre mesmo depois de o provedor voltar"
    )
    assert s["bot_disabled_until"] > datetime.now(timezone.utc)
    assert s["skip_reason"] == SkipReason.LLM_DOWN


async def test_bot_volta_sozinho_quando_o_prazo_vence(base_data, monkeypatch) -> None:
    """O caso do Lucas: provedor se recupera e o agente volta a atender sem intervenção."""
    await _falha_ate_desativar(base_data, monkeypatch)

    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "UPDATE bot_sessions SET bot_disabled_until = now() - interval '1 minute' "
                "WHERE conversation_id = :cid"
            ),
            {"cid": str(base_data["conv_id"])},
        )
        await db.commit()

    enviadas: list[str] = []

    async def turno_bom(**kwargs):
        return {
            "reply": "A sua data está confirmada pela equipe!",
            "captured": {},
            "status": "gathering",
            "sentiment": "neutral",
            "priority": "normal",
        }

    _prepara(monkeypatch, turno=turno_bom, enviadas=enviadas)
    await _turno(base_data)

    assert enviadas, "com o prazo vencido o agente tem que voltar a responder"
    assert "confirmada" in enviadas[0]

    s = await _sessao(base_data["conv_id"])
    assert s["bot_disabled"] is False
    assert s["bot_disabled_until"] is None
    assert s["ai_failures"] == 0, (
        "zerar o contador é parte do lift: em 2 a próxima falha isolada re-desativaria "
        "o bot na hora"
    )
    assert s["skip_reason"] == SkipReason.OK


async def test_mensagem_nova_dentro_do_prazo_e_respondida(
    base_data, monkeypatch
) -> None:
    """O caso do Lucas: encaminhado por falha, perguntou de novo e ficou sem resposta."""
    await _falha_ate_desativar(base_data, monkeypatch)

    enviadas: list[str] = []

    async def turno_bom(**kwargs):
        return _decisao("Respondendo a sua dúvida.")

    _prepara(monkeypatch, turno=turno_bom, enviadas=enviadas)
    await _turno(base_data)

    assert enviadas == ["Respondendo a sua dúvida."]
    s = await _sessao(base_data["conv_id"])
    assert s["bot_disabled"] is False
    assert s["bot_disabled_until"] is None
    assert s["ai_failures"] == 0


async def test_retentativa_dentro_do_prazo_continua_calada(
    base_data, monkeypatch
) -> None:
    """O prazo continua valendo para a re-tentativa: ela não martela o provedor caído."""
    await _falha_ate_desativar(base_data, monkeypatch)

    enviadas: list[str] = []

    async def turno_bom(**kwargs):
        raise AssertionError("re-tentativa não pode chamar o LLM dentro do prazo")

    _prepara(monkeypatch, turno=turno_bom, enviadas=enviadas)
    await _turno(base_data, from_retry=True)

    assert enviadas == []
    s = await _sessao(base_data["conv_id"])
    assert s["bot_disabled"] is True
    assert s["skip_reason"] == SkipReason.LLM_COOLDOWN


async def test_falha_em_conversa_encaminhada_avisa_instabilidade(
    base_data, monkeypatch
) -> None:
    """Já encaminhada: não repete a transferência, avisa a instabilidade e não duplica nota."""
    enviadas = await _falha_ate_desativar(base_data, monkeypatch)
    assert enviadas[-1] == BotConfig().transfer_message
    assert len(await _notas(base_data["conv_id"])) == 1

    enviadas.clear()
    # Pergunta nova durante a queda: a 1ª falha só agenda a re-tentativa, e é ela que
    # estoura o teto de novo.
    await _turno(base_data)
    await _turno(base_data, from_retry=True)

    assert enviadas == [bot_orchestrator.INSTABILIDADE_MESSAGE]
    assert len(await _notas(base_data["conv_id"])) == 1


async def test_teto_pos_handoff_continua_permanente(base_data, monkeypatch) -> None:
    """Desativado por DECISÃO não ganha prazo — e não pode voltar sozinho.

    Se este teste cair junto com o de cima numa refatoração, as duas causas voltaram a
    ser a mesma coisa e o bug do incidente volta com elas.
    """
    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO bot_sessions "
                "(conversation_id, tenant_id, variables, handoff, post_handoff_turns) "
                "VALUES (:cid, :tid, '{}'::jsonb, true, :turnos)"
            ),
            {
                "cid": str(base_data["conv_id"]),
                "tid": str(base_data["tenant_id"]),
                "turnos": bot_orchestrator.POST_HANDOFF_MAX_TURNS,
            },
        )
        await db.commit()

    enviadas: list[str] = []

    async def turno_bom(**kwargs):
        return {
            "reply": "oi",
            "captured": {},
            "status": "gathering",
            "sentiment": "neutral",
            "priority": "normal",
        }

    _prepara(monkeypatch, turno=turno_bom, enviadas=enviadas)
    await _turno(base_data)

    assert enviadas == [bot_orchestrator.POST_HANDOFF_LIMIT_MESSAGE], (
        "atingir o teto avisa uma vez; calar direto deixava o cliente falando sozinho"
    )
    s = await _sessao(base_data["conv_id"])
    assert s["bot_disabled"] is True
    assert s["bot_disabled_until"] is None, "teto pós-handoff é decisão, não acidente"

    # Um turno depois: continua calado, sem prazo para resgatá-lo — nem por mensagem nova.
    await _turno(base_data)
    assert enviadas == [bot_orchestrator.POST_HANDOFF_LIMIT_MESSAGE]
    s = await _sessao(base_data["conv_id"])
    assert s["bot_disabled"] is True
    assert s["skip_reason"] == SkipReason.SESSION_OFF


@pytest.mark.parametrize("recebidas,atingiu", [(24, False), (25, True)])
async def test_limite_de_mensagens_conta_no_banco(
    base_data, monkeypatch, recebidas: int, atingiu: bool
) -> None:
    """O `history` é cortado em 24: contado nele, o limite de 25 nunca chegaria."""
    async with AsyncSessionLocal() as db:
        for i in range(recebidas):
            await db.execute(
                text(
                    "INSERT INTO messages (id, conversation_id, tenant_id, direction, "
                    "content_type, content, channel, external_id) VALUES "
                    "(gen_random_uuid(), :cid, :tid, 'INBOUND', 'TEXT', :c, 'WHATSAPP', :e)"
                ),
                {
                    "cid": str(base_data["conv_id"]),
                    "tid": str(base_data["tenant_id"]),
                    "c": f"mensagem {i}",
                    "e": str(uuid.uuid4()),
                },
            )
        await db.commit()

    vistos: list[bool] = []

    async def turno(**kwargs):
        vistos.append(kwargs["max_turns_reached"])
        return _decisao("ok")

    _prepara(monkeypatch, turno=turno, enviadas=[])
    await _turno(base_data)

    assert vistos == [atingiu]


async def test_dois_turnos_simultaneos_no_prazo_vencido(base_data, monkeypatch) -> None:
    """O lift é UPDATE condicional (regra 2). Dois turnos juntos não podem corromper nada."""
    await _falha_ate_desativar(base_data, monkeypatch)

    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "UPDATE bot_sessions SET bot_disabled_until = now() - interval '1 minute' "
                "WHERE conversation_id = :cid"
            ),
            {"cid": str(base_data["conv_id"])},
        )
        await db.commit()

    enviadas: list[str] = []

    async def turno_bom(**kwargs):
        await asyncio.sleep(0)
        return {
            "reply": "resposta",
            "captured": {},
            "status": "gathering",
            "sentiment": "neutral",
            "priority": "normal",
        }

    _prepara(monkeypatch, turno=turno_bom, enviadas=enviadas)
    await asyncio.gather(_turno(base_data), _turno(base_data))

    s = await _sessao(base_data["conv_id"])
    assert s["bot_disabled"] is False
    assert s["bot_disabled_until"] is None
    assert s["ai_failures"] == 0


async def test_resolution_during_llm_discards_response_and_actions(
    base_data, monkeypatch
):
    sent = []

    async def close_during_completion(**kwargs):
        async with AsyncSessionLocal() as db:
            await db.execute(
                text(
                    "UPDATE conversations SET status='RESOLVED',resolved_at=now(),closure_reason='inactivity' WHERE id=:cid"
                ),
                {"cid": str(base_data["conv_id"])},
            )
            await db.commit()
        return _decisao("Esta resposta não pode sair.")

    _prepara(monkeypatch, turno=close_during_completion, enviadas=sent)
    await _turno(base_data)
    assert sent == []
    assert (await _sessao(base_data["conv_id"]))["skip_reason"] == SkipReason.RESOLVED
