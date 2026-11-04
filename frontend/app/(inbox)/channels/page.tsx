'use client'
import { useState, useEffect, useCallback, useRef } from 'react'
import Link from 'next/link'
import { useAuth } from '@/contexts/AuthContext'
import { useToast } from '@/contexts/ToastContext'
import { api } from '@/lib/api'
import type { ChannelConnection, ChannelState, ChannelStatus } from '@/types/api'
import {
  Bot,
  Check,
  ChevronRight,
  MessageCircle,
  MoreHorizontal,
  RefreshCw,
  Smartphone,
  UserPlus,
  Zap,
  type LucideIcon,
} from 'lucide-react'
import PageShell from '@/components/PageShell'
import { Logo } from '@/components/Logo'
import { Badge, type BadgeProps } from '@/components/ui/badge'
import { Button, buttonVariants } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Field } from '@/components/ui/field'
import { Card } from '@/components/ui/card'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { Spinner } from '@/components/ui/spinner'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'

// Janela que a tela dá para ler o código. O Evolution troca o QR sozinho durante a
// sessão (o polling já busca o novo); passado isto o polling para e a tela pede um
// código novo, em vez de ficar consultando para sempre uma aba esquecida aberta.
const QR_SEGUNDOS = 300

const PASSOS = ['Nome', 'Escanear', 'Pronto'] as const

function Passos({ atual }: { atual: 0 | 1 | 2 }) {
  return (
    <ol aria-label="Etapas" className="flex flex-wrap items-center gap-1.5 sm:gap-2">
      {PASSOS.map((nome, i) => (
        <li
          key={nome}
          aria-current={i === atual ? 'step' : undefined}
          className={cn(
            'flex items-center gap-2 text-13',
            i === atual ? 'font-semibold text-ink' : i < atual ? 'font-medium text-ink' : 'font-medium text-faint'
          )}
        >
          {i > 0 && (
            <span
              aria-hidden="true"
              className={cn('h-0.5 w-4 flex-none rounded-full sm:w-7', i <= atual ? 'bg-sky' : 'bg-line')}
            />
          )}
          <span
            className={cn(
              'flex h-6 w-6 items-center justify-center rounded-full font-mono text-xs',
              i < atual && 'bg-sky text-on-sky',
              i === atual && 'bg-accent text-on-accent',
              i > atual && 'border-[1.5px] border-line-strong'
            )}
          >
            {i < atual ? <Check size={14} strokeWidth={2.25} aria-hidden="true" /> : i + 1}
          </span>
          {nome}
          {i < atual && <span className="sr-only">(concluída)</span>}
        </li>
      ))}
    </ol>
  )
}

const INSTRUCOES = [
  <>Abra o <strong className="font-semibold text-ink">WhatsApp</strong>.</>,
  <>Toque em <strong className="font-semibold text-ink">Mais opções</strong> no Android ou em <strong className="font-semibold text-ink">Configurações</strong> no iPhone.</>,
  <>Entre em <strong className="font-semibold text-ink">Aparelhos conectados</strong> e toque em <strong className="font-semibold text-ink">Conectar um aparelho</strong>.</>,
  <>Aponte a câmera para o código.</>,
]

const PROXIMOS: { href: string; icon: LucideIcon; tom: string; titulo: string; texto: string }[] = [
  { href: '/agente', icon: Bot, tom: 'bg-ai-soft text-ai-fg', titulo: 'Ligar o agente de IA', texto: 'Responde quando ninguém da equipe está online' },
  { href: '/equipe', icon: UserPlus, tom: 'bg-accent-soft text-accent-fg', titulo: 'Convidar a equipe', texto: 'Cada atendente entra com o próprio acesso' },
  { href: '/respostas', icon: Zap, tom: 'bg-subtle text-muted', titulo: 'Criar respostas rápidas', texto: 'Atalhos com / para o que se repete' },
]

function Conectado({ label, onOutro }: { label: string | null; onOutro: () => void }) {
  return (
    <div className="flex flex-col gap-4">
      <section className="flex flex-col items-center gap-1.5 rounded-[20px] bg-sky px-5 pb-6 pt-7 text-center text-on-sky">
        <div className="relative mb-2.5">
          <Logo variant="icon" className="h-16 w-16 rounded-[19px] shadow-pop" />
          <span className="absolute -bottom-1.5 -right-1.5 flex h-[26px] w-[26px] items-center justify-center rounded-full border-[3px] border-sky bg-success text-on-success">
            <Check size={14} strokeWidth={2.5} aria-hidden="true" />
          </span>
        </div>
        <h2 aria-live="polite" className="font-display text-2xl font-semibold tracking-[-0.02em]">
          WhatsApp conectado
        </h2>
        <p className="text-sm">
          As mensagens de <strong className="font-semibold">{label ?? 'este número'}</strong> já chegam na inbox.
        </p>
      </section>

      <Card padding="none" className="overflow-hidden">
        <h3 className="px-4 pb-1 pt-3.5 text-2xs font-semibold uppercase tracking-[0.04em] text-faint">
          Próximos passos
        </h3>
        <ul className="divide-y divide-line">
          {PROXIMOS.map(({ href, icon: Icon, tom, titulo, texto }) => (
            <li key={href}>
              <Link
                href={href}
                className="flex min-h-[60px] items-center gap-3 px-4 py-2.5 transition-colors duration-[120ms] hover:bg-subtle motion-reduce:transition-none"
              >
                <span className={cn('flex h-9 w-9 flex-none items-center justify-center rounded-[9px]', tom)}>
                  <Icon size={18} strokeWidth={1.75} aria-hidden="true" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-sm font-medium text-ink">{titulo}</span>
                  <span className="block text-13 text-muted">{texto}</span>
                </span>
                <ChevronRight size={16} className="text-faint" aria-hidden="true" />
              </Link>
            </li>
          ))}
        </ul>
      </Card>

      <p className="px-1 text-13 text-muted">
        Mantenha o celular com internet. Sem abrir o WhatsApp por 14 dias, a conexão cai.
      </p>
      <div className="flex flex-col gap-2 sm:flex-row">
        <Link href="/inbox" className={cn(buttonVariants(), 'sm:flex-1')}>
          Abrir a inbox
        </Link>
        <Button variant="ghost" onClick={onOutro}>
          Conectar outro número
        </Button>
      </div>
    </div>
  )
}

// O que o estado do provider quer dizer na lista. `falhou` é a consulta de status que
// não respondeu: vira um badge que diz isso, nunca "Conectado" por omissão.
const ESTADO: Record<ChannelState | 'falhou', { rotulo: string; badge: BadgeProps['variant']; icone: string }> = {
  connected: { rotulo: 'Conectado', badge: 'success', icone: 'bg-success-soft text-success-fg' },
  connecting: { rotulo: 'Conectando', badge: 'warning', icone: 'bg-warning-soft text-warning-fg' },
  qr_pending: { rotulo: 'Desconectado', badge: 'danger', icone: 'bg-danger-soft text-danger-fg' },
  disconnected: { rotulo: 'Desconectado', badge: 'danger', icone: 'bg-danger-soft text-danger-fg' },
  error: { rotulo: 'Sem status', badge: 'neutral', icone: 'bg-subtle text-muted' },
  falhou: { rotulo: 'Sem status', badge: 'neutral', icone: 'bg-subtle text-muted' },
}

const diaMes = (iso: string) =>
  new Date(iso).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })

export default function ChannelsPage() {
  const { token } = useAuth()
  const toast = useToast()
  const [connections, setConnections] = useState<ChannelConnection[]>([])
  const [estados, setEstados] = useState<Record<string, ChannelState | 'falhou'>>({})
  const [loading, setLoading] = useState(true)
  const [instance, setInstance] = useState('')
  const [connecting, setConnecting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Falha ao carregar o conteúdo principal da tela: vira estado do painel, não lista
  // vazia (`frontend/CLAUDE.md` §5).
  const [erroPainel, setErroPainel] = useState<string | null>(null)

  const [pairingId, setPairingId] = useState<string | null>(null)
  // Remover conexão desconecta o número do WhatsApp do negócio — antes isso
  // acontecia no primeiro clique, sem pergunta nenhuma.
  const [removerId, setRemoverId] = useState<string | null>(null)
  const [removendo, setRemovendo] = useState(false)
  const [pairStatus, setPairStatus] = useState<ChannelStatus | null>(null)
  const [restante, setRestante] = useState(0)
  // O Evolution responde `connecting` antes de gerar o primeiro QR E depois da leitura.
  // Só dá para dizer "código lido" se esta sessão já mostrou um código.
  const [viuQr, setViuQr] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const fetchConnections = useCallback(async () => {
    if (!token) return
    setLoading(true)
    try {
      const data = await api.channels.list(token)
      setConnections(data.connections)
      setErroPainel(null)
      // A lista não traz estado; ele vem do status de cada conexão, sem segurar a tela.
      // ponytail: para conexão caída, o status do Evolution abre uma sessão de QR
      // (`/instance/connect`). Uma rota só de leitura no back tira esse efeito.
      Promise.allSettled(data.connections.map((c) => api.channels.status(token, c.id))).then((r) =>
        setEstados(
          Object.fromEntries(
            data.connections.map((c, i) => {
              const res = r[i]
              return [c.id, res.status === 'fulfilled' ? res.value.state : 'falhou']
            })
          )
        )
      )
    } catch (e) {
      // Antes isto fazia `setConnections([])`: com a API fora do ar a tela dizia
      // "nenhum número conectado", e o caminho natural era parear de novo um número que
      // já estava conectado. Fingir sucesso é pior que erro (`frontend/CLAUDE.md` §5).
      setErroPainel((e as Error).message || 'Não foi possível carregar as conexões.')
    } finally {
      setLoading(false)
    }
  }, [token])

  useEffect(() => {
    fetchConnections()
  }, [fetchConnections])

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }, [])

  const startPairing = useCallback(
    (id: string) => {
      if (!token) return
      stopPolling()
      setPairingId(id)
      setPairStatus(null)
      setRestante(QR_SEGUNDOS)
      setViuQr(false)
      const poll = async () => {
        try {
          const s = await api.channels.status(token, id)
          setPairStatus(s)
          if (s.qr_code) setViuQr(true)
          setEstados((e) => ({ ...e, [id]: s.state }))
          if (s.state === 'connected') {
            stopPolling()
            fetchConnections()
          }
        } catch { /* keep trying */ }
      }
      poll()
      pollRef.current = setInterval(poll, 3000)
    },
    [token, stopPolling, fetchConnections]
  )

  useEffect(() => stopPolling, [stopPolling])

  const pairState = pairStatus?.state
  const aguardando = pairingId !== null && pairState !== 'connected'

  // Contagem regressiva do código. Ao zerar, o polling para: quem voltar à aba vê
  // "Código expirado" e pede outro, em vez de um QR que o Evolution já descartou.
  useEffect(() => {
    if (!aguardando) return
    if (restante === 0) {
      stopPolling()
      return
    }
    const t = setTimeout(() => setRestante((r) => r - 1), 1000)
    return () => clearTimeout(t)
  }, [aguardando, restante, stopPolling])

  async function handleConnect() {
    if (!token || !instance.trim()) return
    setConnecting(true)
    setError(null)
    try {
      const conn = await api.channels.connectWhatsApp(token, instance.trim())
      setInstance('')
      await fetchConnections()
      startPairing(conn.id)
    } catch (e) {
      setError((e as Error).message || 'Não deu para criar a conexão.')
    } finally {
      setConnecting(false)
    }
  }

  function resetPairing() {
    stopPolling()
    setPairingId(null)
    setPairStatus(null)
  }

  async function handleRemove(id: string) {
    if (!token) return
    if (pairingId === id) resetPairing()
    setRemovendo(true)
    try {
      await api.channels.remove(token, id)
      await fetchConnections()
      setRemoverId(null)
    } catch (e) {
      // Toast e não o erro do campo: a remoção não tem lugar na tela, e durante o
      // pareamento o campo nem está visível.
      toast.error((e as Error).message || 'Não deu para remover a conexão. Tente de novo.')
    } finally {
      setRemovendo(false)
    }
  }

  const pairingConn = connections.find((c) => c.id === pairingId) ?? null
  const expirou = aguardando && restante === 0
  const removerConn = connections.find((c) => c.id === removerId)

  return (
    <>
      <PageShell largura="grade" className="gap-7 lg:py-8">
        <header>
          <h1 className="font-display text-2xl font-semibold tracking-[-0.018em] text-ink">WhatsApp</h1>
          <p className="mt-1 max-w-[560px] text-sm text-muted">
            Conecte o número da empresa. Toda mensagem que chegar nele aparece na inbox, e qualquer
            atendente pode responder daqui.
          </p>
        </header>

        {pairState === 'connected' ? (
          <div className="max-w-[480px]">
            <Conectado label={pairingConn?.label ?? null} onOutro={resetPairing} />
          </div>
        ) : (
          <section
            aria-labelledby="t-conectar"
            // Empilhado, o código vem PRIMEIRO: quem pareia lê com outro aparelho, e o QR
            // abaixo das instruções ficava fora da tela no celular.
            className="flex flex-col-reverse overflow-hidden rounded-2xl border border-line bg-surface xl:flex-row"
          >
            <div className="flex min-w-0 flex-col gap-6 p-5 sm:px-8 sm:py-7 xl:flex-[1_1_420px]">
              <div>
                <h2 id="t-conectar" className="text-15 font-semibold text-ink">Conectar um número</h2>
                <p className="mt-0.5 text-13 text-muted">Leva menos de um minuto, com o celular da empresa na mão.</p>
              </div>

              <Passos atual={pairingId ? 1 : 0} />

              {pairingId ? (
                <div className="flex items-center gap-3 rounded-xl bg-subtle px-3 py-2.5">
                  <span className="flex h-8 w-8 flex-none items-center justify-center rounded-lg bg-surface text-muted shadow-card">
                    <MessageCircle size={16} strokeWidth={1.75} aria-hidden="true" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="text-xs text-faint">Nome da conexão</div>
                    <div className="truncate text-sm font-medium text-ink">{pairingConn?.label ?? '—'}</div>
                  </div>
                  <Button variant="ghost" size="sm" onClick={resetPairing}>
                    Cancelar
                  </Button>
                </div>
              ) : (
                <Field label="Nome da conexão" hint="letras, números, - e _" htmlFor="instancia" error={error ?? undefined}>
                  {/* Empilha no mobile: o campo não encolhe abaixo da largura intrínseca
                      de um <input> (~200px), e lado a lado o botão saía do card em 375px. */}
                  <div className="flex flex-col gap-2 sm:flex-row">
                    <Input
                      id="instancia"
                      value={instance}
                      onChange={(e) => setInstance(e.target.value)}
                      onKeyDown={(e) => e.key === 'Enter' && handleConnect()}
                      placeholder="atendimento"
                      className="min-w-0 sm:flex-1"
                    />
                    <Button onClick={handleConnect} disabled={connecting || !instance.trim()}>
                      {connecting ? 'Gerando código…' : 'Gerar código'}
                    </Button>
                  </div>
                </Field>
              )}

              <div>
                <p className="mb-2.5 text-13 font-medium text-ink">No celular com o número da empresa:</p>
                <ol className="flex flex-col gap-3">
                  {INSTRUCOES.map((passo, i) => (
                    <li key={i} className="flex items-start gap-3 text-sm text-ink-2">
                      <span className="flex h-[22px] w-[22px] flex-none items-center justify-center rounded-md bg-accent-soft font-mono text-xs text-accent-fg">
                        {i + 1}
                      </span>
                      <span>{passo}</span>
                    </li>
                  ))}
                </ol>
              </div>

              <div className="mt-auto flex items-start gap-2.5 rounded-xl border border-line px-3.5 py-3 text-13 text-muted">
                <Smartphone size={16} strokeWidth={1.75} className="mt-0.5 flex-none" aria-hidden="true" />
                <span>
                  O celular precisa continuar com internet. Se ele ficar 14 dias sem abrir o WhatsApp,
                  a conexão cai e é preciso escanear de novo.
                </span>
              </div>
            </div>

            {/* O lado do código. Antes do nome ele só ocupa espaço: empilhado, some. */}
            <div
              className={cn(
                'min-w-0 flex-col items-center justify-center gap-4 bg-sky p-8 text-center text-on-sky xl:flex-[1_1_380px]',
                pairingId ? 'flex' : 'hidden xl:flex'
              )}
            >
              {pairState === 'connecting' && viuQr ? (
                <>
                  <Spinner />
                  <div aria-live="polite">
                    <p className="text-sm font-semibold">Código lido. Confirmando a conexão…</p>
                    <p className="text-13">Isso leva alguns segundos.</p>
                  </div>
                </>
              ) : (
                <>
                  {pairStatus?.qr_code ? (
                    /* eslint-disable-next-line @next/next/no-img-element */
                    <img
                      src={pairStatus.qr_code}
                      alt="QR code de pareamento"
                      className={cn(
                        'h-[min(261px,70vw)] w-[min(261px,70vw)] rounded-[20px] shadow-pop',
                        expirou && 'opacity-30'
                      )}
                    />
                  ) : (
                    <div className="flex h-[min(261px,70vw)] w-[min(261px,70vw)] items-center justify-center rounded-[20px] border-2 border-dashed border-on-sky/25 px-8 text-13">
                      {pairingId ? (
                        <span className="flex flex-col items-center gap-2">
                          <Spinner />
                          Gerando o código…
                        </span>
                      ) : (
                        'O código aparece aqui depois do nome.'
                      )}
                    </div>
                  )}
                  {pairingId && (
                    <div className="flex flex-col items-center gap-1.5">
                      <p aria-live="polite" className="flex items-center gap-2 text-sm font-semibold">
                        {!expirou && (
                          <span
                            className="h-2 w-2 animate-pulse rounded-full bg-accent motion-reduce:animate-none"
                            aria-hidden="true"
                          />
                        )}
                        {expirou ? 'Código expirado' : 'Aguardando leitura'}
                      </p>
                      {!expirou && (
                        <p className="text-13">
                          O código expira em{' '}
                          {/* role=timer e não aria-live: o leitor de tela não deve anunciar
                              cada segundo; o que muda de estado é anunciado na linha acima. */}
                          <span role="timer" className="font-mono font-medium tabular-nums">
                            {Math.floor(restante / 60)}:{String(restante % 60).padStart(2, '0')}
                          </span>
                        </p>
                      )}
                    </div>
                  )}
                  {pairingId && (
                    <Button
                      variant="secondary"
                      size="sm"
                      className="border-transparent"
                      onClick={() => startPairing(pairingId)}
                    >
                      <RefreshCw size={14} strokeWidth={1.75} aria-hidden="true" />
                      Gerar novo código
                    </Button>
                  )}
                </>
              )}
            </div>
          </section>
        )}

        {/* Falha ao listar: dizer que falhou, nunca "nenhum número conectado" — a tela
            vazia levava a parear de novo um número que já estava no ar. */}
        {erroPainel && (
          <Card padding="md">
            <EmptyState
              title="Não foi possível carregar as conexões"
              description={erroPainel}
              action={
                <Button size="sm" variant="secondary" onClick={fetchConnections}>
                  Tentar de novo
                </Button>
              }
            />
          </Card>
        )}

        {!loading && connections.length > 0 && (
          <section aria-labelledby="t-numeros" className="flex flex-col gap-3">
            <div>
              <h2 id="t-numeros" className="text-15 font-semibold text-ink">Números conectados</h2>
              <p className="mt-0.5 text-13 text-muted">
                As conversas de todos os números caem na mesma inbox, com o nome da conexão em cada uma.
              </p>
            </div>
            <Card padding="none" className="divide-y divide-line overflow-hidden">
              {connections.map((conn) => {
                const estado = estados[conn.id]
                const e = estado ? ESTADO[estado] : null
                return (
                  <div key={conn.id} className="flex flex-wrap items-center gap-x-4 gap-y-3 px-4 py-3.5">
                    <span
                      className={cn(
                        'flex h-9 w-9 flex-none items-center justify-center rounded-[9px]',
                        e?.icone ?? 'bg-subtle text-muted'
                      )}
                    >
                      <MessageCircle size={18} strokeWidth={1.75} aria-hidden="true" />
                    </span>
                    <div className="min-w-0 flex-[1_1_160px] truncate text-sm font-medium text-ink">
                      {conn.label ?? 'WhatsApp'}
                    </div>
                    {e ? <Badge variant={e.badge}>{e.rotulo}</Badge> : <Spinner />}
                    {estado === 'connected' && (
                      <span className="text-xs text-faint">
                        desde <span className="font-mono tabular-nums">{diaMes(conn.created_at)}</span>
                      </span>
                    )}
                    <div className="ml-auto flex items-center gap-1">
                      {estado && estado !== 'connected' && conn.id !== pairingId && (
                        <Button variant="secondary" size="sm" onClick={() => startPairing(conn.id)}>
                          Escanear de novo
                        </Button>
                      )}
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label={`Mais ações para ${conn.label ?? 'esta conexão'}`}
                          >
                            <MoreHorizontal size={16} strokeWidth={1.75} aria-hidden="true" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent>
                          <DropdownMenuItem destructive onSelect={() => setRemoverId(conn.id)}>
                            Remover conexão
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </div>
                  </div>
                )
              })}
            </Card>
          </section>
        )}
      </PageShell>

      <ConfirmDialog
        open={removerId !== null}
        onOpenChange={(o) => !o && setRemoverId(null)}
        title={`Remover ${removerConn?.label ?? 'esta conexão'}?`}
        description="O número deixa de receber e enviar mensagens por aqui. As conversas já existentes continuam na inbox. Para voltar a atender, será preciso escanear o QR code de novo."
        confirmLabel="Remover conexão"
        destructive
        loading={removendo}
        onConfirm={() => removerId && handleRemove(removerId)}
      />
    </>
  )
}
