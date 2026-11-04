'use client'
import { useEffect, useState } from 'react'
import { ChevronLeft } from 'lucide-react'
import { useAuth } from '@/contexts/AuthContext'
import { api } from '@/lib/api'
import type { ContactProfile, Conversation, User } from '@/types/api'
import { channelDotClass, channelLabel } from '@/lib/channels'
import { cn } from '@/lib/utils'
import { Avatar } from '@/components/ui/avatar'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
} from '@/components/ui/dropdown-menu'

const STATUS_LABELS: Record<string, string> = {
  open: 'Aberta',
  pending: 'Em atendimento',
  resolved: 'Resolvida',
}

// Por que a IA ficou calada no último turno. Espelha bot_orchestrator.SkipReason —
// só os motivos acionáveis: 'conversa_capturada' é o funcionamento esperado depois de
// assumir, e avisar sobre ele viraria ruído permanente na thread.
const BOT_SKIP_LABELS: Record<string, string> = {
  ia_desligada: 'A IA não respondeu: o atendimento automático está desligado em Agente.',
  mensagem_antiga:
    'A IA não respondeu: a mensagem chegou atrasada demais (proteção contra o backlog do WhatsApp).',
  sessao_desativada:
    'A IA atingiu o limite de respostas nesta conversa. Libere a conversa para reativá-la.',
  llm_indisponivel:
    'A IA não respondeu: o provedor de IA falhou. Teste a conexão em Agente.',
  // Difere de 'sessao_desativada' na AÇÃO: aqui não há nada a fazer, o agente volta
  // sozinho. Prometer isso só é honesto porque bot_disabled_until existe de verdade.
  llm_em_espera:
    'A IA falhou e está em espera por alguns minutos — ela volta sozinha. Se precisar antes, libere a conversa.',
  // Modo roteiro ligado sem roteiro publicado. Sem este rótulo o bot ficaria mudo com a
  // tela dizendo que está ligado, que é o sintoma que este vocabulário existe para matar.
  fluxo_ausente:
    'O bot não respondeu: o atendimento está em modo Roteiro e esta conta não tem roteiro publicado.',
  // O único da lista em que o bot FALOU: ele começou a responder e o canal recusou no
  // meio. O cliente ficou com meia resposta, então a conversa já veio para a fila — o
  // aviso existe para o atendente saber que precisa retomar do ponto certo.
  envio_falhou:
    'O cliente não recebeu a resposta inteira: o envio falhou no meio. Confira a conexão em Configurações › WhatsApp e retome a conversa.',
}

// Rótulos amigáveis para as chaves que a IA coleta. Chave não mapeada aparece com o
// underscore virando espaço — a lista é aberta de propósito, cada negócio coleta o seu.
function rotuloCampo(chave: string): string {
  const mapa: Record<string, string> = {
    nome: 'Nome',
    tipo_de_evento: 'Tipo de evento',
    data_pretendida: 'Data pretendida',
    numero_de_convidados: 'Convidados',
    publico: 'Público',
    assunto: 'Assunto',
    segmento: 'Segmento',
    cidade_uf: 'Cidade/UF',
    em_andamento: 'Em andamento',
    relato: 'Relato',
    corretora: 'Corretora',
  }
  return mapa[chave] ?? chave.replace(/_/g, ' ')
}

interface ThreadHeaderProps {
  conversation: Conversation
  botSkipReason: string | null
  contactProfile: ContactProfile
  /** Volta para a lista no mestre-detalhe do mobile. */
  onBack?: () => void
  /** Abre o painel do cliente. O avatar + nome é o gatilho. */
  onOpenProfile: () => void
  onConversationUpdate: (conv: Conversation) => void
}

export default function ThreadHeader({ conversation, botSkipReason, contactProfile, onBack, onOpenProfile, onConversationUpdate }: ThreadHeaderProps) {
  const { token, userId } = useAuth()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [transferOpen, setTransferOpen] = useState(false)
  const [colegas, setColegas] = useState<User[] | null>(null)

  const isMine = conversation.assigned_user_id === userId
  const isClaimed = conversation.assigned_user_id !== null
  const isResolved = conversation.status === 'resolved'
  const botAviso = botSkipReason ? BOT_SKIP_LABELS[botSkipReason] : undefined
  const intake = Object.entries(contactProfile?.intake ?? {}).filter(([, v]) => String(v).trim())
  const temMemoria = intake.length > 0 || Boolean(contactProfile?.last_summary)
  const chDot = channelDotClass(conversation.contact_channel)
  const chLabel = channelLabel(conversation.contact_channel)

  async function handleClaim() {
    if (!token) return
    setLoading(true)
    setError(null)
    try {
      const updated = await api.conversations.claim(token, conversation.id)
      onConversationUpdate(updated)
    } catch (err: unknown) {
      const status = (err as { status?: number }).status
      if (status === 409) {
        setError('Esta conversa já foi assumida por outro atendente.')
      } else {
        setError('Não foi possível assumir a conversa.')
      }
    } finally {
      setLoading(false)
    }
  }

  // Fecha ao trocar de conversa: o painel aberto pertencia à conversa anterior.
  // (Clicar fora, Escape, foco e navegação por setas são do DropdownMenu.)
  useEffect(() => {
    setTransferOpen(false)
  }, [conversation.id])

  async function abrirTransferencia(aberto: boolean) {
    setTransferOpen(aberto)
    if (!aberto) return
    setError(null)
    if (colegas !== null || !token) return
    try {
      const { users } = await api.users.list(token)
      // Fora eu mesmo: transferir para si é um no-op que o backend aceitaria em
      // silêncio (o WHERE casa), deixando o atendente achar que fez algo.
      setColegas(users.filter((u) => u.id !== userId))
    } catch {
      setError('Não foi possível carregar a equipe.')
      setColegas([])
    }
  }

  async function handleTransfer(toUserId: string) {
    if (!token) return
    setLoading(true)
    setError(null)
    try {
      const updated = await api.conversations.transfer(token, conversation.id, toUserId)
      setTransferOpen(false)
      onConversationUpdate(updated)
    } catch (err: unknown) {
      const status = (err as { status?: number }).status
      // 409 = o UPDATE atômico não casou: alguém liberou ou assumiu no meio do caminho.
      setError(
        status === 409
          ? 'Esta conversa não é mais sua — não foi possível transferir.'
          : 'Não foi possível transferir a conversa.',
      )
    } finally {
      setLoading(false)
    }
  }

  // Resolver dá fim de vida à conversa. Sem isso nada nunca saía do 'aberto': os KPIs de
  // resolução ficavam sempre zerados e a MESMA conversa era reusada para sempre (a
  // ingestão reaproveita a conversa não-resolvida do contato), crescendo sem limite.
  async function handleStatus(next: 'resolved' | 'open') {
    if (!token) return
    setLoading(true)
    setError(null)
    try {
      const updated = await api.conversations.updateStatus(token, conversation.id, next)
      onConversationUpdate(updated)
    } catch {
      setError(
        next === 'resolved'
          ? 'Não foi possível resolver a conversa.'
          : 'Não foi possível reabrir a conversa.',
      )
    } finally {
      setLoading(false)
    }
  }

  async function handleRelease() {
    if (!token) return
    setLoading(true)
    setError(null)
    try {
      const updated = await api.conversations.release(token, conversation.id)
      onConversationUpdate(updated)
    } catch {
      setError('Não foi possível liberar a conversa.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="bg-surface border-b border-line">
      {/* flex-wrap: em 375px o nome + 3 ações não cabem numa linha só; as ações
          descem em vez de estourar a largura. */}
      <div className="flex flex-wrap items-center justify-between gap-2 px-3 sm:px-[18px] py-[13px]">
        <div className="flex items-center gap-2 sm:gap-3 min-w-0 flex-1">
          {/* Só existe abaixo de lg: acima, a lista está do lado e não há de onde voltar. */}
          {onBack && (
            <button
              onClick={onBack}
              aria-label="Voltar para a lista de conversas"
              className="lg:hidden -ml-1 h-11 w-11 flex items-center justify-center rounded-lg text-muted hover:bg-subtle hover:text-ink transition-colors flex-shrink-0"
            >
              <ChevronLeft size={20} />
            </button>
          )}
          {/* Avatar + nome é o gatilho do perfil do cliente. É <button> e não uma div
              com onClick: precisa de foco por teclado e de nome acessível, porque é o
              acesso ao histórico e às anotações da pessoa. */}
          <button
            onClick={onOpenProfile}
            aria-label={`Ver perfil de ${conversation.contact_name ?? conversation.contact_phone ?? 'cliente'}`}
            className="flex items-center gap-2 sm:gap-3 min-w-0 text-left rounded-lg -mx-1 px-1 py-0.5 hover:bg-subtle transition-colors"
          >
            <Avatar
              name={conversation.contact_name}
              src={conversation.contact_avatar_url}
            />
            <div className="min-w-0">
              <p
                className={cn(
                  'font-semibold text-ink truncate',
                  conversation.contact_name ? 'text-15' : 'font-mono text-sm tabular-nums'
                )}
              >
                {conversation.contact_name ?? conversation.contact_phone ?? 'Desconhecido'}
              </p>
              <div className="flex items-center gap-[6px]">
                <span className={cn('w-[6px] h-[6px] rounded-full', chDot)} />
                <span className="text-xs font-medium text-muted">
                  {chLabel} · {STATUS_LABELS[conversation.status] ?? conversation.status}
                </span>
              </div>
            </div>
          </button>
        </div>

        <div className="flex items-center gap-2 flex-shrink-0">
          {isResolved ? (
            // Conversa resolvida sai de todas as filas — reabrir é o único caminho de
            // volta, então o botão não pode depender de ser o dono.
            <Button variant="secondary" size="sm" onClick={() => handleStatus('open')} disabled={loading}>
              {loading ? 'Reabrindo…' : 'Reabrir'}
            </Button>
          ) : isMine ? (
            <>
              {/* Só aparece quando a conversa é sua: o UPDATE atômico exige
                  assigned_user_id = solicitante, então em qualquer outro estado
                  o botão seria um 409 garantido. */}
              <DropdownMenu open={transferOpen} onOpenChange={abrirTransferencia}>
                <DropdownMenuTrigger asChild>
                  <Button variant="secondary" size="sm" disabled={loading}>
                    Transferir
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent className="max-h-72">
                  <DropdownMenuLabel>Transferir para</DropdownMenuLabel>
                  {colegas === null ? (
                    <div className="px-2.5 py-2 text-[12.5px] text-faint">Carregando…</div>
                  ) : colegas.length === 0 ? (
                    <div className="px-2.5 py-2 text-[12.5px] text-faint leading-relaxed max-w-[220px]">
                      Ninguém mais na equipe. Convide um atendente em Configurações › Equipe.
                    </div>
                  ) : (
                    colegas.map((u) => (
                      <DropdownMenuItem
                        key={u.id}
                        disabled={loading}
                        onSelect={() => handleTransfer(u.id)}
                        className="flex-col items-start gap-0"
                      >
                        <span className="text-[13px] font-medium truncate w-full">
                          {u.full_name || u.email}
                        </span>
                        {u.full_name && (
                          <span className="text-[11.5px] text-faint truncate w-full">{u.email}</span>
                        )}
                      </DropdownMenuItem>
                    ))
                  )}
                </DropdownMenuContent>
              </DropdownMenu>
              <Button variant="secondary" size="sm" onClick={handleRelease} disabled={loading}>
                {loading ? 'Liberando…' : 'Liberar'}
              </Button>
              <Button variant="success" size="sm" onClick={() => handleStatus('resolved')} disabled={loading}>
                Resolver
              </Button>
            </>
          ) : !isClaimed ? (
            <Button size="sm" onClick={handleClaim} disabled={loading}>
              {loading ? 'Assumindo…' : 'Assumir'}
            </Button>
          ) : (
            <span className="text-xs font-medium text-warning-fg bg-warning-soft rounded-full px-3 py-[6px]">
              Em atendimento
            </span>
          )}
        </div>
      </div>

      {isMine && (
        <div className="mx-[18px] mb-3 bg-accent-soft text-accent-fg text-xs font-semibold text-center rounded-xl px-3 py-[9px]">
          Você assumiu esta conversa — só você pode responder agora.
        </div>
      )}

      {/* Memória do cliente. Só aparece quando há algo de atendimentos ANTERIORES —
          num primeiro contato seria uma caixa vazia ocupando o topo da conversa. */}
      {temMemoria && (
        <div className="mx-[18px] mb-3 bg-canvas border border-line rounded-xl px-3.5 py-2.5 flex flex-col gap-1.5">
          <span className="text-[10.5px] font-bold uppercase tracking-wide text-faint">
            O que já sabemos deste cliente
          </span>
          {intake.length > 0 && (
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              {intake.map(([chave, valor]) => (
                <span key={chave} className="text-[12px] text-muted">
                  <span className="text-faint">{rotuloCampo(chave)}:</span>{' '}
                  <span className="font-medium text-ink">{String(valor)}</span>
                </span>
              ))}
            </div>
          )}
          {contactProfile?.last_summary && (
            <span className="text-[12px] text-muted leading-relaxed">
              {contactProfile.last_summary}
            </span>
          )}
        </div>
      )}

      {botAviso && (
        <div className="mx-[18px] mb-3 bg-warning-soft text-warning-fg border border-warning-line text-xs font-medium rounded-xl px-3 py-[9px] text-center">
          {botAviso}
        </div>
      )}

      {error && (
        <div className="mx-[18px] mb-3 text-xs font-medium text-danger-fg text-center">
          {error}
        </div>
      )}
    </div>
  )
}
