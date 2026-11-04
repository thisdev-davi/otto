const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'

/**
 * URL absoluta de um anexo.
 *
 * `<img>` e `<audio>` buscam a URL sozinhos e não mandam header `Authorization`. A
 * solução anterior era pendurar o JWT em `?token=` — o que funcionava e espalhava a
 * credencial de sessão por log de acesso, histórico e `Referer`.
 *
 * Agora o caminho JÁ VEM ASSINADO do servidor (`media_url`, `arquivo_url`): assinatura
 * de um recurso só, com validade curta, que não autoriza mais nada na API. Aqui só falta
 * o host. Não volte a concatenar token nesta função.
 */
export function mediaUrl(path: string): string {
  return `${API_URL}${path}`
}

interface FetchOptions extends RequestInit {
  token?: string
}

// `type` e não `interface`: só alias de objeto ganha index signature implícita, que é
// o que permite passar isto para `queryString(Record<string, ...>)`.
export type ConversationQuery = {
  limit?: number
  offset?: number
  search?: string
}

export type AuditQuery = {
  date_from?: string
  date_to?: string
  actor_id?: string
  action?: string
  resource_type?: string
  resource_id?: string
  result?: string
  cursor?: string
  limit?: number
}

/** '?a=1&b=2' ignorando vazios, ou '' quando não há nada a passar. */
function queryString(params?: Record<string, string | number | undefined>): string {
  const qs = new URLSearchParams(
    Object.entries(params || {})
      .filter(([, v]) => v !== undefined && v !== '')
      .map(([k, v]) => [k, String(v)])
  ).toString()
  return qs ? `?${qs}` : ''
}

async function apiFetch<T>(path: string, options: FetchOptions = {}): Promise<T> {
  const { token, headers: extraHeaders, ...fetchOptions } = options
  // Com FormData o Content-Type NÃO pode ser cravado: quem monta o `boundary` do
  // multipart é o navegador, e um header fixo aqui quebra o upload no servidor.
  const isForm = fetchOptions.body instanceof FormData
  const headers: Record<string, string> = {
    ...(isForm ? {} : { 'Content-Type': 'application/json' }),
    ...(extraHeaders as Record<string, string> || {}),
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }

  const res = await fetch(`${API_URL}${path}`, { ...fetchOptions, headers })

  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }))
    const err = new Error(error.detail || 'Erro na API') as Error & {
      status: number
      body: unknown
    }
    err.status = res.status
    err.body = error
    throw err
  }

  if (res.status === 204 || res.headers.get('content-length') === '0') {
    return undefined as T
  }
  return res.json()
}

function rangeQs(from?: string, to?: string): string {
  const qs = new URLSearchParams()
  if (from) qs.set('date_from', from)
  if (to) qs.set('date_to', to)
  const s = qs.toString()
  return s ? `?${s}` : ''
}

export const api = {
  audit: {
    list: (token: string, params?: AuditQuery) =>
      apiFetch<import('@/types/api').AuditList>(`/audit${queryString(params)}`, { token }),
    export: async (token: string, params?: AuditQuery): Promise<Blob> => {
      const response = await fetch(`${API_URL}/audit/export${queryString(params)}`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!response.ok) {
        const body = await response.json().catch(() => ({ detail: 'Não foi possível exportar a auditoria.' }))
        throw new Error(body.detail || 'Não foi possível exportar a auditoria.')
      }
      return response.blob()
    },
  },
  auth: {
    login: (email: string, password: string) => {
      const form = new URLSearchParams()
      form.set('username', email)
      form.set('password', password)
      return apiFetch<{ access_token: string; refresh_token: string; token_type: string }>('/auth/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: form.toString(),
      })
    },
    refresh: (refreshToken: string) =>
      apiFetch<{ access_token: string; refresh_token: string; token_type: string }>('/auth/refresh', {
        method: 'POST',
        body: JSON.stringify({ refresh_token: refreshToken }),
      }),
    /** Invalida no servidor os refresh tokens deste usuário. */
    logout: (token: string) => apiFetch<void>('/auth/logout', { method: 'POST', token }),
    changePassword: (token: string, senha_atual: string, nova_senha: string) =>
      apiFetch<void>('/auth/password', {
        method: 'POST',
        token,
        body: JSON.stringify({ senha_atual, nova_senha }),
      }),
  },
  conversations: {
    list: (token: string, params?: ConversationQuery) =>
      apiFetch<import('@/types/api').ConversationList>(
        `/conversations${queryString(params)}`,
        { token }
      ),
    mine: (token: string, params?: ConversationQuery) =>
      apiFetch<import('@/types/api').ConversationList>(
        `/conversations/mine${queryString(params)}`,
        { token }
      ),
    all: (token: string, params?: ConversationQuery) =>
      apiFetch<import('@/types/api').ConversationList>(
        `/conversations/all${queryString(params)}`,
        { token }
      ),
    // offset conta para TRÁS a partir da mensagem mais recente (paginação de histórico).
    get: (token: string, id: string, params?: { limit?: number; offset?: number }) => {
      const qs = new URLSearchParams(
        Object.fromEntries(
          Object.entries(params || {}).filter(([, v]) => v !== undefined).map(([k, v]) => [k, String(v)])
        )
      ).toString()
      return apiFetch<import('@/types/api').ConversationDetail>(
        `/conversations/${id}${qs ? '?' + qs : ''}`,
        { token },
      )
    },
    markRead: (token: string, id: string) =>
      apiFetch<import('@/types/api').Conversation>(`/conversations/${id}/read`, {
        method: 'POST',
        token,
      }),
    claim: (token: string, id: string) =>
      apiFetch<import('@/types/api').Conversation>(`/conversations/${id}/claim`, {
        method: 'POST',
        token,
      }),
    release: (token: string, id: string) =>
      apiFetch<import('@/types/api').Conversation>(`/conversations/${id}/release`, {
        method: 'POST',
        token,
      }),
    transfer: (token: string, id: string, toUserId: string) =>
      apiFetch<import('@/types/api').Conversation>(`/conversations/${id}/transfer`, {
        method: 'POST',
        token,
        body: JSON.stringify({ to_user_id: toUserId }),
      }),
    sendMessage: (token: string, id: string, content: string) =>
      apiFetch<import('@/types/api').Message>(`/conversations/${id}/messages`, {
        method: 'POST',
        token,
        body: JSON.stringify({ content, content_type: 'text' }),
      }),
    /** Anexo do atendente. O tipo (imagem/áudio/documento) é decidido no servidor. */
    /** `ptt` marca nota de voz: vira balão de áudio no WhatsApp, não anexo. Só vale
     *  para áudio — o servidor ignora em imagem e documento. */
    sendMedia: (token: string, id: string, file: File, caption: string, ptt = false) => {
      const form = new FormData()
      form.append('file', file)
      form.append('caption', caption)
      if (ptt) form.append('ptt', 'true')
      return apiFetch<import('@/types/api').Message>(
        `/conversations/${id}/messages/media`,
        { method: 'POST', token, body: form }
      )
    },
    /** "digitando…" no WhatsApp do cliente. Dispare e esqueça — nunca falha em 4xx. */
    typing: (token: string, id: string) =>
      apiFetch<void>(`/conversations/${id}/typing`, { method: 'POST', token }),
    suggestReply: (token: string, id: string) =>
      apiFetch<import('@/types/api').SuggestReply>(`/conversations/${id}/suggest-reply`, {
        method: 'POST',
        token,
      }),
    updateStatus: (token: string, id: string, status: string) =>
      apiFetch<import('@/types/api').Conversation>(`/conversations/${id}/status`, {
        method: 'PATCH',
        token,
        body: JSON.stringify({ status }),
      }),
  },
  bot: {
    getSettings: (token: string) =>
      apiFetch<import('@/types/api').BotSettings>('/bot/settings', { token }),
    updateSettings: (token: string, body: import('@/types/api').BotSettingsUpdate) =>
      apiFetch<import('@/types/api').BotSettings>('/bot/settings', {
        method: 'PUT',
        token,
        body: JSON.stringify(body),
      }),
    ping: (token: string) =>
      apiFetch<import('@/types/api').BotPing>('/bot/ping', { method: 'POST', token }),
    listKbDocuments: (token: string) =>
      apiFetch<import('@/types/api').KbSourceList>('/bot/kb/documents', { token }),
    ragAnswer: (token: string, question: string, topK = 5) =>
      apiFetch<import('@/types/api').RagAnswer>('/bot/rag/answer', {
        method: 'POST',
        token,
        body: JSON.stringify({ question, top_k: topK }),
      }),
  },
  quickReplies: {
    list: (token: string) =>
      apiFetch<import('@/types/api').QuickReplyList>('/quick-replies', { token }),
    create: (token: string, shortcut: string, title: string, content: string) =>
      apiFetch<import('@/types/api').QuickReply>('/quick-replies', {
        method: 'POST',
        token,
        body: JSON.stringify({ shortcut, title, content }),
      }),
    update: (token: string, id: string, shortcut: string, title: string, content: string) =>
      apiFetch<import('@/types/api').QuickReply>(`/quick-replies/${id}`, {
        method: 'PUT',
        token,
        body: JSON.stringify({ shortcut, title, content }),
      }),
    remove: (token: string, id: string) =>
      apiFetch<void>(`/quick-replies/${id}`, { method: 'DELETE', token }),
  },
  reports: {
    overview: (token: string, from?: string, to?: string) =>
      apiFetch<import('@/types/api').ReportOverview>(`/reports/overview${rangeQs(from, to)}`, { token }),
    volume: (token: string, from?: string, to?: string) =>
      apiFetch<import('@/types/api').ReportVolume>(`/reports/volume${rangeQs(from, to)}`, { token }),
    agents: (token: string, from?: string, to?: string) =>
      apiFetch<import('@/types/api').ReportAgents>(`/reports/agents${rangeQs(from, to)}`, { token }),
    bot: (token: string, from?: string, to?: string) =>
      apiFetch<import('@/types/api').ReportBot>(`/reports/bot${rangeQs(from, to)}`, { token }),
  },
  channels: {
    list: (token: string) =>
      apiFetch<import('@/types/api').ChannelConnectionList>('/channels', { token }),
    connectWhatsApp: (token: string, instance: string) =>
      apiFetch<import('@/types/api').ChannelConnection>('/channels/whatsapp', {
        method: 'POST',
        token,
        body: JSON.stringify({ instance }),
      }),
    status: (token: string, id: string) =>
      apiFetch<import('@/types/api').ChannelStatus>(`/channels/${id}/status`, { token }),
    remove: (token: string, id: string) =>
      apiFetch<void>(`/channels/${id}`, { method: 'DELETE', token }),
  },
  users: {
    /** Quem sou eu, com o papel. Barato — não baixa a equipe inteira. */
    me: (token: string) => apiFetch<import('@/types/api').User>('/users/me', { token }),
    /** Ativos do tenant. Disponível para qualquer atendente (seletor de transferência). */
    list: (token: string) =>
      apiFetch<import('@/types/api').UserList>('/users', { token }),
  },
  team: {
    listMembers: (token: string) =>
      apiFetch<import('@/types/api').MemberList>('/users/members', { token }),
    updateMember: (
      token: string,
      userId: string,
      body: import('@/types/api').MemberUpdate
    ) =>
      apiFetch<import('@/types/api').Member>(`/users/members/${userId}`, {
        method: 'PATCH',
        token,
        body: JSON.stringify(body),
      }),
    listInvites: (token: string) =>
      apiFetch<import('@/types/api').InviteList>('/users/invites', { token }),
    createInvite: (token: string, body: import('@/types/api').InviteCreate) =>
      apiFetch<import('@/types/api').InviteCreated>('/users/invites', {
        method: 'POST',
        token,
        body: JSON.stringify(body),
      }),
    revokeInvite: (token: string, inviteId: string) =>
      apiFetch<void>(`/users/invites/${inviteId}`, { method: 'DELETE', token }),
  },
  invites: {
    // Sem token: quem aceita um convite ainda não tem conta.
    peek: (inviteToken: string) =>
      apiFetch<import('@/types/api').InvitePublic>(
        `/invites/${encodeURIComponent(inviteToken)}`
      ),
    accept: (inviteToken: string, password: string, fullName?: string) =>
      apiFetch<{ access_token: string; refresh_token: string; token_type: string }>(
        `/invites/${encodeURIComponent(inviteToken)}/accept`,
        {
          method: 'POST',
          body: JSON.stringify({ password, full_name: fullName || null }),
        }
      ),
  },
  // ───────────────── salão: agenda e espaço ─────────────────
  // No FIM do objeto de propósito (`PLATAFORMA.md` §1): acrescentar aqui não reordena
  // nada do que a base já tem, então o merge não conflita.
  agenda: {
    list: (token: string, de?: string, ate?: string) =>
      apiFetch<import('@/types/api').AgendaList>(`/agenda${queryString({ de, ate })}`, {
        token,
      }),
    /** `data` no formato 'AAAA-MM-DD' — é ela que identifica o dia na rota. */
    setDate: (token: string, data: string, body: import('@/types/api').AgendaDataIn) =>
      apiFetch<import('@/types/api').AgendaData>(`/agenda/${data}`, {
        method: 'PUT',
        token,
        body: JSON.stringify(body),
      }),
    bulk: (token: string, body: import('@/types/api').AgendaLoteIn) =>
      apiFetch<import('@/types/api').AgendaLoteResult>('/agenda/lote', {
        method: 'POST',
        token,
        body: JSON.stringify(body),
      }),
    listReservas: (token: string) =>
      apiFetch<import('@/types/api').ReservaList>('/agenda/reservas', { token }),
    createReserva: (token: string, body: import('@/types/api').ReservaIn) =>
      apiFetch<import('@/types/api').Reserva>('/agenda/reservas', {
        method: 'POST',
        token,
        body: JSON.stringify(body),
      }),
    reservaResumo: (token: string, id: string) =>
      apiFetch<import('@/types/api').ReservaDetalhe>(
        `/agenda/reservas/${id}/resumo`,
        { token }
      ),
    /** Confirma a reserva. Com `mensagem`, avisa o cliente no WhatsApp — ação
     *  irreversível, então o texto vem revisado pelo diálogo. Sem `mensagem`, só
     *  confirma. */
    confirmReserva: (token: string, id: string, mensagem?: string | null) =>
      apiFetch<import('@/types/api').ConfirmarReservaResponse>(
        `/agenda/reservas/${id}/confirmar`,
        {
          method: 'POST',
          token,
          body: JSON.stringify({ mensagem: mensagem ?? null }),
        }
      ),
    cancelReserva: (token: string, id: string) =>
      apiFetch<import('@/types/api').Reserva>(`/agenda/reservas/${id}/cancelar`, {
        method: 'POST',
        token,
      }),
  },
  espaco: {
    listFotos: (token: string) =>
      apiFetch<import('@/types/api').FotoEspacoList>('/espaco/fotos', { token }),
    /** URL da imagem para `<img src>`; o token vai na query porque `<img>` não manda header. */
    // A URL assinada vem em `foto.arquivo_url`; aqui só se acrescenta o host.
    fotoUrl: (foto: { arquivo_url: string }) => mediaUrl(foto.arquivo_url),
    upload: (token: string, file: File, titulo: string) => {
      const form = new FormData()
      form.append('file', file)
      form.append('titulo', titulo)
      return apiFetch<import('@/types/api').FotoEspaco>('/espaco/fotos', {
        method: 'POST',
        token,
        body: form,
      })
    },
    updateFoto: (
      token: string,
      id: string,
      body: import('@/types/api').FotoEspacoUpdate
    ) =>
      apiFetch<import('@/types/api').FotoEspaco>(`/espaco/fotos/${id}`, {
        method: 'PATCH',
        token,
        body: JSON.stringify(body),
      }),
    removeFoto: (token: string, id: string) =>
      apiFetch<void>(`/espaco/fotos/${id}`, { method: 'DELETE', token }),
  },
  contacts: {
    perfil: (token: string, contactId: string) =>
      apiFetch<import('@/types/api').ContactPerfil>(`/contacts/${contactId}`, { token }),
    criarNota: (token: string, contactId: string, texto: string) =>
      apiFetch<import('@/types/api').ContactNote>(`/contacts/${contactId}/notes`, {
        method: 'POST',
        token,
        body: JSON.stringify({ texto }),
      }),
    removerNota: (token: string, contactId: string, notaId: string) =>
      apiFetch<void>(`/contacts/${contactId}/notes/${notaId}`, {
        method: 'DELETE',
        token,
      }),
  },
  // Atendimento por fluxo (nível 1). No FIM do objeto, por `PLATAFORMA.md` §1.
  fluxo: {
    obter: (token: string) => apiFetch<import('@/types/api').Fluxo>('/fluxo', { token }),
    validar: (token: string, grafo: import('@/types/api').GrafoDoFluxo, nome = '') =>
      apiFetch<import('@/types/api').FluxoValidacao>('/fluxo/validar', {
        method: 'POST',
        token,
        body: JSON.stringify({ grafo, nome }),
      }),
    publicar: (token: string, grafo: import('@/types/api').GrafoDoFluxo, nome = '') =>
      apiFetch<import('@/types/api').Fluxo>('/fluxo', {
        method: 'PUT',
        token,
        body: JSON.stringify({ grafo, nome }),
      }),
    // Só texto: o corpo traz o que mudou e o servidor aplica sobre o grafo publicado.
    editarTextos: (token: string, textos: import('@/types/api').FluxoTextos) =>
      apiFetch<import('@/types/api').Fluxo>('/fluxo/textos', {
        method: 'PATCH',
        token,
        body: JSON.stringify(textos),
      }),
    assumir: (token: string) =>
      apiFetch<import('@/types/api').Fluxo>('/fluxo/assumir', { method: 'POST', token }),
    simular: (
      token: string,
      mensagens: string[],
      grafo?: import('@/types/api').GrafoDoFluxo,
    ) =>
      apiFetch<import('@/types/api').FluxoSimulacao>('/fluxo/simular', {
        method: 'POST',
        token,
        body: JSON.stringify({ mensagens, grafo: grafo ?? null }),
      }),
  },
}
