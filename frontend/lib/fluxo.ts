import type { GrafoDoFluxo, NoDoFluxo, TipoNo } from '@/types/api'

/**
 * Geometria e vocabulário do canvas do roteiro.
 *
 * Sem lib de grafo: o projeto não tem react-flow, d3 nem dnd-kit, e o gráfico de
 * relatórios já é feito com `div` e Tailwind. Aqui é a mesma escolha — caixas são `div`
 * posicionados e as ligações são um `<svg>` de fundo. O que faz isso funcionar sem
 * algoritmo de layout é `pos` vir no DADO: quem desenha o roteiro decide onde cada caixa
 * fica, e é exatamente o que um editor precisaria depois.
 */

export const LARGURA_NO = 200
export const ALTURA_NO = 72
export const MARGEM_CANVAS = 48

/** Fallback para nó sem `pos`: empilha em coluna, para nada sumir fora da tela. */
const PASSO_FALLBACK = 112

export const ROTULO_TIPO: Record<TipoNo, string> = {
  mensagem: 'mensagem',
  menu: 'menu',
  pergunta: 'pergunta',
  condicao: 'condição',
  horario: 'horário',
  agenda: 'agenda',
  acao: 'ação',
  handoff: 'handoff',
  fim: 'fim',
}

/**
 * Strings literais completas, nunca `bg-${tipo}` — o Tailwind lê o texto-fonte e uma
 * classe montada em runtime não gera CSS (`frontend/CLAUDE.md` §3).
 */
export const CLASSE_TIPO: Record<TipoNo, string> = {
  mensagem: 'bg-subtle border-line-strong',
  menu: 'bg-accent-soft border-accent-ring',
  pergunta: 'bg-ai-soft border-ai',
  condicao: 'bg-warning-soft border-warning-line',
  horario: 'bg-warning-soft border-warning-line',
  agenda: 'bg-note-soft border-note-line',
  acao: 'bg-success-soft border-success-line',
  handoff: 'bg-danger-soft border-danger-line',
  fim: 'bg-surface border-line-strong',
}

export const CLASSE_ROTULO: Record<TipoNo, string> = {
  mensagem: 'text-muted',
  menu: 'text-accent-fg',
  pergunta: 'text-ai-fg',
  condicao: 'text-warning-fg',
  horario: 'text-warning-fg',
  agenda: 'text-note-fg',
  acao: 'text-success-fg',
  handoff: 'text-danger-fg',
  fim: 'text-ink-2',
}

/** Nós que param o turno esperando o cliente responder. */
const ESPERAM_RESPOSTA: TipoNo[] = ['menu', 'pergunta']
const TERMINAIS: TipoNo[] = ['handoff', 'fim']

export function esperaResposta(no: NoDoFluxo): boolean {
  return ESPERAM_RESPOSTA.includes(no.tipo)
}

export function ehTerminal(no: NoDoFluxo): boolean {
  return TERMINAIS.includes(no.tipo)
}

export interface Aresta {
  de: string
  para: string
  rotulo: string
  /** `volta` = aponta para trás no roteiro; desenhada em âmbar para não virar espaguete. */
  volta: boolean
}

/**
 * As saídas de um nó. Espelha `app/flow/schema.py::saidas_de` — o backend é quem manda,
 * e divergir aqui só produziria um desenho que mente sobre o que roda.
 */
export function saidasDoNo(no: NoDoFluxo): { rotulo: string; para: string }[] {
  const fora: { rotulo: string; para: string }[] = []
  if (no.proximo) fora.push({ rotulo: '', para: no.proximo })
  if (no.senao) fora.push({ rotulo: 'senão', para: no.senao })
  for (const [nome, destino] of Object.entries(no.saidas ?? {})) {
    if (destino) fora.push({ rotulo: nome, para: destino })
  }
  ;(no.opcoes ?? []).forEach((op, i) => {
    if (op.proximo) fora.push({ rotulo: `${i + 1}`, para: op.proximo })
  })
  return fora
}

export function posicaoDoNo(
  nos: Record<string, NoDoFluxo>,
  id: string,
): { x: number; y: number } {
  const pos = nos[id]?.pos
  if (Array.isArray(pos) && pos.length === 2) return { x: pos[0], y: pos[1] }
  const indice = Object.keys(nos).indexOf(id)
  return { x: MARGEM_CANVAS, y: MARGEM_CANVAS + Math.max(indice, 0) * PASSO_FALLBACK }
}

export function arestasDoGrafo(grafo: Partial<GrafoDoFluxo>): Aresta[] {
  const nos = grafo.nos ?? {}
  const arestas: Aresta[] = []
  for (const [id, no] of Object.entries(nos)) {
    for (const { rotulo, para } of saidasDoNo(no)) {
      if (!nos[para]) continue
      const origem = posicaoDoNo(nos, id)
      const destino = posicaoDoNo(nos, para)
      arestas.push({
        de: id,
        para,
        rotulo,
        volta: destino.y < origem.y - ALTURA_NO || (destino.x < origem.x && destino.y <= origem.y),
      })
    }
  }
  return arestas
}

export function tamanhoDoCanvas(grafo: Partial<GrafoDoFluxo>): { largura: number; altura: number } {
  const nos = grafo.nos ?? {}
  let maxX = 0
  let maxY = 0
  for (const id of Object.keys(nos)) {
    const { x, y } = posicaoDoNo(nos, id)
    maxX = Math.max(maxX, x + LARGURA_NO)
    maxY = Math.max(maxY, y + ALTURA_NO)
  }
  return { largura: maxX + MARGEM_CANVAS, altura: maxY + MARGEM_CANVAS }
}

/**
 * Curva entre duas caixas, saindo pela borda mais próxima do destino. Bézier e não reta:
 * com 24 caixas as retas se sobrepõem e viram um novelo.
 */
export function caminhoDaAresta(
  origem: { x: number; y: number },
  destino: { x: number; y: number },
): { d: string; meio: { x: number; y: number } } {
  const oc = { x: origem.x + LARGURA_NO / 2, y: origem.y + ALTURA_NO / 2 }
  const dc = { x: destino.x + LARGURA_NO / 2, y: destino.y + ALTURA_NO / 2 }
  const horizontal = Math.abs(dc.x - oc.x) > Math.abs(dc.y - oc.y)

  const p0 = horizontal
    ? { x: dc.x > oc.x ? origem.x + LARGURA_NO : origem.x, y: oc.y }
    : { x: oc.x, y: dc.y > oc.y ? origem.y + ALTURA_NO : origem.y }
  const p1 = horizontal
    ? { x: dc.x > oc.x ? destino.x : destino.x + LARGURA_NO, y: dc.y }
    : { x: dc.x, y: dc.y > oc.y ? destino.y : destino.y + ALTURA_NO }

  const folga = horizontal ? Math.max(28, Math.abs(p1.x - p0.x) / 2) : Math.max(28, Math.abs(p1.y - p0.y) / 2)
  const c0 = horizontal ? { x: p0.x + (p1.x > p0.x ? folga : -folga), y: p0.y } : { x: p0.x, y: p0.y + (p1.y > p0.y ? folga : -folga) }
  const c1 = horizontal ? { x: p1.x + (p1.x > p0.x ? -folga : folga), y: p1.y } : { x: p1.x, y: p1.y + (p1.y > p0.y ? -folga : folga) }

  return {
    d: `M ${p0.x} ${p0.y} C ${c0.x} ${c0.y} ${c1.x} ${c1.y} ${p1.x} ${p1.y}`,
    meio: { x: (p0.x + p1.x) / 2, y: (p0.y + p1.y) / 2 },
  }
}
