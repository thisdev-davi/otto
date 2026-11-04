'use client'
import { useState, useCallback, useEffect, useRef } from 'react'
import { useSearchParams } from 'next/navigation'
import ConversationList from '@/components/ConversationList'
import ConversationThread from '@/components/ConversationThread'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useAuth } from '@/contexts/AuthContext'
import { useToast } from '@/contexts/ToastContext'
import { useNotificacoes } from '@/contexts/NotificacoesContext'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import type { Conversation } from '@/types/api'

export type InboxQueue = 'all' | 'unclaimed' | 'mine'

export default function InboxView({ queue }: { queue: InboxQueue }) {
  const { tenantId, token } = useAuth()
  const toast = useToast()
  const { notificar } = useNotificacoes()
  const params = useSearchParams()
  const [selectedConv, setSelectedConv] = useState<Conversation | null>(null)
  const [listRefreshKey, setListRefreshKey] = useState(0)
  const [threadRefreshKey, setThreadRefreshKey] = useState(0)

  // ── deep link: /inbox?c=<id> ──
  // A conversa selecionada é estado local, não rota, então quem chega de fora (o botão
  // "Abrir a conversa" da Agenda) precisa que alguém a busque e a selecione. O ref evita
  // reabrir a mesma conversa se a pessoa navegar para outra e o `?c=` continuar na URL.
  const deepLink = params.get('c')
  const jaAbriu = useRef<string | null>(null)
  useEffect(() => {
    if (!token || !deepLink || jaAbriu.current === deepLink) return
    jaAbriu.current = deepLink
    api.conversations
      .get(token, deepLink)
      .then(setSelectedConv)
      .catch((e: Error) => {
        // Ação que a pessoa pediu (clicou num link) e que não tem lugar na tela:
        // toast (`frontend/CLAUDE.md` §5). A lista continua utilizável atrás.
        toast.error(e.message || 'Não foi possível abrir essa conversa.')
      })
  }, [token, deepLink, toast])

  const handleWsMessage = useCallback((data: unknown) => {
    const event = data as {
      type?: string
      conversation_id?: string
      contact_name?: string
      preview?: string
      resumo?: string
    }
    if (!event.conversation_id) return

    // Só mensagem DO CLIENTE avisa. A nossa própria resposta e a do bot também passam
    // por aqui (`message.outbound`), e bipar na própria resposta é ruído puro.
    if (event.type === 'message.inbound') {
      notificar(
        event.contact_name || 'Nova mensagem',
        event.preview || 'Você recebeu uma mensagem no WhatsApp.',
      )
    }

    // O bot desistiu: esta é a única conversa que exige alguém AGORA, e por isso é a
    // única que avisa nas duas situações. Fora da aba, o mesmo canal da mensagem nova
    // (título, bip, cartão do sistema). Dentro da aba, um toast — quem está olhando a
    // lista não ouve bip nenhum por decisão do NotificacoesContext, e o 🔥 de prioridade
    // aparece calado no meio de conversas que o bot está atendendo bem.
    if (event.type === 'conversation.handoff') {
      const quem = event.contact_name?.trim() || 'Um cliente'
      notificar(
        `${quem} precisa de um atendente`,
        event.resumo || 'O bot encaminhou esta conversa para a equipe.',
      )
      toast.show({ tone: 'info', message: `${quem} precisa de um atendente.` })
    }

    // message.status só afeta a thread aberta (checks ✓✓); os demais também mexem na lista.
    if (event.type !== 'message.status') {
      setListRefreshKey((k) => k + 1)
    }
    if (event.conversation_id === selectedConv?.id) {
      setThreadRefreshKey((k) => k + 1)
    }
  }, [selectedConv?.id, notificar, toast])

  const { connected } = useWebSocket(token, handleWsMessage)

  function handleConversationUpdate(updated: Conversation) {
    setSelectedConv(updated)
  }

  return (
    // Mestre-detalhe abaixo de lg: os dois painéis somam 538px de largura fixa,
    // então em telas estreitas eles se revezam em vez de conviverem. A partir de
    // lg nada muda — as duas colunas voltam a aparecer juntas.
    <div className="flex h-full">
      <ConversationList
        key={`${queue}-${listRefreshKey}`}
        queue={queue}
        selectedId={selectedConv?.id ?? null}
        className={cn(selectedConv ? 'hidden lg:flex' : 'flex')}
        onSelect={(conv) => {
          setSelectedConv(conv)
        }}
      />
      <div
        className={cn(
          'flex-1 flex-col overflow-hidden relative min-w-0',
          selectedConv ? 'flex' : 'hidden lg:flex'
        )}
      >
        {!connected && tenantId && (
          <div className="absolute bottom-4 right-4 z-10 flex items-center gap-1.5 bg-warning-soft text-warning-fg text-[11px] font-semibold rounded-lg px-2.5 py-1.5 shadow-card border border-warning-line">
            <span className="w-1.5 h-1.5 rounded-full bg-warning animate-pulse" />
            Reconectando…
          </div>
        )}
        <ConversationThread
          conversationId={selectedConv?.id ?? null}
          conversation={selectedConv}
          onConversationUpdate={handleConversationUpdate}
          onBack={() => setSelectedConv(null)}
          refreshTrigger={threadRefreshKey}
        />
      </div>
    </div>
  )
}
