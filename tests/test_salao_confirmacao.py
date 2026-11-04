"""Confirmar a reserva avisa o cliente no WhatsApp.

Feature nova, e a primeira vez que a agenda fala com o WhatsApp — o `CLAUDE.md` listava
isso como fora de escopo justamente porque manda mensagem para o celular de alguém e não
dá para desfazer. Os testes que seguram os guarda-corpos são
`test_confirmar_duas_vezes_envia_uma_vez_so` (a trava contra aviso duplicado é o UPDATE
condicional, não uma checagem nova) e `test_falha_no_envio_nao_desfaz_a_confirmacao`.
"""

import uuid
from datetime import date, timedelta

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.types import SendResult
from app.core.database import AsyncSessionLocal
from app.models.salao import ReservaStatus
from app.services import salao_agenda, salao_aviso
from app.services.bot_settings import render_confirmacao

# Relativa a hoje, e não fixa: uma data cravada no futuro vira uma data no passado, e a
# agenda passa a recusar a reserva com MOTIVO_PASSADO — o cenário inteiro quebra meses
# depois, longe de qualquer mudança de código. Foi o que aconteceu com `date(2026, 8, 7)`.
DATA = date.today() + timedelta(days=30)
DATA_BR = DATA.strftime("%d/%m/%Y")


class _ProviderFake:
    def __init__(self, sucesso: bool = True):
        self.sucesso = sucesso
        self.enviadas: list[str] = []

    async def send(self, outbound):
        self.enviadas.append(outbound.content)
        if not self.sucesso:
            return SendResult(success=False, error="provedor fora do ar")
        return SendResult(success=True, external_id=f"EXT-{len(self.enviadas)}")


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def cenario(db_session: AsyncSession) -> dict:
    tenant_id, conn_id = uuid.uuid4(), uuid.uuid4()
    contact_id, conv_id = uuid.uuid4(), uuid.uuid4()

    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, 'Salao Confirmacao')"),
        {"id": str(tenant_id)},
    )
    await db_session.execute(
        text(
            "INSERT INTO channel_connections (id, tenant_id, channel, credentials, is_active) "
            "VALUES (:id, :t, 'WHATSAPP', '{\"instance\": \"conf-test\"}'::jsonb, true)"
        ),
        {"id": str(conn_id), "t": str(tenant_id)},
    )
    await db_session.execute(
        text(
            "INSERT INTO contacts (id, tenant_id, channel, external_id, name, phone) "
            "VALUES (:id, :t, 'WHATSAPP', '21711730823330@lid', 'Lucas Mateus', "
            "'5511000000001')"
        ),
        {"id": str(contact_id), "t": str(tenant_id)},
    )
    # RESOLVED de propósito: é o estado real da conversa que originou a reserva no
    # incidente, e o aviso precisa achar uma conversa mesmo assim.
    await db_session.execute(
        text(
            "INSERT INTO conversations "
            "(id, tenant_id, contact_id, channel_connection_id, status, unread_count) "
            "VALUES (:id, :t, :c, :conn, 'RESOLVED', 0)"
        ),
        {
            "id": str(conv_id),
            "t": str(tenant_id),
            "c": str(contact_id),
            "conn": str(conn_id),
        },
    )
    await salao_agenda.definir(db_session, tenant_id, DATA, 220000)
    reserva, _ = await salao_agenda.criar_reserva(
        db_session,
        tenant_id,
        DATA,
        status=ReservaStatus.PRE_RESERVADA,
        contact_id=contact_id,
        conversation_id=conv_id,
        criada_por="bot",
        nome_cliente="Lucas Mateus",
        telefone="5511000000001",
    )
    await db_session.commit()

    yield {
        "tenant_id": tenant_id,
        "conv_id": conv_id,
        "contact_id": contact_id,
        "reserva": reserva,
    }

    for stmt in (
        "DELETE FROM salao_reservas WHERE tenant_id = :t",
        "DELETE FROM salao_datas WHERE tenant_id = :t",
        "DELETE FROM messages WHERE tenant_id = :t",
        "DELETE FROM conversations WHERE tenant_id = :t",
        "DELETE FROM contacts WHERE tenant_id = :t",
        "DELETE FROM channel_connections WHERE tenant_id = :t",
        "DELETE FROM bot_settings WHERE tenant_id = :t",
        "DELETE FROM tenants WHERE id = :t",
    ):
        await db_session.execute(text(stmt), {"t": str(tenant_id)})
    await db_session.commit()


async def _mensagens(db: AsyncSession, tenant_id: uuid.UUID) -> list[tuple[str, str]]:
    rows = await db.execute(
        text(
            "SELECT content, status FROM messages "
            "WHERE tenant_id = :t AND direction = 'OUTBOUND' ORDER BY created_at"
        ),
        {"t": str(tenant_id)},
    )
    return [(r.content, r.status) for r in rows.fetchall()]


class TestRenderConfirmacao:
    """Substituição literal, nunca `str.format` — o modelo é editado pelo dono."""

    def test_preenche_os_marcadores(self):
        texto = render_confirmacao(
            "Oi, {nome}! Dia {data} confirmado por {valor}.",
            {"nome": "Lucas", "data": DATA_BR, "valor": "R$ 2.200,00"},
        )
        assert texto == f"Oi, Lucas! Dia {DATA_BR} confirmado por R$ 2.200,00."

    def test_marcador_sem_valor_vira_vazio_sem_quebrar(self):
        texto = render_confirmacao("Dia {data} por {valor}.", {"data": "07/08"})
        assert texto == "Dia 07/08 por ."
        assert "{valor}" not in texto

    def test_chave_desconhecida_nao_levanta(self):
        """Com `str.format` isto seria KeyError e derrubaria a confirmação inteira."""
        assert "{cliente}" in render_confirmacao("Oi {cliente}", {"nome": "x"})

    def test_nao_avalia_atributo_do_objeto(self):
        """`str.format` daria acesso a `__class__`; a substituição literal não dá."""
        modelo = "{nome.__class__.__init__.__globals__}"
        assert render_confirmacao(modelo, {"nome": "Lucas"}) == modelo


async def test_envia_uma_vez_e_registra_na_conversa(
    db_session: AsyncSession, cenario: dict, monkeypatch
) -> None:
    provider = _ProviderFake()
    monkeypatch.setattr(salao_aviso, "get_provider", lambda canal: provider)

    reserva = await salao_agenda.confirmar(
        db_session, cenario["tenant_id"], uuid.UUID(cenario["reserva"]["id"])
    )
    await db_session.commit()
    assert reserva is not None

    enviado = await salao_aviso.enviar_confirmacao(
        db_session, cenario["tenant_id"], reserva, "Sua reserva está confirmada!"
    )
    await db_session.commit()

    assert enviado is True
    assert provider.enviadas == ["Sua reserva está confirmada!"]
    # Persistir importa: sem isso o atendente não vê o que o cliente recebeu, e o agente,
    # que lê o histórico, contradiria o aviso na mensagem seguinte.
    assert ("Sua reserva está confirmada!", "SENT") in await _mensagens(
        db_session, cenario["tenant_id"]
    )


async def test_confirmar_duas_vezes_envia_uma_vez_so(
    db_session: AsyncSession, cenario: dict, monkeypatch
) -> None:
    """A trava é o UPDATE condicional de `confirmar`, não uma checagem no aviso.

    Se alguém "simplificar" aquele UPDATE, o cliente passa a receber o aviso duas vezes —
    e é aqui que isso aparece.
    """
    provider = _ProviderFake()
    monkeypatch.setattr(salao_aviso, "get_provider", lambda canal: provider)
    reserva_id = uuid.UUID(cenario["reserva"]["id"])

    primeira = await salao_agenda.confirmar(
        db_session, cenario["tenant_id"], reserva_id
    )
    await db_session.commit()
    assert primeira is not None
    await salao_aviso.enviar_confirmacao(
        db_session, cenario["tenant_id"], primeira, "Confirmada!"
    )
    await db_session.commit()

    # Segundo clique: o UPDATE não casa mais (status já não é pre_reservada).
    segunda = await salao_agenda.confirmar(db_session, cenario["tenant_id"], reserva_id)
    await db_session.commit()
    assert segunda is None, "confirmar duas vezes não pode reescrever o estado"
    assert provider.enviadas == ["Confirmada!"]


async def test_falha_no_envio_nao_desfaz_a_confirmacao(
    db_session: AsyncSession, cenario: dict, monkeypatch
) -> None:
    """A data está mesmo segurada. Reverter seria mentir para a agenda."""
    provider = _ProviderFake(sucesso=False)
    monkeypatch.setattr(salao_aviso, "get_provider", lambda canal: provider)

    reserva = await salao_agenda.confirmar(
        db_session, cenario["tenant_id"], uuid.UUID(cenario["reserva"]["id"])
    )
    await db_session.commit()

    enviado = await salao_aviso.enviar_confirmacao(
        db_session, cenario["tenant_id"], reserva, "Confirmada!"
    )
    await db_session.commit()

    assert enviado is False
    row = await db_session.execute(
        text("SELECT status FROM salao_reservas WHERE tenant_id = :t"),
        {"t": str(cenario["tenant_id"])},
    )
    assert row.scalar_one() == ReservaStatus.CONFIRMADA
    # A tentativa fica registrada: é ela que explica ao atendente por que precisa reenviar.
    assert ("Confirmada!", "FAILED") in await _mensagens(
        db_session, cenario["tenant_id"]
    )


async def test_mensagem_vazia_nao_envia(
    db_session: AsyncSession, cenario: dict, monkeypatch
) -> None:
    """Confirmar sem avisar é caminho legítimo — não pode virar mensagem em branco."""
    provider = _ProviderFake()
    monkeypatch.setattr(salao_aviso, "get_provider", lambda canal: provider)

    reserva = await salao_agenda.confirmar(
        db_session, cenario["tenant_id"], uuid.UUID(cenario["reserva"]["id"])
    )
    await db_session.commit()

    assert (
        await salao_aviso.enviar_confirmacao(
            db_session, cenario["tenant_id"], reserva, "   "
        )
        is False
    )
    assert provider.enviadas == []


async def test_reserva_sem_contato_nao_tem_a_quem_avisar(
    db_session: AsyncSession, cenario: dict
) -> None:
    """Reserva lançada à mão no painel: `pode_avisar` falso, e confirmar segue possível."""
    reserva_manual, _ = await salao_agenda.criar_reserva(
        db_session,
        cenario["tenant_id"],
        DATA + timedelta(days=2),
        status=ReservaStatus.PRE_RESERVADA,
        criada_por="humano",
        nome_cliente="Sem WhatsApp",
        exigir_preco=False,
        criar_data_se_faltar=True,
    )
    await db_session.commit()

    assert (
        await salao_aviso.montar_mensagem(
            db_session, cenario["tenant_id"], reserva_manual
        )
        is None
    )


async def test_mensagem_montada_traz_nome_e_data(
    db_session: AsyncSession, cenario: dict
) -> None:
    texto = await salao_aviso.montar_mensagem(
        db_session, cenario["tenant_id"], cenario["reserva"]
    )
    assert texto is not None
    assert "Lucas" in texto
    assert DATA_BR in texto
