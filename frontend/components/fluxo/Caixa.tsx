import type { NoDoFluxo } from '@/types/api'
import { cn } from '@/lib/utils'
import {
  ALTURA_NO,
  CLASSE_ROTULO,
  CLASSE_TIPO,
  LARGURA_NO,
  ROTULO_TIPO,
  esperaResposta,
  saidasDoNo,
} from '@/lib/fluxo'

/** Primeira linha do texto, para caber na caixa. O resto abre no painel lateral. */
function resumo(no: NoDoFluxo, id: string): string {
  const bruto = (no.texto ?? '').split('|||')[0]?.trim()
  if (bruto) return bruto
  if (no.tipo === 'horario') return 'Está no expediente?'
  if (no.tipo === 'agenda') return 'Consulta a agenda'
  if (no.tipo === 'acao') return no.acao === 'fotos' ? 'Envia a galeria' : 'Segura a data'
  if (no.tipo === 'handoff') return 'Vai para a fila da equipe'
  return id
}

export function Caixa({
  id,
  no,
  x,
  y,
  inicial,
  selecionado,
  apagado,
  onSelecionar,
}: {
  id: string
  no: NoDoFluxo
  x: number
  y: number
  inicial: boolean
  selecionado: boolean
  apagado: boolean
  onSelecionar: (id: string) => void
}) {
  const opcoes = no.opcoes?.length ?? 0
  return (
    <button
      type="button"
      onClick={() => onSelecionar(id)}
      aria-pressed={selecionado}
      style={{ left: x, top: y, width: LARGURA_NO, height: ALTURA_NO }}
      className={cn(
        'absolute flex flex-col justify-center gap-0.5 rounded-lg border px-3 py-2 text-left',
        'border-l-4 transition-opacity duration-[120ms] motion-reduce:transition-none',
        CLASSE_TIPO[no.tipo],
        selecionado && 'ring-2 ring-accent ring-offset-2 ring-offset-canvas',
        apagado && 'opacity-25',
      )}
    >
      <span className={cn('text-[9px] font-semibold uppercase tracking-[0.04em]', CLASSE_ROTULO[no.tipo])}>
        {ROTULO_TIPO[no.tipo]}
        {opcoes > 0 && ` · ${opcoes} opções`}
        {no.validacao && no.validacao !== 'texto' && ` · ${no.validacao}`}
      </span>
      <span className="text-[12.5px] font-semibold leading-tight text-ink line-clamp-2">
        {resumo(no, id)}
      </span>
      <span className="flex items-center gap-1.5 text-[9.5px] text-faint">
        <span className="truncate font-mono">{id}</span>
        {inicial && <span className="text-accent-fg">· início</span>}
        {esperaResposta(no) && <span title="espera resposta do cliente">· espera</span>}
        {saidasDoNo(no).length === 0 && <span>· fim</span>}
      </span>
    </button>
  )
}
