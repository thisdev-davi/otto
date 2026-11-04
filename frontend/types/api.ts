export type Channel = 'whatsapp' // MVP: só WhatsApp
export type ConversationStatus = 'open' | 'pending' | 'resolved'

export interface AuditEntry {
  id: string
  created_at: string
  actor_type: string
  actor_user_id: string | null
  action: string
  resource_type: string | null
  resource_id: string | null
  result: string
  reason: string | null
  correlation_id: string | null
  ip_network: string | null
  metadata: Record<string, unknown>
}

export interface AuditList {
  items: AuditEntry[]
  next_cursor: string | null
}
export type MessageDirection = 'inbound' | 'outbound'
export type MessageStatus = 'sent' | 'delivered' | 'read' | 'failed'
export type ContentType = 'text' | 'image' | 'video' | 'document' | 'audio' | 'sticker'

export interface Message {
  id: string
  direction: MessageDirection
  content_type: ContentType
  content: string | null
  status: MessageStatus | null
  created_at: string
  is_internal?: boolean
  is_bot?: boolean
  media_url?: string | null
  transcript_status?: string | null
  media_removed_at?: string | null
  media_removed_by?: string | null
  deleted_at?: string | null
  deleted_by?: string | null
  edited_at?: string | null
  original_content?: string | null
}

export interface Conversation {
  id: string
  tenant_id: string
  contact_id: string
  contact_name: string | null
  contact_external_id: string | null
  /** Telefone real do contato. `contact_external_id` é endereço do canal e pode ser um
   *  identificador opaco (`@lid`) — não use aquele para exibir nem para discar. */
  contact_phone: string | null
  contact_channel: string | null
  /** Foto de perfil do WhatsApp: caminho + assinatura, prontos do servidor. Ver
   *  lib/api.ts::mediaUrl. `null` quando o contato não expõe foto — o comum. */
  contact_avatar_url: string | null
  status: ConversationStatus
  assigned_user_id: string | null
  priority: string
  tags: string[]
  last_message_preview: string | null
  last_message_at: string | null
  unread_count: number
  created_at: string
}

export interface QuickReply {
  id: string
  shortcut: string
  title: string
  content: string
}

export type BotProvider = 'stub' | 'anthropic' | 'openai' | 'xai' | 'gemini' | 'deepseek' | 'groq'

/**
 * Qual cérebro atende. Junto com `ai_enabled` forma o controle de 3 estados do painel:
 * desligado (`ai_enabled: false`), fluxo e agente de IA. Trocar 'agente' por 'fluxo' é o
 * freio de emergência — custo de LLM cai a zero e o atendimento continua.
 */
export type BotModo = 'agente' | 'fluxo'

/** Expediente do negócio. Chave = dia ISO ('1' seg … '7' dom); lista vazia = fechado. */
export interface HorarioAtendimento {
  fuso?: string
  dias?: Record<string, [string, string][]>
}

export interface BotSettings {
  ai_enabled: boolean
  persona: string
  knowledge: string
  provider: BotProvider
  model: string
  api_key_masked: string
  transfer_message: string
  /** Aviso enviado ao cliente quando a equipe confirma a reserva dele. */
  confirmacao_message: string
  use_rag: boolean
  embeddings_ready: boolean // derivado no backend; não editável
  embeddings_provider: string // qual provedor vai embedar ('' = nenhum resolve)
  /**
   * Persona e conhecimento vêm de `conteudo/*.md`, gravados por
   * `scripts/sincronizar_conteudo.py`. Ligado, o PUT recusa alterar esses dois campos —
   * a tela os mostra em leitura para não oferecer uma edição que seria perdida.
   */
  conteudo_gerenciado: boolean
  modo: BotModo
  horario: HorarioAtendimento
  /** Derivado no backend: o tenant já publicou um roteiro? Sem ele o modo fluxo é mudo. */
  fluxo_publicado: boolean
}

export interface BotSettingsUpdate {
  ai_enabled: boolean
  persona: string
  knowledge: string
  provider: BotProvider
  model: string
  api_key?: string | null // null/omitido = mantém a chave atual
  transfer_message?: string | null
  confirmacao_message?: string | null
  use_rag: boolean
  modo: BotModo
  horario?: HorarioAtendimento | null // null/omitido = mantém o expediente atual
}

export interface BotPing {
  ok: boolean
  detail: string
}

export interface KbSource {
  source: string | null
  title: string | null
  chunks: number
  embedding_model: string | null
  /** Embedada com outro modelo: a busca não a enxerga até ser sincronizada de novo. */
  stale: boolean
  created_at: string | null
}

export interface KbSourceList {
  sources: KbSource[]
}

export interface RagSource {
  title: string | null
  source_url: string | null
  score: number
}

export interface RagAnswer {
  answer: string
  sources: RagSource[]
}

export interface QuickReplyList {
  quick_replies: QuickReply[]
}

export interface ContactProfile {
  /** Dados coletados pela IA, acumulados entre conversas. */
  intake?: Record<string, string>
  /** Resumo da última triagem, gerado pela IA no encaminhamento. */
  last_summary?: string
  last_interaction_at?: string
  conversations?: number
}

export interface ConversationDetail extends Conversation {
  messages: Message[]
  /** Motivo pelo qual a IA não respondeu no último turno (BotSkipReason). */
  bot_skip_reason: string | null
  /** Memória do cliente entre conversas. Vazio quando é o primeiro atendimento. */
  contact_profile: ContactProfile
}

export interface ConversationList {
  conversations: Conversation[]
  total: number
  limit: number
  offset: number
}

export interface TokenResponse {
  access_token: string
  token_type: string
}

export interface SendMessageRequest {
  content: string
  content_type?: ContentType
}

export interface TransferRequest {
  to_user_id: string
}

export interface StatusUpdateRequest {
  status: ConversationStatus
}

export interface ApiError {
  detail: string
}

export interface SuggestReply {
  suggestion: string | null
  reason: string | null
}

export interface ReportOverview {
  open_count: number
  unclaimed_count: number
  resolved_count: number
  avg_resolution_seconds: number | null
  avg_first_response_seconds: number | null
}

export interface VolumePoint {
  day: string
  channel: string
  direction: MessageDirection
  total: number
}

export interface ReportVolume {
  points: VolumePoint[]
}

export interface AgentStat {
  user_id: string
  name: string
  claimed: number
  resolved: number
  messages_sent: number
}

export interface ReportAgents {
  agents: AgentStat[]
}

export interface ReportBot {
  triaged: number
  handoffs: number
  failures: number
  resolved_by_bot: number
  closed_by_inactivity: number
}

export interface ChannelConnection {
  id: string
  channel: Channel
  label: string | null
  is_active: boolean
  created_at: string
}

export interface ChannelConnectionList {
  connections: ChannelConnection[]
}

export type ChannelState =
  | 'connected'
  | 'connecting'
  | 'qr_pending'
  | 'disconnected'
  | 'error'

export interface ChannelStatus {
  state: ChannelState
  qr_code: string | null
  detail: string | null
}

// ─────────────────────────── usuários e equipe ───────────────────────────

export type UserRole = 'ADMIN' | 'AGENT'

/** Usuário ativo do tenant — o que qualquer atendente pode ver (ex.: transferência). */
export interface User {
  id: string
  full_name: string | null
  email: string
  role: UserRole
}

export interface UserList {
  users: User[]
}

/** Membro visto pelo admin: inclui inativos, ao contrário de `User`. */
export interface Member {
  id: string
  email: string
  full_name: string | null
  role: UserRole
  is_active: boolean
  created_at: string
}

export interface MemberList {
  members: Member[]
}

export interface MemberUpdate {
  role?: UserRole
  is_active?: boolean
}

export interface InviteCreate {
  email: string
  full_name?: string | null
  role: UserRole
}

export interface Invite {
  id: string
  email: string
  full_name: string | null
  role: UserRole
  expires_at: string
  created_at: string
}

/** O `token` existe SÓ nesta resposta — o banco guarda apenas o hash dele. */
export interface InviteCreated {
  invite: Invite
  token: string
}

export interface InviteList {
  invites: Invite[]
}

/** O que a tela pública de aceite enxerga: nada além de onde se está entrando. */
export interface InvitePublic {
  email: string
  full_name: string | null
  role: UserRole
  tenant_name: string
}

// ─────────────────── salão: agenda, reservas e espaço ───────────────────
// Acrescentado no FIM (`docs/produto/PLATAFORMA.md` §1): região que a base não mexe. O cliente TS é
// escrito à mão — mudou o schema Pydantic, muda aqui na mesma leva.

/** Só `pre_reservada` e `confirmada` ocupam a data (índice parcial no banco). */
export type ReservaStatus = 'pre_reservada' | 'confirmada' | 'cancelada' | 'expirada'

export interface ReservaResumo {
  id: string
  status: ReservaStatus
  nome_cliente: string | null
  telefone: string | null
  expira_em: string | null
  conversation_id: string | null
  criada_por: 'bot' | 'humano'
}

/**
 * Por que uma data está (ou não) à venda. Espelha `salao_agenda.MOTIVO_*`.
 *
 * União fechada de propósito: cada caso tem uma frase diferente na tela, e um `string`
 * solto deixaria um motivo novo do backend passar sem ninguém escrever o texto dele.
 */
export type MotivoData =
  | 'ok'
  | 'sem_cadastro'
  | 'sem_preco'
  | 'bloqueada'
  | 'ocupada'
  | 'passado'

export interface AgendaData {
  /** 'AAAA-MM-DD' — a data é a chave do dia, não um instante. */
  data: string
  /** null é "valor não cadastrado", nunca zero: o agente trata como desconhecido. */
  preco_centavos: number | null
  bloqueada: boolean
  observacao: string | null
  reserva: ReservaResumo | null
  /** Existe linha em `salao_datas`? Distingue "não cadastrei" de "cadastrei sem valor". */
  cadastrada: boolean
  motivo: MotivoData
  /**
   * Já vem resolvido do backend — a tela NUNCA recalcula isto a partir dos outros campos.
   * Duas implementações da mesma regra divergem, e a divergência vira preço errado.
   */
  livre: boolean
}

export interface AgendaList {
  datas: AgendaData[]
}

export interface AgendaDataIn {
  preco_centavos: number | null
  bloqueada: boolean
  observacao: string | null
}

export interface AgendaLoteIn {
  de: string
  ate: string
  /** ISO: 1=segunda … 7=domingo. Vazio = todos os dias do intervalo. */
  dias_da_semana: number[]
  preco_centavos: number | null
  /** null NÃO mexe no bloqueio existente — mandar `false` desbloqueia a faixa inteira. */
  bloqueada: boolean | null
}


export interface AgendaLoteResult {
  datas_afetadas: number
}

export interface Reserva {
  id: string
  data: string
  status: ReservaStatus
  contact_id: string | null
  conversation_id: string | null
  criada_por: 'bot' | 'humano'
  expira_em: string | null
  nome_cliente: string | null
  telefone: string | null
  observacao: string | null
}

export interface ReservaList {
  reservas: Reserva[]
}

/** Contexto da reserva para quem vai decidir — ver `salao_resumo`. */
export interface ReservaDetalhe {
  reserva_id: string
  data: string
  status: ReservaStatus
  criada_por: 'bot' | 'humano'
  expira_em: string | null
  criada_em: string
  /** Cópia do nome no momento da reserva. */
  nome_cliente: string | null
  /** Nome ATUAL do contato — pode ter mudado desde então. */
  contato_nome: string | null
  telefone: string | null
  observacao: string | null
  preco_centavos: number | null
  conversation_id: string | null
  /** Chaves livres: quem decide o que perguntar é a persona, não o código. */
  dados_coletados: Record<string, string>
  nota_interna: string | null
  /** Aviso pronto para o cliente, renderizado NO SERVIDOR. Nunca monte este texto aqui:
   *  duas implementações da mesma frase divergem, e a divergência sai no WhatsApp. */
  mensagem_confirmacao: string | null
  /** `false` quando não há a quem avisar (reserva lançada à mão, sem contato). */
  pode_avisar: boolean
}

export interface ConfirmarReservaResponse {
  reserva: Reserva
  /** `false` também quando o aviso nem foi pedido. A confirmação vale de qualquer forma. */
  aviso_enviado: boolean
}

export interface ReservaIn {
  data: string
  nome_cliente?: string | null
  telefone?: string | null
  observacao?: string | null
  confirmar: boolean
}

export interface FotoEspaco {
  id: string
  titulo: string | null
  mimetype: string
  tamanho_bytes: number
  ordem: number
  ativa: boolean
  created_at: string
  /** Caminho + assinatura de curta duração, prontos do servidor. Ver lib/api.ts::mediaUrl. */
  arquivo_url: string
}

export interface FotoEspacoList {
  fotos: FotoEspaco[]
}

export interface FotoEspacoUpdate {
  titulo?: string | null
  ordem?: number
  ativa?: boolean
}

// ─────────────────────────── perfil do contato ───────────────────────────

export interface ContactNote {
  id: string
  texto: string
  criada_em: string
  /** `null` quando quem escreveu saiu da equipe — a nota fica, sem autor. */
  autor_id: string | null
  autor: string | null
}

export interface ContactConversa {
  conversation_id: string
  status: ConversationStatus
  priority: string
  criada_em: string
  ultima_mensagem_em: string | null
  atendente: string | null
}

/** Agendamento no histórico do cliente — inclui cancelado e expirado. */
export interface ContactReserva {
  id: string
  data: string
  status: ReservaStatus
  criada_por: 'bot' | 'humano'
  expira_em: string | null
  observacao: string | null
  conversation_id: string | null
}

export interface ContactPerfil {
  contact_id: string
  nome: string | null
  telefone: string | null
  canal: string
  /** Foto do canal: caminho + assinatura, prontos do servidor. Ver lib/api.ts::mediaUrl. */
  avatar_url: string | null
  cliente_desde: string
  /** Quantas linhas de `contacts` são esta pessoa. > 1 = histórico que estava partido. */
  contatos_unidos: number
  /** Dicionário livre: a persona decide as chaves, a tela rotula o que conhece. */
  dados_coletados: Record<string, string>
  ultimo_resumo: string | null
  conversas: ContactConversa[]
  reservas: ContactReserva[]
  notas: ContactNote[]
}

// ─────────────────────────── atendimento por fluxo ───────────────────────────
// No FIM do arquivo (`docs/produto/PLATAFORMA.md` §1): a base também mexe aqui, e append não conflita.

/** Os nove tipos de caixa. Conjunto FECHADO — espelha `app/flow/schema.py::ESPEC`. */
export type TipoNo =
  | 'mensagem'
  | 'menu'
  | 'pergunta'
  | 'condicao'
  | 'horario'
  | 'agenda'
  | 'acao'
  | 'handoff'
  | 'fim'

export interface OpcaoDeMenu {
  rotulo: string
  sinonimos?: string[]
  proximo: string
}

/**
 * Uma caixa do roteiro. Os campos variam por tipo — o formato canônico vive em
 * `app/flow/schema.py`, e o validador do backend é quem manda.
 */
export interface NoDoFluxo {
  tipo: TipoNo
  /** Posição no canvas. Mora no DADO, não num algoritmo de layout. */
  pos?: [number, number]
  texto?: string
  campo?: string
  validacao?: 'texto' | 'data' | 'numero'
  opcoes?: OpcaoDeMenu[]
  proximo?: string
  senao?: string
  saidas?: Record<string, string>
  acao?: 'fotos' | 'pre_reserva'
  resumo?: string
  prioridade?: 'alta' | 'normal'
  sem_livres?: string
}

export interface GrafoDoFluxo {
  nome?: string
  inicio?: string
  config?: {
    no_handoff?: string
    max_invalidas?: number
    nao_entendi?: string
    palavras_atendente?: string[]
  }
  nos: Record<string, NoDoFluxo>
}

export interface Fluxo {
  nome: string
  grafo: Partial<GrafoDoFluxo>
  versao: number
  /**
   * Publicado a partir de `conteudo/fluxo.json` pelo sincronizador. Ligado, a tela mostra
   * o roteiro em leitura — igual a persona, e pelo mesmo motivo: editar pelo painel e
   * sincronizar depois se sobrescreveriam em silêncio, nos dois sentidos.
   */
  gerenciado_por_arquivo: boolean
}

export interface FluxoValidacao {
  ok: boolean
  erros: string[]
}

/** O que o painel edita: TEXTO, e só. A estrutura do roteiro nunca sai do arquivo. */
export interface FluxoTextos {
  nos: Record<string, { texto?: string; resumo?: string; sem_livres?: string; opcoes?: { rotulo?: string; sinonimos?: string[] }[] }>
  config?: Record<string, string>
}

/** Um turno da simulação: o que o cliente disse e o que o roteiro responderia. */
export interface FluxoPasso {
  cliente: string
  bolhas: string[]
  no: string | null
  encaminha: boolean
  notas: string[]
  coletado: Record<string, string>
}

export interface FluxoSimulacao {
  passos: FluxoPasso[]
}

// Banco de mídia: o que os clientes mandaram (`GET /media`). No FIM, como os demais acréscimos.
export type TipoMidia = 'image' | 'sticker' | 'video' | 'document' | 'audio'

export interface MidiaRecebida {
  message_id: string
  conversation_id: string
  content_type: TipoMidia
  legenda: string | null
  mimetype: string | null
  contato: string | null
  telefone: string | null
  created_at: string
  media_url: string
}

export interface MidiaRecebidaList {
  itens: MidiaRecebida[]
}
