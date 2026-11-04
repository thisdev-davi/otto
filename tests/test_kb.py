"""Testes da ingestão da base de conhecimento (sem rede, sem banco).

O que importa aqui é a assimetria que o endpoint de ingestão protege: sem chave de
embeddings a PERGUNTA degrada para busca textual (ver tests/test_retrieval.py), mas a
INGESTÃO não grava nada — por isso a rota recusa com 422 em vez de enfileirar. Se estes
testes começarem a falhar porque a ingestão passou a gravar sem vetor, reveja o comentário
em app/api/bot.py::ingest_kb_document antes de ajustar a expectativa.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.services import kb

TENANT = uuid4()
CONTEUDO = "A consulta custa 120 reais. Atendemos de segunda a sexta."


def _fake_db() -> AsyncMock:
    """AsyncSession mínima: execute() devolve algo com .fetchall()."""
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(fetchall=MagicMock(return_value=[])))
    return db


class TestIngestaoExigeChave:
    async def test_sem_chave_nao_grava_nada(self):
        """Sem chave a ingestão é um no-op silencioso — daí o 422 na rota.

        Ela roda no worker: sem o corte no endpoint, o usuário receberia "enfileirado"
        e esperaria por um documento que nunca aparece.
        """
        db = _fake_db()

        gravados = await kb.ingest_document(
            db, TENANT, "precos", "Preços", CONTEUDO, api_key="", provider="gemini"
        )

        assert gravados == 0
        assert db.execute.await_count == 0
        assert db.commit.await_count == 0

    async def test_falha_do_provedor_tambem_nao_grava_parcial(self, monkeypatch):
        """Chave presente mas provedor fora do ar: nada de meia-ingestão.

        O DELETE da re-ingestão só pode rodar depois dos vetores em mãos — senão uma
        falha de rede apagaria a versão boa da fonte e não gravaria a nova.
        """
        monkeypatch.setattr(kb, "embed_batch", AsyncMock(return_value=None))
        db = _fake_db()

        gravados = await kb.ingest_document(
            db,
            TENANT,
            "precos",
            "Preços",
            CONTEUDO,
            api_key="chave-valida",
            provider="gemini",
        )

        assert gravados == 0
        assert db.execute.await_count == 0

    async def test_com_chave_grava_os_chunks(self, monkeypatch):
        monkeypatch.setattr(kb, "embed_batch", AsyncMock(return_value=[[0.1] * 1536]))
        db = _fake_db()

        gravados = await kb.ingest_document(
            db,
            TENANT,
            "precos",
            "Preços",
            CONTEUDO,
            api_key="chave-valida",
            provider="gemini",
        )

        assert gravados == 1
        assert db.commit.await_count == 1

    async def test_texto_vazio_nao_chama_o_provedor(self, monkeypatch):
        """Sem chunk não há o que embedar — não gasta chamada de API."""
        embed = AsyncMock(return_value=[[0.1] * 1536])
        monkeypatch.setattr(kb, "embed_batch", embed)
        db = _fake_db()

        gravados = await kb.ingest_document(
            db,
            TENANT,
            "vazio",
            "Vazio",
            "   \n  ",
            api_key="chave-valida",
            provider="gemini",
        )

        assert gravados == 0
        assert embed.await_count == 0
        assert db.execute.await_count == 0


class TestModeloGravadoNoChunk:
    async def test_grava_o_modelo_do_provedor(self, monkeypatch):
        """Sem o modelo no INSERT a busca não consegue distinguir espaços vetoriais.

        openai e gemini produzem os mesmos 1536 valores, então a coluna é a ÚNICA
        coisa que impede a busca de comparar vetores de modelos diferentes e devolver
        vizinhos sem sentido — falha que não gera erro nenhum.
        """
        monkeypatch.setattr(kb, "embed_batch", AsyncMock(return_value=[[0.1] * 1536]))
        db = _fake_db()

        await kb.ingest_document(
            db, TENANT, "precos", "Preços", CONTEUDO, api_key="k", provider="gemini"
        )

        inserts = [
            c
            for c in db.execute.await_args_list
            if "INSERT INTO kb_documents" in str(c.args[0])
        ]
        assert len(inserts) == 1
        assert inserts[0].args[1]["model"] == "gemini-embedding-001"

    async def test_provider_chega_no_embed_batch(self, monkeypatch):
        """O provedor precisa atravessar: é ele que decide URL, modelo e dimensão."""
        embed = AsyncMock(return_value=[[0.1] * 1536])
        monkeypatch.setattr(kb, "embed_batch", embed)

        await kb.ingest_document(
            _fake_db(),
            TENANT,
            "precos",
            "Preços",
            CONTEUDO,
            api_key="k",
            provider="openai",
        )

        assert embed.await_args.args[1:] == ("k", "openai")
