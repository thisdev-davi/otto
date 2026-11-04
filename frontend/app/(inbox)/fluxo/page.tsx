'use client'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { FileText, GitBranch, Maximize2, Minus, Pencil, Plus, X } from 'lucide-react'
import { useAuth } from '@/contexts/AuthContext'
import { useToast } from '@/contexts/ToastContext'
import { api } from '@/lib/api'
import type { Fluxo, FluxoTextos, NoDoFluxo, TipoNo } from '@/types/api'
import PageShell from '@/components/PageShell'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { EmptyState } from '@/components/ui/empty-state'
import { Spinner } from '@/components/ui/spinner'
import { Hero, Section } from '@/components/ui/page'
import { Caixa } from '@/components/fluxo/Caixa'
import { Ligacoes } from '@/components/fluxo/Ligacoes'
import { Simulador } from '@/components/fluxo/Simulador'
import { fieldBase } from '@/components/ui/input'
import {
  CLASSE_ROTULO,
  CLASSE_TIPO,
  ROTULO_TIPO,
  posicaoDoNo,
  saidasDoNo,
  tamanhoDoCanvas,
} from '@/lib/fluxo'
import { cn } from '@/lib/utils'

const ZOOM_MIN = 0.4
const ZOOM_MAX = 1.4
const PASSO_ZOOM = 0.15

// Os tipos que aparecem na legenda, na ordem em que uma conversa os encontra.
const LEGENDA: TipoNo[] = [
  'horario',
  'mensagem',
  'menu',
  'pergunta',
  'condicao',
  'agenda',
  'acao',
  'handoff',
  'fim',
]

const EXPLICACAO: Record<TipoNo, string> = {
  mensagem: 'Envia texto e segue em frente.',
  menu: 'Oferece opções numeradas e espera a escolha.',
  pergunta: 'Pergunta livre e guarda a resposta num campo.',
  condicao: 'Ramifica comparando um campo já coletado.',
  horario: 'Ramifica em dentro ou fora do expediente.',
  agenda: 'Resolve a data na agenda real e ramifica pelos motivos de disponibilidade.',
  acao: 'Envia a galeria ou cria uma pré-reserva.',
  handoff: 'Encerra o roteiro e põe a conversa na fila da equipe.',
  fim: 'Fecha o atendimento sem chamar ninguém.',
}

export default function FluxoPage() {
  const { token } = useAuth()
  const toast = useToast()
  const [fluxo, setFluxo] = useState<Fluxo | null>(null)
  const [carregando, setCarregando] = useState(true)
  const [erroPainel, setErroPainel] = useState<string | null>(null)
  const [zoom, setZoom] = useState(1)
  const [selecionado, setSelecionado] = useState<string | null>(null)
  const viewport = useRef<HTMLDivElement>(null)
  const arrasto = useRef<{ x: number; y: number; left: number; top: number } | null>(null)

  const carregar = useCallback(async () => {
    if (!token) return
    try {
      setFluxo(await api.fluxo.obter(token))
      setErroPainel(null)
    } catch (e) {
      setErroPainel((e as Error).message || 'Não foi possível carregar o fluxo.')
    } finally {
      setCarregando(false)
    }
  }, [token])

  useEffect(() => {
    carregar()
  }, [carregar])

  async function salvarTextos(textos: FluxoTextos) {
    if (!token) return false
    try {
      setFluxo(await api.fluxo.editarTextos(token, textos))
      return true
    } catch (e) {
      toast.error((e as Error).message || 'Não foi possível salvar o texto.')
      return false
    }
  }

  async function assumirEdicao() {
    if (!token) return
    try {
      setFluxo(await api.fluxo.assumir(token))
      toast.success('Agora o roteiro é editado por aqui.')
    } catch (e) {
      toast.error((e as Error).message || 'Não foi possível assumir a edição.')
    }
  }

  const grafo = fluxo?.grafo ?? {}
  const nos = grafo.nos ?? {}
  const ids = useMemo(() => Object.keys(nos), [nos])
  const incompativel = Object.values(nos).some((no) => !Object.hasOwn(ROTULO_TIPO, no.tipo))
  const { largura, altura } = useMemo(() => tamanhoDoCanvas(grafo), [grafo])

  // Vizinhança do nó selecionado: o resto esmaece em vez de sumir, para o contexto do
  // roteiro continuar visível enquanto se lê uma caixa.
  const vizinhos = useMemo(() => {
    if (!selecionado) return null
    const perto = new Set<string>([selecionado])
    for (const [id, no] of Object.entries(nos)) {
      if (id === selecionado) saidasDoNo(no).forEach((s) => perto.add(s.para))
      else if (saidasDoNo(no).some((s) => s.para === selecionado)) perto.add(id)
    }
    return perto
  }, [selecionado, nos])

  function onPointerDown(e: React.PointerEvent<HTMLDivElement>) {
    const el = viewport.current
    if (!el || (e.target as HTMLElement).closest('button')) return
    el.setPointerCapture(e.pointerId)
    arrasto.current = { x: e.clientX, y: e.clientY, left: el.scrollLeft, top: el.scrollTop }
  }

  function onPointerMove(e: React.PointerEvent<HTMLDivElement>) {
    const el = viewport.current
    if (!el || !arrasto.current) return
    el.scrollLeft = arrasto.current.left - (e.clientX - arrasto.current.x)
    el.scrollTop = arrasto.current.top - (e.clientY - arrasto.current.y)
  }

  function onPointerUp() {
    arrasto.current = null
  }

  const no: NoDoFluxo | undefined = selecionado ? nos[selecionado] : undefined

  return (
    <PageShell largura="amplo">
      {erroPainel ? (
        <Card>
          <EmptyState
            icon={GitBranch}
            title="Não foi possível carregar o fluxo"
            description={erroPainel}
            action={
              <Button variant="secondary" size="sm" onClick={carregar}>
                Tentar de novo
              </Button>
            }
          />
        </Card>
      ) : carregando ? (
        <div className="flex items-center justify-center gap-2 py-10 text-faint">
          <Spinner />
          <span className="text-13">Carregando…</span>
        </div>
      ) : incompativel ? (
        <Card>
          <EmptyState
            icon={GitBranch}
            title="Roteiro incompatível"
            description="Este roteiro contém tipos de caixa não suportados. Publique um roteiro compatível para visualizar, editar e simular o atendimento."
          />
        </Card>
      ) : ids.length === 0 ? (
        <Card>
          <EmptyState
            icon={GitBranch}
            title="Nenhum roteiro publicado"
            description="O atendimento por fluxo responde seguindo um roteiro desenhado. Publique um com scripts/sincronizar_conteudo.py a partir de conteudo/fluxo.json."
          />
        </Card>
      ) : (
        <div className="flex flex-col gap-4">
          <Cabecalho fluxo={fluxo!} quantidade={ids.length} onAssumir={assumirEdicao} />

          <Section
            title="Roteiro"
            description="Arraste para mover · clique numa caixa para ver o texto"
            action={
              <>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="Diminuir zoom"
                  onClick={() => setZoom((z) => Math.max(ZOOM_MIN, z - PASSO_ZOOM))}
                >
                  <Minus size={16} />
                </Button>
                <span className="w-10 text-center font-mono text-xs tabular-nums text-muted">
                  {Math.round(zoom * 100)}%
                </span>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label="Aumentar zoom"
                  onClick={() => setZoom((z) => Math.min(ZOOM_MAX, z + PASSO_ZOOM))}
                >
                  <Plus size={16} />
                </Button>
                <Button variant="ghost" size="icon" aria-label="Zoom padrão" onClick={() => setZoom(1)}>
                  <Maximize2 size={15} />
                </Button>
              </>
            }
          >
            <Card padding="none" className="overflow-hidden">
              <Origem gerenciado={fluxo!.gerenciado_por_arquivo} />
              <div
                ref={viewport}
                onPointerDown={onPointerDown}
                onPointerMove={onPointerMove}
                onPointerUp={onPointerUp}
                onPointerCancel={onPointerUp}
                className="relative h-[62vh] min-h-[380px] overflow-auto border-t border-line bg-canvas touch-pan-x touch-pan-y"
              >
                <div
                  style={{
                    width: largura * zoom,
                    height: altura * zoom,
                  }}
                >
                  <div
                    style={{
                      width: largura,
                      height: altura,
                      transform: `scale(${zoom})`,
                      transformOrigin: '0 0',
                    }}
                    className="relative"
                  >
                    <Ligacoes grafo={grafo} destacado={selecionado} />
                    {ids.map((id) => {
                      const { x, y } = posicaoDoNo(nos, id)
                      return (
                        <Caixa
                          key={id}
                          id={id}
                          no={nos[id]}
                          x={x}
                          y={y}
                          inicial={grafo.inicio === id}
                          selecionado={selecionado === id}
                          apagado={!!vizinhos && !vizinhos.has(id)}
                          onSelecionar={(alvo) => setSelecionado((atual) => (atual === alvo ? null : alvo))}
                        />
                      )
                    })}
                  </div>
                </div>
              </div>
            </Card>
          </Section>

          {no && selecionado && (
            <Detalhe
              key={`${selecionado}-${fluxo!.versao}`}
              id={selecionado}
              no={no}
              podeEditar={!fluxo!.gerenciado_por_arquivo}
              onSalvar={salvarTextos}
            />
          )}
          <Simulador />
          <Legenda />
        </div>
      )}
    </PageShell>
  )
}

function Cabecalho({
  fluxo,
  quantidade,
  onAssumir,
}: {
  fluxo: Fluxo
  quantidade: number
  onAssumir: () => void
}) {
  return (
    <Hero
      icon={GitBranch}
      title={fluxo.nome || 'Roteiro sem nome'}
      aside={
        fluxo.gerenciado_por_arquivo ? (
          <Button variant="secondary" size="sm" onClick={onAssumir}>
            <Pencil size={14} /> Editar o texto por aqui
          </Button>
        ) : (
          <Badge>Texto editado por aqui</Badge>
        )
      }
    >
      <span className="font-mono tabular-nums text-ink-2">{quantidade}</span> caixas · versão{' '}
      <span className="font-mono tabular-nums text-ink-2">{fluxo.versao}</span>
    </Hero>
  )
}

/** De onde vem o roteiro e o que a edição por aqui alcança — a regra 7 dita ao usuário. */
function Origem({ gerenciado }: { gerenciado: boolean }) {
  return (
    <div className="flex items-start gap-2.5 px-4 py-3 text-13 leading-relaxed text-muted">
      <FileText size={16} strokeWidth={1.75} className="mt-0.5 flex-none text-faint" aria-hidden="true" />
      {gerenciado ? (
        <p>
          Publicado a partir de <code className="font-mono text-xs text-ink-2">conteudo/fluxo.json</code>.
          Editar por aqui seria perdido na próxima sincronização — a menos que você assuma a edição.
        </p>
      ) : (
        <p>
          O texto é editado por aqui. O sincronizador não publica mais neste roteiro sem{' '}
          <code className="font-mono text-xs text-ink-2">--forcar</code>. O desenho — quais caixas
          existem e como se ligam — continua vindo do arquivo.
        </p>
      )}
    </div>
  )
}

function Detalhe({
  id,
  no,
  podeEditar,
  onSalvar,
}: {
  id: string
  no: NoDoFluxo
  podeEditar: boolean
  onSalvar: (textos: FluxoTextos) => Promise<boolean>
}) {
  const [editando, setEditando] = useState(false)
  const [salvando, setSalvando] = useState(false)
  const [texto, setTexto] = useState(no.texto ?? '')
  const [rotulos, setRotulos] = useState<string[]>((no.opcoes ?? []).map((o) => o.rotulo))

  const bolhas = (no.texto ?? '').split('|||').map((b) => b.trim()).filter(Boolean)
  const temTexto = no.texto !== undefined || (no.opcoes?.length ?? 0) > 0

  async function salvar() {
    setSalvando(true)
    const mudanca: FluxoTextos['nos'][string] = {}
    if (no.texto !== undefined) mudanca.texto = texto
    if (no.opcoes?.length) mudanca.opcoes = rotulos.map((rotulo) => ({ rotulo }))
    const ok = await onSalvar({ nos: { [id]: mudanca } })
    setSalvando(false)
    if (ok) setEditando(false)
  }

  function cancelar() {
    setTexto(no.texto ?? '')
    setRotulos((no.opcoes ?? []).map((o) => o.rotulo))
    setEditando(false)
  }

  return (
    <Section
      title="Caixa selecionada"
      action={
        podeEditar &&
        temTexto &&
        !editando && (
          <Button variant="ghost" size="sm" onClick={() => setEditando(true)}>
            <Pencil size={14} /> Editar texto
          </Button>
        )
      }
    >
      <Card padding="sm">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 pb-3">
          <span className={cn('text-2xs font-semibold uppercase tracking-[0.04em]', CLASSE_ROTULO[no.tipo])}>
            {ROTULO_TIPO[no.tipo]}
          </span>
          <span className="font-mono text-xs text-faint">{id}</span>
          {no.campo && (
            <span className="text-xs text-muted">
              grava em <span className="font-mono">{no.campo}</span>
            </span>
          )}
        </div>

        {editando ? (
          <div className="flex flex-col gap-3 pb-3">
            {no.texto !== undefined && (
              <label className="flex flex-col gap-1.5">
                <span className="text-13 font-medium text-ink">
                  Mensagem
                  <span className="font-normal text-faint">
                    {' '}
                    — <code className="font-mono">|||</code> separa bolhas;{' '}
                    <code className="font-mono">{'{{variavel}}'}</code> é preenchido na hora
                  </span>
                </span>
                <textarea
                  value={texto}
                  onChange={(e) => setTexto(e.target.value)}
                  rows={4}
                  className={cn(fieldBase, 'resize-y leading-relaxed')}
                />
              </label>
            )}
            {rotulos.map((r, i) => (
              <label key={i} className="flex flex-col gap-1.5">
                <span className="text-13 font-medium text-ink">
                  Opção {i + 1}
                  <span className="font-mono text-xs font-normal text-faint"> → {no.opcoes?.[i]?.proximo}</span>
                </span>
                <input
                  value={r}
                  onChange={(e) =>
                    setRotulos((atual) => atual.map((v, j) => (j === i ? e.target.value : v)))
                  }
                  className={fieldBase}
                />
              </label>
            ))}
            <p className="text-xs leading-relaxed text-faint">
              Só o texto muda por aqui. Para onde cada opção leva, quais caixas existem e como se
              ligam continuam vindo de <code className="font-mono">conteudo/fluxo.json</code>.
            </p>
            <div className="flex flex-wrap gap-2">
              <Button size="sm" onClick={salvar} disabled={salvando}>
                {salvando ? 'Salvando…' : 'Salvar e publicar'}
              </Button>
              <Button variant="ghost" size="sm" onClick={cancelar} disabled={salvando}>
                <X size={14} /> Cancelar
              </Button>
            </div>
          </div>
        ) : (
          <>
            {bolhas.length > 0 && (
              <div className="flex flex-col gap-1.5 pb-3">
                {bolhas.map((b, i) => (
                  <p
                    key={i}
                    className="max-w-[62ch] whitespace-pre-line rounded-lg bg-subtle px-3 py-2 text-13 leading-relaxed text-ink"
                  >
                    {b}
                  </p>
                ))}
              </div>
            )}

            {!!no.opcoes?.length && (
              <ol className="flex flex-col gap-1 pb-3">
                {no.opcoes.map((op, i) => (
                  <li key={i} className="flex flex-wrap gap-x-2 text-13 text-ink-2">
                    <span className="font-mono tabular-nums text-faint">{i + 1}.</span>
                    <span>{op.rotulo}</span>
                    <span className="font-mono text-xs leading-5 text-faint">→ {op.proximo}</span>
                  </li>
                ))}
              </ol>
            )}
          </>
        )}

        <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-line pt-2.5 text-xs text-muted">
          {saidasDoNo(no).length === 0 ? (
            <span>Terminal: o roteiro acaba aqui.</span>
          ) : (
            saidasDoNo(no).map((s, i) => (
              <span key={i}>
                {s.rotulo && <span className="text-faint">{s.rotulo} </span>}→{' '}
                <span className="font-mono">{s.para}</span>
              </span>
            ))
          )}
        </div>
      </Card>
    </Section>
  )
}

function Legenda() {
  return (
    <Section title="Os nove tipos de caixa">
      <Card padding="sm">
        <div className="grid gap-x-6 gap-y-2.5 sm:grid-cols-2 lg:grid-cols-3">
          {LEGENDA.map((tipo) => (
            <div key={tipo} className="flex items-start gap-2.5">
              <span className={cn('mt-1 h-3 w-3 flex-none rounded border', CLASSE_TIPO[tipo])} />
              <span className="text-13 leading-snug">
                <span className={cn('font-medium', CLASSE_ROTULO[tipo])}>{ROTULO_TIPO[tipo]}</span>
                <span className="text-muted"> — {EXPLICACAO[tipo]}</span>
              </span>
            </div>
          ))}
        </div>
      </Card>
    </Section>
  )
}
