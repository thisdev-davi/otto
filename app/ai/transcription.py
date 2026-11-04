"""Transcrição de áudio. Dois caminhos, porque as APIs não têm a mesma forma.

Mesma filosofia do `llm.py`: `None` em qualquer falha — áudio sem transcrição vira um
aviso na thread, nunca uma exceção que derruba a task.

**Whisper** (Groq, OpenAI): `POST /audio/transcriptions`, multipart. Groq
(whisper-large-v3-turbo) é o mais barato dos dois.

**Gemini**: NÃO tem `/audio/transcriptions`. A camada compatível com OpenAI do Google
(`_OPENAI_COMPAT_BASE` no `llm.py`) não expõe esse endpoint, e foi por isso que a
transcrição nasceu exigindo uma segunda chave mesmo com o chat no Gemini. O que existe é
outro caminho: os modelos Gemini são multimodais nativos e aceitam áudio como parte do
`generateContent` — manda-se o arquivo inline e pede-se a transcrição em texto.

Vale a pena porque elimina uma conta e uma chave: quem já configurou o Gemini no painel
transcreve com a mesma credencial, e não há um segundo provedor para expirar sem ninguém
notar. O modelo padrão do agente é multimodal e aceita áudio — ver `_DEFAULT_MODEL`.

Duas diferenças que o código precisa respeitar, e não são detalhe:

- **20 MB, não 25.** É o teto do request inline do Gemini (conta o prompt junto), contra
  os 25 MB do Whisper. Passar disso é 400, não truncamento.
- **A saída é texto livre de um LLM**, não o campo `text` de uma API de transcrição. Daí
  o prompt pedir só a transcrição, e `_limpar` remover a moldura conversacional que o
  modelo às vezes acrescenta mesmo assim.
"""

import base64
import logging
import re

import httpx

from app.core.config import settings

log = logging.getLogger("uvicorn.error")

_BASE = {
    "groq": "https://api.groq.com/openai/v1",
    "openai": "https://api.openai.com/v1",
}
_DEFAULT_MODEL = {
    "groq": "whisper-large-v3-turbo",
    "openai": "whisper-1",
    # Acompanha o default do agente em `llm.py::_DEFAULT_MODEL` de propósito: é a mesma
    # chave e a mesma conta. Modelo padrão do Gemini EXPIRA (o 2.5-flash-lite que estava
    # aqui responde 404 para chave criada depois da aposentadoria) — o porquê e a data
    # estão documentados lá, e valem igual para esta linha. Quem escolheu modelo no
    # painel não depende deste default: `_modelo` prefere o dele.
    "gemini": "gemini-3.1-flash-lite",
}
_MAX_BYTES = 25 * 1024 * 1024  # limite das APIs Whisper
# Teto do request inline do Gemini. O limite oficial é 20 MB para o request INTEIRO, e o
# base64 infla o áudio em ~33%, então o corte é sobre o tamanho já codificado.
_MAX_BYTES_GEMINI_B64 = 20 * 1024 * 1024

_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"

# Provedores que sabem transcrever, e a lista canônica do projeto — `bot_settings` a
# importa daqui. Mora neste módulo porque é ele que implementa os dois caminhos: quem
# souber transcrever tem código aqui, e quem não tiver não entra na lista.
PROVIDERS_TRANSCRICAO = ("gemini", "groq", "openai")

# Formatos que o Gemini aceita. O WhatsApp manda OGG/opus, que está na lista.
_GEMINI_MIMES = {
    "audio/wav",
    "audio/mp3",
    "audio/mpeg",
    "audio/aiff",
    "audio/aac",
    "audio/ogg",
    "audio/flac",
}

_PROMPT = (
    "Transcreva o áudio em português do Brasil. "
    "Responda APENAS com a transcrição literal, sem comentar, sem traduzir, "
    "sem resumir e sem aspas em volta. "
    "Se não houver fala audível, responda exatamente: (sem fala)"
)

# O que o modelo devolve quando não há fala — não é transcrição, é ausência dela, e
# gravar isso como conteúdo da mensagem poria a frase na boca do cliente.
_SEM_FALA = "(sem fala)"

# Molduras que o modelo às vezes acrescenta apesar do prompt.
_PREFIXOS = re.compile(
    r"^\s*(aqui est[áa] a transcri[çc][ãa]o[:.]?|transcri[çc][ãa]o[:.]?)\s*",
    re.IGNORECASE,
)


def _limpar(texto: str) -> str | None:
    """Tira a moldura conversacional do LLM. `None` quando não sobra transcrição."""
    limpo = _PREFIXOS.sub("", texto.strip())
    # Aspas em volta do bloco inteiro: o modelo às vezes "cita" a transcrição.
    if len(limpo) >= 2 and limpo[0] in '"“' and limpo[-1] in '"”':
        limpo = limpo[1:-1].strip()
    if not limpo or limpo.lower().startswith(_SEM_FALA):
        return None
    return limpo


# Nome de modelo aceitável. Estreito de propósito: o valor vai DENTRO da URL do Gemini,
# e um "#" ali transforma o resto do caminho em fragmento — o request sai para
# `/v1beta/models/` e volta 404 com corpo vazio, que não diz nada a ninguém.
_MODELO_VALIDO = re.compile(r"^[A-Za-z0-9._:-]+$")


def _modelo(provider: str, do_painel: str = "") -> str:
    """Modelo a usar: `.env` > painel do tenant (só Gemini) > default do provedor.

    A validação existe por causa de um bug real: o `.env.example` trazia
    `TRANSCRIPTION_MODEL=   # vazio = default do provider`, e o python-dotenv só descarta
    comentário depois de um VALOR — com o valor vazio, o comentário inteiro virava o nome
    do modelo. Toda transcrição da instalação falhava, sempre, e o log dizia
    "HTTP 404: " sem corpo. Config torta agora cai no default e diz o que ignorou.

    O modelo do painel entra só no Gemini, e o motivo é a assimetria dos dois caminhos:
    ali o transcritor é o MESMO modelo multimodal do chat, então o que o tenant escolheu
    serve e acompanha as trocas dele. No Whisper não serve — o modelo de chat do painel
    (`llama-3.3-70b`) não existe em `/audio/transcriptions`.
    """
    escolhido = (settings.TRANSCRIPTION_MODEL or "").strip()
    if escolhido and not _MODELO_VALIDO.match(escolhido):
        log.warning(
            "TRANSCRIPTION_MODEL inválido (%.60r) — usando o default do provedor",
            escolhido,
        )
        escolhido = ""
    if not escolhido and provider == "gemini":
        painel = (do_painel or "").strip()
        if painel and _MODELO_VALIDO.match(painel):
            escolhido = painel
    return escolhido or _DEFAULT_MODEL[provider]


def _resolve(api_key: str = "", provider: str = "") -> tuple[str, str]:
    """Provedor + chave a usar. Painel do tenant > chave dedicada > chave do LLM.

    O par vem junto de propósito: chave de um provedor com URL de outro é 401, e o modo
    de falha é silencioso — o áudio só nunca transcreve.

    A ordem é o recurso inteiro. O objetivo é **não precisar de uma segunda conta de IA
    só para o áudio**, e a chave que já existe pode estar em três lugares diferentes:
    no painel do tenant, numa variável dedicada, ou na do próprio LLM. Os três são
    tentados, do mais específico para o mais geral.
    """
    if provider and api_key:
        return provider, api_key

    ambiente = (settings.TRANSCRIPTION_PROVIDER or "groq").strip()
    # Chave dedicada vence: quem a preencheu quis separar as contas de propósito.
    if settings.TRANSCRIPTION_API_KEY:
        return ambiente, settings.TRANSCRIPTION_API_KEY
    if ambiente == settings.LLM_PROVIDER and settings.LLM_API_KEY:
        return ambiente, settings.LLM_API_KEY

    # Último passo, e o que faz o recurso funcionar na configuração real: sem chave
    # dedicada, transcreve com a do LLM se aquele provedor souber transcrever — mesmo
    # que TRANSCRIPTION_PROVIDER aponte para outro lugar.
    #
    # É o caso de quem tem LLM_PROVIDER=gemini com chave e TRANSCRIPTION_PROVIDER=groq
    # SEM chave, que é o default do .env.example. Sem este ramo, o valor default de uma
    # variável que ninguém preencheu vetava a chave que existe, e todo áudio falhava.
    if settings.LLM_PROVIDER in PROVIDERS_TRANSCRICAO and settings.LLM_API_KEY:
        return settings.LLM_PROVIDER, settings.LLM_API_KEY
    return ambiente, ""


async def _transcrever_whisper(
    provider: str, key: str, audio: bytes, mimetype: str, filename: str, model: str
) -> str | None:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_BASE[provider]}/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}"},
                files={"file": (filename, audio, mimetype or "audio/ogg")},
                data={"model": model, "language": "pt"},
                timeout=60.0,
            )
    except Exception as exc:
        log.warning("transcribe %s falhou: %s", provider, exc)
        return None
    if resp.status_code != 200:
        log.warning(
            "transcribe %s (%s) HTTP %s: %s",
            provider,
            model,
            resp.status_code,
            resp.text[:200],
        )
        return None
    text = resp.json().get("text")
    return text.strip() if text else None


async def _transcrever_gemini(
    key: str, audio: bytes, mimetype: str, model: str
) -> str | None:
    """Áudio inline num `generateContent`. Ver o cabeçalho do módulo para o porquê."""
    # `audio/ogg; codecs=opus` chega assim do WhatsApp — o Gemini quer o tipo sem
    # parâmetro. Fora da lista, tenta como audio/ogg em vez de desistir: o que o
    # remetente declara nem sempre corresponde ao arquivo (mesma lição do `SEGURANCA.md`
    # §C2, aqui sem risco de execução porque o destino é a API do Google).
    base_mime = (mimetype or "").split(";")[0].strip().lower()
    if base_mime not in _GEMINI_MIMES:
        base_mime = "audio/ogg"

    dados = base64.b64encode(audio).decode()
    if len(dados) > _MAX_BYTES_GEMINI_B64:
        log.warning("transcribe gemini: áudio de %d bytes excede o inline", len(audio))
        return None

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_GEMINI_BASE}/models/{model}:generateContent",
                # Chave no header, nunca em `?key=` — query string vaza em log de proxy.
                headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                json={
                    "contents": [
                        {
                            "parts": [
                                {
                                    "inline_data": {
                                        "mime_type": base_mime,
                                        "data": dados,
                                    }
                                },
                                {"text": _PROMPT},
                            ]
                        }
                    ],
                    # Determinístico: transcrição não é tarefa criativa, e temperatura
                    # alta aqui inventa palavra em áudio ruim — que é o caso comum no
                    # WhatsApp.
                    "generationConfig": {"temperature": 0.0},
                },
                timeout=120.0,
            )
    except Exception as exc:
        log.warning("transcribe gemini falhou: %s", exc)
        return None
    if resp.status_code != 200:
        log.warning(
            "transcribe gemini (%s) HTTP %s: %s",
            model,
            resp.status_code,
            resp.text[:200],
        )
        return None

    try:
        partes = resp.json()["candidates"][0]["content"]["parts"]
        texto = "".join(p.get("text", "") for p in partes)
    except (KeyError, IndexError, TypeError):
        # Resposta sem candidato é o formato de bloqueio por safety filter — o áudio
        # não vira texto, e insistir não muda isso.
        log.warning("transcribe gemini: resposta sem transcrição")
        return None
    return _limpar(texto)


async def transcribe(
    audio: bytes,
    mimetype: str,
    filename: str = "audio.ogg",
    api_key: str = "",
    provider: str = "",
    model: str = "",
) -> str | None:
    """Transcreve o áudio para texto (pt-BR). `None` em falha.

    `api_key`/`provider`/`model` vêm do painel do tenant (`bot_settings`); vazios, cai no
    ambiente. É o mesmo desenho de `bot_settings.resolve_embeddings`: quem configurou o
    Gemini no painel não precisa de uma segunda chave só para o áudio — nem de saber que
    existe uma variável de modelo separada (ver `_modelo`).
    """
    # O modelo do painel só vale quando foi o painel que decidiu o par provedor+chave —
    # a mesma condição da primeira linha de `_resolve`. Caindo no ambiente, o modelo
    # escolhido no painel é de outra conta.
    do_painel = model if (provider and api_key) else ""
    provider, key = _resolve(api_key, provider)
    if not key:
        return None
    if not audio:
        return None

    if provider == "gemini":
        return await _transcrever_gemini(
            key, audio, mimetype, _modelo("gemini", do_painel)
        )
    if provider in _BASE:
        if len(audio) > _MAX_BYTES:
            return None
        return await _transcrever_whisper(
            provider, key, audio, mimetype, filename, _modelo(provider)
        )
    return None
