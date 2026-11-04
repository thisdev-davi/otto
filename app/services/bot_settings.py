"""Carrega/salva a config do agente de IA por tenant, cifrando a api_key em repouso."""

import base64
import hashlib
import json
from uuid import UUID

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embeddings import PROVIDERS
from app.ai.transcription import PROVIDERS_TRANSCRICAO
from app.core.config import settings

# Fallback de encaminhamento, usado só quando o modelo devolve `reply` vazia. O convite no
# fim é proposital: sem ele a mensagem soa como despedida e o cliente para de perguntar,
# embora o agente siga atendendo enquanto nenhum humano assume.
DEFAULT_TRANSFER_MESSAGE = (
    "Perfeito! Já passei seu atendimento para a nossa equipe — um atendente continua com "
    "você por aqui. Enquanto isso, se quiser saber mais alguma coisa, é só me perguntar. 😊"
)


# Aviso enviado ao cliente quando a equipe CONFIRMA a reserva dele. Editável por tenant:
# tom de mensagem é o que cada salão mais quer diferente, e mudar tom não pode exigir
# deploy. Os marcadores são substituídos por `render_confirmacao`.
DEFAULT_CONFIRMACAO_MESSAGE = (
    "Oi, {nome}! 🎉 Sua reserva para o dia {data} está CONFIRMADA com a nossa equipe. "
    "Já pode comemorar! Qualquer dúvida sobre o evento, é só chamar por aqui."
)

# Marcadores aceitos no modelo acima. Fechado de propósito: ver `render_confirmacao`.
CAMPOS_CONFIRMACAO = ("nome", "data", "valor")


def render_confirmacao(modelo: str, valores: dict[str, str]) -> str:
    """Preenche os marcadores do modelo. Substituição literal, NUNCA `str.format`.

    `str.format` sobre texto que o usuário edita é duas armadilhas ao mesmo tempo: uma
    chave que o modelo cita e nós não conhecemos levanta `KeyError` e derruba a
    confirmação, e `{x.__class__.__init__.__globals__}` alcança objetos do processo. Aqui
    o conjunto de marcadores é fechado e tudo que não estiver nele fica como está.

    Marcador sem valor vira string vazia — a frase precisa continuar legível sem ele, e é
    por isso que o modelo padrão não depende de `{valor}`.
    """
    texto = modelo
    for campo in CAMPOS_CONFIRMACAO:
        texto = texto.replace("{" + campo + "}", valores.get(campo, ""))
    # Espaços duplos sobrando de um marcador vazio no meio da frase.
    return " ".join(texto.split())


# Qual cérebro atende. `agente` é o LLM (comportamento histórico); `fluxo` é o roteiro
# determinístico de `app/flow/`, que não faz nenhuma chamada de modelo. Fechado de
# propósito: um valor inesperado no banco tem de cair no agente, não em lugar nenhum.
MODO_AGENTE = "agente"
MODO_FLUXO = "fluxo"
MODOS = (MODO_AGENTE, MODO_FLUXO)


class BotConfig(BaseModel):
    ai_enabled: bool = False
    persona: str = ""  # COMO atender (tom, postura, fluxo)
    knowledge: str = ""  # O QUE a empresa vende (fatos, catálogo)
    provider: str = "stub"
    model: str = ""
    api_key: str = ""  # em claro na memória; cifrada no banco
    transfer_message: str = DEFAULT_TRANSFER_MESSAGE
    confirmacao_message: str = DEFAULT_CONFIRMACAO_MESSAGE
    use_rag: bool = False
    # `ai_enabled` + `modo` formam o controle de 3 estados do painel: desligado, fluxo,
    # agente de IA. Trocar `agente` por `fluxo` é o freio de emergência — custo de LLM cai
    # a zero e o atendimento continua, sem migration e sem restart.
    #
    # O padrão é FLUXO, e isso é decisão de produto: conta nova nasce previsível e sem
    # custo, e ligar a IA passa a ser escolha consciente de quem contrata. Nascer em
    # `agente` significava que qualquer conta criada e esquecida podia gastar com modelo
    # antes de alguém revisar a persona. Tenant que já existe NÃO é migrado.
    modo: str = MODO_FLUXO
    # Expediente do negócio, lido pelo nó `horario` do roteiro. Formato e o porquê de isto
    # NÃO ser o padrão semanal que a agenda recusou estão em `app/flow/horario.py`.
    horario: dict = Field(default_factory=dict)
    # Somente leitura pela API: quem liga é o sincronizador de conteúdo.
    conteudo_gerenciado: bool = False

    @property
    def em_fluxo(self) -> bool:
        return self.modo == MODO_FLUXO


def _fernet() -> Fernet:
    # Chave derivada do SECRET_KEY do app — sem variável de ambiente extra.
    digest = hashlib.sha256(settings.SECRET_KEY.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(plain: str) -> str:
    if not plain:
        return ""
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_secret(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return ""


def mask_secret(plain: str) -> str:
    if not plain:
        return ""
    return f"...{plain[-4:]}" if len(plain) > 4 else "****"


async def load_bot_config(db: AsyncSession, tenant_id: UUID) -> BotConfig:
    row = await db.execute(
        text(
            "SELECT ai_enabled, persona, knowledge, provider, model, api_key, "
            "transfer_message, confirmacao_message, use_rag, conteudo_gerenciado, "
            "modo, horario "
            "FROM bot_settings WHERE tenant_id = :tenant_id"
        ),
        {"tenant_id": str(tenant_id)},
    )
    r = row.fetchone()
    if r is None:
        return BotConfig()
    return BotConfig(
        ai_enabled=r.ai_enabled,
        persona=r.persona,
        knowledge=r.knowledge,
        provider=r.provider,
        model=r.model,
        api_key=decrypt_secret(r.api_key),
        transfer_message=r.transfer_message or DEFAULT_TRANSFER_MESSAGE,
        confirmacao_message=r.confirmacao_message or DEFAULT_CONFIRMACAO_MESSAGE,
        use_rag=r.use_rag,
        modo=r.modo if r.modo in MODOS else MODO_FLUXO,
        horario=dict(r.horario or {}),
        conteudo_gerenciado=r.conteudo_gerenciado,
    )


async def save_bot_config(db: AsyncSession, tenant_id: UUID, cfg: BotConfig) -> None:
    await db.execute(
        text(
            "INSERT INTO bot_settings "
            "(tenant_id, ai_enabled, persona, knowledge, provider, model, api_key, "
            "transfer_message, confirmacao_message, use_rag, modo, horario) "
            "VALUES (:tenant_id, :ai_enabled, :persona, :knowledge, :provider, :model, "
            ":api_key, :transfer_message, :confirmacao_message, :use_rag, :modo, "
            "CAST(:horario AS jsonb)) "
            "ON CONFLICT (tenant_id) DO UPDATE SET "
            "ai_enabled = EXCLUDED.ai_enabled, persona = EXCLUDED.persona, "
            "knowledge = EXCLUDED.knowledge, provider = EXCLUDED.provider, "
            "model = EXCLUDED.model, api_key = EXCLUDED.api_key, "
            "transfer_message = EXCLUDED.transfer_message, "
            "confirmacao_message = EXCLUDED.confirmacao_message, "
            "use_rag = EXCLUDED.use_rag, modo = EXCLUDED.modo, "
            "horario = EXCLUDED.horario, "
            "updated_at = now()"
        ),
        {
            "tenant_id": str(tenant_id),
            "ai_enabled": cfg.ai_enabled,
            "persona": cfg.persona,
            "knowledge": cfg.knowledge,
            "provider": cfg.provider,
            "model": cfg.model,
            "api_key": encrypt_secret(cfg.api_key),
            "transfer_message": cfg.transfer_message,
            "confirmacao_message": cfg.confirmacao_message,
            "use_rag": cfg.use_rag,
            "modo": cfg.modo if cfg.modo in MODOS else MODO_FLUXO,
            "horario": json.dumps(cfg.horario or {}, ensure_ascii=False),
        },
    )
    await db.commit()


class EmbeddingsTarget(BaseModel):
    """Provedor + chave para embedar. O provedor decide modelo, URL e corte de relevância."""

    provider: str
    api_key: str


def resolve_embeddings(cfg: BotConfig) -> EmbeddingsTarget | None:
    """Onde embedar. Painel > ambiente. None quando não há chave utilizável.

    Nem todo provedor de chat sabe embedar: o Groq, por exemplo, não tem endpoint de
    embeddings, então um tenant no Groq cai no ramo de ambiente ou fica sem RAG (a
    busca degrada para texto em retrieval._keyword_search).
    """
    if settings.EMBEDDINGS_API_KEY and settings.EMBEDDINGS_PROVIDER in PROVIDERS:
        return EmbeddingsTarget(
            provider=settings.EMBEDDINGS_PROVIDER, api_key=settings.EMBEDDINGS_API_KEY
        )
    if cfg.provider in PROVIDERS and cfg.api_key:
        return EmbeddingsTarget(provider=cfg.provider, api_key=cfg.api_key)
    if settings.LLM_PROVIDER in PROVIDERS and settings.LLM_API_KEY:
        return EmbeddingsTarget(
            provider=settings.LLM_PROVIDER, api_key=settings.LLM_API_KEY
        )
    return None


def resolve_transcription(cfg: BotConfig) -> tuple[str, str]:
    """Provedor + chave para transcrever. `('', '')` quando o painel não serve.

    Painel primeiro, ao contrário de `resolve_embeddings`: o pedido aqui é justamente
    **não** manter uma segunda conta de IA só para o áudio. Quem configurou o Gemini
    transcreve com aquela chave, e `TRANSCRIPTION_API_KEY` no `.env` deixa de ser
    obrigatória — ela vira o caminho de quem quer separar as duas coisas de propósito.

    O par vazio NÃO significa "não transcreve": significa "o painel não decide", e
    `transcription._resolve` segue para o ambiente — inclusive para a chave do LLM do
    serviço, que é onde ela costuma estar em instalação de um tenant só.
    """
    if cfg.provider in PROVIDERS_TRANSCRICAO and cfg.api_key:
        return cfg.provider, cfg.api_key
    return "", ""
