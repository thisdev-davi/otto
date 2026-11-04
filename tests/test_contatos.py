"""Perfil do cliente: isolamento por tenant, agrupamento por telefone e anotações.

Três coisas aqui podem quebrar em silêncio, e são as três que estes testes seguram:

1. **Isolamento.** Toda função pública de `contatos` passa por `_irmaos`, que é onde o
   `tenant_id` entra. Um caminho que escape dele devolve dado de outro cliente da
   plataforma sem erro nenhum.
2. **Agrupamento por telefone.** É a razão de o módulo existir — o endereço do WhatsApp
   já mudou de forma uma vez (`@lid`) e partiu o histórico de quem voltou a escrever.
3. **Quem pode apagar anotação.** A condição vive no `WHERE` do DELETE; se alguém a
   mover para uma leitura anterior, apagar a nota de outra pessoa volta a ser possível.
"""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.salao import ReservaStatus
from app.services import contatos

pytestmark = pytest.mark.asyncio

TELEFONE = "5511988887777"


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def tenant(db_session: AsyncSession) -> uuid.UUID:
    tenant_id = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, 'Perfil Contato')"),
        {"id": str(tenant_id)},
    )
    await db_session.commit()
    yield tenant_id
    for stmt in (
        "DELETE FROM contact_notes WHERE tenant_id = :t",
        "DELETE FROM salao_reservas WHERE tenant_id = :t",
        "DELETE FROM salao_datas WHERE tenant_id = :t",
        "DELETE FROM messages WHERE tenant_id = :t",
        "DELETE FROM conversations WHERE tenant_id = :t",
        "DELETE FROM contacts WHERE tenant_id = :t",
        "DELETE FROM users WHERE tenant_id = :t",
        "DELETE FROM tenants WHERE id = :t",
    ):
        await db_session.execute(text(stmt), {"t": str(tenant_id)})
    await db_session.commit()


async def _contato(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    external_id: str,
    *,
    nome: str | None = None,
    telefone: str | None = None,
    profile: str = "{}",
) -> uuid.UUID:
    contact_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO contacts (id, tenant_id, channel, external_id, name, phone, profile) "
            "VALUES (:id, :t, 'WHATSAPP', :ext, :nome, :fone, CAST(:prof AS jsonb))"
        ),
        {
            "id": str(contact_id),
            "t": str(tenant_id),
            "ext": external_id,
            "nome": nome,
            "fone": telefone,
            "prof": profile,
        },
    )
    await db.commit()
    return contact_id


async def _usuario(
    db: AsyncSession, tenant_id: uuid.UUID, email: str, nome: str
) -> uuid.UUID:
    user_id = uuid.uuid4()
    # `is_active` é default do lado do Python (`Column(default=True)`), não do banco —
    # INSERT em SQL puro precisa dizer, senão é violação de NOT NULL.
    await db.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, hashed_password, full_name, is_active) "
            "VALUES (:id, :t, :e, 'x', :n, true)"
        ),
        {"id": str(user_id), "t": str(tenant_id), "e": email, "n": nome},
    )
    await db.commit()
    return user_id


# ─────────────────────────── isolamento ───────────────────────────


async def test_contato_de_outro_tenant_nao_existe(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    """Contato real, tenant errado: `None`, e não o perfil dele."""
    outro = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, 'Vizinho')"),
        {"id": str(outro)},
    )
    await db_session.commit()
    alheio = await _contato(db_session, outro, "999@s.whatsapp.net", nome="Alheio")

    assert await contatos.perfil(db_session, tenant, alheio) is None

    await db_session.execute(
        text("DELETE FROM contacts WHERE tenant_id = :t"), {"t": str(outro)}
    )
    await db_session.execute(
        text("DELETE FROM tenants WHERE id = :t"), {"t": str(outro)}
    )
    await db_session.commit()


async def test_nota_em_contato_de_outro_tenant_e_recusada(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    outro = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, 'Vizinho 2')"),
        {"id": str(outro)},
    )
    await db_session.commit()
    alheio = await _contato(db_session, outro, "888@s.whatsapp.net")
    autor = await _usuario(db_session, tenant, "a@x.com", "Ana")

    assert await contatos.criar_nota(db_session, tenant, alheio, autor, "oi") is None

    await db_session.execute(
        text("DELETE FROM contacts WHERE tenant_id = :t"), {"t": str(outro)}
    )
    await db_session.execute(
        text("DELETE FROM tenants WHERE id = :t"), {"t": str(outro)}
    )
    await db_session.commit()


# ─────────────────────── agrupamento por telefone ───────────────────────


async def test_perfil_une_contatos_com_o_mesmo_telefone(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    """O caso que motivou o módulo: a mesma pessoa em dois registros de contato.

    Acontece quando o endereço do canal muda de forma (`@lid`), e é exatamente o que
    `ingestion.py` loga. Sem o agrupamento, o painel do contato NOVO mostraria zero
    agendamentos para quem já fechou um sábado.
    """
    antigo = await _contato(
        db_session,
        tenant,
        "5511988887777@s.whatsapp.net",
        nome="Carlos",
        telefone=TELEFONE,
        profile='{"intake": {"tipo_de_evento": "aniversario"}}',
    )
    novo = await _contato(
        db_session,
        tenant,
        "21711730823330@lid",
        nome="Carlos Souza",
        telefone=TELEFONE,
        profile='{"intake": {"numero_de_convidados": "120"}}',
    )
    # A reserva ficou pendurada no contato ANTIGO.
    await db_session.execute(
        text(
            "INSERT INTO salao_reservas (id, tenant_id, data, status, contact_id, criada_por) "
            "VALUES (gen_random_uuid(), :t, :d, 'confirmada', :c, 'humano')"
        ),
        {"t": str(tenant), "d": date(2026, 8, 15), "c": str(antigo)},
    )
    await db_session.commit()

    perfil = await contatos.perfil(db_session, tenant, novo)
    assert perfil is not None
    assert perfil["contatos_unidos"] == 2
    # O agendamento do registro antigo aparece no perfil do registro novo.
    assert [r["data"] for r in perfil["reservas"]] == [date(2026, 8, 15)]
    # Intake dos dois, somado.
    assert perfil["dados_coletados"] == {
        "tipo_de_evento": "aniversario",
        "numero_de_convidados": "120",
    }


async def test_contato_sem_telefone_nao_agrupa(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    """Telefone NULL não casa com telefone NULL — juntaria clientes distintos."""
    a = await _contato(db_session, tenant, "111@lid", nome="Sem Numero A")
    await _contato(db_session, tenant, "222@lid", nome="Sem Numero B")

    perfil = await contatos.perfil(db_session, tenant, a)
    assert perfil is not None
    assert perfil["contatos_unidos"] == 1


async def test_intake_do_contato_atual_vence_o_antigo(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    """Chave repetida entre irmãos: o contato pedido é o mais recente e ganha."""
    await _contato(
        db_session,
        tenant,
        "antigo@s.whatsapp.net",
        telefone=TELEFONE,
        profile='{"intake": {"numero_de_convidados": "50"}}',
    )
    atual = await _contato(
        db_session,
        tenant,
        "atual@lid",
        telefone=TELEFONE,
        profile='{"intake": {"numero_de_convidados": "120"}}',
    )

    perfil = await contatos.perfil(db_session, tenant, atual)
    assert perfil is not None
    assert perfil["dados_coletados"]["numero_de_convidados"] == "120"


async def test_chave_de_controle_da_persona_nao_vaza_para_a_tela(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    c = await _contato(
        db_session,
        tenant,
        "acao@s.whatsapp.net",
        telefone=TELEFONE,
        profile='{"intake": {"nome": "Gui", "_acao_reservar": "2026-08-15"}}',
    )
    perfil = await contatos.perfil(db_session, tenant, c)
    assert perfil is not None
    assert perfil["dados_coletados"] == {"nome": "Gui"}


# ─────────────────────────── agendamentos ───────────────────────────


async def test_pre_reserva_vencida_aparece_expirada(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    """Expiração preguiçosa também na abertura do painel.

    Sem ela o painel diria "pré-reservada" para um hold que já caiu — a promessa que o
    sistema não pode mais cumprir, dita justamente a quem vai decidir sobre a data.
    """
    c = await _contato(db_session, tenant, "vencida@s.whatsapp.net", telefone=TELEFONE)
    await db_session.execute(
        text(
            "INSERT INTO salao_reservas "
            "(id, tenant_id, data, status, contact_id, criada_por, expira_em) "
            "VALUES (gen_random_uuid(), :t, :d, 'pre_reservada', :c, 'bot', :exp)"
        ),
        {
            "t": str(tenant),
            "d": date(2026, 12, 20),
            "c": str(c),
            "exp": datetime.now(timezone.utc) - timedelta(hours=1),
        },
    )
    await db_session.commit()

    perfil = await contatos.perfil(db_session, tenant, c)
    assert perfil is not None
    assert perfil["reservas"][0]["status"] == ReservaStatus.EXPIRADA


async def test_historico_inclui_reserva_cancelada(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    """O painel é histórico, não a lista de datas ocupadas: cancelada continua contando."""
    c = await _contato(db_session, tenant, "hist@s.whatsapp.net", telefone=TELEFONE)
    for dia, st in ((date(2026, 3, 2), "cancelada"), (date(2026, 8, 15), "confirmada")):
        await db_session.execute(
            text(
                "INSERT INTO salao_reservas (id, tenant_id, data, status, contact_id, criada_por) "
                "VALUES (gen_random_uuid(), :t, :d, :s, :c, 'humano')"
            ),
            {"t": str(tenant), "d": dia, "s": st, "c": str(c)},
        )
    await db_session.commit()

    perfil = await contatos.perfil(db_session, tenant, c)
    assert perfil is not None
    # Mais recente primeiro.
    assert [r["data"] for r in perfil["reservas"]] == [
        date(2026, 8, 15),
        date(2026, 3, 2),
    ]


# ─────────────────────────── anotações ───────────────────────────


async def test_nota_grava_autor_e_aparece_no_perfil(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    c = await _contato(db_session, tenant, "nota@s.whatsapp.net", telefone=TELEFONE)
    autor = await _usuario(db_session, tenant, "ana@x.com", "Ana Lima")

    nota = await contatos.criar_nota(
        db_session, tenant, c, autor, "  pediu desconto à vista  "
    )
    await db_session.commit()
    assert nota is not None
    assert nota["texto"] == "pediu desconto à vista"  # strip aplicado
    assert nota["autor"] == "Ana Lima"

    perfil = await contatos.perfil(db_session, tenant, c)
    assert perfil is not None
    assert [n["texto"] for n in perfil["notas"]] == ["pediu desconto à vista"]


async def test_nota_vazia_e_recusada(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    c = await _contato(db_session, tenant, "vazia@s.whatsapp.net", telefone=TELEFONE)
    autor = await _usuario(db_session, tenant, "b@x.com", "Bia")
    assert await contatos.criar_nota(db_session, tenant, c, autor, "   \n  ") is None


async def test_nota_acompanha_o_cliente_entre_contatos_irmaos(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    """Anotação escrita no registro antigo tem de aparecer no perfil do novo."""
    antigo = await _contato(
        db_session, tenant, "irm-a@s.whatsapp.net", telefone=TELEFONE
    )
    novo = await _contato(db_session, tenant, "irm-b@lid", telefone=TELEFONE)
    autor = await _usuario(db_session, tenant, "c@x.com", "Cris")

    await contatos.criar_nota(db_session, tenant, antigo, autor, "irmão da noiva")
    await db_session.commit()

    perfil = await contatos.perfil(db_session, tenant, novo)
    assert perfil is not None
    assert [n["texto"] for n in perfil["notas"]] == ["irmão da noiva"]


async def test_atendente_nao_apaga_nota_de_outro(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    """A condição de posse vive no WHERE do DELETE — este é o teste que a segura lá."""
    c = await _contato(db_session, tenant, "posse@s.whatsapp.net", telefone=TELEFONE)
    dona = await _usuario(db_session, tenant, "dona@x.com", "Dona")
    intruso = await _usuario(db_session, tenant, "intruso@x.com", "Intruso")

    nota = await contatos.criar_nota(db_session, tenant, c, dona, "minha nota")
    await db_session.commit()
    assert nota is not None

    apagou = await contatos.apagar_nota(
        db_session, tenant, uuid.UUID(nota["id"]), intruso, is_admin=False
    )
    await db_session.commit()
    assert apagou is False

    perfil = await contatos.perfil(db_session, tenant, c)
    assert perfil is not None
    assert len(perfil["notas"]) == 1


async def test_autor_apaga_a_propria_nota(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    c = await _contato(db_session, tenant, "propria@s.whatsapp.net", telefone=TELEFONE)
    dona = await _usuario(db_session, tenant, "dona2@x.com", "Dona")

    nota = await contatos.criar_nota(db_session, tenant, c, dona, "some depois")
    await db_session.commit()
    assert nota is not None

    assert (
        await contatos.apagar_nota(
            db_session, tenant, uuid.UUID(nota["id"]), dona, is_admin=False
        )
        is True
    )
    await db_session.commit()

    perfil = await contatos.perfil(db_session, tenant, c)
    assert perfil is not None
    assert perfil["notas"] == []


async def test_admin_apaga_nota_de_qualquer_um(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    c = await _contato(db_session, tenant, "admin@s.whatsapp.net", telefone=TELEFONE)
    dona = await _usuario(db_session, tenant, "dona3@x.com", "Dona")
    chefe = await _usuario(db_session, tenant, "chefe@x.com", "Chefe")

    nota = await contatos.criar_nota(db_session, tenant, c, dona, "nota alheia")
    await db_session.commit()
    assert nota is not None

    assert (
        await contatos.apagar_nota(
            db_session, tenant, uuid.UUID(nota["id"]), chefe, is_admin=True
        )
        is True
    )
    await db_session.commit()


async def test_nota_de_outro_tenant_nao_e_apagavel(
    db_session: AsyncSession, tenant: uuid.UUID
) -> None:
    """Admin de um tenant não alcança a anotação de outro, nem sabendo o id."""
    outro = uuid.uuid4()
    await db_session.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, 'Vizinho 3')"),
        {"id": str(outro)},
    )
    await db_session.commit()
    alheio = await _contato(
        db_session, outro, "777@s.whatsapp.net", telefone="5511900000000"
    )
    dono = await _usuario(db_session, outro, "dono@y.com", "Dono")
    nota = await contatos.criar_nota(db_session, outro, alheio, dono, "segredo")
    await db_session.commit()
    assert nota is not None

    invasor = await _usuario(db_session, tenant, "invasor@x.com", "Invasor")
    apagou = await contatos.apagar_nota(
        db_session, tenant, uuid.UUID(nota["id"]), invasor, is_admin=True
    )
    await db_session.commit()
    assert apagou is False

    for stmt in (
        "DELETE FROM contact_notes WHERE tenant_id = :t",
        "DELETE FROM contacts WHERE tenant_id = :t",
        "DELETE FROM users WHERE tenant_id = :t",
        "DELETE FROM tenants WHERE id = :t",
    ):
        await db_session.execute(text(stmt), {"t": str(outro)})
    await db_session.commit()
