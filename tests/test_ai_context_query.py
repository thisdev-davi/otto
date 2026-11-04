"""Com que PERGUNTA a busca da base é feita (sem rede, sem banco).

Ninguém escreve no WhatsApp como quem preenche um campo de busca. A pergunta chega
partida em mensagens — "queria saber sobre o dia 12" / "de agosto" — e cada pedaço
dispara seu próprio turno, porque `bot_orchestrator._handle` não agrupa. Buscando só com
o último pedaço, o turno que importa procura por "de agosto" e não acha nada: o agente
encaminha para um humano com a resposta estando na base o tempo todo.

Daí a segunda tentativa. O que estes testes protegem é o **quando** dela: só depois de a
busca normal voltar vazia. Juntar falas nem sempre ajuda ("oi" antes de "cabe quanta
gente?" é ruído puro), então no turno que já achou trecho não se mexe.
"""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services import ai_context
from app.services.bot_settings import BotConfig

TENANT = uuid4()


@pytest.fixture(autouse=True)
def _sem_agenda(monkeypatch):
    """`build_knowledge` termina em salao_contexto.aumentar, que iria ao banco."""
    monkeypatch.setattr(
        ai_context.salao_contexto,
        "aumentar",
        AsyncMock(side_effect=lambda db, t, knowledge, *a, **k: knowledge),
    )


def _bot() -> BotConfig:
    return BotConfig(use_rag=True, provider="gemini", api_key="k")


def _falas(*textos: str) -> list[dict]:
    return [{"role": "user", "content": t} for t in textos]


def _achou(*_a, **_k) -> list[dict]:
    return [{"title": "FAQ", "content": "cabem 220 sentados", "source_url": "faq"}]


class TestSegundaTentativa:
    async def test_busca_que_acha_nao_tenta_de_novo(self, monkeypatch):
        """O caminho comum não muda: uma busca, com a última mensagem, e acabou."""
        search = AsyncMock(side_effect=_achou)
        monkeypatch.setattr(ai_context.retrieval, "search", search)

        await ai_context.build_knowledge(
            AsyncMock(), TENANT, _bot(), _falas("oi", "cabem quantas pessoas?")
        )

        assert search.await_count == 1
        assert search.await_args.args[2] == "cabem quantas pessoas?"

    async def test_busca_vazia_repete_com_as_falas_juntas(self, monkeypatch):
        """A pergunta partida: 'de agosto' sozinho não acha, com o contexto acha."""
        search = AsyncMock(side_effect=[[], _achou()])
        monkeypatch.setattr(ai_context.retrieval, "search", search)

        knowledge = await ai_context.build_knowledge(
            AsyncMock(),
            TENANT,
            _bot(),
            _falas("queria saber sobre o dia 12", "de agosto"),
        )

        assert search.await_count == 2
        assert search.await_args_list[0].args[2] == "de agosto"
        assert (
            search.await_args_list[1].args[2] == "queria saber sobre o dia 12 de agosto"
        )
        # O trecho recuperado na segunda tentativa chega mesmo ao conhecimento do turno.
        assert "cabem 220 sentados" in knowledge

    async def test_uma_fala_so_nao_repete_a_mesma_busca(self, monkeypatch):
        """Sem fala anterior, a query alternativa seria idêntica — e o embed é pago."""
        search = AsyncMock(return_value=[])
        monkeypatch.setattr(ai_context.retrieval, "search", search)

        await ai_context.build_knowledge(
            AsyncMock(), TENANT, _bot(), _falas("como faço lasanha?")
        )

        assert search.await_count == 1

    async def test_falas_do_bot_ficam_de_fora(self, monkeypatch):
        """A query é o que o CLIENTE perguntou. Repetir a resposta do bot dentro dela
        ancoraria a busca no que já foi dito, em vez do que falta saber."""
        search = AsyncMock(side_effect=[[], []])
        monkeypatch.setattr(ai_context.retrieval, "search", search)
        history = [
            {"role": "user", "content": "tem churrasqueira?"},
            {"role": "assistant", "content": "Tem sim! É de alvenaria 😊"},
            {"role": "user", "content": "e coberta?"},
        ]

        await ai_context.build_knowledge(AsyncMock(), TENANT, _bot(), history)

        assert search.await_args.args[2] == "tem churrasqueira? e coberta?"


class TestFalasRecentes:
    def test_respeita_o_orcamento(self):
        history = _falas("a" * 200, "b" * 200, "c" * 50)
        query = ai_context._falas_recentes(history)
        # "c" + "b" cabem em 300; "a" faria estourar e fica de fora.
        assert query == "b" * 200 + " " + "c" * 50

    def test_a_fala_mais_recente_entra_mesmo_estourando(self):
        """Senão a segunda busca teria MENOS contexto que a primeira — pior que não ter."""
        longa = "x" * (ai_context.MAX_CHARS_QUERY + 500)
        assert ai_context._falas_recentes(_falas("curta", longa)) == longa

    def test_ordem_cronologica(self):
        """'dia 12' + 'de agosto' só quer dizer algo na ordem em que foi escrito."""
        query = ai_context._falas_recentes(_falas("dia 12", "de agosto"))
        assert query == "dia 12 de agosto"

    def test_historico_sem_cliente(self):
        assert (
            ai_context._falas_recentes([{"role": "assistant", "content": "oi"}]) == ""
        )
