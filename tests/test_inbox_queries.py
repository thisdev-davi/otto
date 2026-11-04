import uuid
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text

from app.core.config import settings
from app.models.enums import Channel, ConversationStatus
from app.services.inbox_queries import (
    get_all,
    get_assigned_to,
    get_conversation_messages,
    get_unclaimed,
)

# Sai do .env em vez de hardcode: a porta do Postgres do Otto é 5436 (não a padrão),
# e uma cópia literal aqui já apontou para 5433 por meses — a suíte falhava inteira
# e ninguém via, porque falha por conexão recusada parece "banco não está de pé".
DATABASE_URL = settings.DATABASE_URL

_engine = create_async_engine(DATABASE_URL, echo=False)
_session_maker = async_sessionmaker(_engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    async with _session_maker() as session:
        yield session


async def _insert_tenant(db: AsyncSession, name: str) -> uuid.UUID:
    tid = uuid.uuid4()
    await db.execute(
        text("INSERT INTO tenants (id, name) VALUES (:id, :name)"),
        {"id": str(tid), "name": name},
    )
    return tid


async def _insert_user(db: AsyncSession, tenant_id: uuid.UUID, email: str) -> uuid.UUID:
    uid = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO users (id, tenant_id, email, hashed_password, is_active) "
            "VALUES (:id, :tenant_id, :email, 'x', true)"
        ),
        {"id": str(uid), "tenant_id": str(tenant_id), "email": email},
    )
    return uid


async def _insert_contact(
    db: AsyncSession, tenant_id: uuid.UUID, external_id: str
) -> uuid.UUID:
    cid = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO contacts (id, tenant_id, channel, external_id, name) "
            "VALUES (:id, :tenant_id, :channel, :external_id, :name)"
        ),
        {
            "id": str(cid),
            "tenant_id": str(tenant_id),
            "channel": Channel.WHATSAPP.name,
            "external_id": external_id,
            "name": f"Contact {external_id}",
        },
    )
    return cid


async def _insert_conversation(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    contact_id: uuid.UUID,
    status: ConversationStatus,
    assigned_user_id: uuid.UUID | None = None,
) -> uuid.UUID:
    conv_id = uuid.uuid4()
    assigned = str(assigned_user_id) if assigned_user_id else None
    await db.execute(
        text(
            "INSERT INTO conversations "
            "(id, tenant_id, contact_id, status, assigned_user_id, unread_count) "
            "VALUES (:id, :tenant_id, :contact_id, :status, :assigned_user_id, 0)"
        ),
        {
            "id": str(conv_id),
            "tenant_id": str(tenant_id),
            "contact_id": str(contact_id),
            "status": status.name,
            "assigned_user_id": assigned,
        },
    )
    return conv_id


@pytest.mark.asyncio
async def test_get_unclaimed(db: AsyncSession) -> None:
    tenant_id = await _insert_tenant(db, "Tenant Test Unclaimed")
    user1 = await _insert_user(db, tenant_id, "u1@test.com")
    user2 = await _insert_user(db, tenant_id, "u2@test.com")
    contact = await _insert_contact(db, tenant_id, "55119000001")

    conv_a = await _insert_conversation(
        db, tenant_id, contact, ConversationStatus.OPEN, None
    )
    await _insert_conversation(
        db, tenant_id, contact, ConversationStatus.PENDING, user1
    )
    await _insert_conversation(
        db, tenant_id, contact, ConversationStatus.RESOLVED, user2
    )

    rows = await get_unclaimed(db, tenant_id)

    ids = [r["id"] for r in rows]
    assert str(conv_a) in ids
    for row in rows:
        assert row["assigned_user_id"] is None
        assert row["status"] != "RESOLVED"

    _ = user1, user2


@pytest.mark.asyncio
async def test_get_assigned_to(db: AsyncSession) -> None:
    tenant_id = await _insert_tenant(db, "Tenant Test Assigned")
    user1 = await _insert_user(db, tenant_id, "u1@assigned.com")
    user2 = await _insert_user(db, tenant_id, "u2@assigned.com")
    contact = await _insert_contact(db, tenant_id, "55119000002")

    await _insert_conversation(db, tenant_id, contact, ConversationStatus.OPEN, None)
    conv_b = await _insert_conversation(
        db, tenant_id, contact, ConversationStatus.PENDING, user1
    )
    await _insert_conversation(
        db, tenant_id, contact, ConversationStatus.RESOLVED, user2
    )

    rows = await get_assigned_to(db, tenant_id, user1)

    ids = [r["id"] for r in rows]
    assert str(conv_b) in ids
    for row in rows:
        assert row["assigned_user_id"] == str(user1)
        assert row["status"] != "RESOLVED"


@pytest.mark.asyncio
async def test_get_all(db: AsyncSession) -> None:
    tenant_id = await _insert_tenant(db, "Tenant Test All")
    user1 = await _insert_user(db, tenant_id, "u1@all.com")
    user2 = await _insert_user(db, tenant_id, "u2@all.com")
    contact = await _insert_contact(db, tenant_id, "55119000003")

    conv_a = await _insert_conversation(
        db, tenant_id, contact, ConversationStatus.OPEN, None
    )
    conv_b = await _insert_conversation(
        db, tenant_id, contact, ConversationStatus.PENDING, user1
    )
    conv_c = await _insert_conversation(
        db, tenant_id, contact, ConversationStatus.RESOLVED, user2
    )

    rows = await get_all(db, tenant_id)

    ids = [r["id"] for r in rows]
    assert str(conv_a) in ids
    assert str(conv_b) in ids
    assert str(conv_c) in ids


@pytest.mark.asyncio
async def test_tenant_isolation(db: AsyncSession) -> None:
    tenant1 = await _insert_tenant(db, "Tenant Isolation 1")
    tenant2 = await _insert_tenant(db, "Tenant Isolation 2")

    user_t2 = await _insert_user(db, tenant2, "u@tenant2.com")
    contact_t1 = await _insert_contact(db, tenant1, "55119000004")
    contact_t2 = await _insert_contact(db, tenant2, "55119000005")

    await _insert_conversation(db, tenant1, contact_t1, ConversationStatus.OPEN, None)
    await _insert_conversation(db, tenant2, contact_t2, ConversationStatus.OPEN, None)
    await _insert_conversation(
        db, tenant2, contact_t2, ConversationStatus.PENDING, user_t2
    )
    await _insert_conversation(
        db, tenant2, contact_t2, ConversationStatus.RESOLVED, user_t2
    )

    unclaimed_t1 = await get_unclaimed(db, tenant1)
    assigned_t1 = await get_assigned_to(db, tenant1, user_t2)
    all_t1 = await get_all(db, tenant1)

    for row in unclaimed_t1:
        assert row["tenant_id"] == str(tenant1)
    for row in assigned_t1:
        assert row["tenant_id"] == str(tenant1)
    for row in all_t1:
        assert row["tenant_id"] == str(tenant1)

    assert len(unclaimed_t1) == 1
    assert len(assigned_t1) == 0
    assert len(all_t1) == 1


# ─────────────────────── ordem das mensagens da thread ───────────────────────
#
# Regressão fixada aqui: `get_conversation_messages` fazia `ORDER BY created_at ASC
# LIMIT 50`. Numa conversa com mais de 50 mensagens a thread mostrava as 50 MAIS
# ANTIGAS e a mensagem nova nunca aparecia — em silêncio, e só nas conversas longas,
# que são as que importam.


async def _insert_messages(
    db: AsyncSession, tenant_id: uuid.UUID, conversation_id: uuid.UUID, n: int
) -> None:
    """n mensagens com created_at crescente e conteúdo 'msg-000', 'msg-001', …"""
    for i in range(n):
        await db.execute(
            text(
                "INSERT INTO messages "
                "(id, conversation_id, tenant_id, direction, content_type, content, "
                " channel, created_at) "
                "VALUES (gen_random_uuid(), :cid, :tid, 'INBOUND', 'TEXT', :content, "
                " 'WHATSAPP', now() + make_interval(secs => :i))"
            ),
            {
                "cid": str(conversation_id),
                "tid": str(tenant_id),
                "content": f"msg-{i:03d}",
                "i": float(i),
            },
        )


@pytest.mark.asyncio
async def test_mensagens_traz_as_mais_recentes(db: AsyncSession) -> None:
    tenant_id = await _insert_tenant(db, "Tenant Msgs Recentes")
    contact = await _insert_contact(db, tenant_id, "55119000010")
    conv = await _insert_conversation(db, tenant_id, contact, ConversationStatus.OPEN)
    await _insert_messages(db, tenant_id, conv, 60)

    rows = await get_conversation_messages(db, tenant_id, conv, limit=50)

    assert len(rows) == 50
    conteudos = [r["content"] for r in rows]
    # As 50 MAIS RECENTES: msg-010 até msg-059.
    assert conteudos[0] == "msg-010"
    assert conteudos[-1] == "msg-059"
    # O ponto do bug: a última mensagem da conversa tem que estar na página inicial.
    assert "msg-059" in conteudos
    # Devolvidas em ordem cronológica, não invertidas.
    assert conteudos == sorted(conteudos)


@pytest.mark.asyncio
async def test_mensagens_offset_anda_para_tras(db: AsyncSession) -> None:
    tenant_id = await _insert_tenant(db, "Tenant Msgs Offset")
    contact = await _insert_contact(db, tenant_id, "55119000011")
    conv = await _insert_conversation(db, tenant_id, contact, ConversationStatus.OPEN)
    await _insert_messages(db, tenant_id, conv, 60)

    antigas = await get_conversation_messages(db, tenant_id, conv, limit=50, offset=50)

    conteudos = [r["content"] for r in antigas]
    # Sobraram as 10 mais antigas, ainda em ordem cronológica.
    assert conteudos == [f"msg-{i:03d}" for i in range(10)]


# ─────────────── bolhas do mesmo turno: a ordem de inserção manda ───────────────
#
# O helper acima soma `i` segundos em cada linha, então nunca produz empate — e foi por
# isso que a suíte ficou verde enquanto a produção embaralhava. O turno do bot grava as
# bolhas na MESMA transação, e `now()` (= transaction_timestamp) dava a todas o mesmo
# instante ao microssegundo. Estes testes gravam como o orquestrador grava.


async def _insert_bolhas(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    conversation_id: uuid.UUID,
    textos: list[str],
) -> None:
    """As bolhas de um turno, uma transação só, como `_send_and_persist` faz."""
    for texto in textos:
        await db.execute(
            text(
                "INSERT INTO messages "
                "(id, conversation_id, tenant_id, direction, content_type, content, "
                " channel, created_at) "
                "VALUES (gen_random_uuid(), :cid, :tid, 'OUTBOUND', 'TEXT', :content, "
                " 'WHATSAPP', clock_timestamp())"
            ),
            {"cid": str(conversation_id), "tid": str(tenant_id), "content": texto},
        )


@pytest.mark.asyncio
async def test_bolhas_do_mesmo_turno_saem_na_ordem_de_envio(db: AsyncSession) -> None:
    """Saudação, oferta e menu — nesta ordem, não em qualquer uma.

    Com `now()` as três ficavam com `created_at` idêntico e o cliente via o menu antes
    do "oi". `clock_timestamp()` anda dentro da transação e grava a cadência real.
    """
    tenant_id = await _insert_tenant(db, "Tenant Bolhas")
    contact = await _insert_contact(db, tenant_id, "55119000020")
    conv = await _insert_conversation(db, tenant_id, contact, ConversationStatus.OPEN)
    turno = [
        "Oi! Aqui é a Bia.",
        "Posso te ajudar com datas e valores.",
        "1. Ver datas",
    ]
    await _insert_bolhas(db, tenant_id, conv, turno)

    rows = await get_conversation_messages(db, tenant_id, conv)

    assert [r["content"] for r in rows] == turno


@pytest.mark.asyncio
async def test_mensagens_empatadas_saem_sempre_na_mesma_ordem(db: AsyncSession) -> None:
    """Histórico antigo empatado não pode embaralhar a cada refresh.

    As linhas gravadas antes da correção têm `created_at` idêntico e não há como
    recuperar a ordem verdadeira delas. O que dá para garantir é estabilidade: o
    desempate por `id` faz a thread mostrar sempre o mesmo arranjo.
    """
    tenant_id = await _insert_tenant(db, "Tenant Empate")
    contact = await _insert_contact(db, tenant_id, "55119000021")
    conv = await _insert_conversation(db, tenant_id, contact, ConversationStatus.OPEN)
    for i in range(5):
        await db.execute(
            text(
                "INSERT INTO messages "
                "(id, conversation_id, tenant_id, direction, content_type, content, "
                " channel, created_at) "
                "VALUES (gen_random_uuid(), :cid, :tid, 'OUTBOUND', 'TEXT', :content, "
                " 'WHATSAPP', TIMESTAMPTZ '2026-09-08 16:06:21.904763+00')"
            ),
            {"cid": str(conv), "tid": str(tenant_id), "content": f"bolha-{i}"},
        )

    primeira = [
        r["content"] for r in await get_conversation_messages(db, tenant_id, conv)
    ]
    segunda = [
        r["content"] for r in await get_conversation_messages(db, tenant_id, conv)
    ]

    assert primeira == segunda
    assert sorted(primeira) == [f"bolha-{i}" for i in range(5)]


@pytest.mark.asyncio
async def test_mensagens_isoladas_por_tenant(db: AsyncSession) -> None:
    tenant_a = await _insert_tenant(db, "Tenant Msgs A")
    tenant_b = await _insert_tenant(db, "Tenant Msgs B")
    contact = await _insert_contact(db, tenant_a, "55119000012")
    conv = await _insert_conversation(db, tenant_a, contact, ConversationStatus.OPEN)
    await _insert_messages(db, tenant_a, conv, 3)

    assert await get_conversation_messages(db, tenant_b, conv) == []


# ---------------------------------------------------------------------------
# Busca. O campo "Buscar conversas…" da TopBar era decoração: não existia nenhum
# parâmetro de busca no backend.
# ---------------------------------------------------------------------------


async def _conversa_buscavel(
    db: AsyncSession, tenant_id: uuid.UUID, external_id: str, nome: str, preview: str
) -> uuid.UUID:
    cid = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO contacts (id, tenant_id, channel, external_id, name) "
            "VALUES (:id, :tid, :ch, :ext, :nome)"
        ),
        {
            "id": str(cid),
            "tid": str(tenant_id),
            "ch": Channel.WHATSAPP.name,
            "ext": external_id,
            "nome": nome,
        },
    )
    conv_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO conversations (id, tenant_id, contact_id, status, unread_count, "
            "last_message_preview) VALUES (:id, :tid, :cid, 'OPEN', 0, :preview)"
        ),
        {
            "id": str(conv_id),
            "tid": str(tenant_id),
            "cid": str(cid),
            "preview": preview,
        },
    )
    return conv_id


@pytest.mark.asyncio
async def test_busca_por_nome_parcial_e_case_insensitive(db: AsyncSession) -> None:
    tid = await _insert_tenant(db, "Tenant Busca Nome")
    alvo = await _conversa_buscavel(
        db, tid, "5511900001@s.whatsapp.net", "Maria Silva", "bom dia"
    )
    await _conversa_buscavel(
        db, tid, "5511900002@s.whatsapp.net", "João Souza", "boa tarde"
    )

    achados = await get_all(db, tid, search="mAri")
    assert [c["id"] for c in achados] == [str(alvo)]


@pytest.mark.asyncio
async def test_busca_por_trecho_do_telefone(db: AsyncSession) -> None:
    """external_id é '5511...@s.whatsapp.net' — digitar com máscara tem que achar."""
    tid = await _insert_tenant(db, "Tenant Busca Fone")
    alvo = await _conversa_buscavel(
        db, tid, "5511988887777@s.whatsapp.net", "Sem Nome", "oi"
    )
    await _conversa_buscavel(db, tid, "5521911112222@s.whatsapp.net", "Outro", "oi")

    achados = await get_all(db, tid, search="(11) 98888")
    assert [c["id"] for c in achados] == [str(alvo)]


@pytest.mark.asyncio
async def test_busca_no_preview_da_ultima_mensagem(db: AsyncSession) -> None:
    tid = await _insert_tenant(db, "Tenant Busca Preview")
    alvo = await _conversa_buscavel(
        db, tid, "5511900003@s.whatsapp.net", "Cliente A", "segue o orçamento em anexo"
    )
    await _conversa_buscavel(
        db, tid, "5511900004@s.whatsapp.net", "Cliente B", "obrigado!"
    )

    achados = await get_all(db, tid, search="orçamento")
    assert [c["id"] for c in achados] == [str(alvo)]


@pytest.mark.asyncio
async def test_sem_busca_devolve_tudo(db: AsyncSession) -> None:
    """Não-regressão: as três filas continuam funcionando sem o parâmetro."""
    tid = await _insert_tenant(db, "Tenant Sem Busca")
    user = await _insert_user(db, tid, "sembusca@test.com")
    contato = await _insert_contact(db, tid, "55119000010")
    await _insert_conversation(db, tid, contato, ConversationStatus.OPEN, None)
    await _insert_conversation(db, tid, contato, ConversationStatus.OPEN, user)

    assert len(await get_all(db, tid)) == 2
    assert len(await get_unclaimed(db, tid)) == 1
    assert len(await get_assigned_to(db, tid, user)) == 1


@pytest.mark.asyncio
async def test_busca_respeita_o_tenant(db: AsyncSession) -> None:
    """Dois tenants com o MESMO nome de contato não podem se enxergar."""
    dono = await _insert_tenant(db, "Tenant Dono Busca")
    intruso = await _insert_tenant(db, "Tenant Intruso Busca")
    await _conversa_buscavel(db, dono, "5511900005@s.whatsapp.net", "Ana Costa", "x")
    await _conversa_buscavel(db, intruso, "5511900006@s.whatsapp.net", "Ana Costa", "x")

    achados = await get_all(db, intruso, search="Ana")
    assert len(achados) == 1
    assert achados[0]["tenant_id"] == str(intruso)


@pytest.mark.asyncio
async def test_curinga_do_like_nao_vaza(db: AsyncSession) -> None:
    """Sem escapar, '%' casaria com tudo — um filtro que finge filtrar."""
    tid = await _insert_tenant(db, "Tenant Curinga")
    await _conversa_buscavel(db, tid, "5511900007@s.whatsapp.net", "Bruno", "oi")
    await _conversa_buscavel(db, tid, "5511900008@s.whatsapp.net", "Carla", "oi")

    assert await get_all(db, tid, search="%") == []
    assert await get_all(db, tid, search="_") == []


@pytest.mark.asyncio
async def test_busca_na_fila_mantem_prioridade_no_topo(db: AsyncSession) -> None:
    tid = await _insert_tenant(db, "Tenant Busca Prioridade")
    normal = await _conversa_buscavel(
        db, tid, "5511900011@s.whatsapp.net", "Pedro Normal", "orçamento"
    )
    urgente = await _conversa_buscavel(
        db, tid, "5511900012@s.whatsapp.net", "Pedro Urgente", "orçamento"
    )
    await db.execute(
        text("UPDATE conversations SET priority = 'high' WHERE id = :id"),
        {"id": str(urgente)},
    )

    achados = await get_unclaimed(db, tid, search="Pedro")
    assert [c["id"] for c in achados] == [str(urgente), str(normal)]
