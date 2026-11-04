'use client'
import { AlertCircle, CheckCircle2, Info, X } from 'lucide-react'
import { cn } from '@/lib/utils'

export type ToastTone = 'danger' | 'success' | 'info'

export interface Toast {
  id: number
  tone: ToastTone
  message: string
}

// Pílula escura: o aviso se destaca da tela inteira sem depender de cor.
// `bg-ink text-surface` inverte junto com o tema, e o contraste é o mesmo par ink/surface que
// o gate já mede. Erro é a única exceção, em vermelho sólido. O ícone herda a cor do texto:
// verde ou cinza sobre a pílula reprovaria no escuro, onde ela fica clara.
// Classes literais por tom: o Tailwind lê o texto-fonte, então `bg-${tone}` não geraria CSS.
const TOM: Record<ToastTone, { fundo: string; Icone: typeof AlertCircle }> = {
  danger: { fundo: 'bg-danger text-on-danger', Icone: AlertCircle },
  success: { fundo: 'bg-ink text-surface', Icone: CheckCircle2 },
  info: { fundo: 'bg-ink text-surface', Icone: Info },
}

export function ToastViewport({
  toasts,
  onDismiss,
}: {
  toasts: Toast[]
  onDismiss: (id: number) => void
}) {
  return (
    // O <ol> renderiza SEMPRE, mesmo vazio. Se a região viva só existisse quando há mensagem, o
    // leitor de tela não anunciaria nada — é o bug de acessibilidade clássico de toast.
    //
    // z-[60] fica acima do z-50 de dialog e popover: os portais do Radix entram no fim do <body>,
    // então um aviso disparado com um ConfirmDialog aberto ficaria por baixo.
    //
    // No mobile o aviso vem de cima porque o ReplyInput ocupa o rodapé; a partir de lg desce para o
    // canto. Mesma virada que separa a inbox de uma para duas colunas.
    <ol
      role="region"
      aria-label="Notificações"
      aria-live="polite"
      aria-atomic="false"
      className="fixed z-[60] flex flex-col gap-2 pointer-events-none
                 top-[max(0.75rem,env(safe-area-inset-top))] left-1/2 -translate-x-1/2 w-[min(24rem,calc(100vw-1.5rem))]
                 lg:top-auto lg:left-auto lg:translate-x-0 lg:bottom-4 lg:right-4"
    >
      {toasts.map((t) => {
        const { fundo, Icone } = TOM[t.tone]
        return (
          <li
            key={t.id}
            // Os seletores de animação em globals.css só leem estes atributos — não sabem que
            // normalmente vêm do Radix. Escrevê-los à mão dá a animação de entrada E o respeito a
            // `prefers-reduced-motion` sem uma linha de CSS nova.
            data-otto-pop=""
            data-state="open"
            className={cn(
              'pointer-events-auto flex items-start gap-2.5 rounded-[10px] shadow-pop pl-3.5 py-1.5 pr-1.5',
              fundo
            )}
          >
            <Icone size={16} aria-hidden="true" className="flex-shrink-0 mt-[9px]" />
            <span className="flex-1 py-2 text-sm">{t.message}</span>
            <button
              onClick={() => onDismiss(t.id)}
              aria-label="Fechar aviso"
              className="h-9 w-9 coarse:h-11 coarse:w-11 flex items-center justify-center rounded-lg opacity-70 hover:opacity-100 hover:bg-surface/10 transition-opacity flex-shrink-0"
            >
              <X size={14} />
            </button>
          </li>
        )
      })}
    </ol>
  )
}
