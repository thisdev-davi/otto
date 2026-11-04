"""Testes da resolução de provedor de embeddings (sem rede, sem banco).

Esta é a decisão que destrava (ou trava) o RAG inteiro: qual provedor e qual chave
usar para embedar. O caso que importa na prática é o terceiro — tenant sem chave
própria, LLM_PROVIDER=gemini no ambiente: a mesma chave do chat passa a cobrir os
embeddings, sem o usuário colar nada e sem conta na OpenAI.
"""

import pytest

from app.ai import embeddings
from app.services import bot_settings
from app.services.bot_settings import BotConfig, resolve_embeddings


@pytest.fixture(autouse=True)
def ambiente_limpo(monkeypatch):
    """Zera o ambiente: cada teste declara só o que precisa."""
    for campo, valor in (
        ("EMBEDDINGS_PROVIDER", "openai"),
        ("EMBEDDINGS_API_KEY", ""),
        ("LLM_PROVIDER", "stub"),
        ("LLM_API_KEY", ""),
    ):
        monkeypatch.setattr(bot_settings.settings, campo, valor)


class TestResolucaoDeProvedor:
    def test_sem_nada_configurado_nao_resolve(self):
        assert resolve_embeddings(BotConfig()) is None

    def test_chave_dedicada_do_ambiente_vence(self, monkeypatch):
        """EMBEDDINGS_API_KEY é o override explícito: ganha até do painel do tenant."""
        monkeypatch.setattr(bot_settings.settings, "EMBEDDINGS_API_KEY", "dedicada")
        monkeypatch.setattr(bot_settings.settings, "EMBEDDINGS_PROVIDER", "openai")

        alvo = resolve_embeddings(BotConfig(provider="gemini", api_key="do-painel"))

        assert alvo is not None
        assert (alvo.provider, alvo.api_key) == ("openai", "dedicada")

    def test_painel_do_tenant_vence_o_llm_do_ambiente(self, monkeypatch):
        monkeypatch.setattr(bot_settings.settings, "LLM_PROVIDER", "openai")
        monkeypatch.setattr(bot_settings.settings, "LLM_API_KEY", "do-ambiente")

        alvo = resolve_embeddings(BotConfig(provider="gemini", api_key="do-painel"))

        assert alvo is not None
        assert (alvo.provider, alvo.api_key) == ("gemini", "do-painel")

    def test_chave_do_llm_do_ambiente_serve_para_embeddings(self, monkeypatch):
        """O caso real do projeto: uma chave Gemini cobre chat E embeddings."""
        monkeypatch.setattr(bot_settings.settings, "LLM_PROVIDER", "gemini")
        monkeypatch.setattr(bot_settings.settings, "LLM_API_KEY", "chave-gemini")

        alvo = resolve_embeddings(BotConfig())

        assert alvo is not None
        assert (alvo.provider, alvo.api_key) == ("gemini", "chave-gemini")

    def test_provedor_que_nao_embeda_nao_resolve(self, monkeypatch):
        """Groq move chat e transcrição, mas não tem endpoint de embeddings.

        Sem esta guarda o RAG tentaria embedar contra uma URL que não existe e falharia
        a cada pergunta, em vez de degradar para a busca textual de uma vez.
        """
        monkeypatch.setattr(bot_settings.settings, "LLM_PROVIDER", "groq")
        monkeypatch.setattr(bot_settings.settings, "LLM_API_KEY", "chave-groq")

        assert (
            resolve_embeddings(BotConfig(provider="groq", api_key="tambem-groq"))
            is None
        )

    def test_provedor_invalido_na_chave_dedicada_e_ignorado(self, monkeypatch):
        """EMBEDDINGS_PROVIDER errado não sequestra a resolução: cai para o próximo ramo."""
        monkeypatch.setattr(bot_settings.settings, "EMBEDDINGS_API_KEY", "dedicada")
        monkeypatch.setattr(bot_settings.settings, "EMBEDDINGS_PROVIDER", "groq")
        monkeypatch.setattr(bot_settings.settings, "LLM_PROVIDER", "gemini")
        monkeypatch.setattr(bot_settings.settings, "LLM_API_KEY", "chave-gemini")

        alvo = resolve_embeddings(BotConfig())

        assert alvo is not None
        assert alvo.provider == "gemini"


class TestMapasDoProvedor:
    def test_modelo_por_provedor(self):
        assert embeddings.model_id("gemini") == "gemini-embedding-001"
        assert embeddings.model_id("openai") == "text-embedding-3-small"

    def test_provedor_desconhecido_nao_tem_modelo(self):
        """String vazia, não exceção: o caminho sem chave passa provider="" de propósito."""
        assert embeddings.model_id("groq") == ""

    def test_corte_e_mais_apertado_no_gemini(self):
        """Medido contra a API real com chunks realistas — ver o comentário longo em
        embeddings._MAX_DISTANCE antes de mexer. Números tirados de frases soltas dão
        um corte MUITO mais apertado e derrubam pergunta legítima."""
        assert embeddings.max_distance("gemini") == 0.50
        assert embeddings.max_distance("openai") == 0.55
