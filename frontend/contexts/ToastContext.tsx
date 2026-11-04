'use client'
import { createContext, useContext, useState, useCallback, useRef, useEffect, ReactNode } from 'react'
import { ToastViewport, type Toast, type ToastTone } from '@/components/ui/toast'

interface ToastApi {
  error: (message: string) => void
  success: (message: string) => void
  show: (t: { tone: ToastTone; message: string }) => void
}

const ToastContext = createContext<ToastApi | null>(null)

const DURACAO: Record<ToastTone, number> = {
  // Falha precisa de tempo para ser lida; confirmação some antes de virar estorvo.
  danger: 6000,
  success: 4000,
  info: 4000,
}

const MAX_VISIVEIS = 3

/**
 * Superfície global de aviso.
 *
 * Não usa Radix, e isso NÃO viola a regra de `frontend/CLAUDE.md` §3. Aquela regra existe por
 * motivos nomeados — focus trap, Escape, retorno de foco e detecção de colisão — e um toast não
 * quer nenhum deles: não é modal, não pode roubar o foco de quem está digitando, e a posição é
 * fixa. `@radix-ui/react-toast` traria Provider+Viewport+Root+Title+Description+Action+Close e um
 * modelo de swipe para exibir no máximo três frases que somem sozinhas.
 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>())
  const proximoId = useRef(0)

  const dismiss = useCallback((id: number) => {
    const t = timers.current.get(id)
    if (t) {
      clearTimeout(t)
      timers.current.delete(id)
    }
    setToasts((atuais) => atuais.filter((x) => x.id !== id))
  }, [])

  const agendar = useCallback(
    (id: number, tone: ToastTone) => {
      timers.current.set(id, setTimeout(() => dismiss(id), DURACAO[tone]))
    },
    [dismiss]
  )

  const show = useCallback(
    ({ tone, message }: { tone: ToastTone; message: string }) => {
      setToasts((atuais) => {
        // Deduplicação não é refinamento: `ConversationThread` refaz o fetch a cada evento de
        // WebSocket do tenant, então com a API fora isto viraria uma parede de avisos idênticos.
        // Mensagem repetida só reinicia o relógio da que já está na tela.
        const existente = atuais.find((t) => t.message === message)
        if (existente) {
          const antigo = timers.current.get(existente.id)
          if (antigo) clearTimeout(antigo)
          agendar(existente.id, existente.tone)
          return atuais
        }

        const id = proximoId.current++
        agendar(id, tone)
        const proximos = [...atuais, { id, tone, message }]
        // Descarta o mais antigo em vez de empilhar sem fim.
        if (proximos.length > MAX_VISIVEIS) {
          const removido = proximos.shift()
          if (removido) {
            const t = timers.current.get(removido.id)
            if (t) {
              clearTimeout(t)
              timers.current.delete(removido.id)
            }
          }
        }
        return proximos
      })
    },
    [agendar]
  )

  const error = useCallback((message: string) => show({ tone: 'danger', message }), [show])
  const success = useCallback((message: string) => show({ tone: 'success', message }), [show])

  const timersAtuais = timers.current
  useEffect(() => {
    return () => {
      timersAtuais.forEach((t) => clearTimeout(t))
      timersAtuais.clear()
    }
  }, [timersAtuais])

  return (
    <ToastContext.Provider value={{ error, success, show }}>
      {children}
      <ToastViewport toasts={toasts} onDismiss={dismiss} />
    </ToastContext.Provider>
  )
}

export function useToast() {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used within ToastProvider')
  return ctx
}
