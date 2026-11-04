'use client'
import { useState, useEffect, useCallback, useRef } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { Inbox, RefreshCw } from 'lucide-react'
import { useAuth } from '@/contexts/AuthContext'
import { api } from '@/lib/api'
import type { Conversation } from '@/types/api'
import type { InboxQueue } from '@/components/InboxView'
import { useInboxSearch } from '@/contexts/InboxSearchContext'
import { useToast } from '@/contexts/ToastContext'
import { Spinner } from '@/components/ui/spinner'
import { EmptyState } from '@/components/ui/empty-state'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { Avatar } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'

const TABS: { key: InboxQueue; label: string; href: string; vazio: string; quando: string }[] = [
  {
    key: 'mine',
    label: 'Minhas',
    href: '/inbox/mine',
    vazio: 'Nenhuma conversa com você',
    quando: 'Aparece aqui a conversa que você capturar ou receber por transferência.',
  },
  {
    key: 'unclaimed',
    label: 'Sem dono',
    href: '/inbox/unclaimed',
    vazio: 'Nenhuma conversa sem dono',
    quando: 'Cliente que escrever e ainda não tiver atendente cai aqui primeiro.',
  },
  {
    key: 'all',
    label: 'Todas',
    href: '/inbox',
    vazio: 'Nenhuma conversa ainda',
    quando: 'Toda mensagem que chegar no WhatsApp conectado aparece aqui.',
  },
]

// A lista REMONTA a cada troca de fila (a `key` do InboxView), e o foco da aba que a
// seta escolheu morreria junto. O flag atravessa a remontagem e devolve o foco.
let focarAbaAoMontar = false

// Hoje é sempre hh:mm; relativo só para "ontem" e os dias da semana. Mais antigo que
// isso, a data — "terça" de duas semanas atrás é ambíguo.
function formatTime(dateStr: string | null): string {
  if (!dateStr) return ''
  const d = new Date(dateStr)
  const dias = Math.round(
    (new Date().setHours(0, 0, 0, 0) - new Date(d).setHours(0, 0, 0, 0)) / 86_400_000
  )
  if (dias <= 0) return d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
  if (dias === 1) return 'ontem'
  if (dias < 7) return d.toLocaleDateString('pt-BR', { weekday: 'short' }).replace('.', '')
  return d.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })
}

interface ConversationListProps {
  queue: InboxQueue
  selectedId: string | null
  className?: string
  onSelect: (conv: Conversation) => void
}

export default function ConversationList({ queue, selectedId, className, onSelect }: ConversationListProps) {
  const { token } = useAuth()
  // Debounced: quem segura o timer é o provider, porque este componente remonta a
  // cada evento de WebSocket (a `key` do InboxView) e um timer local reiniciaria.
  const { queryDebounced: busca, setQuery } = useInboxSearch()
  const toast = useToast()
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [loading, setLoading] = useState(false)
  const [refreshing, setRefreshing] = useState(false)
  const router = useRouter()
  const abas = useRef<(HTMLAnchorElement | null)[]>([])
  const aba = TABS.find((t) => t.key === queue) ?? TABS[2]

  const fetchConversations = useCallback(async (silent = false) => {
    if (!token) return
    if (silent) setRefreshing(true)
    else setLoading(true)
    try {
      const params = busca ? { search: busca } : undefined
      let data
      if (queue === 'all') data = await api.conversations.all(token, params)
      else if (queue === 'mine') data = await api.conversations.mine(token, params)
      else data = await api.conversations.list(token, params)
      setConversations(data.conversations)
    } catch {
      // NÃO zerar a lista: mostrar fila vazia com a API fora é a UI mentindo, e faz o
      // atendente parar de procurar. Mantém o que já estava na tela e avisa.
      toast.error('Não foi possível atualizar a lista de conversas.')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [token, queue, busca, toast])

  useEffect(() => {
    fetchConversations()
  }, [fetchConversations])

  useEffect(() => {
    if (!focarAbaAoMontar) return
    focarAbaAoMontar = false
    abas.current[TABS.findIndex((t) => t.key === queue)]?.focus()
  }, [queue])

  return (
    <div
      className={cn(
        // Ocupa a tela toda no mobile (é o painel "mestre") e volta a ser coluna
        // fixa a partir de lg.
        'w-full lg:w-[300px] lg:flex-shrink-0 border-r border-line flex-col h-full bg-surface',
        className
      )}
    >
      <div className="px-3 pt-3 pb-2.5 border-b border-line">
        {/* Abas que navegam: cada fila é uma rota. O padrão `tablist` vem por cima — setas
            trocam de fila, e só a ativa entra no Tab (roving tabindex). */}
        <div role="tablist" aria-label="Filas" className="flex gap-1 rounded-[10px] bg-subtle p-1">
          {TABS.map((t, i) => (
            <Link
              key={t.key}
              ref={(el) => {
                abas.current[i] = el
              }}
              href={t.href}
              role="tab"
              aria-selected={queue === t.key}
              tabIndex={queue === t.key ? 0 : -1}
              onKeyDown={(e) => {
                if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return
                e.preventDefault()
                const j = (i + (e.key === 'ArrowRight' ? 1 : TABS.length - 1)) % TABS.length
                focarAbaAoMontar = true
                router.push(TABS[j].href)
              }}
              className={cn(
                'flex-1 flex items-center justify-center h-8 coarse:h-11 rounded-lg px-2.5 text-13 font-medium whitespace-nowrap transition-colors',
                'focus-visible:outline-none focus-visible:shadow-ring',
                queue === t.key ? 'bg-surface text-ink shadow-card' : 'text-muted hover:text-ink'
              )}
            >
              {t.label}
            </Link>
          ))}
        </div>
        <div className="flex items-center justify-between mt-2 px-1">
          <span className="text-xs text-faint">
            {/* Com busca ativa o rótulo muda: senão o filtro fica invisível e o
                número parece o tamanho real da fila. */}
            {busca
              ? `${conversations.length} ${conversations.length === 1 ? 'resultado' : 'resultados'}`
              : `${conversations.length} ${conversations.length === 1 ? 'conversa' : 'conversas'}`}
          </span>
          <button
            onClick={() => fetchConversations(true)}
            disabled={refreshing || loading}
            title="Atualizar conversas"
            className="flex items-center gap-1 -mr-2 px-2 py-2 rounded-lg text-xs text-muted hover:text-ink hover:bg-subtle disabled:opacity-40 transition-colors"
          >
            <RefreshCw size={14} className={cn(refreshing && 'animate-spin')} />
            Atualizar
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto py-1">
        {loading && (
          <div className="flex items-center justify-center gap-2 py-8 text-faint">
            <Spinner />
            <span className="text-xs">Carregando…</span>
          </div>
        )}
        {/* Vazio por busca ≠ vazio de verdade. Confundir os dois faz o atendente
            achar que a fila esvaziou. */}
        {!loading && conversations.length === 0 && (
          busca ? (
            <EmptyState
              title={`Nada encontrado para “${busca}”`}
              description="A busca olha nome, telefone e a última mensagem desta fila."
              action={
                <Button size="sm" variant="ghost" onClick={() => setQuery('')}>
                  Limpar busca
                </Button>
              }
            />
          ) : (
            <EmptyState icon={Inbox} title={aba.vazio} description={aba.quando} />
          )
        )}
        {!loading && conversations.map((conv) => {
          const isSelected = conv.id === selectedId
          const naoLida = conv.unread_count > 0
          return (
            <button
              key={conv.id}
              onClick={() => onSelect(conv)}
              aria-current={isSelected || undefined}
              className={cn(
                'w-[calc(100%-12px)] mx-1.5 my-0.5 p-3 flex gap-3 items-start text-left rounded-lg transition-colors',
                'focus-visible:outline-none focus-visible:shadow-ring',
                isSelected ? 'bg-accent-soft' : 'hover:bg-subtle'
              )}
            >
              <Avatar name={conv.contact_name} src={conv.contact_avatar_url} tone="categorico" />
              <div className="flex-1 min-w-0">
                <div className="flex items-baseline gap-2">
                  {/* Nunca o external_id: com remetente `@lid` ele é um identificador
                      opaco, e a lista mostrava "21711730823330@lid" como se fosse nome.
                      Sem nome, o número aparece como dado: mono. */}
                  <span
                    className={cn(
                      'flex-1 min-w-0 truncate text-ink',
                      conv.contact_name ? 'text-sm' : 'font-mono text-13 tabular-nums',
                      naoLida ? 'font-semibold' : 'font-medium'
                    )}
                  >
                    {conv.contact_name ?? conv.contact_phone ?? 'Desconhecido'}
                  </span>
                  <span
                    className={cn(
                      'flex-shrink-0 font-mono text-2xs tabular-nums',
                      naoLida ? 'text-accent-fg' : 'text-faint'
                    )}
                  >
                    {formatTime(conv.last_message_at)}
                  </span>
                </div>
                <div className="flex items-center gap-2 mt-0.5">
                  {/* Conversa resolvida continua na fila "Todas" — é o histórico. Sem
                      este selo, o cliente que voltou depois de ser atendido aparece duas
                      vezes com a mesma cara, e a lista parece ter duplicado o contato. */}
                  {conv.status === 'resolved' && (
                    <span className="text-2xs font-semibold text-faint flex-shrink-0">Resolvida</span>
                  )}
                  <span className={cn('flex-1 min-w-0 truncate text-13', naoLida ? 'text-ink-2' : 'text-muted')}>
                    {conv.last_message_preview ?? ''}
                  </span>
                  {naoLida && (
                    <span
                      aria-label={`${conv.unread_count} não lidas`}
                      className="flex-shrink-0 min-w-[18px] h-[18px] px-[5px] rounded-full bg-accent text-on-accent text-2xs font-semibold tabular-nums flex items-center justify-center"
                    >
                      {conv.unread_count}
                    </span>
                  )}
                </div>
                {/* Só etiqueta que muda a ordem de atendimento. */}
                {conv.priority === 'high' && (
                  <div className="mt-1.5">
                    <Badge variant="danger">Urgente</Badge>
                  </div>
                )}
              </div>
            </button>
          )
        })}
      </div>
    </div>
  )
}
