"""Ciclo de vida da FONTE da base de conhecimento: gravar, substituir, listar, apagar.

Por que existe: `kb_documents` guarda chunks, e chunk não remonta documento (overlap
de 200 chars, sem coluna de ordem, `now()` idêntico dentro da transação). Por isso a
ingestão guarda o texto original em `kb_sources`.

Quem lê essa tabela hoje é `scripts/sincronizar_conteudo.py`, para saber quais fontes já
existem e quais viraram órfãs (o arquivo sumiu de `conteudo/base/`). O painel já teve um
editor que a lia por `kb.get_source`; ele saiu junto com as rotas de escrita, e as
asserções aqui passaram a consultar a tabela direto — é o comportamento que importa, não
o acessor.
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.database import AsyncSessionLocal
from app.services import kb

pytestmark = pytest.mark.asyncio

CHAVE_FALSA = "chave-de-teste"


@pytest_asyncio.fixture
async def tenant():
    tid = uuid.uuid4()
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("INSERT INTO tenants (id, name) VALUES (:id, 'Tenant KB Sources')"),
            {"id": str(tid)},
        )
        await db.commit()

    yield tid

    async with AsyncSessionLocal() as db:
        await db.execute(
            text("DELETE FROM kb_documents WHERE tenant_id = :t"), {"t": str(tid)}
        )
        await db.execute(
            text("DELETE FROM kb_sources WHERE tenant_id = :t"), {"t": str(tid)}
        )
        await db.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": str(tid)})
        await db.commit()


async def _ingerir(monkeypatch, tid: uuid.UUID, source: str, title: str, content: str):
    """Ingere sem chamar o provedor de embeddings (vetor fixo, sem rede)."""

    async def fake_embed(chunks, api_key, provider):
        return [[0.0] * 1536 for _ in chunks]

    monkeypatch.setattr(kb, "embed_batch", fake_embed)
    async with AsyncSessionLocal() as db:
        return await kb.ingest_document(
            db, tid, source, title, content, CHAVE_FALSA, "gemini"
        )


async def _original(tid: uuid.UUID, source: str) -> dict | None:
    """O texto guardado em kb_sources — o que o sincronizador enxerga."""
    async with AsyncSessionLocal() as db:
        row = await db.execute(
            text(
                "SELECT title, content FROM kb_sources "
                "WHERE tenant_id = :t AND source = :s"
            ),
            {"t": str(tid), "s": source},
        )
        r = row.fetchone()
    return None if r is None else {"title": r.title, "content": r.content}


async def test_ingestao_guarda_o_texto_original(tenant, monkeypatch) -> None:
    texto = "O salão comporta 150 convidados. Não é permitido fogo de artifício."
    await _ingerir(monkeypatch, tenant, "regras", "Regras", texto)

    doc = await _original(tenant, "regras")

    assert doc is not None
    # Íntegro: é o texto do arquivo, não a costura dos chunks.
    assert doc["content"] == texto
    assert doc["title"] == "Regras"


async def test_reingestao_substitui_e_nao_duplica(tenant, monkeypatch) -> None:
    """Sincronizar de novo atualiza a fonte; não cria uma segunda com o valor antigo."""
    await _ingerir(monkeypatch, tenant, "regras", "Regras", "Capacidade: 100 pessoas.")
    await _ingerir(
        monkeypatch, tenant, "regras", "Regras v2", "Capacidade: 150 pessoas."
    )

    doc = await _original(tenant, "regras")
    async with AsyncSessionLocal() as db:
        fontes = await kb.list_sources(db, tenant)

    assert doc["content"] == "Capacidade: 150 pessoas."
    assert doc["title"] == "Regras v2"
    assert len([f for f in fontes if f["source"] == "regras"]) == 1


async def test_lista_inclui_fonte_sem_texto_original(tenant, monkeypatch) -> None:
    """Fonte antiga, com chunks mas sem linha em kb_sources, continua na lista.

    A listagem já fez LEFT JOIN em kb_sources para dizer se a fonte era editável. O
    editor saiu e o JOIN também — este teste trava o efeito colateral que importaria:
    uma fonte sem texto original sumir da lista e ficar respondendo ao cliente sem
    aparecer para ninguém.
    """
    await _ingerir(monkeypatch, tenant, "com-texto", "Com texto", "conteúdo qualquer")

    async with AsyncSessionLocal() as db:
        await db.execute(
            text(
                "INSERT INTO kb_documents (id, tenant_id, source_url, title, content, "
                " embedding_model, created_at) "
                "VALUES (gen_random_uuid(), :t, 'legada', 'Legada', 'trecho', "
                " 'gemini-embedding-001', now())"
            ),
            {"t": str(tenant)},
        )
        await db.commit()
        fontes = {f["source"]: f for f in await kb.list_sources(db, tenant)}

    assert set(fontes) == {"com-texto", "legada"}
    assert fontes["legada"]["chunks"] == 1
    assert await _original(tenant, "legada") is None


async def test_delete_leva_o_original_junto(tenant, monkeypatch) -> None:
    await _ingerir(monkeypatch, tenant, "regras", "Regras", "texto")

    async with AsyncSessionLocal() as db:
        await kb.delete_source(db, tenant, "regras")

    # Se o original ficasse para trás, o sincronizador veria uma fonte que não existe
    # mais em kb_documents e a trataria como já indexada.
    assert await _original(tenant, "regras") is None


async def test_isolamento_por_tenant(tenant, monkeypatch) -> None:
    await _ingerir(monkeypatch, tenant, "regras", "Regras", "segredo do tenant A")
    async with AsyncSessionLocal() as db:
        assert await kb.list_sources(db, uuid.uuid4()) == []
