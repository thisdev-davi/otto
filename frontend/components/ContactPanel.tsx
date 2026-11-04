'use client'
import { useCallback, useEffect, useState } from 'react'
import { AlertCircle, Trash2, X } from 'lucide-react'
import { useAuth } from '@/contexts/AuthContext'
import { useToast } from '@/contexts/ToastContext'
import { api } from '@/lib/api'
import { formatarData, tempoRestante } from '@/lib/salao'
import type { ContactPerfil, ContactReserva, ReservaStatus } from '@/types/api'
import { Avatar } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { Spinner } from '@/components/ui/spinner'
import { Textarea } from '@/components/ui/textarea'

// Mesmo teto do backend (`contatos.TAMANHO_MAX_NOTA`). Aqui ele serve para o contador
// avisar ANTES; lá ele é a garantia. Duplicar o número é barato, e a alternativa —
// deixar o servidor truncar em silêncio — some com o fim do texto sem avisar ninguém.
const MAX_NOTA = 2000

const STATUS_RESERVA: Record<ReservaStatus, { rotulo: string; variant: 'success' | 'warning' | 'neutral' }> = {
  confirmada: { rotulo: 'Confirmada', variant: 'success' },
  pre_reservada: { rotulo: 'Pré-reservada', variant: 'warning' },
  cancelada: { rotulo: 'Cancelada', variant: 'neutral' },
  expirada: { rotulo: 'Expirada', variant: 'neutral' },
}

const STATUS_CONVERSA: Record<string, string> = {
  open: 'Aberta',
  pending: 'Em atendimento',
  resolved: 'Resolvida',
}

// Os mesmos rótulos do `ThreadHeader`: a lista é aberta de propósito, porque as chaves
// do intake são decididas pela persona e cada negócio coleta o seu.
function rotuloCampo(chave: string): string {
  const mapa: Record<string, string> = {
    nome: 'Nome',
    tipo_de_evento: 'Tipo de evento',
    data_pretendida: 'Data pretendida',
    numero_de_convidados: 'Convidados',
  }
  return mapa[chave] ?? chave.replace(/_/g, ' ')
}

/** '2026-07-12T14:32:00Z' → '12/07/2026'. Instante, ao contrário da data da reserva. */
function dataCurta(iso: string): string {
  return new Date(iso).toLocaleDateString('pt-BR')
}

function dataHora(iso: string): string {
  return new Date(iso).toLocaleString('pt-BR', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function Secao({ titulo, acao, children }: { titulo: string; acao?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[10.5px] font-bold uppercase tracking-wide text-faint">{titulo}</span>
        {acao}
      </div>
      {children}
    </div>
  )
}

function LinhaReserva({ r }: { r: ContactReserva }) {
  const st = STATUS_RESERVA[r.status]
  const prazo = r.status === 'pre_reservada' ? tempoRestante(r.expira_em) : ''
  return (
    <div className="flex items-center justify-between gap-2 bg-canvas border border-line rounded-xl px-3 py-2">
      <div className="min-w-0">
        <p className="text-[13px] font-semibold text-ink">{formatarData(r.data)}</p>
        <p className="text-[11.5px] text-faint truncate">
          {r.criada_por === 'bot' ? 'pelo agente' : 'pela equipe'}
          {prazo && ` · ${prazo}`}
        </p>
      </div>
      <Badge variant={st.variant}>{st.rotulo}</Badge>
    </div>
  )
}

interface ContactPanelProps {
  contactId: string
  onClose: () => void
}

export default function ContactPanel({ contactId, onClose }: ContactPanelProps) {
  const { token, userId } = useAuth()
  const toast = useToast()
  const [perfil, setPerfil] = useState<ContactPerfil | null>(null)
  const [carregando, setCarregando] = useState(true)
  const [erro, setErro] = useState(false)
  const [rascunho, setRascunho] = useState('')
  const [salvando, setSalvando] = useState(false)
  const [apagando, setApagando] = useState<string | null>(null)

  const carregar = useCallback(() => {
    if (!token) return
    setCarregando(true)
    setErro(false)
    api.contacts
      .perfil(token, contactId)
      .then(setPerfil)
      // Conteúdo principal do painel: o erro fica NO painel, com "Tentar de novo".
      // Um toast sumiria e deixaria um painel vazio sem explicação.
      .catch(() => { setErro(true) })
      .finally(() => { setCarregando(false) })
  }, [token, contactId])

  useEffect(() => { carregar() }, [carregar])

  // Escape fecha, como em qualquer overlay. O painel não é Radix (ele não sobrepõe a
  // conversa — divide a largura com ela), então focus trap e clique-fora não se aplicam:
  // a thread ao lado continua sendo uma área legítima para trabalhar com o painel aberto.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  async function salvarNota() {
    if (!token || !perfil || !rascunho.trim()) return
    setSalvando(true)
    try {
      const nota = await api.contacts.criarNota(token, perfil.contact_id, rascunho.trim())
      setPerfil({ ...perfil, notas: [nota, ...perfil.notas] })
      setRascunho('')
    } catch {
      toast.error('Não foi possível salvar a anotação.')
    } finally {
      setSalvando(false)
    }
  }

  async function apagarNota(notaId: string) {
    if (!token || !perfil) return
    try {
      await api.contacts.removerNota(token, perfil.contact_id, notaId)
      setPerfil({ ...perfil, notas: perfil.notas.filter((n) => n.id !== notaId) })
    } catch {
      toast.error('Não foi possível apagar a anotação.')
    } finally {
      setApagando(null)
    }
  }

  const dados = Object.entries(perfil?.dados_coletados ?? {})

  return (
    <aside
      aria-label="Perfil do cliente"
      className="flex flex-col bg-surface border-l border-line w-full lg:w-[340px] lg:flex-shrink-0 overflow-hidden"
    >
      <div className="flex items-start justify-between gap-2 px-4 py-3 border-b border-line flex-shrink-0">
        <div className="flex items-center gap-3 min-w-0">
          <Avatar name={perfil?.nome ?? null} src={perfil?.avatar_url} />
          <div className="min-w-0">
            <p className="text-[14px] font-bold text-ink truncate">
              {perfil?.nome ?? perfil?.telefone ?? 'Cliente'}
            </p>
            {perfil?.telefone && (
              <p className="font-mono text-xs tabular-nums text-muted">{perfil.telefone}</p>
            )}
          </div>
        </div>
        <button
          onClick={onClose}
          aria-label="Fechar perfil do cliente"
          className="h-11 w-11 -mr-2 -mt-1 flex items-center justify-center rounded-lg text-muted hover:bg-subtle hover:text-ink transition-colors flex-shrink-0"
        >
          <X size={18} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-4 flex flex-col gap-5">
        {carregando && (
          <div className="flex items-center justify-center py-8 gap-2">
            <Spinner />
            <span className="text-xs text-faint">Carregando perfil…</span>
          </div>
        )}

        {!carregando && erro && (
          <EmptyState
            icon={AlertCircle}
            title="Não foi possível carregar o perfil."
            description="A conversa continua funcionando — o que falhou foi a busca dos dados do cliente."
            action={
              <Button size="sm" variant="secondary" onClick={carregar}>
                Tentar de novo
              </Button>
            }
          />
        )}

        {!carregando && !erro && perfil && (
          <>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-muted">
              <span>
                <span className="text-faint">Cliente desde:</span>{' '}
                <span className="font-medium text-ink">{dataCurta(perfil.cliente_desde)}</span>
              </span>
              <span>
                <span className="text-faint">Atendimentos:</span>{' '}
                <span className="font-medium text-ink">{perfil.conversas.length}</span>
              </span>
            </div>

            {/* O cliente tinha mais de um registro em `contacts` — o endereço do canal
                mudou em algum momento e o histórico estava partido. O painel já soma os
                dois; dizer isso evita que o número pareça errado para quem conhece a
                conversa antiga. */}
            {perfil.contatos_unidos > 1 && (
              <p className="text-[11.5px] text-warning-fg bg-warning-soft border border-warning-line rounded-xl px-3 py-2 leading-relaxed">
                Este cliente aparece em {perfil.contatos_unidos} registros de contato com o
                mesmo telefone. O histórico abaixo está somado.
              </p>
            )}

            {dados.length > 0 && (
              <Secao titulo="O que já sabemos">
                <div className="flex flex-col gap-1">
                  {dados.map(([chave, valor]) => (
                    <span key={chave} className="text-[12px] text-muted">
                      <span className="text-faint">{rotuloCampo(chave)}:</span>{' '}
                      <span className="font-medium text-ink">{valor}</span>
                    </span>
                  ))}
                </div>
              </Secao>
            )}

            {perfil.ultimo_resumo && (
              <Secao titulo="Resumo da última triagem">
                <p className="text-[12px] text-muted leading-relaxed">{perfil.ultimo_resumo}</p>
              </Secao>
            )}

            <Secao titulo="Agendamentos">
              {perfil.reservas.length === 0 ? (
                <p className="text-[12px] text-faint">Nenhuma data reservada até agora.</p>
              ) : (
                <div className="flex flex-col gap-1.5">
                  {perfil.reservas.map((r) => <LinhaReserva key={r.id} r={r} />)}
                </div>
              )}
            </Secao>

            <Secao titulo="Anotações">
              <div className="flex flex-col gap-1.5">
                <Textarea
                  value={rascunho}
                  onChange={(e) => setRascunho(e.target.value.slice(0, MAX_NOTA))}
                  placeholder="O que a equipe precisa lembrar deste cliente…"
                  rows={2}
                  disabled={salvando}
                />
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[11px] text-faint">
                    {rascunho.length > MAX_NOTA - 200 && `${rascunho.length}/${MAX_NOTA}`}
                  </span>
                  <Button
                    size="sm"
                    onClick={salvarNota}
                    disabled={salvando || !rascunho.trim()}
                  >
                    {salvando ? 'Salvando…' : 'Anotar'}
                  </Button>
                </div>
              </div>

              {perfil.notas.length === 0 ? (
                <p className="text-[12px] text-faint">Nenhuma anotação ainda.</p>
              ) : (
                <div className="flex flex-col gap-1.5">
                  {perfil.notas.map((n) => (
                    <div
                      key={n.id}
                      className="group bg-canvas border border-line rounded-xl px-3 py-2 flex items-start justify-between gap-2"
                    >
                      <div className="min-w-0">
                        <p className="text-[12.5px] text-ink leading-relaxed whitespace-pre-wrap break-words">
                          {n.texto}
                        </p>
                        <p className="text-[11px] text-faint mt-1">
                          {n.autor ?? 'Autor removido'} · {dataHora(n.criada_em)}
                        </p>
                      </div>
                      {/* Só a própria nota. Admin também pode apagar as dos outros, mas
                          quem decide isso é o servidor — o papel não está no token, e
                          esconder o botão aqui é conveniência, não a regra. */}
                      {n.autor_id === userId && (
                        <button
                          onClick={() => setApagando(n.id)}
                          aria-label="Apagar anotação"
                          className="h-8 w-8 flex items-center justify-center rounded-lg text-faint hover:bg-subtle hover:text-danger-fg transition-colors flex-shrink-0"
                        >
                          <Trash2 size={14} />
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </Secao>

            <Secao titulo="Atendimentos">
              {perfil.conversas.length === 0 ? (
                <p className="text-[12px] text-faint">Nenhum atendimento registrado.</p>
              ) : (
                <div className="flex flex-col gap-1.5">
                  {perfil.conversas.map((c) => (
                    <div
                      key={c.conversation_id}
                      className="bg-canvas border border-line rounded-xl px-3 py-2"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-[12.5px] font-medium text-ink">
                          {dataCurta(c.ultima_mensagem_em ?? c.criada_em)}
                        </span>
                        <span className="text-[11px] text-muted">
                          {STATUS_CONVERSA[c.status] ?? c.status}
                        </span>
                      </div>
                      {c.atendente && (
                        <p className="text-[11.5px] text-faint truncate">{c.atendente}</p>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </Secao>
          </>
        )}
      </div>

      <ConfirmDialog
        open={apagando !== null}
        onOpenChange={(aberto) => { if (!aberto) setApagando(null) }}
        title="Apagar esta anotação?"
        description="Ela some para toda a equipe e não dá para recuperar."
        confirmLabel="Apagar"
        destructive
        onConfirm={() => { if (apagando) apagarNota(apagando) }}
      />
    </aside>
  )
}
