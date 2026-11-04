'use client'
import { Fragment, useState, useEffect, useLayoutEffect, useRef } from 'react'
import { AlertCircle, MessageSquare } from 'lucide-react'
import { useAuth } from '@/contexts/AuthContext'
import { api } from '@/lib/api'
import { useToast } from '@/contexts/ToastContext'
import type { ContactProfile, Conversation, Message } from '@/types/api'
import ThreadHeader from '@/components/ThreadHeader'
import ContactPanel from '@/components/ContactPanel'
import ReplyInput from '@/components/ReplyInput'
import MessageBubble from '@/components/MessageBubble'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { cn, diaLocal, rotuloDia } from '@/lib/utils'

// Tamanho da página de mensagens. A thread abre sempre nas MAIS RECENTES; o botão
// "Carregar anteriores" anda para trás somando offset.
const PAGE = 50

/** Faixa de data entre os blocos de mensagens do dia. */
function SeparadorDia({ iso }: { iso: string }) {
  return (
    <div
      role="separator"
      className="self-center my-1 rounded-full bg-subtle px-2.5 py-0.5 text-2xs font-medium text-muted whitespace-nowrap"
    >
      {rotuloDia(iso)}
    </div>
  )
}

interface ConversationThreadProps {
  conversationId: string | null
  conversation: Conversation | null
  onConversationUpdate: (conv: Conversation) => void
  /** Volta para a lista no mestre-detalhe do mobile. */
  onBack?: () => void
  refreshTrigger?: number
}

export default function ConversationThread({ conversationId, conversation, onConversationUpdate, onBack, refreshTrigger }: ConversationThreadProps) {
  const { token, userId } = useAuth()
  const toast = useToast()
  const isMine = conversation?.assigned_user_id === userId && userId !== null
  const [messages, setMessages] = useState<Message[]>([])
  const [botSkipReason, setBotSkipReason] = useState<string | null>(null)
  const [contactProfile, setContactProfile] = useState<ContactProfile>({})
  const [erroThread, setErroThread] = useState<string | null>(null)
  const [perfilAberto, setPerfilAberto] = useState(false)
  const [loading, setLoading] = useState(false)
  const [loadingOlder, setLoadingOlder] = useState(false)
  const [hasOlder, setHasOlder] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  // Altura anterior do conteúdo, gravada só quando carregamos histórico: é o que
  // permite devolver o usuário à mesma linha depois do prepend.
  const prependFrom = useRef<number | null>(null)

  const fetchMessages = () => {
    if (!conversationId || !token) {
      setMessages([])
      setBotSkipReason(null)
      setContactProfile({})
      setHasOlder(false)
      setErroThread(null)
      return
    }
    setLoading(true)
    setErroThread(null)
    api.conversations.get(token, conversationId, { limit: PAGE })
      .then((detail) => {
        setMessages(detail.messages)
        setBotSkipReason(detail.bot_skip_reason)
        setContactProfile(detail.contact_profile ?? {})
        // Página cheia = provavelmente há mais para trás.
        setHasOlder(detail.messages.length === PAGE)
      })
      .catch(() => {
        // Erro do painel principal fica NO painel, com saída. Um toast sumiria e
        // deixaria a thread vazia sem explicação nem caminho de volta.
        setErroThread('Não foi possível carregar esta conversa.')
      })
      .finally(() => { setLoading(false) })
  }

  function loadOlder() {
    if (!conversationId || !token || loadingOlder) return
    setLoadingOlder(true)
    prependFrom.current = scrollRef.current?.scrollHeight ?? null
    api.conversations.get(token, conversationId, { limit: PAGE, offset: messages.length })
      .then((detail) => {
        const older = detail.messages
        setHasOlder(older.length === PAGE)
        if (older.length === 0) {
          prependFrom.current = null
          return
        }
        setMessages((prev) => [...older, ...prev])
      })
      .catch(() => {
        prependFrom.current = null
        // Aqui o conteúdo atual continua válido: só o incremento falhou.
        toast.error('Não foi possível carregar as mensagens anteriores.')
      })
      .finally(() => { setLoadingOlder(false) })
  }

  useEffect(() => { fetchMessages() }, [conversationId, token, refreshTrigger])

  // Fecha ao trocar de conversa: o painel aberto era de OUTRO cliente, e mantê-lo
  // aberto mostraria o perfil errado ao lado da thread nova por um instante.
  useEffect(() => { setPerfilAberto(false) }, [conversationId])

  // Zera o badge de não-lidas ao abrir. Uma vez por conversa — não a cada mensagem
  // nova, senão o contador do atendente com a thread aberta nunca sobe e desce à toa.
  useEffect(() => {
    if (!conversationId || !token) return
    // Silencioso de propósito: é higiene de badge, não ação do usuário. Avisar aqui
    // dispararia em TODA conversa aberta e treinaria a pessoa a ignorar os avisos que
    // importam. A próxima abertura refaz.
    api.conversations.markRead(token, conversationId).catch(() => {})
  }, [conversationId, token])

  // Preserva a posição de leitura ao carregar histórico: sem isto o botão
  // "Carregar anteriores" empurraria o usuário de volta para o fim da conversa.
  useLayoutEffect(() => {
    const el = scrollRef.current
    const before = prependFrom.current
    if (el && before !== null) {
      el.scrollTop += el.scrollHeight - before
      prependFrom.current = null
    }
  }, [messages])

  useEffect(() => {
    // Só desce sozinho em mensagem nova; num prepend a rolagem já foi restaurada acima.
    if (prependFrom.current === null) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [messages])

  if (!conversationId) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center bg-canvas gap-3">
        <div className="w-12 h-12 rounded-2xl bg-surface border border-line flex items-center justify-center">
          <MessageSquare size={22} className="text-faint" strokeWidth={1.75} />
        </div>
        <p className="text-[13px] text-faint">Selecione uma conversa para começar</p>
      </div>
    )
  }

  return (
    // Abaixo de lg a conversa CEDE a tela ao perfil, como a lista já cede à thread: em
    // 375px, thread e painel lado a lado deixariam duas colunas ilegíveis. Acima, os
    // dois convivem — é o ponto do painel lateral, responder já sabendo do cliente.
    <div className="flex-1 flex overflow-hidden">
      <div
        className={cn(
          'flex-1 flex-col bg-canvas overflow-hidden',
          perfilAberto ? 'hidden lg:flex' : 'flex',
        )}
      >
        {conversation && (
          <ThreadHeader
            conversation={conversation}
            botSkipReason={botSkipReason}
            contactProfile={contactProfile}
            onBack={onBack}
            onOpenProfile={() => setPerfilAberto(true)}
            onConversationUpdate={onConversationUpdate}
          />
        )}
        <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 lg:p-5 flex flex-col gap-2">
          {loading && (
            <div className="flex items-center justify-center py-8">
              <span className="text-xs text-faint">Carregando mensagens…</span>
            </div>
          )}
          {!loading && erroThread && (
            <EmptyState
              icon={AlertCircle}
              title={erroThread}
              description="A conversa existe — o que falhou foi a busca das mensagens."
              action={
                <Button size="sm" variant="secondary" onClick={fetchMessages}>
                  Tentar de novo
                </Button>
              }
            />
          )}
          {!loading && !erroThread && hasOlder && (
            <div className="flex items-center justify-center pb-1">
              <button
                onClick={loadOlder}
                disabled={loadingOlder}
                className="text-[12px] font-semibold text-muted border border-line bg-surface rounded-full px-3.5 py-1.5 hover:bg-surface hover:text-ink transition-colors disabled:opacity-40"
              >
                {loadingOlder ? 'Carregando…' : 'Carregar mensagens anteriores'}
              </button>
            </div>
          )}
          {!loading && !erroThread && messages.length === 0 && (
            <div className="flex items-center justify-center py-8">
              <span className="text-xs text-faint">Nenhuma mensagem</span>
            </div>
          )}
          {/* Separador sempre que a mensagem cai num dia diferente da anterior — e na
              primeira, que abre o bloco mais antigo carregado. Comparação por chave de
              dia LOCAL: em UTC a virada acontece às 21h de Brasília. */}
          {!loading &&
            messages.map((msg, i) => {
              const anterior = messages[i - 1]
              const virouODia =
                !anterior || diaLocal(anterior.created_at) !== diaLocal(msg.created_at)
              return (
                <Fragment key={msg.id}>
                  {virouODia && <SeparadorDia iso={msg.created_at} />}
                  <MessageBubble msg={msg} />
                </Fragment>
              )
            })}
          <div ref={bottomRef} />
        </div>
        {conversationId && (
          <ReplyInput
            conversationId={conversationId}
            disabled={!isMine}
            onMessageSent={(msg) => setMessages((prev) => [...prev, msg])}
          />
        )}
      </div>
      {perfilAberto && conversation && (
        <ContactPanel
          contactId={conversation.contact_id}
          onClose={() => setPerfilAberto(false)}
        />
      )}
    </div>
  )
}
