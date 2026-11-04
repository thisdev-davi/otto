"""As ações que o agente dispara pelo `captured`: mostrar o espaço e segurar uma data.

Dois riscos são o motivo deste arquivo existir:

1. **Vazamento de chave de controle para o perfil do cliente.** `_acao_fotos` é comando,
   não dado da festa, e `contact_memory.remember` não filtra chaves com `_`. Se `extrair`
   parar de limpar `captured`, o perfil permanente do contato passa a guardar lixo.
2. **Persona não é contrato.** O modelo pede foto sem o cliente ter pedido e manda data
   que não existe. As guardas têm de segurar isso sem derrubar o turno.

O provider é trocado no REGISTRO do factory, como em `test_send_media.py`: se alguém
importar Evolution dentro de `services/`, este teste falha — que é o alarme desejado.
"""

import os
import uuid
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels import factory
from app.channels.base import ChannelProvider
from app.channels.types import OutboundMessage, SendResult
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.enums import Channel
from app.services import salao_acoes, salao_agenda

pytestmark = pytest.mark.asyncio

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
HOJE = date.today()
SABADO = HOJE + timedelta(days=(5 - HOJE.weekday()) % 7 + 7)
PRECO = 420_000

enviadas: list[OutboundMessage] = []


class _FakeProvider(ChannelProvider):
    def verify_webhook(self, headers: dict, body: bytes) -> bool:
        return True

    def parse_webhook(self, headers: dict, body: bytes) -> list:
        return []

    async def send(self, message: OutboundMessage) -> SendResult:
        enviadas.append(message)
        return SendResult(success=True, external_id=f"FAKE-{len(enviadas)}")


@pytest_asyncio.fixture(autouse=True)
def provider_fake(monkeypatch, tmp_path):
    enviadas.clear()
    monkeypatch.setitem(factory._registry, Channel.WHATSAPP, _FakeProvider)
    monkeypatch.setattr(settings, "MEDIA_DIR", str(tmp_path))
    yield


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def cenario(db_session: AsyncSession):
    """Tenant com conversa aberta, sem atendente — o estado em que o bot fala."""
    tenant_id = uuid.uuid4()
    contact_id = uuid.uuid4()
    conv_id = uuid.uuid4()
    conn_id = uuid.uuid4()

    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, 'Salao Acoes')"),
        {"id": str(tenant_id)},
    )
    await db_session.execute(
        text(
            "INSERT INTO channel_connections (id, tenant_id, channel, credentials, is_active) "
            "VALUES (:id, :tid, 'WHATSAPP', CAST('{\"instance\": \"otto-test\"}' AS jsonb), true)"
        ),
        {"id": str(conn_id), "tid": str(tenant_id)},
    )
    await db_session.execute(
        text(
            "INSERT INTO contacts (id, tenant_id, channel, external_id, name) "
            "VALUES (:id, :tid, 'WHATSAPP', '5511977777@s.whatsapp.net', 'Ana')"
        ),
        {"id": str(contact_id), "tid": str(tenant_id)},
    )
    await db_session.execute(
        text(
            "INSERT INTO conversations (id, tenant_id, contact_id, channel_connection_id, "
            "status, unread_count) VALUES (:id, :tid, :cid, :conn, 'OPEN', 0)"
        ),
        {
            "id": str(conv_id),
            "tid": str(tenant_id),
            "cid": str(contact_id),
            "conn": str(conn_id),
        },
    )
    await db_session.commit()

    conv = SimpleNamespace(
        channel="WHATSAPP",
        channel_connection_id=conn_id,
        # Endereço do canal em forma de `@lid`, como o WhatsApp entrega hoje: de
        # propósito, para que qualquer código que confunda endereço com telefone apareça
        # nos testes em vez de aparecer numa reserva confirmada.
        contact_external_id="21711730823330@lid",
        contact_phone="5511977777",
        contact_id=contact_id,
        contact_name="Ana",
        instance_key="otto-test",
    )
    yield {"tenant_id": tenant_id, "conv_id": conv_id, "conv": conv}

    for stmt in (
        "DELETE FROM salao_reservas WHERE tenant_id = :tid",
        "DELETE FROM salao_datas WHERE tenant_id = :tid",
        "DELETE FROM salao_fotos WHERE tenant_id = :tid",
        "DELETE FROM messages WHERE tenant_id = :tid",
        "DELETE FROM conversations WHERE tenant_id = :tid",
        "DELETE FROM contacts WHERE tenant_id = :tid",
        "DELETE FROM channel_connections WHERE tenant_id = :tid",
        "DELETE FROM tenants WHERE id = :tid",
    ):
        await db_session.execute(text(stmt), {"tid": str(tenant_id)})
    await db_session.commit()


def _sessao() -> dict:
    return {
        "variables": {},
        "ai_failures": 0,
        "post_handoff_turns": 0,
        "handoff": False,
        "bot_disabled": False,
        "skip_reason": None,
    }


async def _notas(db: AsyncSession, tenant_id: uuid.UUID) -> list[str]:
    rows = await db.execute(
        text(
            "SELECT content FROM messages WHERE tenant_id = :tid "
            "AND channel_metadata->>'internal' = 'true' ORDER BY created_at"
        ),
        {"tid": str(tenant_id)},
    )
    return [r.content for r in rows.fetchall()]


# ─────────────────────────── extrair ───────────────────────────


async def test_extrair_limpa_captured() -> None:
    decision = {
        "captured": {
            "nome": "Ana",
            "convidados": "150",
            "_acao_fotos": "sim",
            "_acao_pre_reserva": "2026-08-15",
        }
    }
    acoes = salao_acoes.extrair(decision, HOJE)

    assert acoes["fotos"] is True
    assert acoes["pre_reserva"] == date(2026, 8, 15)
    # O que sobra é só dado do cliente — é isto que vai para o perfil permanente.
    assert decision["captured"] == {"nome": "Ana", "convidados": "150"}


async def test_extrair_aceita_apelidos_e_nega_valor_negativo() -> None:
    decision = {"captured": {"acao_foto": "nao", "acao_reserva": ""}}
    acoes = salao_acoes.extrair(decision, HOJE)

    assert acoes["fotos"] is False
    assert acoes["pre_reserva"] is None
    # Mesmo sem virar ação, a chave de controle sai do dicionário.
    assert decision["captured"] == {}


async def test_extrair_sem_captured_nao_quebra() -> None:
    assert salao_acoes.extrair({}, HOJE) == {"fotos": False, "pre_reserva": None}
    assert salao_acoes.extrair({"captured": None}, HOJE) == {
        "fotos": False,
        "pre_reserva": None,
    }


async def test_parse_data_aceita_o_que_o_modelo_manda() -> None:
    decision = {"captured": {"_acao_pre_reserva": "15/08/2026"}}
    assert salao_acoes.extrair(decision, date(2026, 7, 28))["pre_reserva"] == date(
        2026, 8, 15
    )

    decision = {"captured": {"_acao_pre_reserva": "15 de agosto"}}
    assert salao_acoes.extrair(decision, date(2026, 7, 28))["pre_reserva"] == date(
        2026, 8, 15
    )

    decision = {"captured": {"_acao_pre_reserva": "não sei"}}
    assert salao_acoes.extrair(decision, date(2026, 7, 28))["pre_reserva"] is None


# ─────────────────────────── pré-reserva ───────────────────────────


async def test_pre_reserva_cria_nota_e_encaminha(
    db_session: AsyncSession, cenario: dict
) -> None:
    await salao_agenda.definir(db_session, cenario["tenant_id"], SABADO, PRECO)
    await db_session.commit()

    sessao = _sessao()
    await salao_acoes.executar(
        db_session,
        cenario["tenant_id"],
        cenario["conv_id"],
        cenario["conv"],
        sessao,
        {"fotos": False, "pre_reserva": SABADO},
    )
    await db_session.commit()

    ativas = await salao_agenda.reservas_ativas(db_session, cenario["tenant_id"])
    await db_session.commit()
    assert len(ativas) == 1
    assert ativas[0]["criada_por"] == "bot"
    assert ativas[0]["nome_cliente"] == "Ana"
    # O telefone da reserva é o TELEFONE, nunca o endereço do canal. Uma reserva
    # confirmada chegou a nascer com `21711730823330@lid` aqui, e o dono do salão não
    # tinha como ligar para o cliente.
    assert ativas[0]["telefone"] == "5511977777"
    assert "@" not in (ativas[0]["telefone"] or "")

    assert sessao["handoff"] is True, "pré-reserva sem humano avisado é o pior desfecho"
    assert sessao["variables"]["pre_reserva_id"] == ativas[0]["id"]

    notas = await _notas(db_session, cenario["tenant_id"])
    assert len(notas) == 1
    assert "Pré-reserva criada" in notas[0]
    assert "R$ 4.200,00" in notas[0]


async def test_pre_reserva_em_data_ocupada_avisa_sem_explodir(
    db_session: AsyncSession, cenario: dict
) -> None:
    await salao_agenda.definir(db_session, cenario["tenant_id"], SABADO, PRECO)
    await salao_agenda.criar_reserva(
        db_session, cenario["tenant_id"], SABADO, status="confirmada"
    )
    await db_session.commit()

    sessao = _sessao()
    await salao_acoes.executar(
        db_session,
        cenario["tenant_id"],
        cenario["conv_id"],
        cenario["conv"],
        sessao,
        {"fotos": False, "pre_reserva": SABADO},
    )
    await db_session.commit()

    assert sessao["handoff"] is True
    assert "pre_reserva_id" not in sessao["variables"]
    notas = await _notas(db_session, cenario["tenant_id"])
    assert "não conseguiu" in notas[0]
    assert "já estava reservada" in notas[0]


async def test_pre_reserva_em_data_sem_cadastro_nao_inventa(
    db_session: AsyncSession, cenario: dict
) -> None:
    """O modelo alucinou uma data que não existe na agenda: nada é gravado."""
    sessao = _sessao()
    await salao_acoes.executar(
        db_session,
        cenario["tenant_id"],
        cenario["conv_id"],
        cenario["conv"],
        sessao,
        {"fotos": False, "pre_reserva": SABADO},
    )
    await db_session.commit()

    ativas = await salao_agenda.reservas_ativas(db_session, cenario["tenant_id"])
    await db_session.commit()
    assert ativas == []
    notas = await _notas(db_session, cenario["tenant_id"])
    assert "não está cadastrada" in notas[0]


async def test_uma_pre_reserva_por_conversa(
    db_session: AsyncSession, cenario: dict
) -> None:
    outro_sabado = SABADO + timedelta(days=7)
    for dia in (SABADO, outro_sabado):
        await salao_agenda.definir(db_session, cenario["tenant_id"], dia, PRECO)
    await db_session.commit()

    sessao = _sessao()
    for dia in (SABADO, outro_sabado):
        await salao_acoes.executar(
            db_session,
            cenario["tenant_id"],
            cenario["conv_id"],
            cenario["conv"],
            sessao,
            {"fotos": False, "pre_reserva": dia},
        )
    await db_session.commit()

    ativas = await salao_agenda.reservas_ativas(db_session, cenario["tenant_id"])
    await db_session.commit()
    assert len(ativas) == 1, "a segunda data é conversa de humano, não do bot"


# ─────────────────────────── fotos ───────────────────────────


async def _cadastrar_foto(
    db: AsyncSession, tenant_id: uuid.UUID, ordem: int, ativa: bool = True
) -> None:
    caminho = os.path.join(settings.MEDIA_DIR, f"foto-{ordem}.png")
    with open(caminho, "wb") as f:
        f.write(PNG)
    await db.execute(
        text(
            "INSERT INTO salao_fotos "
            "(id, tenant_id, caminho, mimetype, tamanho_bytes, ordem, ativa) "
            "VALUES (gen_random_uuid(), :tid, :caminho, 'image/png', :tam, :ordem, :ativa)"
        ),
        {
            "tid": str(tenant_id),
            "caminho": caminho,
            "tam": len(PNG),
            "ordem": ordem,
            "ativa": ativa,
        },
    )


async def test_fotos_saem_uma_vez_por_conversa(
    db_session: AsyncSession, cenario: dict
) -> None:
    for i in range(3):
        await _cadastrar_foto(db_session, cenario["tenant_id"], i)
    await db_session.commit()

    sessao = _sessao()
    acoes = {"fotos": True, "pre_reserva": None}
    await salao_acoes.executar(
        db_session,
        cenario["tenant_id"],
        cenario["conv_id"],
        cenario["conv"],
        sessao,
        acoes,
    )
    await db_session.commit()
    assert len(enviadas) == 3
    assert sessao["variables"]["fotos_enviadas"] is True

    # Pedir de novo na mesma conversa não repete as mesmas imagens.
    await salao_acoes.executar(
        db_session,
        cenario["tenant_id"],
        cenario["conv_id"],
        cenario["conv"],
        sessao,
        acoes,
    )
    await db_session.commit()
    assert len(enviadas) == 3


async def test_foto_desativada_nao_sai(db_session: AsyncSession, cenario: dict) -> None:
    await _cadastrar_foto(db_session, cenario["tenant_id"], 0, ativa=True)
    await _cadastrar_foto(db_session, cenario["tenant_id"], 1, ativa=False)
    await db_session.commit()

    await salao_acoes.executar(
        db_session,
        cenario["tenant_id"],
        cenario["conv_id"],
        cenario["conv"],
        _sessao(),
        {"fotos": True, "pre_reserva": None},
    )
    await db_session.commit()
    assert len(enviadas) == 1


async def test_bot_nao_manda_foto_em_conversa_capturada(
    db_session: AsyncSession, cenario: dict
) -> None:
    """Humano no comando: o bot não escreve por cima, nem com mídia."""
    user_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, hashed_password, is_active) "
            "VALUES (:id, :tid, :email, 'x', true)"
        ),
        {
            "id": str(user_id),
            "tid": str(cenario["tenant_id"]),
            "email": f"a{user_id.hex[:6]}@t.com",
        },
    )
    await db_session.execute(
        text("UPDATE conversations SET assigned_user_id = :u WHERE id = :c"),
        {"u": str(user_id), "c": str(cenario["conv_id"])},
    )
    await _cadastrar_foto(db_session, cenario["tenant_id"], 0)
    await db_session.commit()

    sessao = _sessao()
    await salao_acoes.executar(
        db_session,
        cenario["tenant_id"],
        cenario["conv_id"],
        cenario["conv"],
        sessao,
        {"fotos": True, "pre_reserva": None},
    )
    await db_session.commit()

    assert enviadas == []
    assert "fotos_enviadas" not in sessao["variables"]

    await db_session.execute(
        text("UPDATE conversations SET assigned_user_id = NULL WHERE id = :c"),
        {"c": str(cenario["conv_id"])},
    )
    await db_session.execute(
        text("DELETE FROM users WHERE id = :u"), {"u": str(user_id)}
    )
    await db_session.commit()


async def test_sem_foto_cadastrada_nao_marca_como_enviada(
    db_session: AsyncSession, cenario: dict
) -> None:
    """Senão o cliente que pediu foto antes do cadastro nunca mais receberia nenhuma."""
    sessao = _sessao()
    await salao_acoes.executar(
        db_session,
        cenario["tenant_id"],
        cenario["conv_id"],
        cenario["conv"],
        sessao,
        {"fotos": True, "pre_reserva": None},
    )
    await db_session.commit()
    assert enviadas == []
    assert "fotos_enviadas" not in sessao["variables"]


# ─────────────────────── ligação com o orquestrador ───────────────────────


class _RedisFalso:
    async def publish(self, canal: str, corpo: str) -> None:
        return None


async def test_turno_completo_dispara_as_duas_acoes(
    db_session: AsyncSession, cenario: dict, monkeypatch
) -> None:
    """Prova que as DUAS linhas do `bot_orchestrator` estão de fato ligadas.

    Os outros testes chamam `salao_acoes` direto. Este passa pelo caminho real —
    `handle_inbound` → `agent_turn` → extração → envio → execução — porque o risco que
    sobra não está na lógica das ações, está no fio: uma linha no lugar errado do
    orquestrador e o recurso inteiro fica mudo, sem erro nenhum em log.
    """
    from app.ai import llm
    from app.models.enums import ContentType
    from app.services import bot_orchestrator, contact_memory

    await salao_agenda.definir(db_session, cenario["tenant_id"], SABADO, PRECO)
    await db_session.execute(
        text(
            "INSERT INTO bot_settings "
            "(tenant_id, ai_enabled, modo, persona, knowledge, provider) "
            "VALUES (:tid, true, 'agente', 'persona', "
            "'O salão fica na Rua Exemplo.', 'stub')"
        ),
        {"tid": str(cenario["tenant_id"])},
    )
    await db_session.execute(
        text(
            "INSERT INTO messages "
            "(id, conversation_id, tenant_id, direction, content_type, content, channel, created_at) "
            "VALUES (gen_random_uuid(), :cid, :tid, 'INBOUND', 'TEXT', :texto, 'WHATSAPP', now())"
        ),
        {
            "cid": str(cenario["conv_id"]),
            "tid": str(cenario["tenant_id"]),
            "texto": f"tem foto? quero segurar o dia {SABADO.strftime('%d/%m')}",
        },
    )
    await _cadastrar_foto(db_session, cenario["tenant_id"], 0)
    await db_session.commit()

    conhecimento_visto: dict = {}

    async def _agent_turn_falso(**kwargs):
        conhecimento_visto["knowledge"] = kwargs.get("knowledge", "")
        return {
            "reply": "Tenho sim! Olha só 😊",
            "captured": {
                "nome": "Ana",
                "_acao_fotos": "sim",
                "_acao_pre_reserva": SABADO.isoformat(),
            },
            "status": "gathering",
            "summary": "",
            "priority": "normal",
            "sentiment": "positive",
        }

    monkeypatch.setattr(llm, "agent_turn", _agent_turn_falso)

    await bot_orchestrator.handle_inbound(
        db_session,
        _RedisFalso(),
        cenario["tenant_id"],
        cenario["conv_id"],
        ContentType.TEXT.name,
    )

    # 1. A agenda chegou ao prompt, com o valor exato da data citada.
    assert "AGENDA E VALORES" in conhecimento_visto["knowledge"]
    assert "R$ 4.200,00" in conhecimento_visto["knowledge"]
    assert "Rua Exemplo" in conhecimento_visto["knowledge"], (
        "o bloco da agenda não pode substituir o conhecimento do tenant"
    )

    # 2. Texto primeiro, foto depois — o "olha só" tem de anunciar a imagem.
    assert [m.content_type.value for m in enviadas] == ["text", "image"]

    # 3. A data foi segurada pelo bot.
    ativas = await salao_agenda.reservas_ativas(db_session, cenario["tenant_id"])
    await db_session.commit()
    assert len(ativas) == 1
    assert ativas[0]["criada_por"] == "bot"

    # 4. E a chave de controle NÃO virou dado permanente do cliente.
    perfil = await contact_memory.load(
        db_session, cenario["tenant_id"], cenario["conv"].contact_id
    )
    assert perfil["intake"] == {"nome": "Ana"}

    await db_session.execute(
        text("DELETE FROM bot_settings WHERE tenant_id = :tid"),
        {"tid": str(cenario["tenant_id"])},
    )
    await db_session.execute(
        text("DELETE FROM bot_sessions WHERE tenant_id = :tid"),
        {"tid": str(cenario["tenant_id"])},
    )
    await db_session.commit()
