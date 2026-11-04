'use client'
import type { VolumePoint } from '@/types/api'

// Teto do eixo num número redondo e par: a linha do meio é teto/2 e precisa ser inteira.
function tetoDoEixo(max: number): number {
  const passo = max <= 10 ? 2 : max <= 50 ? 10 : 50
  return Math.max(passo, Math.ceil(max / passo) * passo)
}

const ddmm = (iso: string) => `${iso.slice(8, 10)}/${iso.slice(5, 7)}`

/**
 * Barras empilhadas por dia (enviadas em cima, recebidas embaixo), sem lib.
 * `days` é o período inteiro: a API só devolve dia com mensagem, e pular o dia vazio
 * desenharia uma semana parada como se ela não tivesse existido.
 */
export default function VolumeChart({ points, days }: { points: VolumePoint[]; days: string[] }) {
  const byDay = new Map<string, { inbound: number; outbound: number }>()
  for (const p of points) {
    const d = byDay.get(p.day) ?? { inbound: 0, outbound: 0 }
    if (p.direction === 'inbound') d.inbound += p.total
    else d.outbound += p.total
    byDay.set(p.day, d)
  }
  const serie = days.map((day) => ({ day, ...(byDay.get(day) ?? { inbound: 0, outbound: 0 }) }))
  const max = Math.max(0, ...serie.map((d) => d.inbound + d.outbound))

  if (max === 0) {
    return <p className="py-10 text-center text-13 text-faint">Sem mensagens no período.</p>
  }

  const teto = tetoDoEixo(max)
  const pct = (n: number) => `${(n / teto) * 100}%`
  // No celular cabe metade dos rótulos: um a cada 6 dias, mais o último.
  const rotulo = (k: number) =>
    serie.length <= 7 || k % 6 === 0 || k === serie.length - 1 ? '' : k % 3 === 0 ? 'hidden sm:inline' : null

  return (
    <div>
      <div className="relative flex h-[200px] items-end gap-0.5 pl-9 sm:gap-1.5">
        {[0, 50, 100].map((top) => (
          <div key={top} aria-hidden="true">
            <div className="absolute left-9 right-0 border-t border-dashed border-line" style={{ top: `${top}%` }} />
            <span
              className="absolute left-0 w-7 -translate-y-1/2 text-right font-mono text-2xs tabular-nums text-faint"
              style={{ top: `${top}%` }}
            >
              {top === 0 ? teto : top === 50 ? teto / 2 : 0}
            </span>
          </div>
        ))}
        {serie.map((d) => (
          <div
            key={d.day}
            className="relative z-[1] flex h-full min-w-0 flex-1 flex-col justify-end gap-0.5"
            title={`${ddmm(d.day)}: ${d.inbound} recebidas, ${d.outbound} enviadas`}
          >
            {d.outbound > 0 && <div className="rounded-t-[3px] bg-chart-1" style={{ height: pct(d.outbound) }} />}
            {d.inbound > 0 && <div className="rounded-b-[1px] bg-chart-2/85" style={{ height: pct(d.inbound) }} />}
          </div>
        ))}
      </div>
      <div className="mt-2 flex gap-0.5 pl-9 sm:gap-1.5" aria-hidden="true">
        {serie.map((d, k) => (
          <span
            key={d.day}
            className="min-w-0 flex-1 whitespace-nowrap text-center font-mono text-2xs tabular-nums text-faint"
          >
            {rotulo(k) !== null && <span className={rotulo(k) || undefined}>{ddmm(d.day)}</span>}
          </span>
        ))}
      </div>
    </div>
  )
}
