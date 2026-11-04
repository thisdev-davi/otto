'use client'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Check, Copy, Lock, Mail, MoreHorizontal, UserPlus, Users } from 'lucide-react'
import { useAuth } from '@/contexts/AuthContext'
import { useToast } from '@/contexts/ToastContext'
import { api } from '@/lib/api'
import type { Invite, Member, UserRole } from '@/types/api'
import PageShell from '@/components/PageShell'
import { Hero, List, Row, Section } from '@/components/ui/page'
import { Avatar } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Spinner } from '@/components/ui/spinner'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

const PAPEL_LABEL: Record<UserRole, string> = {
  ADMIN: 'Administrador',
  AGENT: 'Atendente',
}

function fmtData(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })
  } catch {
    return ''
  }
}

function expirou(iso: string): boolean {
  return new Date(iso).getTime() < Date.now()
}

const plural = (n: number, um: string, varios: string) => `${n} ${n === 1 ? um : varios}`

export default function EquipePage() {
  const { token, userId, isAdmin, me } = useAuth()
  const toast = useToast()
  const [members, setMembers] = useState<Member[]>([])
  const [invites, setInvites] = useState<Invite[]>([])
  const [loading, setLoading] = useState(true)
  // Falha ao carregar a equipe é estado do painel, não lista vazia (`frontend/CLAUDE.md` §5).
  const [erroPainel, setErroPainel] = useState<string | null>(null)

  // formulário de convite
  const [email, setEmail] = useState('')
  const [nome, setNome] = useState('')
  const [papel, setPapel] = useState<UserRole>('AGENT')
  const [enviando, setEnviando] = useState(false)
  const [erroConvite, setErroConvite] = useState<string | null>(null)
  const emailRef = useRef<HTMLInputElement>(null)
  // link gerado — só existe aqui, uma vez. Depois de sair da tela, não volta.
  const [linkNovo, setLinkNovo] = useState<string | null>(null)
  const [copiado, setCopiado] = useState(false)

  // Desativar tira o acesso na hora: antes era um clique só, sem pergunta.
  const [desativar, setDesativar] = useState<Member | null>(null)
  const [desativando, setDesativando] = useState(false)

  const carregar = useCallback(async () => {
    if (!token) return
    try {
      const [m, i] = await Promise.all([
        api.team.listMembers(token),
        api.team.listInvites(token),
      ])
      setMembers(m.members)
      setInvites(i.invites)
      setErroPainel(null)
    } catch (e) {
      setErroPainel((e as Error).message || 'Não foi possível carregar a equipe.')
    } finally {
      setLoading(false)
    }
  }, [token])

  useEffect(() => {
    if (isAdmin) carregar()
    else setLoading(false)
  }, [carregar, isAdmin])

  async function convidar() {
    if (!token || enviando) return
    const e = email.trim()
    if (!e) {
      setErroConvite('Informe o e-mail de quem você quer convidar.')
      return
    }
    setEnviando(true)
    setErroConvite(null)
    setLinkNovo(null)
    setCopiado(false)
    try {
      const criado = await api.team.createInvite(token, {
        email: e,
        full_name: nome.trim() || null,
        role: papel,
      })
      setLinkNovo(`${window.location.origin}/convite/${criado.token}`)
      setEmail('')
      setNome('')
      await carregar()
    } catch (err) {
      setErroConvite((err as Error).message || 'Não foi possível criar o convite.')
    } finally {
      setEnviando(false)
    }
  }

  async function copiar(link: string) {
    try {
      await navigator.clipboard.writeText(link)
      setCopiado(true)
      setTimeout(() => setCopiado(false), 2000)
    } catch {
      toast.error('Não consegui copiar — selecione e copie o link manualmente.')
    }
  }

  async function revogar(id: string) {
    if (!token) return
    try {
      await api.team.revokeInvite(token, id)
      setInvites((xs) => xs.filter((x) => x.id !== id))
    } catch (e) {
      toast.error((e as Error).message || 'Não foi possível revogar o convite.')
    }
  }

  async function alterar(m: Member, patch: { role?: UserRole; is_active?: boolean }) {
    if (!token) return
    try {
      const atualizado = await api.team.updateMember(token, m.id, patch)
      setMembers((xs) => xs.map((x) => (x.id === atualizado.id ? atualizado : x)))
    } catch (e) {
      // O backend recusa rebaixar/desativar o último admin (409) — a mensagem dele
      // já explica o porquê, então mostramos como veio.
      toast.error((e as Error).message || 'Não foi possível alterar o membro.')
    }
  }

  async function confirmarDesativar() {
    if (!desativar) return
    setDesativando(true)
    await alterar(desativar, { is_active: false })
    setDesativando(false)
    setDesativar(null)
  }

  function irParaConvite() {
    emailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    emailRef.current?.focus({ preventScroll: true })
  }

  if (!isAdmin) {
    return (
      <PageShell largura="form" className="lg:py-8">
        <Card padding="none">
          <EmptyState
            icon={Lock}
            title="Acesso restrito"
            description={`Só administradores gerenciam a equipe. Se você precisa convidar alguém, peça a um administrador do ${me ? 'seu negócio' : 'time'}.`}
          />
        </Card>
      </PageShell>
    )
  }

  const ativos = members.filter((m) => m.is_active)
  const admins = ativos.filter((m) => m.role === 'ADMIN').length

  return (
    <>
      <PageShell largura="form" className="gap-8 lg:py-8">
        {members.length > 0 && (
          <Hero
            icon={Users}
            title={`${plural(ativos.length, 'pessoa', 'pessoas')} na equipe`}
            aside={
              <Button variant="secondary" size="sm" onClick={irParaConvite}>
                <UserPlus size={14} strokeWidth={1.75} aria-hidden="true" />
                Convidar
              </Button>
            }
          >
            {plural(admins, 'administrador', 'administradores')} ·{' '}
            {plural(ativos.length - admins, 'atendente', 'atendentes')}
            {invites.length > 0 && ` · ${plural(invites.length, 'convite pendente', 'convites pendentes')}`}
          </Hero>
        )}

        {erroPainel && (
          <Card padding="none">
            <EmptyState
              title="Não foi possível carregar a equipe"
              description={erroPainel}
              action={
                <Button size="sm" variant="secondary" onClick={carregar}>
                  Tentar de novo
                </Button>
              }
            />
          </Card>
        )}

        <Section title="Membros" description="Cada pessoa entra no Otto com o próprio acesso.">
          {loading ? (
            <Card className="flex justify-center py-8">
              <Spinner />
            </Card>
          ) : (
            members.length > 0 && (
              <List>
                {members.map((m) => {
                  const souEu = m.id === userId
                  const nomeMembro = m.full_name || m.email
                  return (
                    <Row
                      key={m.id}
                      className={m.is_active ? undefined : 'opacity-60'}
                      leading={<Avatar name={nomeMembro} size="sm" tone="categorico" />}
                      title={
                        <>
                          {nomeMembro}
                          {souEu && <span className="ml-1.5 text-13 font-normal text-faint">você</span>}
                        </>
                      }
                      description={
                        <span className="block truncate">
                          {!m.is_active && <span className="font-medium text-danger-fg">Desativado · </span>}
                          {m.email}
                        </span>
                      }
                      trailing={
                        <>
                          <Badge>{PAPEL_LABEL[m.role]}</Badge>
                          <DropdownMenu>
                            <DropdownMenuTrigger asChild>
                              <Button variant="ghost" size="icon" aria-label={`Mais ações para ${nomeMembro}`}>
                                <MoreHorizontal size={16} strokeWidth={1.75} aria-hidden="true" />
                              </Button>
                            </DropdownMenuTrigger>
                            <DropdownMenuContent>
                              <DropdownMenuItem
                                onSelect={() => alterar(m, { role: m.role === 'ADMIN' ? 'AGENT' : 'ADMIN' })}
                              >
                                {m.role === 'ADMIN' ? 'Tornar atendente' : 'Tornar admin'}
                              </DropdownMenuItem>
                              {!souEu &&
                                (m.is_active ? (
                                  <DropdownMenuItem destructive onSelect={() => setDesativar(m)}>
                                    Desativar
                                  </DropdownMenuItem>
                                ) : (
                                  <DropdownMenuItem onSelect={() => alterar(m, { is_active: true })}>
                                    Reativar
                                  </DropdownMenuItem>
                                ))}
                            </DropdownMenuContent>
                          </DropdownMenu>
                        </>
                      }
                    />
                  )
                })}
              </List>
            )
          )}
        </Section>

        {invites.length > 0 && (
          <Section title="Convites pendentes" description="Cada link funciona uma vez e vale por 7 dias.">
            <List>
              {invites.map((i) => {
                const vencido = expirou(i.expires_at)
                return (
                  <Row
                    key={i.id}
                    icon={Mail}
                    tom={vencido ? 'danger' : 'neutral'}
                    title={i.email}
                    description={
                      <>
                        {PAPEL_LABEL[i.role]} ·{' '}
                        {vencido ? (
                          <span className="font-medium text-danger-fg">expirado</span>
                        ) : (
                          <>
                            expira <span className="font-mono tabular-nums">{fmtData(i.expires_at)}</span>
                          </>
                        )}
                      </>
                    }
                    trailing={
                      <Button variant="ghost" size="sm" onClick={() => revogar(i.id)}>
                        Revogar
                      </Button>
                    }
                  />
                )
              })}
            </List>
          </Section>
        )}

        <Section
          title="Convidar para a equipe"
          description="Gera um link de uso único, válido por 7 dias. Envie por WhatsApp ou onde preferir — quem receber define a própria senha."
        >
          <Card className="flex flex-col gap-4">
            <Field label="E-mail" htmlFor="convite-email" error={erroConvite ?? undefined}>
              <Input
                id="convite-email"
                ref={emailRef}
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="E-mail de quem vai entrar"
              />
            </Field>
            {/* Empilha no mobile: com o papel fixo em 190px ao lado, sobrava ~130px
                para o nome em 375px. */}
            <div className="flex flex-col gap-4 sm:flex-row">
              <Field label="Nome" hint="opcional" htmlFor="convite-nome" className="min-w-0 sm:flex-1">
                <Input id="convite-nome" value={nome} onChange={(e) => setNome(e.target.value)} />
              </Field>
              <Field label="Papel" htmlFor="convite-papel" className="sm:w-[190px] sm:flex-none">
                <Select id="convite-papel" value={papel} onChange={(e) => setPapel(e.target.value as UserRole)}>
                  <option value="AGENT">Atendente</option>
                  <option value="ADMIN">Administrador</option>
                </Select>
              </Field>
            </div>
            <p className="-mt-1 text-13 text-muted">
              {papel === 'AGENT'
                ? 'Atendente trabalha a inbox: vê filas, assume, responde e transfere conversas.'
                : 'Administrador faz tudo: equipe, agente de IA, relatórios e conexão do WhatsApp.'}
            </p>
            <div>
              <Button onClick={convidar} disabled={enviando}>
                {enviando ? 'Gerando…' : 'Gerar link de convite'}
              </Button>
            </div>

            {linkNovo && (
              <div className="flex flex-col gap-2 rounded-lg border border-line bg-subtle p-3">
                <div>
                  <p className="text-13 font-semibold text-ink">Link criado — copie agora</p>
                  <p className="text-13 text-muted">
                    Este link não aparece de novo: guardamos só um hash dele. Se perder, gere outro
                    convite para a mesma pessoa (o anterior é substituído).
                  </p>
                </div>
                <div className="flex flex-col gap-2 sm:flex-row">
                  <Input
                    readOnly
                    aria-label="Link do convite"
                    value={linkNovo}
                    onFocus={(e) => e.currentTarget.select()}
                    className="min-w-0 font-mono sm:flex-1"
                  />
                  <Button variant="secondary" onClick={() => copiar(linkNovo)}>
                    {copiado ? (
                      <Check size={14} strokeWidth={2} aria-hidden="true" />
                    ) : (
                      <Copy size={14} strokeWidth={1.75} aria-hidden="true" />
                    )}
                    {copiado ? 'Copiado!' : 'Copiar'}
                  </Button>
                </div>
              </div>
            )}
          </Card>
        </Section>
      </PageShell>

      <ConfirmDialog
        open={desativar !== null}
        onOpenChange={(o) => !o && setDesativar(null)}
        title={`Desativar ${desativar?.full_name || desativar?.email || 'este membro'}?`}
        description="A pessoa perde o acesso ao Otto na hora. As conversas e o histórico dela continuam. Dá para reativar depois."
        confirmLabel="Desativar"
        destructive
        loading={desativando}
        onConfirm={confirmarDesativar}
      />
    </>
  )
}
