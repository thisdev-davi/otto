import type { GrafoDoFluxo } from '@/types/api'
import { arestasDoGrafo, caminhoDaAresta, posicaoDoNo, tamanhoDoCanvas } from '@/lib/fluxo'

/**
 * As ligações entre as caixas, num `<svg>` atrás delas.
 *
 * A cor vem de `currentColor` sobre uma classe de token: em SVG não dá para usar
 * `stroke-accent` do Tailwind sem o plugin, e hex aqui reprovaria no `check:tokens`.
 */
export function Ligacoes({
  grafo,
  destacado,
}: {
  grafo: Partial<GrafoDoFluxo>
  destacado?: string | null
}) {
  const nos = grafo.nos ?? {}
  const { largura, altura } = tamanhoDoCanvas(grafo)
  const arestas = arestasDoGrafo(grafo)

  return (
    <svg
      width={largura}
      height={altura}
      viewBox={`0 0 ${largura} ${altura}`}
      className="absolute inset-0 pointer-events-none"
      aria-hidden="true"
    >
      <defs>
        <marker id="seta" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
          <path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" />
        </marker>
      </defs>
      {arestas.map((a, i) => {
        const { d, meio } = caminhoDaAresta(posicaoDoNo(nos, a.de), posicaoDoNo(nos, a.para))
        const ligada = !destacado || destacado === a.de || destacado === a.para
        const cor = a.volta ? 'text-warning' : 'text-line-strong'
        return (
          <g key={`${a.de}-${a.para}-${i}`} className={cor} opacity={ligada ? 1 : 0.18}>
            <path
              d={d}
              fill="none"
              stroke="currentColor"
              strokeWidth={destacado === a.de ? 2.2 : 1.5}
              strokeDasharray={a.volta ? '5 4' : undefined}
              markerEnd="url(#seta)"
            />
            {a.rotulo && (
              <text
                x={meio.x}
                y={meio.y - 6}
                textAnchor="middle"
                className="fill-muted"
                fontSize={10}
              >
                {a.rotulo}
              </text>
            )}
          </g>
        )
      })}
    </svg>
  )
}
