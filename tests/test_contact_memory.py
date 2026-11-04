"""Memória do cliente entre conversas (contacts.profile).

Sem isto, cliente que volta depois de uma conversa resolvida chega numa conversa NOVA e é
atendido como desconhecido — o contexto da IA é carregado por conversation_id. Era latente
enquanto nada era resolvido; com o botão Resolver na thread virou o caso comum.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.services import contact_memory

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def contato():
    tenant_id = uuid.uuid4()
    contact_id = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("INSERT INTO tenants (id, name) VALUES (:id, 'Tenant Memoria')"),
            {"id": str(tenant_id)},
        )
        await db.execute(
            text(
                "INSERT INTO contacts (id, tenant_id, channel, external_id, name) "
                "VALUES (:id, :t, 'WHATSAPP', '5511900001', 'Gui')"
            ),
            {"id": str(contact_id), "t": str(tenant_id)},
        )
        await db.commit()

    yield {"tenant_id": tenant_id, "contact_id": contact_id}

    async with AsyncSessionLocal() as db:
        await db.execute(
            text("DELETE FROM contacts WHERE tenant_id = :t"), {"t": str(tenant_id)}
        )
        await db.execute(
            text("DELETE FROM tenants WHERE id = :t"), {"t": str(tenant_id)}
        )
        await db.commit()


async def test_cliente_novo_tem_perfil_vazio(contato) -> None:
    async with AsyncSessionLocal() as db:
        assert (
            await contact_memory.load(db, contato["tenant_id"], contato["contact_id"])
            == {}
        )


async def test_remember_mescla_sem_perder_dado_antigo(contato) -> None:
    """O `captured` de um turno é o que a IA viu NAQUELA conversa. Substituir apagaria o
    que ela soube antes — é justamente o que faz o cliente ser interrogado de novo."""
    async with AsyncSessionLocal() as db:
        await contact_memory.remember(
            db,
            contato["tenant_id"],
            contato["contact_id"],
            {"nome": "Gui", "tipo_de_evento": "15 anos"},
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        await contact_memory.remember(
            db,
            contato["tenant_id"],
            contato["contact_id"],
            {"numero_de_convidados": "120"},
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        perfil = await contact_memory.load(
            db, contato["tenant_id"], contato["contact_id"]
        )

    assert perfil["intake"] == {
        "nome": "Gui",
        "tipo_de_evento": "15 anos",
        "numero_de_convidados": "120",
    }


async def test_valor_vazio_nao_apaga_o_que_ja_se_sabia(contato) -> None:
    async with AsyncSessionLocal() as db:
        await contact_memory.remember(
            db, contato["tenant_id"], contato["contact_id"], {"nome": "Gui"}
        )
        await contact_memory.remember(
            db, contato["tenant_id"], contato["contact_id"], {"nome": "  "}
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        perfil = await contact_memory.load(
            db, contato["tenant_id"], contato["contact_id"]
        )
    assert perfil["intake"]["nome"] == "Gui"


async def test_resumo_do_handoff_fica_no_contato(contato) -> None:
    """O resumo que a IA gera no encaminhamento deixa de morrer na nota da conversa."""
    async with AsyncSessionLocal() as db:
        await contact_memory.remember(
            db,
            contato["tenant_id"],
            contato["contact_id"],
            {"nome": "Gui"},
            summary="Quer 15 anos para 120 convidados em marco.",
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        perfil = await contact_memory.load(
            db, contato["tenant_id"], contato["contact_id"]
        )

    assert perfil["last_summary"] == "Quer 15 anos para 120 convidados em marco."
    assert perfil["last_interaction_at"]
    assert contact_memory.is_returning(perfil) is True


async def test_isolamento_por_tenant(contato) -> None:
    async with AsyncSessionLocal() as db:
        await contact_memory.remember(
            db, contato["tenant_id"], contato["contact_id"], {"nome": "Gui"}
        )
        await db.commit()

    async with AsyncSessionLocal() as db:
        # Mesmo contact_id, tenant errado: não enxerga nada.
        assert await contact_memory.load(db, uuid.uuid4(), contato["contact_id"]) == {}


async def test_is_returning_falso_sem_dado(contato) -> None:
    assert contact_memory.is_returning({}) is False
    assert contact_memory.is_returning({"conversations": 2}) is False
