"""Testes da lógica pura do agente de IA (sem rede, sem banco)."""

import pytest

from app.ai import llm
from app.ai.chunking import chunk_text
from app.ai.llm import (
    AGENT_MAX_TOKENS,
    AGENT_MAX_TOKENS_RETRY,
    MAX_BUBBLE_CHARS,
    MAX_BUBBLES,
    RespostaLLM,
    _agent_system,
    _extract_json,
    _normalize_decision,
    _stub_turn,
    agent_turn,
    split_bubbles,
)

# Resposta REAL colhida do log do worker no incidente de 31/07: o modelo foi cortado no
# meio da palavra "formalização" e o objeto nunca fechou. Tem 125 chars — abaixo do corte
# de 200 do log — ou seja, isto é a resposta INTEIRA, não um trecho.
TRUNCADA_DO_INCIDENTE = (
    '{\n  "reply": "Oi, Lucas! Tudo bem por aqui. 😊 ||| A data que você '
    "escolheu continua segurada no sistema, mas a formalização"
)


class TestAgentSystem:
    def test_injeta_conhecimento_e_persona(self):
        s = _agent_system("Seja o Otto, caloroso.", "Serviço X: fato.")
        assert "Serviço X: fato." in s
        assert "Seja o Otto, caloroso." in s
        assert "CONHECIMENTO" in s
        # persona por último = prioridade sobre conhecimento
        assert s.index("PERSONA E FLUXO") > s.index("CONHECIMENTO")

    def test_sem_persona_nao_gera_bloco_fantasma(self):
        s = _agent_system("", "")
        assert "=== PERSONA E FLUXO" not in s
        assert "=== CONHECIMENTO" not in s
        assert "consultiva" in s.lower()

    def test_conhecimento_vazio_ou_so_espacos_nao_gera_bloco(self):
        s = _agent_system("Persona.", "   \n  ")
        assert "=== CONHECIMENTO" not in s


class TestContratoDaPersona:
    @pytest.mark.asyncio
    async def test_contexto_nao_sobrepoe_persona_configurada(self, monkeypatch) -> None:
        prompts: list[str] = []

        async def complete(system, *args, **kwargs):
            prompts.append(system)
            return RespostaLLM('{"reply":"Olá","captured":{},"status":"gathering"}')

        monkeypatch.setattr(llm, "_complete", complete)
        await agent_turn(
            "PERSONA_EXCLUSIVA: não faça perguntas extras.",
            {},
            [],
            knowledge="FATO_APROVADO",
            provider="gemini",
            api_key="chave-ficticia",
            post_handoff=True,
            returning=True,
        )
        assert prompts[0].endswith("PERSONA_EXCLUSIVA: não faça perguntas extras.")
        assert "FATO_APROVADO" in prompts[0]
        assert "VOCÊ JÁ ENCAMINHOU" in prompts[0]
        assert "ESTE CLIENTE JÁ FOI ATENDIDO" in prompts[0]

    def test_persona_nao_recebe_ordens_consultivas_conflitantes(self) -> None:
        prompt = _agent_system("Não acrescente perguntas após o contexto mínimo.")
        assert "e faça UMA pergunta" not in prompt
        assert "enquanto a equipe confirma" not in prompt
        assert "CONVIDE" not in prompt
        assert "Responda SOMENTE com um objeto JSON" in prompt
        assert "O QUE É INSTRUÇÃO E O QUE É DADO" in prompt

    @pytest.mark.asyncio
    async def test_captura_parcial_preserva_dados_e_aceita_correcao(
        self, monkeypatch
    ) -> None:
        anterior = {"publico": "Corretor", "cidade_uf": "Vitória/ES"}

        async def complete(*args, **kwargs):
            return RespostaLLM(
                '{"reply":"Certo","captured":{"cidade_uf":"Serra/ES","relato":"Parceria"}}'
            )

        monkeypatch.setattr(llm, "_complete", complete)
        decisao = await agent_turn(
            "p", anterior, [], provider="gemini", api_key="chave-ficticia"
        )
        assert decisao["captured"] == {
            "publico": "Corretor",
            "cidade_uf": "Serra/ES",
            "relato": "Parceria",
        }
        assert anterior == {"publico": "Corretor", "cidade_uf": "Vitória/ES"}


class TestExtractJson:
    def test_json_puro(self):
        assert _extract_json('{"a": 1}') == {"a": 1}

    def test_json_cercado_de_prosa(self):
        raw = 'Claro! Aqui está:\n{"reply": "oi"}\nEspero ter ajudado.'
        assert _extract_json(raw) == {"reply": "oi"}

    def test_sem_json(self):
        assert _extract_json("nenhum objeto aqui") is None

    def test_json_invalido(self):
        assert _extract_json("{quebrado: ") is None

    def test_resposta_truncada_do_incidente(self):
        """A resposta cortada no teto de tokens não vira decisão — vira None.

        Sem `}` não há objeto. O ponto do teste é documentar QUAL entrada real derrubou
        o atendimento; quem tentar "salvar" o JSON fechando chaves à força quebra aqui,
        e deve quebrar: a última bolha estava pela metade ("mas a formalização") e
        mandar meia frase a quem pergunta se a reserva foi confirmada é pior que calar.
        """
        assert _extract_json(TRUNCADA_DO_INCIDENTE) is None


class TestMotivoDeParada:
    """`finish_reason` tem que chegar a quem decide, inclusive QUANDO VEIO TEXTO.

    Era esta a lacuna: resposta cortada voltava pelo caminho feliz, o parse falhava
    depois e o log só dizia "resposta não-JSON". Nada apontava para o teto de tokens.
    """

    @pytest.mark.asyncio
    async def test_length_com_conteudo_parcial_chega_ao_chamador(self, monkeypatch):
        class RespFake:
            status_code = 200

            @staticmethod
            def json() -> dict:
                return {
                    "choices": [
                        {
                            "finish_reason": "length",
                            "message": {"content": TRUNCADA_DO_INCIDENTE},
                        }
                    ]
                }

        class ClientFake:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **kw):
                return RespFake()

        monkeypatch.setattr(llm.httpx, "AsyncClient", lambda *a, **kw: ClientFake())
        r = await llm._call_openai_compatible(
            "gemini", "sys", "user", 1200, "modelo", "chave"
        )
        assert r.motivo_de_parada == "length"
        assert r.texto == TRUNCADA_DO_INCIDENTE, (
            "o texto parcial não pode ser descartado"
        )

    def test_stop_reason_da_anthropic_vira_o_mesmo_vocabulario(self):
        assert llm._STOP_REASON_ANTHROPIC["max_tokens"] == "length"
        assert llm._STOP_REASON_ANTHROPIC["end_turn"] == "stop"


class TestMotivoDoPing:
    """503 de modelo sobrecarregado não pode aparecer como "verifique a chave"."""

    @pytest.mark.asyncio
    async def test_status_http_chega_ao_ping(self, monkeypatch):
        class RespFake:
            status_code = 503
            text = "high demand"

        class ClientFake:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **kw):
                return RespFake()

        monkeypatch.setattr(llm.httpx, "AsyncClient", lambda *a, **kw: ClientFake())
        ok, detalhe = await llm.ping("gemini", "modelo", "chave")
        assert not ok
        assert "503" in detalhe and "chave não foi recusada" in detalhe

    def test_chave_recusada_fala_da_chave(self):
        assert "chave" in llm._motivo_do_ping("gemini", "m", "http_401")


class TestOrcamentoDoTurno:
    """O caso do Lucas: cortou no teto, tem que re-tentar com teto MAIOR."""

    @pytest.mark.asyncio
    async def test_re_tenta_com_orcamento_maior_apos_length(self, monkeypatch):
        orcamentos: list[int] = []

        async def fake_complete(system, user, max_tokens, prov, mdl, key, **kw):
            orcamentos.append(max_tokens)
            if len(orcamentos) == 1:
                return RespostaLLM(TRUNCADA_DO_INCIDENTE, "length")
            return RespostaLLM(
                '{"reply": "Está confirmada!", "status": "gathering"}', "stop"
            )

        monkeypatch.setattr(llm, "_complete", fake_complete)
        monkeypatch.setattr(llm.asyncio, "sleep", _nao_espera)

        decisao = await agent_turn(
            persona="p",
            state={},
            history=[],
            knowledge="k",
            provider="gemini",
            api_key="x",
        )
        assert decisao is not None, "a 2ª tentativa tinha resposta válida"
        assert decisao["reply"] == "Está confirmada!"
        assert orcamentos == [AGENT_MAX_TOKENS, AGENT_MAX_TOKENS_RETRY]

    @pytest.mark.asyncio
    async def test_sem_length_o_orcamento_nao_sobe(self, monkeypatch):
        """Prosa sem JSON não é problema de teto — subir o orçamento só gastaria mais."""
        orcamentos: list[int] = []

        async def fake_complete(system, user, max_tokens, prov, mdl, key, **kw):
            orcamentos.append(max_tokens)
            return RespostaLLM("desculpe, não entendi", "stop")

        monkeypatch.setattr(llm, "_complete", fake_complete)
        monkeypatch.setattr(llm.asyncio, "sleep", _nao_espera)

        assert (
            await agent_turn(
                persona="p",
                state={},
                history=[],
                knowledge="k",
                provider="gemini",
                api_key="x",
            )
            is None
        )
        assert orcamentos == [AGENT_MAX_TOKENS, AGENT_MAX_TOKENS]

    @pytest.mark.asyncio
    async def test_duas_falhas_devolvem_none(self, monkeypatch):
        """Contrato do módulo: a IA enriquece, não bloqueia. Falhou, devolve None."""

        async def fake_complete(*a, **kw):
            return RespostaLLM(None, None)

        monkeypatch.setattr(llm, "_complete", fake_complete)
        monkeypatch.setattr(llm.asyncio, "sleep", _nao_espera)
        assert (
            await agent_turn(
                persona="p",
                state={},
                history=[],
                knowledge="k",
                provider="gemini",
                api_key="x",
            )
            is None
        )


async def _nao_espera(_segundos: float) -> None:
    """Substitui o backoff entre tentativas para o teste não pagar 0,8s."""


class TestNormalizeDecision:
    def test_sanitiza_status_inventado(self):
        d = _normalize_decision({"status": "banana"})
        assert d["status"] == "gathering"

    def test_captured_lista_vira_dict_vazio(self):
        d = _normalize_decision({"captured": ["a", "b"]})
        assert d["captured"] == {}

    def test_captured_valores_viram_string(self):
        d = _normalize_decision({"captured": {"idade": 30}})
        assert d["captured"] == {"idade": "30"}

    def test_sentiment_e_priority_invalidos(self):
        d = _normalize_decision({"sentiment": "furioso", "priority": "urgentissimo"})
        assert d["sentiment"] == "neutral"
        assert d["priority"] == "normal"

    def test_decisao_valida_passa_intacta(self):
        d = _normalize_decision(
            {
                "reply": "oi",
                "captured": {"nome": "Ana"},
                "status": "ready_to_handoff",
                "summary": "resumo",
                "priority": "high",
                "sentiment": "positive",
            }
        )
        assert d["reply"] == "oi"
        assert d["status"] == "ready_to_handoff"
        assert d["priority"] == "high"
        assert d["sentiment"] == "positive"


class TestStubTurn:
    def test_primeiras_mensagens_coletam(self):
        history = [{"role": "user", "content": "quero um orçamento"}]
        d = _stub_turn({}, history, max_turns_reached=False)
        assert d["status"] == "gathering"
        assert d["reply"]
        assert "detalhe_1" in d["captured"]

    def test_apos_3_turnos_encaminha(self):
        history = [{"role": "user", "content": f"msg {i}"} for i in range(3)]
        d = _stub_turn({}, history, max_turns_reached=False)
        assert d["status"] == "ready_to_handoff"
        assert d["summary"]

    def test_limite_forcado_encaminha(self):
        history = [{"role": "user", "content": "oi"}]
        d = _stub_turn({"nome": "Ana"}, history, max_turns_reached=True)
        assert d["status"] == "ready_to_handoff"
        assert "nome=Ana" in d["summary"]


class TestSplitBubbles:
    def test_separa_por_marcador(self):
        assert split_bubbles("oi ||| tudo bem? |||") == ["oi", "tudo bem?"]

    def test_sem_marcador_uma_bolha(self):
        assert split_bubbles("mensagem única") == ["mensagem única"]

    def test_vazio(self):
        assert split_bubbles("") == []

    def test_dois_pipes_tambem_separam(self):
        """Regressão de teste real: o modelo escreveu `||` e o marcador foi ENTREGUE ao
        cliente no meio da frase, porque o split literal só reconhecia `|||`."""
        bolhas = split_bubbles(
            "Seria ótimo ter uma visão geral, né? || Como a agenda muda"
        )
        assert bolhas == ["Seria ótimo ter uma visão geral, né?", "Como a agenda muda"]

    def test_nenhuma_variacao_de_pipe_vaza_para_o_cliente(self):
        for marcador in ("||", "|||", "||||", "|||||"):
            for bolha in split_bubbles(f"primeira {marcador} segunda"):
                assert "|" not in bolha, f"pipe vazou com {marcador!r}"

    def test_pipe_solto_nas_pontas_e_removido(self):
        assert split_bubbles("| oi |") == ["oi"]

    def test_bolha_longa_e_quebrada_em_fim_de_frase(self):
        frase = "Esta é uma frase de tamanho médio para o teste. "
        bolhas = split_bubbles(frase * 20)  # ~960 chars, sem marcador nenhum
        assert len(bolhas) > 1
        for bolha in bolhas:
            assert len(bolha) <= MAX_BUBBLE_CHARS
        # Quebra em fim de frase, não no meio de uma palavra.
        assert bolhas[0].endswith(".")

    def test_teto_de_bolhas_por_turno(self):
        bolhas = split_bubbles(" ||| ".join(f"bolha {i}" for i in range(12)))
        assert len(bolhas) == MAX_BUBBLES


class TestChunkText:
    def test_texto_curto_um_chunk(self):
        assert chunk_text("catálogo pequeno.") == ["catálogo pequeno."]

    def test_texto_longo_fatia_com_overlap(self):
        text = ("Frase de teste sobre o serviço. " * 200).strip()
        chunks = chunk_text(text, max_chars=500, overlap=100)
        assert len(chunks) > 1
        assert all(len(c) <= 500 for c in chunks)
        # overlap: o começo do chunk seguinte aparece no fim do anterior
        assert chunks[1][:50] in chunks[0] + chunks[1]

    def test_normaliza_espacos(self):
        chunks = chunk_text("a   b\n\nc\t d")
        assert chunks == ["a b c d"]

    def test_vazio(self):
        assert chunk_text("   ") == []


class TestEmojiNaoQuebra:
    """Emoji não é um caractere só, e o corte de bolha não pode parti-lo.

    "👨‍👩‍👧" são 5 code points em Python (dois deles ZWJ invisíveis) e "👋🏽" são 2.
    Cortar no meio entrega ao cliente um ZWJ solto ou um quadradinho de tom de pele
    órfão. O risco cresceu quando o prompt passou a incentivar emoji.
    """

    # (nome, emoji) — família por ZWJ, tom de pele, bandeira (par de indicadores),
    # coração com variation selector.
    CASOS = [
        ("familia", "\U0001f468‍\U0001f469‍\U0001f467"),
        ("tom_de_pele", "\U0001f44b\U0001f3fd"),
        ("bandeira", "\U0001f1e7\U0001f1f7"),
        ("coracao", "❤️"),
    ]

    def test_corte_duro_nao_parte_emoji(self):
        for nome, emoji in self.CASOS:
            # Sem espaço nenhum, o corte cai no fallback duro — e o emoji fica bem
            # na fronteira dos 320 caracteres.
            texto = "a" * (MAX_BUBBLE_CHARS - 2) + emoji + "b" * 400
            for bolha in split_bubbles(texto):
                assert not bolha.startswith("‍"), f"{nome}: bolha abre com ZWJ"
                assert not bolha.endswith("‍"), f"{nome}: bolha termina com ZWJ"
                assert not bolha.startswith("️"), f"{nome}: variation selector órfão"
                # Tom de pele órfão no início vira um quadradinho de cor.
                assert not (bolha and 0x1F3FB <= ord(bolha[0]) <= 0x1F3FF), (
                    f"{nome}: tom de pele órfão"
                )

    def test_emoji_intacto_apos_o_corte(self):
        emoji = "\U0001f468‍\U0001f469‍\U0001f467"
        texto = "a" * (MAX_BUBBLE_CHARS - 2) + emoji + "b" * 400
        # O emoji tem que sobreviver inteiro em alguma das bolhas.
        assert any(emoji in bolha for bolha in split_bubbles(texto))

    def test_bolha_nao_abre_com_emoji_da_frase_anterior(self):
        """Regressão de conversa real: o cliente pediu uma data ocupada e recebeu uma
        bolha que era só "😔" com a oferta das outras datas embaixo. O emoji fechava a
        frase anterior e o corte em ". " caiu antes dele."""
        texto = (
            "Oi, Lucas! Deixa eu conferir aqui pra você... o dia 6 de agosto, "
            "infelizmente, já está reservado por outro cliente e não consigo mais "
            "oferecer essa data. Sei que não era a resposta que você esperava, viu. 😔"
            "\n\nMas tenho outras datas próximas disponíveis, como o dia 7 de agosto "
            "(R$ 2.200,00) ou o dia 8 de agosto (R$ 3.500,00). Alguma dessas "
            "funcionaria para você?"
        )
        assert len(texto) > MAX_BUBBLE_CHARS  # o corte só existe acima do teto
        bolhas = split_bubbles(texto)
        assert len(bolhas) > 1
        assert bolhas[0].endswith("😔")
        assert bolhas[1].startswith("Mas tenho")

    def test_emoji_nunca_abre_bolha(self):
        """Vale para qualquer posição do corte, não só a que apareceu em produção.

        O enchimento varre o fim de frase por toda a faixa de corte, inclusive o trecho
        em que o emoji já não cabe na bolha — onde o ajuste tem de escolher entre jogar
        a frase inteira para a próxima e estourar o teto, nunca deixar o emoji sozinho.
        """
        for enchimento in range(120, 305):
            texto = (
                "a" * enchimento + " fecho a frase aqui. 😔 " + "b" * MAX_BUBBLE_CHARS
            )
            for bolha in split_bubbles(texto):
                assert not bolha.startswith("😔"), f"órfão com enchimento={enchimento}"

    def test_texto_normal_com_emoji_continua_dividindo(self):
        frase = "Que alegria planejar isso com você 😊 vamos deixar tudo lindo. "
        bolhas = split_bubbles(frase * 8)
        assert len(bolhas) > 1
        assert all(len(b) <= MAX_BUBBLE_CHARS for b in bolhas)


class TestRetentativaLenta:
    """Provedor lento na 1ª tentativa não ganha a 2ª: o cliente esperaria o dobro."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("duracao,chamadas", [(12.0, 1), (0.5, 2)])
    async def test_so_re_tenta_se_a_primeira_foi_rapida(
        self, monkeypatch, duracao, chamadas
    ):
        feitas: list[int] = []

        async def fake_complete(*a, **kw):
            feitas.append(1)
            return RespostaLLM(None)

        relogio = iter([0.0, duracao, 100.0, 100.0 + duracao])
        monkeypatch.setattr(llm, "_complete", fake_complete)
        monkeypatch.setattr(llm.asyncio, "sleep", _nao_espera)
        # `llm.monotonic`, não `time.monotonic`: o relógio do event loop é o mesmo.
        monkeypatch.setattr(llm, "monotonic", lambda: next(relogio))

        decisao = await agent_turn(
            persona="p",
            state={},
            history=[],
            knowledge="k",
            provider="gemini",
            api_key="x",
        )
        assert decisao is None
        assert len(feitas) == chamadas


class TestReplacementContracts:
    def test_truncated_outer_json_does_not_accept_inner_object(self):
        assert _extract_json('{"reply":"unfinished","captured":{"nome":"Ana"}') is None

    def test_two_objects_do_not_get_joined_by_greedy_braces(self):
        assert _extract_json('{"reply":"first"} extra {"reply":"second"}') == {
            "reply": "first"
        }

    def test_non_scalar_status_cannot_crash_normalization(self):
        assert (
            _normalize_decision({"status": ["gathering"], "priority": {}})["status"]
            == "gathering"
        )

    def test_agenda_is_explicit_source_even_with_persona(self):
        prompt = _agent_system("Use um estilo objetivo", "Valor antigo: R$ 10")
        assert "exclusivamente os resultados da agenda" in prompt
        assert "Conhecimento e persona não substituem a agenda" in prompt

    @pytest.mark.asyncio
    async def test_rate_limit_does_not_trigger_paid_immediate_retry(self, monkeypatch):
        calls = []

        async def complete(*args, **kwargs):
            calls.append(1)
            return RespostaLLM(None, "http_429")

        monkeypatch.setattr(llm, "_complete", complete)
        assert (
            await agent_turn("persona", {}, [], provider="gemini", api_key="fake")
            is None
        )
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_anthropic_http_errors_reach_ping_without_sdk(self, monkeypatch):
        async def post(*args, **kwargs):
            return None, "http_403"

        monkeypatch.setattr(llm, "_post_json", post)
        response = await llm._call_anthropic("system", "user", 100, "model", "fake")
        assert response == RespostaLLM(None, "http_403")
