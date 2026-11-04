'use client'
import { useRef, useState } from 'react'
import { Play, RotateCcw, Send } from 'lucide-react'
import { useAuth } from '@/contexts/AuthContext'
import { useToast } from '@/contexts/ToastContext'
import { api } from '@/lib/api'
import type { FluxoPasso, GrafoDoFluxo } from '@/types/api'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Spinner } from '@/components/ui/spinner'
import { Section } from '@/components/ui/page'
import { fieldBase } from '@/components/ui/input'
import { cn } from '@/lib/utils'

/**
 * Testar o roteiro sem número de WhatsApp e sem escrever nada.
 *
 * A simulação roda inteira a cada envio: o servidor não guarda sessão de teste, então o
 * histórico daqui é a lista de mensagens que já foram digitadas. Reenviar tudo é mais
 * barato que inventar um estado de teste no banco — e garante que o que se vê é o roteiro
 * do começo, não um resto de tentativa anterior.
 */
export function Simulador({ grafo }: { grafo?: GrafoDoFluxo }) {
  const { token } = useAuth()
  const toast = useToast()
  const [mensagens, setMensagens] = useState<string[]>([])
  const [passos, setPassos] = useState<FluxoPasso[]>([])
  const [rascunho, setRascunho] = useState('')
  const [rodando, setRodando] = useState(false)
  const fim = useRef<HTMLDivElement>(null)

  async function rodar(proximas: string[]) {
    if (!token) return
    setRodando(true)
    try {
      const r = await api.fluxo.simular(token, proximas, grafo)
      setPassos(r.passos)
      setMensagens(proximas)
      requestAnimationFrame(() => fim.current?.scrollIntoView({ block: 'end' }))
    } catch (e) {
      toast.error((e as Error).message || 'Não foi possível simular o roteiro.')
    } finally {
      setRodando(false)
    }
  }

  function enviar(e: React.FormEvent) {
    e.preventDefault()
    const texto = rascunho.trim()
    if (!texto || rodando) return
    setRascunho('')
    rodar([...mensagens, texto])
  }

  return (
    <Section
      title="Testar o roteiro"
      description="Veja as respostas do roteiro sem enviar mensagens nem alterar conversas."
      action={
        mensagens.length > 0 && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setMensagens([])
              setPassos([])
            }}
          >
            <RotateCcw size={14} /> Recomeçar
          </Button>
        )
      }
    >
      <Card padding="sm">
        <div className="flex max-h-[46vh] flex-col gap-2 overflow-y-auto rounded-lg bg-canvas p-3">
          {passos.length === 0 && !rodando && (
            <div className="flex flex-col items-center gap-2 py-6 text-center">
              <Play size={18} strokeWidth={1.75} className="text-faint" aria-hidden="true" />
              <p className="max-w-[36ch] text-13 text-faint">
                Escreva o que um cliente mandaria — “oi” é um bom começo.
              </p>
            </div>
          )}

          {passos.map((p, i) => (
            <div key={i} className="flex flex-col gap-2">
              <p className="max-w-[75%] self-end rounded-2xl rounded-br-md bg-accent px-3 py-1.5 text-13 text-on-accent">
                {p.cliente}
              </p>
              {p.bolhas.map((b, j) => (
                <p
                  key={j}
                  className="max-w-[85%] self-start whitespace-pre-line rounded-2xl rounded-bl-md bg-surface px-3 py-1.5 text-13 leading-relaxed text-ink"
                >
                  {b}
                </p>
              ))}
              {p.encaminha && (
                <p className="self-start rounded-md bg-danger-soft px-2 py-0.5 text-xs font-medium text-danger-fg">
                  Vai para a fila da equipe
                </p>
              )}
              {p.notas.map((n, j) => (
                <p
                  key={`n${j}`}
                  className="max-w-[85%] self-start whitespace-pre-line rounded-lg bg-note-soft px-2.5 py-1.5 text-xs leading-relaxed text-note-fg"
                >
                  Nota interna: {n}
                </p>
              ))}
              {p.no && (
                <p className="self-start text-2xs text-faint">
                  parou em <span className="font-mono">{p.no}</span>
                </p>
              )}
            </div>
          ))}
          {rodando && (
            <div className="flex items-center gap-2 self-start py-1 text-faint">
              <Spinner />
              <span className="text-xs">simulando…</span>
            </div>
          )}
          <div ref={fim} />
        </div>

        <form onSubmit={enviar} className="flex items-center gap-2 pt-3">
          <input
            value={rascunho}
            onChange={(e) => setRascunho(e.target.value)}
            placeholder="Mensagem do cliente…"
            aria-label="Mensagem do cliente"
            className={cn(fieldBase, 'flex-1')}
          />
          <Button type="submit" size="icon" disabled={rodando || !rascunho.trim()} aria-label="Enviar">
            <Send size={16} />
          </Button>
        </form>
      </Card>
    </Section>
  )
}
