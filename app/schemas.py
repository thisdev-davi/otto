from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import (
    Channel,
    ContentType,
    ConversationStatus,
    MessageDirection,
    MessageStatus,
)


class MessageSchema(BaseModel):
    id: UUID
    direction: MessageDirection
    content_type: ContentType
    content: str | None = None
    status: MessageStatus | None = None
    created_at: datetime
    is_internal: bool = (
        False  # nota interna (triagem do bot) — não veio do/foi ao cliente
    )
    is_bot: bool = False  # mensagem enviada pelo agente de IA
    media_url: str | None = None  # /media/{id} quando há mídia baixada
    transcript_status: str | None = None  # pending | done | failed (áudio)
    # Mídia recebida que um admin apagou: o arquivo saiu, a mensagem fica (banco de mídia).
    media_removed_at: datetime | None = None
    media_removed_by: str | None = None
    # Apagada ou editada depois de entregue (`services/revisao_mensagem.py`). A linha
    # nunca some: `deleted_by` é o nome de quem apagou pelo Otto, ou None se veio do canal.
    deleted_at: datetime | None = None
    deleted_by: str | None = None
    edited_at: datetime | None = None
    original_content: str | None = None


class ConversationSchema(BaseModel):
    id: UUID
    tenant_id: UUID
    contact_id: UUID
    contact_name: str | None = None
    contact_external_id: str | None = None
    # Telefone real, quando conhecido. Separado do `contact_external_id`, que é endereço
    # do canal e pode ser um identificador opaco (`@lid`) sem nenhum dígito discável.
    contact_phone: str | None = None
    contact_channel: str | None = None
    # Caminho + assinatura de curta duração da foto de perfil, prontos do servidor
    # (`services/avatars.py`). `None` é o caso comum: contato sem foto visível.
    contact_avatar_url: str | None = None
    status: ConversationStatus
    assigned_user_id: UUID | None = None
    priority: str = "normal"
    tags: list[str] = []
    last_message_preview: str | None = None
    last_message_at: datetime | None = None
    unread_count: int
    created_at: datetime


class ConversationDetailSchema(ConversationSchema):
    messages: list[MessageSchema] = []
    # Por que o bot não respondeu no último turno (bot_orchestrator.SkipReason).
    # Só no detalhe: na lista seria uma coluna a mais por conversa sem ninguém para ler.
    bot_skip_reason: str | None = None
    # Memória do cliente entre conversas (contacts.profile): dados já coletados e resumo
    # do último atendimento.
    contact_profile: dict = {}


class ConversationListSchema(BaseModel):
    conversations: list[ConversationSchema]
    total: int
    limit: int
    offset: int


class TransferRequestSchema(BaseModel):
    to_user_id: UUID


class StatusUpdateSchema(BaseModel):
    status: ConversationStatus


class SendMessageSchema(BaseModel):
    content: str
    content_type: ContentType = ContentType.TEXT


class SuggestReplySchema(BaseModel):
    suggestion: str | None = None
    reason: str | None = None  # "ia_indisponivel" quando não há sugestão


class ChannelConnectionSchema(BaseModel):
    id: UUID
    channel: Channel
    label: str | None = (
        None  # rótulo não-secreto (instância do WhatsApp, usuário de e-mail)
    )
    is_active: bool
    created_at: datetime


class ChannelConnectionListSchema(BaseModel):
    connections: list[ChannelConnectionSchema]


class ChannelStatusSchema(BaseModel):
    state: str  # connected | connecting | qr_pending | disconnected | error
    qr_code: str | None = None
    detail: str | None = None


class CreateWhatsAppConnectionSchema(BaseModel):
    """`instance` vira parte do CAMINHO das URLs da API do Evolution.

    O provider interpola este valor em nove chamadas (`/instance/connect/{instance}`,
    `/instance/delete/{instance}`, `/webhook/set/{instance}`…), e o httpx normaliza
    segmentos `..` — então `../../instance/delete/salao-do-vizinho` deixava de ser um
    nome e virava outra rota, chamada com a chave MASTER do Evolution, que é
    compartilhada por todos os tenants.

    O padrão abaixo é o ponto único onde isso se fecha: sem `.`, sem `/`, sem `%`, nada
    que a normalização de URL possa reinterpretar. Não afrouxe sem reler `whatsapp.py`.
    """

    instance: str = Field(pattern=r"^[A-Za-z0-9_-]{3,64}$")


# ─────────────────────────── agente de IA / RAG ───────────────────────────


class BotSettingsSchema(BaseModel):
    ai_enabled: bool
    persona: str
    knowledge: str
    provider: str  # stub | anthropic | openai | xai | gemini | deepseek | groq
    model: str
    api_key_masked: str  # nunca devolvemos a chave em claro
    transfer_message: str
    confirmacao_message: str
    use_rag: bool
    # Derivado, não editável: existe chave para gerar embeddings? O front usa isso
    # para bloquear a ingestão ANTES de o usuário escrever o documento, em vez de
    # deixá-lo descobrir no 422. Expõe só o fato de existir, nunca a chave.
    embeddings_ready: bool
    # Qual provedor vai embedar (gemini | openai), "" quando nenhum resolve. Serve
    # para a mensagem de bloqueio dizer o que fazer. Provedor, nunca a chave.
    embeddings_provider: str = ""
    # Persona e conhecimento vêm de `conteudo/*.md` e são gravados pelo sincronizador.
    # Só de leitura: quem liga é o script, nunca esta rota.
    conteudo_gerenciado: bool = False
    # Qual cérebro atende: 'agente' (LLM) ou 'fluxo' (roteiro determinístico). Junto
    # com `ai_enabled` forma o controle de 3 estados do painel.
    modo: str = "agente"
    # Expediente do negócio, lido pelo nó `horario` do roteiro. Ver app/flow/horario.py.
    horario: dict = {}
    # O tenant já publicou um roteiro? O front usa para não deixar ligar o modo fluxo
    # sem ter o que rodar — descobrir isso pelo silêncio do bot é o pior caminho.
    fluxo_publicado: bool = False


class BotSettingsUpdateSchema(BaseModel):
    ai_enabled: bool = False
    persona: str = ""
    knowledge: str = ""
    provider: str = "stub"
    model: str = ""
    api_key: str | None = None  # None = mantém a chave atual
    transfer_message: str | None = None
    confirmacao_message: str | None = None
    use_rag: bool = False
    modo: str = "agente"
    horario: dict | None = None  # None = mantém o expediente atual


class BotPingSchema(BaseModel):
    ok: bool
    detail: str


class KbSourceSchema(BaseModel):
    source: str | None
    title: str | None
    chunks: int
    embedding_model: str | None = None
    # True = embedada com outro modelo, então a busca não a enxerga. Precisa de nova
    # sincronização para voltar a valer.
    stale: bool = False
    created_at: datetime | None = None


class KbSourceListSchema(BaseModel):
    sources: list[KbSourceSchema]


class RagAnswerInSchema(BaseModel):
    question: str
    top_k: int = 5


class RagSourceSchema(BaseModel):
    title: str | None
    source_url: str | None
    score: float


class RagAnswerSchema(BaseModel):
    answer: str
    sources: list[RagSourceSchema]


# ─────────────────────────── relatórios / usuários ───────────────────────────


class UserSchema(BaseModel):
    id: UUID
    full_name: str | None = None
    email: str
    # "ADMIN" | "AGENT". O front usa para esconder relatórios, configuração da IA e
    # canais de quem é AGENT — mas isso é conveniência de UI: quem manda é a guarda
    # `require_admin` no backend. Esconder botão não é controle de acesso.
    role: str = "AGENT"


class UserListSchema(BaseModel):
    users: list[UserSchema]


# ─────────────────────────── equipe (admin) ───────────────────────────


class MemberSchema(BaseModel):
    """Membro visto pelo admin — inclui inativos, ao contrário de UserSchema."""

    id: UUID
    email: str
    full_name: str | None = None
    role: str
    is_active: bool
    created_at: datetime


class MemberListSchema(BaseModel):
    members: list[MemberSchema]


class MemberUpdateSchema(BaseModel):
    role: Literal["ADMIN", "AGENT"] | None = None
    is_active: bool | None = None


class InviteCreateSchema(BaseModel):
    email: str
    full_name: str | None = None
    role: Literal["ADMIN", "AGENT"] = "AGENT"


class InviteSchema(BaseModel):
    id: UUID
    email: str
    full_name: str | None = None
    role: str
    expires_at: datetime
    created_at: datetime


class InviteCreatedSchema(BaseModel):
    """Resposta da criação: o token em claro aparece SÓ aqui, uma única vez.

    O backend devolve o token, não a URL: quem sabe a origem do front é o front.
    """

    invite: InviteSchema
    token: str


class InviteListSchema(BaseModel):
    invites: list[InviteSchema]


class InvitePublicSchema(BaseModel):
    """O que a tela pública de aceite pode ver — nada além do necessário."""

    email: str
    full_name: str | None = None
    role: str
    tenant_name: str


class InviteAcceptSchema(BaseModel):
    password: str
    full_name: str | None = None


class ReportOverviewSchema(BaseModel):
    open_count: int
    unclaimed_count: int
    resolved_count: int
    avg_resolution_seconds: int | None = None
    avg_first_response_seconds: int | None = None


class VolumePointSchema(BaseModel):
    day: str
    channel: str
    direction: str
    total: int


class ReportVolumeSchema(BaseModel):
    points: list[VolumePointSchema]


class AgentStatSchema(BaseModel):
    user_id: UUID
    name: str
    claimed: int
    resolved: int
    messages_sent: int


class ReportAgentsSchema(BaseModel):
    agents: list[AgentStatSchema]


class ReportBotSchema(BaseModel):
    triaged: int
    handoffs: int
    failures: int
    resolved_by_bot: int
    closed_by_inactivity: int = 0


# ─────────────────────────── respostas rápidas ───────────────────────────


class QuickReplySchema(BaseModel):
    id: UUID
    shortcut: str
    title: str
    content: str


class QuickReplyListSchema(BaseModel):
    quick_replies: list[QuickReplySchema]


class QuickReplyInSchema(BaseModel):
    shortcut: str
    title: str
    content: str


# ─────────────────────── salão: agenda, reservas e espaço ───────────────────────
# Acrescentado no FIM de propósito (`docs/produto/PLATAFORMA.md` §1): é a região do arquivo que a base
# não mexe, então este bloco do derivado não vira conflito em toda atualização.


class ReservaResumoSchema(BaseModel):
    """A reserva que ocupa uma data, como o calendário precisa vê-la."""

    id: UUID
    status: str  # pre_reservada | confirmada
    nome_cliente: str | None = None
    telefone: str | None = None
    expira_em: datetime | None = None
    conversation_id: UUID | None = None
    criada_por: str  # bot | humano


class AgendaDataSchema(BaseModel):
    """Um dia do calendário, já RESOLVIDO por `salao_agenda.resolver`.

    A tela nunca recompõe essa conta — ela chega pronta da mesma função que decide o que o
    agente pode vender. Duas implementações da mesma regra divergiriam, e a divergência
    apareceria como preço errado dito ao cliente.
    """

    data: date
    # None é "valor não cadastrado", nunca "de graça" — o agente trata como desconhecido.
    preco_centavos: int | None = None
    bloqueada: bool = False
    observacao: str | None = None
    reserva: ReservaResumoSchema | None = None
    # Existe linha em `salao_datas` para este dia? Distingue "não cadastrei" de
    # "cadastrei e não pus valor", que a tela pinta e o agente responde de formas
    # diferentes.
    cadastrada: bool = False
    # Por que está (ou não) à venda — o vocabulário fechado de `salao_agenda.MOTIVO_*`.
    motivo: str = "sem_cadastro"
    livre: bool = False


class AgendaListSchema(BaseModel):
    datas: list[AgendaDataSchema]


class AgendaDataInSchema(BaseModel):
    preco_centavos: int | None = None
    bloqueada: bool = False
    observacao: str | None = None


class AgendaLoteInSchema(BaseModel):
    """Exceção em faixa: "dezembro mais caro", "a semana da reforma bloqueada"."""

    de: date
    ate: date
    # ISO: 1=segunda … 7=domingo. Vazio = todos os dias do intervalo.
    dias_da_semana: list[int] = []
    preco_centavos: int | None = None
    # None **não mexe** no bloqueio existente. Era `False` e todo lote de preço
    # desbloqueava em silêncio as datas bloqueadas do intervalo.
    bloqueada: bool | None = None


class AgendaLoteResultSchema(BaseModel):
    datas_afetadas: int


class ReservaSchema(BaseModel):
    id: UUID
    data: date
    status: str
    contact_id: UUID | None = None
    conversation_id: UUID | None = None
    criada_por: str
    expira_em: datetime | None = None
    nome_cliente: str | None = None
    telefone: str | None = None
    observacao: str | None = None


class ReservaListSchema(BaseModel):
    reservas: list[ReservaSchema]


class ReservaDetalheSchema(BaseModel):
    """O que o dono precisa ver antes de confirmar ou cancelar uma pré-reserva.

    Montado a partir do que o turno do agente já grava (`salao_resumo`): nenhum campo aqui
    exigiu coluna nova. `dados_coletados` é dicionário livre porque a persona é quem decide
    o que perguntar — um salão pede convidados, outro pede tipo de evento.
    """

    reserva_id: UUID
    data: date
    status: str
    criada_por: str
    expira_em: datetime | None = None
    criada_em: datetime
    nome_cliente: str | None = None
    contato_nome: str | None = None
    telefone: str | None = None
    observacao: str | None = None
    preco_centavos: int | None = None
    conversation_id: UUID | None = None
    dados_coletados: dict[str, str] = {}
    nota_interna: str | None = None
    # Aviso pronto para o cliente, renderizado no SERVIDOR (o front nunca monta o texto —
    # duas implementações da mesma frase divergem e a divergência vira mensagem errada no
    # WhatsApp de alguém). `None` quando não há a quem enviar.
    mensagem_confirmacao: str | None = None
    # False quando a reserva não tem contato/conversa — reserva lançada à mão no painel.
    # O diálogo mostra o motivo em vez de esconder o botão.
    pode_avisar: bool = False


class ConfirmarReservaRequest(BaseModel):
    """Corpo do confirmar. Sem `mensagem`, confirma e NÃO avisa o cliente.

    Opcional de propósito: avisar é decisão de quem confirma, tomada depois de ler o
    texto. Um default que envia transformaria qualquer clique no calendário em mensagem
    no WhatsApp de um cliente real, sem volta.
    """

    mensagem: str | None = None


class ConfirmarReservaResponse(BaseModel):
    reserva: ReservaSchema
    # Falso quando o aviso não saiu — inclusive quando nem foi pedido. A confirmação vale
    # de qualquer jeito; o front avisa o atendente para reenviar pela Inbox.
    aviso_enviado: bool = False


class ReservaInSchema(BaseModel):
    data: date
    nome_cliente: str | None = None
    telefone: str | None = None
    observacao: str | None = None
    # O atendente fecha por telefone e já registra confirmado; a pré-reserva é do agente.
    confirmar: bool = True


class FotoEspacoSchema(BaseModel):
    id: UUID
    titulo: str | None = None
    mimetype: str
    tamanho_bytes: int
    ordem: int
    ativa: bool
    created_at: datetime
    # Caminho + assinatura de curta duração. Vem pronto do servidor porque só ele tem a
    # chave — o front não monta mais URL de mídia com o JWT do usuário.
    arquivo_url: str


class FotoEspacoListSchema(BaseModel):
    fotos: list[FotoEspacoSchema]


class FotoEspacoUpdateSchema(BaseModel):
    titulo: str | None = None
    ordem: int | None = None
    ativa: bool | None = None


# ─────────────────────────── perfil do contato ───────────────────────────


class ContactNoteSchema(BaseModel):
    """Uma anotação da equipe sobre o cliente.

    `autor` é `None` quando quem escreveu saiu da equipe (`user_id` é `SET NULL`) — a
    nota fica, porque o valor dela está no texto. `autor_id` é o que a tela usa para
    decidir se mostra o botão de apagar.
    """

    id: UUID
    texto: str
    criada_em: datetime
    autor_id: UUID | None = None
    autor: str | None = None


class ContactNoteInSchema(BaseModel):
    texto: str


class ContactConversaSchema(BaseModel):
    conversation_id: UUID
    status: str
    priority: str
    criada_em: datetime
    ultima_mensagem_em: datetime | None = None
    atendente: str | None = None


class ContactReservaSchema(BaseModel):
    """Agendamento no histórico do cliente — inclui cancelado e expirado.

    Difere de `ReservaSchema` por não trazer `nome_cliente`/`telefone`: no perfil eles
    seriam a repetição do cabeçalho em cada linha da lista.
    """

    id: UUID
    data: date
    status: str
    criada_por: str
    expira_em: datetime | None = None
    observacao: str | None = None
    conversation_id: UUID | None = None


class ContactPerfilSchema(BaseModel):
    """O painel do cliente inteiro, montado por `services/contatos.py::perfil`.

    Nenhum campo aqui exigiu coluna nova além de `contact_notes`: o resto é junção do que
    `contacts`, `contacts.profile`, `conversations` e `salao_reservas` já gravam.
    """

    contact_id: UUID
    nome: str | None = None
    telefone: str | None = None
    canal: str
    # Foto do canal, já assinada. Ver `services/avatars.py`.
    avatar_url: str | None = None
    cliente_desde: datetime
    # Quantas linhas de `contacts` são esta pessoa. > 1 significa que o endereço do canal
    # mudou e o histórico estava partido — a tela avisa em vez de somar em silêncio.
    contatos_unidos: int = 1
    # Dicionário livre: a persona decide o que perguntar (um salão quer convidados, outro
    # quer tipo de evento), então a tela rotula o que conhece e mostra o resto como veio.
    dados_coletados: dict[str, str] = {}
    ultimo_resumo: str | None = None
    conversas: list[ContactConversaSchema] = []
    reservas: list[ContactReservaSchema] = []
    notas: list[ContactNoteSchema] = []


# ─────────────────────────── atendimento por fluxo ───────────────────────────
# Acrescentado no FIM do arquivo (`docs/produto/PLATAFORMA.md` §1): a base também mexe aqui, e append
# não conflita.


class FluxoSchema(BaseModel):
    """O roteiro publicado do tenant. `grafo` é livre — o formato vive em app/flow/schema.py."""

    nome: str = ""
    grafo: dict = {}
    versao: int = 0
    # Publicado por `scripts/sincronizar_conteudo.py` a partir de `conteudo/fluxo.json`.
    # Enquanto for true a tela mostra o roteiro em leitura, igual a persona: editar pelo
    # painel e sincronizar depois se sobrescreveriam em silêncio, nos dois sentidos.
    gerenciado_por_arquivo: bool = False


class FluxoUpdateSchema(BaseModel):
    nome: str = ""
    grafo: dict


class FluxoValidacaoSchema(BaseModel):
    """Resultado do validador. `erros` vazia = o roteiro pode ir ao ar."""

    ok: bool
    erros: list[str] = []


class FluxoTextosSchema(BaseModel):
    """O que o painel edita. Só TEXTO — a estrutura do roteiro nunca chega aqui.

    O corpo é o que MUDOU, e é aplicado sobre o grafo publicado no servidor. O navegador
    nunca manda um grafo inteiro: assim não há como alterar `proximo`, `tipo` ou o número
    de opções nem forjando a requisição.
    """

    nos: dict[str, dict] = {}
    config: dict[str, str] = {}


class FluxoSimulacaoInSchema(BaseModel):
    mensagens: list[str] = []
    # Grafo de rascunho, para conferir uma alteração antes de publicá-la. Ausente = roda o
    # roteiro que está no ar.
    grafo: dict | None = None


class FluxoPassoSchema(BaseModel):
    """Um turno da simulação: o que o cliente disse e o que o roteiro responderia."""

    cliente: str
    bolhas: list[str] = []
    no: str | None = None
    encaminha: bool = False
    notas: list[str] = []
    coletado: dict[str, str] = {}


class FluxoSimulacaoSchema(BaseModel):
    passos: list[FluxoPassoSchema] = []


# Banco de mídia (`services/midia_recebida.py`). No FIM do arquivo, como os demais acréscimos.
class MidiaRecebidaSchema(BaseModel):
    message_id: UUID
    conversation_id: UUID
    content_type: str
    legenda: str | None = None
    mimetype: str | None = None
    contato: str | None = None
    telefone: str | None = None
    created_at: datetime
    media_url: str


class MidiaRecebidaListSchema(BaseModel):
    itens: list[MidiaRecebidaSchema]
