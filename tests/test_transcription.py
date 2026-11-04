"""Testes da transcrição de áudio (httpx mockado, sem rede)."""

import httpx
import pytest

from app.ai import transcription


@pytest.fixture(autouse=True)
def _configura_provider(monkeypatch):
    monkeypatch.setattr(transcription.settings, "TRANSCRIPTION_PROVIDER", "groq")
    monkeypatch.setattr(transcription.settings, "TRANSCRIPTION_API_KEY", "test-key")
    monkeypatch.setattr(transcription.settings, "TRANSCRIPTION_MODEL", "")


def _mock_post(monkeypatch, *, status_code=200, payload=None, raises=None):
    async def fake_post(self, *args, **kwargs):
        if raises:
            raise raises
        return httpx.Response(status_code, json=payload or {})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)


async def test_sucesso(monkeypatch):
    _mock_post(monkeypatch, payload={"text": "  olá tudo bem  "})
    result = await transcription.transcribe(b"audio-bytes", "audio/ogg")
    assert result == "olá tudo bem"


async def test_http_erro_retorna_none(monkeypatch):
    _mock_post(monkeypatch, status_code=401, payload={"error": "unauthorized"})
    assert await transcription.transcribe(b"x", "audio/ogg") is None


async def test_excecao_de_rede_retorna_none(monkeypatch):
    _mock_post(monkeypatch, raises=httpx.ConnectError("down"))
    assert await transcription.transcribe(b"x", "audio/ogg") is None


async def test_sem_chave_retorna_none(monkeypatch):
    monkeypatch.setattr(transcription.settings, "TRANSCRIPTION_API_KEY", "")
    monkeypatch.setattr(transcription.settings, "LLM_PROVIDER", "stub")
    monkeypatch.setattr(transcription.settings, "LLM_API_KEY", "")
    assert await transcription.transcribe(b"x", "audio/ogg") is None


async def test_audio_grande_demais_retorna_none(monkeypatch):
    _mock_post(monkeypatch, payload={"text": "não deveria chegar aqui"})
    big = b"x" * (26 * 1024 * 1024)
    assert await transcription.transcribe(big, "audio/ogg") is None


async def test_provider_invalido_retorna_none(monkeypatch):
    monkeypatch.setattr(transcription.settings, "TRANSCRIPTION_PROVIDER", "banana")
    assert await transcription.transcribe(b"x", "audio/ogg") is None


# ─────────────────────────── Gemini ───────────────────────────
#
# O Gemini não usa /audio/transcriptions: manda o áudio inline num generateContent e
# recebe TEXTO LIVRE DE UM LLM de volta. Daí os testes daqui para baixo — o que eles
# protegem é a diferença entre "o modelo respondeu" e "há uma transcrição utilizável".


def _resposta_gemini(texto: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": texto}]}}]}


def _captura_post(monkeypatch, payload):
    """Mocka o POST e devolve a lista onde os kwargs de cada chamada são gravados."""
    chamadas: list[dict] = []

    async def fake_post(self, url, **kwargs):
        chamadas.append({"url": url, **kwargs})
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    return chamadas


async def test_gemini_transcreve_pelo_generate_content(monkeypatch):
    chamadas = _captura_post(monkeypatch, _resposta_gemini("quero reservar o salão"))
    result = await transcription.transcribe(
        b"audio-bytes", "audio/ogg; codecs=opus", api_key="k-gemini", provider="gemini"
    )
    assert result == "quero reservar o salão"

    enviado = chamadas[0]
    assert ":generateContent" in enviado["url"]
    # A chave vai no header, nunca em ?key= — query string vaza em log de proxy.
    assert enviado["headers"]["x-goog-api-key"] == "k-gemini"
    assert "key=" not in enviado["url"]
    # O parâmetro do mimetype do WhatsApp (`; codecs=opus`) é removido: o Gemini
    # recusa o tipo com parâmetro.
    parte = enviado["json"]["contents"][0]["parts"][0]
    assert parte["inline_data"]["mime_type"] == "audio/ogg"


async def test_gemini_remove_moldura_do_modelo(monkeypatch):
    """O prompt pede só a transcrição; o modelo às vezes acrescenta moldura assim mesmo."""
    _captura_post(
        monkeypatch, _resposta_gemini('Aqui está a transcrição: "oi, bom dia"')
    )
    assert (
        await transcription.transcribe(
            b"x", "audio/ogg", api_key="k", provider="gemini"
        )
        == "oi, bom dia"
    )


async def test_gemini_sem_fala_nao_vira_conteudo(monkeypatch):
    """ "(sem fala)" é ausência de transcrição — gravar poria a frase na boca do cliente."""
    _captura_post(monkeypatch, _resposta_gemini("(sem fala)"))
    assert (
        await transcription.transcribe(
            b"x", "audio/ogg", api_key="k", provider="gemini"
        )
        is None
    )


async def test_gemini_resposta_bloqueada_retorna_none(monkeypatch):
    """Safety filter devolve 200 sem `candidates` — não pode virar KeyError na task."""
    _captura_post(monkeypatch, {"promptFeedback": {"blockReason": "SAFETY"}})
    assert (
        await transcription.transcribe(
            b"x", "audio/ogg", api_key="k", provider="gemini"
        )
        is None
    )


async def test_gemini_respeita_o_teto_de_20mb(monkeypatch):
    """O teto do Gemini é sobre o base64, que infla o áudio em ~33% — não sobre os bytes.

    16 MB de áudio passam dos 20 MB codificados. Sem o corte, o request vira 400.
    """
    _captura_post(monkeypatch, _resposta_gemini("não deveria chegar aqui"))
    grande = b"x" * (16 * 1024 * 1024)
    assert (
        await transcription.transcribe(
            grande, "audio/ogg", api_key="k", provider="gemini"
        )
        is None
    )


async def test_chave_do_painel_vence_o_ambiente(monkeypatch):
    """O ponto do recurso: configurou Gemini no painel, não precisa da chave do .env.

    O ambiente aqui está em groq/whisper; mesmo assim a chamada tem de sair pelo Gemini.
    """
    chamadas = _captura_post(monkeypatch, _resposta_gemini("ok"))
    await transcription.transcribe(
        b"x", "audio/ogg", api_key="k-painel", provider="gemini"
    )
    assert ":generateContent" in chamadas[0]["url"]
    assert chamadas[0]["headers"]["x-goog-api-key"] == "k-painel"


# ───────────────── de onde sai a chave (bot_settings) ─────────────────


def _cfg(provider: str, api_key: str):
    from app.services.bot_settings import BotConfig

    return BotConfig(provider=provider, api_key=api_key)


def test_painel_no_gemini_transcreve_com_a_propria_chave():
    from app.services import bot_settings

    assert bot_settings.resolve_transcription(_cfg("gemini", "k1")) == ("gemini", "k1")


def test_provider_que_nao_transcreve_cai_no_ambiente():
    """xai/deepseek/anthropic não têm como transcrever — a chave do painel não serve.

    Devolver o par vazio é o que faz `transcribe` cair no `.env`. Devolver a chave do
    painel aqui produziria 401 num endpoint que aquele provedor nem expõe, e o áudio
    nunca viraria texto — sem erro visível para ninguém.
    """
    from app.services import bot_settings

    assert bot_settings.resolve_transcription(_cfg("xai", "k1")) == ("", "")
    assert bot_settings.resolve_transcription(_cfg("anthropic", "k1")) == ("", "")


def test_painel_sem_chave_cai_no_ambiente():
    from app.services import bot_settings

    assert bot_settings.resolve_transcription(_cfg("gemini", "")) == ("", "")


# ────────── a configuração real: chave do Gemini no .env, não no painel ──────────


async def test_chave_do_llm_transcreve_mesmo_com_outro_provider_no_env(monkeypatch):
    """Regressão do bug real: todo áudio falhava numa instalação perfeitamente normal.

    Cenário exato do `.env.example`: `LLM_PROVIDER=gemini` COM chave, e
    `TRANSCRIPTION_PROVIDER=groq` (o default) SEM chave, porque a doc diz que não
    precisa preencher. O painel também está sem chave — num tenant só, a credencial
    mora no ambiente.

    Antes, os três passos da resolução passavam batido e sobrava provedor sem chave:
    o valor DEFAULT de uma variável que ninguém tocou vetava a chave que existia.
    """
    monkeypatch.setattr(transcription.settings, "TRANSCRIPTION_PROVIDER", "groq")
    monkeypatch.setattr(transcription.settings, "TRANSCRIPTION_API_KEY", "")
    monkeypatch.setattr(transcription.settings, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(transcription.settings, "LLM_API_KEY", "k-do-env")

    chamadas = _captura_post(monkeypatch, _resposta_gemini("boa noite"))
    assert await transcription.transcribe(b"x", "audio/ogg") == "boa noite"
    assert ":generateContent" in chamadas[0]["url"]
    assert chamadas[0]["headers"]["x-goog-api-key"] == "k-do-env"


async def test_chave_dedicada_vence_a_do_llm(monkeypatch):
    """Quem preencheu TRANSCRIPTION_API_KEY quis separar as contas — respeite."""
    monkeypatch.setattr(transcription.settings, "TRANSCRIPTION_PROVIDER", "groq")
    monkeypatch.setattr(transcription.settings, "TRANSCRIPTION_API_KEY", "k-groq")
    monkeypatch.setattr(transcription.settings, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(transcription.settings, "LLM_API_KEY", "k-gemini")

    chamadas = _captura_post(monkeypatch, {"text": "pelo whisper"})
    assert await transcription.transcribe(b"x", "audio/ogg") == "pelo whisper"
    assert "/audio/transcriptions" in chamadas[0]["url"]


async def test_llm_que_nao_transcreve_nao_e_usado(monkeypatch):
    """anthropic/xai não têm caminho de áudio — a chave deles não pode virar tentativa."""
    monkeypatch.setattr(transcription.settings, "TRANSCRIPTION_PROVIDER", "groq")
    monkeypatch.setattr(transcription.settings, "TRANSCRIPTION_API_KEY", "")
    monkeypatch.setattr(transcription.settings, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(transcription.settings, "LLM_API_KEY", "k-anthropic")
    assert await transcription.transcribe(b"x", "audio/ogg") is None


# ─────────────────────────── qual modelo é pedido ───────────────────────────
#
# Bug real: `TRANSCRIPTION_MODEL=   # vazio = default do provider` no `.env.example`.
# O python-dotenv só descarta comentário depois de um VALOR — com o valor vazio, o
# comentário inteiro virou o nome do modelo. O "#" no meio da URL do Gemini transformou
# o resto do caminho em fragmento, e TODA transcrição da instalação voltou 404 com corpo
# vazio. O cliente ouvia "não consegui entender o que você falou" para áudio limpo.


async def test_modelo_do_env_com_comentario_cai_no_default(monkeypatch):
    monkeypatch.setattr(
        transcription.settings, "TRANSCRIPTION_MODEL", "# vazio = default do provider"
    )
    chamadas = _captura_post(monkeypatch, _resposta_gemini("ok"))
    await transcription.transcribe(b"x", "audio/ogg", api_key="k", provider="gemini")
    url = chamadas[0]["url"]
    assert "#" not in url
    assert transcription._DEFAULT_MODEL["gemini"] in url


async def test_modelo_do_painel_manda_no_gemini(monkeypatch):
    """O transcritor do Gemini é o MESMO modelo multimodal do chat — segue o painel.

    É o que impede que um default aposentado no código derrube o áudio de quem já
    escolheu um modelo que funciona.
    """
    chamadas = _captura_post(monkeypatch, _resposta_gemini("ok"))
    await transcription.transcribe(
        b"x", "audio/ogg", api_key="k", provider="gemini", model="gemini-3.1-flash-lite"
    )
    assert "models/gemini-3.1-flash-lite:generateContent" in chamadas[0]["url"]


async def test_modelo_do_painel_nao_vale_no_whisper(monkeypatch):
    """Modelo de chat do painel não existe em `/audio/transcriptions` — seria 400."""
    chamadas = _captura_post(monkeypatch, {"text": "ok"})
    await transcription.transcribe(
        b"x", "audio/ogg", api_key="k", provider="groq", model="llama-3.3-70b-versatile"
    )
    assert chamadas[0]["data"]["model"] == transcription._DEFAULT_MODEL["groq"]


async def test_modelo_do_painel_ignorado_quando_a_chave_vem_do_ambiente(monkeypatch):
    """Painel sem chave = quem decide é o `.env`, e o modelo dele é de outra conta."""
    monkeypatch.setattr(transcription.settings, "TRANSCRIPTION_API_KEY", "k-env")
    monkeypatch.setattr(transcription.settings, "TRANSCRIPTION_PROVIDER", "gemini")
    chamadas = _captura_post(monkeypatch, _resposta_gemini("ok"))
    await transcription.transcribe(
        b"x", "audio/ogg", provider="", api_key="", model="gemini-3.1-flash-lite"
    )
    assert transcription._DEFAULT_MODEL["gemini"] in chamadas[0]["url"]


async def test_modelo_explicito_do_env_vence_o_painel(monkeypatch):
    monkeypatch.setattr(transcription.settings, "TRANSCRIPTION_MODEL", "gemini-2.5-pro")
    chamadas = _captura_post(monkeypatch, _resposta_gemini("ok"))
    await transcription.transcribe(
        b"x", "audio/ogg", api_key="k", provider="gemini", model="gemini-3.1-flash-lite"
    )
    assert "models/gemini-2.5-pro:generateContent" in chamadas[0]["url"]
