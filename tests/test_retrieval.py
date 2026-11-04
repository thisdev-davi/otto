"""Testes do roteamento da busca na base de conhecimento (sem rede, sem banco).

O que importa aqui é a decisão: quando a busca vetorial não é possível, o RAG cai
na busca textual em vez de emudecer — e quando ela é possível, o caminho textual
não é acionado (senão o fallback mascararia uma falha silenciosa do embedding).

O segundo bloco cobre o corte de relevância, que é POR PROVEDOR: as escalas de
distância de modelos diferentes não são comparáveis, e usar o corte do provedor
errado não quebra nada de forma visível — só deixa passar lixo como se fosse fonte.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.ai import embeddings, retrieval

TENANT = uuid4()


def _fake_db(rows: list | None = None) -> AsyncMock:
    """AsyncSession mínima: execute() devolve algo com .fetchall()."""
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=MagicMock(fetchall=MagicMock(return_value=rows or []))
    )
    return db


def _hit(title: str, distance: float) -> MagicMock:
    return MagicMock(
        title=title, source_url=None, content=f"conteudo {title}", distance=distance
    )


class TestFallbackRouting:
    async def test_embedding_indisponivel_cai_no_keyword(self, monkeypatch):
        monkeypatch.setattr(retrieval, "embed", AsyncMock(return_value=None))
        keyword = AsyncMock(
            return_value=[
                {
                    "title": "Preços",
                    "source_url": "precos",
                    "content": "consulta 120 reais",
                    "distance": 0.5,
                }
            ]
        )
        monkeypatch.setattr(retrieval, "_keyword_search", keyword)

        hits = await retrieval.search(
            _fake_db(), TENANT, "quanto custa?", api_key="", provider=""
        )

        assert keyword.await_count == 1
        assert hits[0]["title"] == "Preços"

    async def test_embedding_ok_nao_usa_keyword(self, monkeypatch):
        """Fallback só em FALHA de embedding. Busca vetorial vazia é resposta legítima
        ('não sei') — cair no keyword aqui ressuscitaria a alucinação que o corte evita."""
        monkeypatch.setattr(retrieval, "embed", AsyncMock(return_value=[0.1] * 1536))
        keyword = AsyncMock(return_value=[])
        monkeypatch.setattr(retrieval, "_keyword_search", keyword)

        hits = await retrieval.search(
            _fake_db(), TENANT, "pergunta", api_key="k", provider="gemini"
        )

        assert keyword.await_count == 0
        assert hits == []

    async def test_keyword_recebe_top_k_do_chamador(self, monkeypatch):
        monkeypatch.setattr(retrieval, "embed", AsyncMock(return_value=None))
        keyword = AsyncMock(return_value=[])
        monkeypatch.setattr(retrieval, "_keyword_search", keyword)

        await retrieval.search(
            _fake_db(), TENANT, "q", api_key="", provider="", top_k=3
        )

        assert keyword.await_args.args[3] == 3

    async def test_provider_chega_no_embed(self, monkeypatch):
        """O provedor precisa atravessar até o embed: é ele que escolhe URL e modelo."""
        embed = AsyncMock(return_value=[0.1] * 1536)
        monkeypatch.setattr(retrieval, "embed", embed)

        await retrieval.search(
            _fake_db(), TENANT, "q", api_key="chave", provider="gemini"
        )

        assert embed.await_args.args == ("q", "chave", "gemini")


class TestMaxDistance:
    async def test_corte_descarta_chunk_irrelevante(self, monkeypatch):
        """O max_distance é o que impede o modelo de responder com base em lixo."""
        monkeypatch.setattr(retrieval, "embed", AsyncMock(return_value=[0.1] * 1536))

        hits = await retrieval.search(
            _fake_db([_hit("perto", 0.20), _hit("longe", 0.95)]),
            TENANT,
            "q",
            api_key="k",
            provider="openai",
            max_distance=0.55,
        )

        assert [h["title"] for h in hits] == ["perto"]

    async def test_corte_default_vem_do_provedor(self, monkeypatch):
        """Sem max_distance explícito, cada provedor usa o SEU corte calibrado.

        0.52 é o caso que separa os dois: passa no corte da OpenAI (0.55) e é cortado
        no do Gemini (0.50). Os dois números foram medidos contra as APIs reais — ver
        o comentário em embeddings._MAX_DISTANCE para a metodologia e para o que este
        corte deliberadamente NÃO tenta pegar.
        """
        monkeypatch.setattr(retrieval, "embed", AsyncMock(return_value=[0.1] * 1536))
        rows = [_hit("duvidoso", 0.52)]

        na_openai = await retrieval.search(
            _fake_db(rows), TENANT, "q", api_key="k", provider="openai"
        )
        no_gemini = await retrieval.search(
            _fake_db(rows), TENANT, "q", api_key="k", provider="gemini"
        )

        assert [h["title"] for h in na_openai] == ["duvidoso"]
        assert no_gemini == []


class TestFiltroPorModelo:
    async def test_busca_filtra_pelo_modelo_do_provedor(self, monkeypatch):
        """Vetores de outro modelo não podem entrar na conta.

        openai e gemini dão os mesmos 1536 valores aqui, então o banco compararia os
        dois sem reclamar e devolveria vizinhos sem sentido — errado em silêncio.
        """
        monkeypatch.setattr(retrieval, "embed", AsyncMock(return_value=[0.1] * 1536))
        db = _fake_db()

        await retrieval.search(db, TENANT, "q", api_key="k", provider="gemini")

        # A última chamada é a busca vetorial (a primeira é o set_config do GUC).
        params = db.execute.await_args.args[1]
        assert params["model"] == embeddings.model_id("gemini")
        assert "embedding_model = :model" in str(db.execute.await_args.args[0])
