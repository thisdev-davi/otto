"""Parsing de datas em pt-BR e o bloco de agenda que o agente lê.

O que estes testes protegem é o anti-alucinação: o bloco só pode afirmar valor de data
que exista na agenda. Um falso positivo do parser ou uma linha otimista aqui viram um
preço errado enviado a um cliente real.
"""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.salao import ReservaStatus
from app.services import salao_agenda, salao_contexto

pytestmark = pytest.mark.asyncio

# Terça-feira, para que "sábado que vem" tenha uma resposta única e verificável.
HOJE = date(2026, 7, 28)


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def tenant(db_session: AsyncSession) -> uuid.UUID:
    tenant_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, 'Salao Contexto')"),
        {"id": str(tenant_id)},
    )
    await db_session.commit()
    yield tenant_id
    for stmt in (
        "DELETE FROM salao_reservas WHERE tenant_id = :t",
        "DELETE FROM salao_datas WHERE tenant_id = :t",
        "DELETE FROM contacts WHERE tenant_id = :t",
        "DELETE FROM tenants WHERE id = :t",
    ):
        await db_session.execute(text(stmt), {"t": str(tenant_id)})
    await db_session.commit()


# ─────────────────────────── parsing ───────────────────────────


async def test_formatos_de_data() -> None:
    assert salao_contexto.extrair_datas("pode ser 15/08?", HOJE) == [date(2026, 8, 15)]
    assert salao_contexto.extrair_datas("15/08/2027 dá?", HOJE) == [date(2027, 8, 15)]
    assert salao_contexto.extrair_datas("quero 15 de agosto", HOJE) == [
        date(2026, 8, 15)
    ]
    assert salao_contexto.extrair_datas("e amanhã?", HOJE) == [date(2026, 7, 29)]


async def test_ano_ausente_resolve_para_o_futuro() -> None:
    """Quem pergunta '15/02' em julho quer fevereiro do ano que vem, não o que passou."""
    assert salao_contexto.extrair_datas("15/02 tá livre?", HOJE) == [date(2027, 2, 15)]


async def test_dia_da_semana_e_fim_de_semana() -> None:
    # 28/07/2026 é terça; o sábado seguinte é 01/08.
    assert salao_contexto.extrair_datas("sábado que vem", HOJE) == [date(2026, 8, 1)]
    assert salao_contexto.extrair_datas("esse fim de semana", HOJE) == [
        date(2026, 8, 1),
        date(2026, 8, 2),
    ]


async def test_numero_solto_nao_vira_data() -> None:
    """O falso positivo é o risco real: cada um vira uma linha errada na fonte de verdade."""
    assert salao_contexto.extrair_datas("vai ser pra 150 pessoas", HOJE) == []
    assert salao_contexto.extrair_datas("o orçamento foi R$ 4.200", HOJE) == []
    assert salao_contexto.extrair_datas("cabe 1.500 convidados?", HOJE) == []


async def test_data_impossivel_e_ignorada() -> None:
    assert salao_contexto.extrair_datas("dia 31/02", HOJE) == []
    assert salao_contexto.extrair_datas("dia 45/13", HOJE) == []


async def test_mes_sozinho() -> None:
    assert salao_contexto.extrair_meses("tem alguma data em agosto?", HOJE) == [
        (2026, 8)
    ]
    # Mês já grudado num dia não conta de novo — aquilo virou data.
    assert salao_contexto.extrair_meses("quero 15 de agosto", HOJE) == []
    # Mês que já passou neste ano resolve para o ano seguinte.
    assert salao_contexto.extrair_meses("e em março?", HOJE) == [(2027, 3)]


# ─────────────────────────── bloco de agenda ───────────────────────────


async def test_sem_agenda_nao_gera_bloco(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    """Tenant que não usa agenda não recebe ruído no prompt — nem o bloco vazio."""
    assert await salao_contexto.bloco_agenda(db_session, tenant, "15/08?", HOJE) == ""
    assert (
        await salao_contexto.aumentar(
            db_session,
            tenant,
            "conhecimento original",
            [{"role": "user", "content": "oi"}],
        )
        == "conhecimento original"
    )


async def test_bloco_traz_valor_da_data_citada(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    await salao_agenda.definir(db_session, tenant, date(2026, 8, 15), 420_000)
    await db_session.commit()

    bloco = await salao_contexto.bloco_agenda(
        db_session, tenant, "quanto fica 15/08?", HOJE
    )
    await db_session.commit()
    assert "15/08/2026" in bloco
    assert "R$ 4.200,00" in bloco
    assert "LIVRE" in bloco


async def test_data_sem_cadastro_nao_ganha_valor(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    """A linha tem de dizer que NÃO se sabe — é o que impede o modelo de preencher."""
    await salao_agenda.definir(db_session, tenant, date(2026, 8, 15), 420_000)
    await db_session.commit()

    bloco = await salao_contexto.bloco_agenda(
        db_session, tenant, "e no dia 20/09?", HOJE
    )
    await db_session.commit()
    assert "20/09/2026" in bloco
    assert "SEM CADASTRO" in bloco
    # O valor da outra data não pode aparecer colado na linha da data desconhecida.
    linha = next(li for li in bloco.splitlines() if "20/09/2026" in li)
    assert "R$" not in linha


async def test_data_reservada_aparece_como_ocupada(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    alvo = date(2026, 8, 15)
    await salao_agenda.definir(db_session, tenant, alvo, 420_000)
    await salao_agenda.criar_reserva(
        db_session, tenant, alvo, status=ReservaStatus.CONFIRMADA, hoje=HOJE
    )
    await db_session.commit()

    bloco = await salao_contexto.bloco_agenda(db_session, tenant, "15/08?", HOJE)
    await db_session.commit()
    assert "JÁ RESERVADA" in bloco


async def test_bloco_lista_proximas_livres(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    hoje_real = date.today()
    for i in (7, 14):
        await salao_agenda.definir(
            db_session, tenant, hoje_real + timedelta(days=i), 350_000
        )
    await db_session.commit()

    bloco = await salao_contexto.bloco_agenda(db_session, tenant, "oi", hoje_real)
    await db_session.commit()
    assert "Próximas datas livres" in bloco
    # Uma data por linha, no formato que o roteiro envia — é o modelo que o agente copia.
    linhas = bloco.splitlines()
    for i in (7, 14):
        dia = (hoje_real + timedelta(days=i)).strftime("%d/%m")
        assert f"*{dia}* — R$ 3.500,00" in linhas


async def test_aumentar_preserva_o_conhecimento_existente(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    await salao_agenda.definir(db_session, tenant, date(2026, 8, 15), 420_000)
    await db_session.commit()

    resultado = await salao_contexto.aumentar(
        db_session,
        tenant,
        "O salão fica na Rua Exemplo, 123.",
        [{"role": "user", "content": "quanto fica 15/08?"}],
    )
    await db_session.commit()
    assert "Rua Exemplo" in resultado
    assert "AGENDA E VALORES" in resultado


# ─────────────────────────── reserva do próprio cliente ───────────────────────────
#
# O agente não enxergava a reserva de quem estava falando com ele. Quando o cliente
# perguntou "ei tudo certo? meu lugar foi confirmado?", tudo o que o modelo tinha era o
# `resumo_do_ultimo_atendimento` — texto congelado que dizia "data segurada por 48h,
# necessário contato para formalização" enquanto a reserva já estava CONFIRMADA havia
# nove minutos. Mesmo com o LLM funcionando, a resposta teria sido errada.


async def _com_reserva(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    status: str,
    *,
    contact_id: uuid.UUID | None = None,
    telefone: str | None = None,
    expira_em: datetime | None = None,
    data: date | None = None,
) -> None:
    """Cria contato, data na agenda (senão `_tem_agenda` corta tudo) e a reserva."""
    dia = data or date(2026, 8, 7)
    if contact_id is not None:
        await db.execute(
            text(
                "INSERT INTO contacts (id, tenant_id, channel, external_id, name) "
                "VALUES (:id, :t, 'WHATSAPP', :ext, 'Lucas') "
                "ON CONFLICT DO NOTHING"
            ),
            {"id": str(contact_id), "t": str(tenant_id), "ext": f"{contact_id}@lid"},
        )
    await salao_agenda.definir(db, tenant_id, dia, 220000)
    await db.execute(
        text(
            "INSERT INTO salao_reservas "
            "(id, tenant_id, data, status, contact_id, criada_por, nome_cliente, "
            " telefone, expira_em, created_at, updated_at) "
            "VALUES (gen_random_uuid(), :t, :d, :s, :c, 'bot', 'Lucas', :tel, "
            ":exp, now(), now())"
        ),
        {
            "t": str(tenant_id),
            "d": dia,
            "s": status,
            "c": str(contact_id) if contact_id else None,
            "tel": telefone,
            "exp": expira_em,
        },
    )
    await db.commit()


async def test_reserva_confirmada_e_dita_como_confirmada(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    """O caso do Lucas."""
    contact_id = uuid.uuid4()
    await _com_reserva(
        db_session, tenant, ReservaStatus.CONFIRMADA, contact_id=contact_id
    )

    bloco = await salao_contexto.bloco_reserva(db_session, tenant, contact_id)
    assert "CONFIRMADA pela equipe" in _linhas_das_reservas(bloco)
    assert "07/08/2026" in bloco
    assert "nunca confirma" in bloco.lower(), (
        "a regra que impede o agente de prometer confirmação tem que viajar junto"
    )


async def test_pre_reserva_nao_e_dita_como_confirmada(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    contact_id = uuid.uuid4()
    await _com_reserva(
        db_session,
        tenant,
        ReservaStatus.PRE_RESERVADA,
        contact_id=contact_id,
        # No FUTURO, sempre: `bloco_reserva` expira pré-reserva vencida na leitura
        # (expiração preguiçosa), então um prazo cravado no passado faz a reserva
        # virar EXPIRADA e este teste falhar meses depois, sozinho.
        expira_em=datetime.now(timezone.utc) + timedelta(hours=48),
    )

    linhas = _linhas_das_reservas(
        await salao_contexto.bloco_reserva(db_session, tenant, contact_id)
    )
    assert "SEGURADA" in linhas
    assert "o prazo vence em" in linhas
    # A linha da reserva não pode afirmar confirmação.
    assert "CONFIRMADA pela equipe" not in linhas


async def test_puxa_pelo_telefone_quando_o_contato_e_outro(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    """ "Puxar pelo número": o contexto acompanha a PESSOA, não o registro de contato.

    O endereço do WhatsApp já mudou de forma uma vez (`@lid`). Se a mesma pessoa
    reaparecer sob outro contato, a reserva dela continua sendo dela.
    """
    await _com_reserva(
        db_session,
        tenant,
        ReservaStatus.CONFIRMADA,
        contact_id=uuid.uuid4(),
        telefone="5511000000001",
    )

    bloco = await salao_contexto.bloco_reserva(
        db_session, tenant, uuid.uuid4(), "5511000000001"
    )
    assert "CONFIRMADA pela equipe" in _linhas_das_reservas(bloco)


def _linhas_das_reservas(bloco: str) -> str:
    """Só as linhas de reserva, sem o bloco de REGRAS.

    As regras citam "SEGURADA" e "confirmado" ao explicar o que dizer ao cliente — se o
    teste olhar o bloco inteiro, ele passa a casar com o texto das instruções em vez do
    estado real da reserva, e deixa de testar qualquer coisa.
    """
    return bloco.split("REGRAS SOBRE")[0]


async def test_pre_reserva_vencida_aparece_como_vencida(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    """Hold vencido não pode ser descrito como "segurada" — a promessa não vale mais.

    Prova, de quebra, que `expirar_vencidas` roda por este caminho.
    """
    contact_id = uuid.uuid4()
    await _com_reserva(
        db_session,
        tenant,
        ReservaStatus.PRE_RESERVADA,
        contact_id=contact_id,
        expira_em=datetime(2026, 7, 1, 10, tzinfo=timezone.utc),
    )

    bloco = await salao_contexto.bloco_reserva(db_session, tenant, contact_id)
    assert "SEGURADA" not in _linhas_das_reservas(bloco)
    assert "voltou a ser vendida" in bloco

    row = await db_session.execute(
        text("SELECT status FROM salao_reservas WHERE tenant_id = :t"),
        {"t": str(tenant)},
    )
    assert row.scalar_one() == ReservaStatus.EXPIRADA


async def test_sem_reserva_o_bloco_e_vazio(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    """Vazio, e não "nenhuma reserva encontrada" — a segunda forma convida a inventar."""
    await salao_agenda.definir(db_session, tenant, date(2026, 8, 7), 220000)
    await db_session.commit()
    assert await salao_contexto.bloco_reserva(db_session, tenant, uuid.uuid4()) == ""


async def test_tenant_sem_agenda_nao_paga_nem_polui(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    assert await salao_contexto.bloco_reserva(db_session, tenant, uuid.uuid4()) == ""


async def test_nota_interna_nunca_vaza_para_o_prompt(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    """O bloco é lido por um modelo que fala com o CLIENTE.

    `salao_resumo` carrega `nota_interna` — texto escrito para a equipe. É por isso que
    este bloco não o reaproveita; se alguém "simplificar" reusando aquele service, este
    teste cai.
    """
    contact_id = uuid.uuid4()
    await _com_reserva(
        db_session, tenant, ReservaStatus.CONFIRMADA, contact_id=contact_id
    )
    await db_session.execute(
        text("UPDATE salao_reservas SET observacao = :obs WHERE tenant_id = :t"),
        {
            "t": str(tenant),
            "obs": "SEGREDO INTERNO: cliente pechincha, não dar desconto",
        },
    )
    await db_session.commit()

    bloco = await salao_contexto.bloco_reserva(db_session, tenant, contact_id)
    assert "SEGREDO INTERNO" not in bloco
    assert "pechincha" not in bloco
