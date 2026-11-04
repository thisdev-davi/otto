"""Agenda do salão: preço por data, disponibilidade e a corrida da reserva.

O teste que justifica o arquivo é `test_reserva_concorrente_so_uma_vence`. Ele é o
equivalente, para a agenda, do `test_claim_race_condition`: prova que quem arbitra dois
clientes disputando o mesmo sábado é o índice parcial no banco, e não uma checagem em
Python que só falha em produção.
"""

import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.salao import ReservaStatus
from app.services import salao_agenda

pytestmark = pytest.mark.asyncio

HOJE = date.today()
SABADO = HOJE + timedelta(days=(5 - HOJE.weekday()) % 7 + 7)  # um sábado futuro
PRECO = 420_000  # R$ 4.200,00


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def tenant(db_session: AsyncSession) -> uuid.UUID:
    tenant_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, 'Salao Teste')"),
        {"id": str(tenant_id)},
    )
    await db_session.commit()

    yield tenant_id

    await db_session.execute(
        text("DELETE FROM salao_reservas WHERE tenant_id = :t"), {"t": str(tenant_id)}
    )
    await db_session.execute(
        text("DELETE FROM salao_datas WHERE tenant_id = :t"), {"t": str(tenant_id)}
    )
    await db_session.execute(
        text("DELETE FROM tenants WHERE id = :t"), {"t": str(tenant_id)}
    )
    await db_session.commit()


# ─────────────────────────── cadastro e consulta ───────────────────────────


async def test_definir_e_consultar(db_session: AsyncSession, tenant: uuid.UUID) -> None:
    await salao_agenda.definir(db_session, tenant, SABADO, PRECO)
    await db_session.commit()

    situacao = await salao_agenda.consultar(db_session, tenant, [SABADO])
    assert situacao[SABADO]["preco_centavos"] == PRECO
    assert situacao[SABADO]["livre"] is True


async def test_data_sem_cadastro_nao_aparece(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    """Ausência é informação: é o que faz o agente mandar confirmar em vez de estimar."""
    await salao_agenda.definir(db_session, tenant, SABADO, PRECO)
    await db_session.commit()

    outra = SABADO + timedelta(days=1)
    situacao = await salao_agenda.consultar(db_session, tenant, [SABADO, outra])
    assert outra not in situacao


async def test_data_sem_preco_nao_e_livre(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    await salao_agenda.definir(db_session, tenant, SABADO, None)
    await db_session.commit()

    situacao = await salao_agenda.consultar(db_session, tenant, [SABADO])
    assert situacao[SABADO]["preco_centavos"] is None
    assert situacao[SABADO]["livre"] is False


async def test_lote_respeita_dia_da_semana(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    de = HOJE
    ate = HOJE + timedelta(days=27)
    afetadas = await salao_agenda.definir_em_lote(
        db_session, tenant, de, ate, [6], PRECO
    )
    await db_session.commit()

    linhas = await salao_agenda.listar(db_session, tenant, de, ate)
    await db_session.commit()

    # `listar` devolve o intervalo INTEIRO, cadastrado ou não — é o que deixa o calendário
    # desenhar o mês sem inventar as lacunas. Quem o lote tocou são as datas cadastradas.
    tocadas = [linha for linha in linhas if linha["cadastrada"]]
    assert afetadas == len(tocadas)
    assert tocadas, "o lote não cadastrou nenhuma data"
    assert all(linha["data"].isoweekday() == 6 for linha in tocadas)
    # E o resto do intervalo continua sem cadastro.
    assert all(
        linha["motivo"] == salao_agenda.MOTIVO_SEM_CADASTRO
        for linha in linhas
        if linha["data"].isoweekday() != 6
    )


async def test_lote_nao_altera_data_reservada(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    """Mudar o valor de um sábado já vendido alteraria o que foi combinado com o cliente."""
    await salao_agenda.definir(db_session, tenant, SABADO, PRECO)
    await salao_agenda.criar_reserva(
        db_session, tenant, SABADO, status=ReservaStatus.CONFIRMADA
    )
    await db_session.commit()

    await salao_agenda.definir_em_lote(db_session, tenant, SABADO, SABADO, [], 999_999)
    await db_session.commit()

    situacao = await salao_agenda.consultar(db_session, tenant, [SABADO])
    assert situacao[SABADO]["preco_centavos"] == PRECO


async def test_proximas_livres_ignora_bloqueada_e_reservada(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    livre = SABADO
    bloqueada = SABADO + timedelta(days=7)
    reservada = SABADO + timedelta(days=14)
    for dia in (livre, bloqueada, reservada):
        await salao_agenda.definir(db_session, tenant, dia, PRECO)
    await salao_agenda.definir(db_session, tenant, bloqueada, PRECO, bloqueada=True)
    await salao_agenda.criar_reserva(
        db_session, tenant, reservada, status=ReservaStatus.CONFIRMADA
    )
    await db_session.commit()

    livres = await salao_agenda.proximas_livres(db_session, tenant, limite=10)
    await db_session.commit()
    assert [linha["data"] for linha in livres] == [livre]


async def test_lote_de_preco_nao_desbloqueia_data_bloqueada(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    """O lote gravava `bloqueada` sempre, e a manutenção voltava a ser vendida sozinha."""
    await salao_agenda.definir(db_session, tenant, SABADO, PRECO, bloqueada=True)
    await db_session.commit()

    await salao_agenda.definir_em_lote(db_session, tenant, SABADO, SABADO, [], 555_000)
    await db_session.commit()

    situacao = await salao_agenda.consultar(db_session, tenant, [SABADO])
    await db_session.commit()
    assert situacao[SABADO]["bloqueada"] is True
    assert situacao[SABADO]["preco_centavos"] == 555_000

    # E o lote continua conseguindo desbloquear quando é isso que se pede.
    await salao_agenda.definir_em_lote(
        db_session, tenant, SABADO, SABADO, [], 555_000, bloqueada=False
    )
    await db_session.commit()
    situacao = await salao_agenda.consultar(db_session, tenant, [SABADO])
    await db_session.commit()
    assert situacao[SABADO]["bloqueada"] is False


# ─────────────────────────── reserva ───────────────────────────


async def test_reserva_recusa_data_sem_preco_para_o_bot(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    await salao_agenda.definir(db_session, tenant, SABADO, None)
    await db_session.commit()

    reserva, motivo = await salao_agenda.criar_reserva(db_session, tenant, SABADO)
    assert reserva is None
    assert motivo == salao_agenda.MOTIVO_SEM_PRECO


async def test_reserva_humana_dispensa_preco(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    """Quem fechou por telefone sabe o que combinou; o agente é que não pode inventar."""
    reserva, motivo = await salao_agenda.criar_reserva(
        db_session,
        tenant,
        SABADO,
        status=ReservaStatus.CONFIRMADA,
        exigir_preco=False,
        criar_data_se_faltar=True,
    )
    await db_session.commit()
    assert motivo == salao_agenda.MOTIVO_OK
    assert reserva is not None


async def test_reserva_recusa_bloqueada_e_passado(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    await salao_agenda.definir(db_session, tenant, SABADO, PRECO, bloqueada=True)
    await db_session.commit()

    _, motivo = await salao_agenda.criar_reserva(db_session, tenant, SABADO)
    assert motivo == salao_agenda.MOTIVO_BLOQUEADA

    _, motivo_passado = await salao_agenda.criar_reserva(
        db_session, tenant, HOJE - timedelta(days=1)
    )
    assert motivo_passado == salao_agenda.MOTIVO_PASSADO


async def test_reserva_concorrente_so_uma_vence(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    """Duas pré-reservas simultâneas na mesma data: o banco deixa passar exatamente uma.

    Cada tentativa roda na SUA sessão, como aconteceria em dois workers atendendo dois
    clientes ao mesmo tempo. Sem o índice parcial, as duas passariam — e o salão teria
    vendido o mesmo sábado duas vezes.
    """
    await salao_agenda.definir(db_session, tenant, SABADO, PRECO)
    await db_session.commit()

    async def tentar(nome: str) -> str:
        async with AsyncSessionLocal() as db:
            _, motivo = await salao_agenda.criar_reserva(
                db, tenant, SABADO, nome_cliente=nome
            )
            await db.commit()
            return motivo

    motivos = await asyncio.gather(tentar("Ana"), tentar("Bruno"))
    assert sorted(motivos) == [salao_agenda.MOTIVO_OCUPADA, salao_agenda.MOTIVO_OK]

    ativas = await salao_agenda.reservas_ativas(db_session, tenant)
    await db_session.commit()
    assert len(ativas) == 1


async def test_pre_reserva_vencida_libera_a_data(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    """Expiração preguiçosa: o hold morto cai na primeira vez que alguém disputa a data."""
    await salao_agenda.definir(db_session, tenant, SABADO, PRECO)
    reserva, _ = await salao_agenda.criar_reserva(
        db_session, tenant, SABADO, nome_cliente="Ana"
    )
    assert reserva is not None
    await db_session.execute(
        text("UPDATE salao_reservas SET expira_em = :quando WHERE id = :id"),
        {
            "quando": datetime.now(timezone.utc) - timedelta(hours=1),
            "id": reserva["id"],
        },
    )
    await db_session.commit()

    nova, motivo = await salao_agenda.criar_reserva(
        db_session, tenant, SABADO, nome_cliente="Bruno"
    )
    await db_session.commit()
    assert motivo == salao_agenda.MOTIVO_OK
    assert nova is not None


async def test_cancelar_libera_e_preserva_historico(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    await salao_agenda.definir(db_session, tenant, SABADO, PRECO)
    reserva, _ = await salao_agenda.criar_reserva(db_session, tenant, SABADO)
    assert reserva is not None
    await db_session.commit()

    cancelada = await salao_agenda.cancelar(
        db_session, tenant, uuid.UUID(reserva["id"])
    )
    await db_session.commit()
    assert cancelada is not None

    nova, motivo = await salao_agenda.criar_reserva(db_session, tenant, SABADO)
    await db_session.commit()
    assert motivo == salao_agenda.MOTIVO_OK
    assert nova is not None

    # A linha cancelada continua no banco: o índice parcial não a conta.
    total = await db_session.execute(
        text("SELECT count(*) FROM salao_reservas WHERE tenant_id = :t"),
        {"t": str(tenant)},
    )
    assert total.scalar_one() == 2


async def test_confirmar_e_idempotente(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    await salao_agenda.definir(db_session, tenant, SABADO, PRECO)
    reserva, _ = await salao_agenda.criar_reserva(db_session, tenant, SABADO)
    assert reserva is not None
    await db_session.commit()

    primeira = await salao_agenda.confirmar(
        db_session, tenant, uuid.UUID(reserva["id"])
    )
    await db_session.commit()
    assert primeira is not None
    assert primeira["status"] == ReservaStatus.CONFIRMADA
    assert primeira["expira_em"] is None

    # Segunda vez não reescreve estado que já mudou.
    segunda = await salao_agenda.confirmar(db_session, tenant, uuid.UUID(reserva["id"]))
    await db_session.commit()
    assert segunda is None


async def test_isolamento_por_tenant(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    outro = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, 'Outro Salao')"),
        {"id": str(outro)},
    )
    await salao_agenda.definir(db_session, tenant, SABADO, PRECO)
    await db_session.commit()

    try:
        assert await salao_agenda.consultar(db_session, outro, [SABADO]) == {}
        assert await salao_agenda.proximas_livres(db_session, outro) == []
        # A data do vizinho não bloqueia a mesma data neste tenant.
        _, motivo = await salao_agenda.criar_reserva(
            db_session, outro, SABADO, exigir_preco=False, criar_data_se_faltar=True
        )
        await db_session.commit()
        assert motivo == salao_agenda.MOTIVO_OK
    finally:
        await db_session.execute(
            text("DELETE FROM salao_reservas WHERE tenant_id = :t"), {"t": str(outro)}
        )
        await db_session.execute(
            text("DELETE FROM salao_datas WHERE tenant_id = :t"), {"t": str(outro)}
        )
        await db_session.execute(
            text("DELETE FROM tenants WHERE id = :t"), {"t": str(outro)}
        )
        await db_session.commit()


async def test_formatar_preco() -> None:
    assert salao_agenda.formatar_preco(420_000) == "R$ 4.200,00"
    assert salao_agenda.formatar_preco(35_050) == "R$ 350,50"
    assert salao_agenda.formatar_preco(None) == ""
