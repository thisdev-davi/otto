'use client'
import * as React from 'react'
import * as DialogPrimitive from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { cn } from '@/lib/utils'

// Radix e não à mão: focus trap, retorno de foco ao fechar, Escape, scroll lock
// e aria-modal são ~200 linhas de código sutil que ninguém revisita.
export const Dialog = DialogPrimitive.Root
export const DialogTrigger = DialogPrimitive.Trigger
export const DialogClose = DialogPrimitive.Close

function Overlay({ className, ...props }: React.ComponentPropsWithoutRef<typeof DialogPrimitive.Overlay>) {
  return (
    <DialogPrimitive.Overlay
      data-otto-overlay=""
      className={cn('fixed inset-0 z-50 bg-overlay/40 backdrop-blur-[2px]', className)}
      {...props}
    />
  )
}

interface DialogContentProps extends React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content> {
  /** 'left' é o drawer de navegação do mobile; 'center' é o modal comum. */
  side?: 'center' | 'left'
  showClose?: boolean
}

export const DialogContent = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Content>,
  DialogContentProps
>(function DialogContent({ className, children, side = 'center', showClose = true, ...props }, ref) {
  const drawer = side === 'left'
  return (
    <DialogPrimitive.Portal>
      <Overlay />
      <DialogPrimitive.Content
        ref={ref}
        // `-center` e não o `data-otto-pop` genérico: este é o único overlay cuja
        // POSIÇÃO depende de um transform, então a animação precisa repeti-lo. Ver o
        // comentário do keyframe em `app/globals.css`.
        {...(drawer ? { 'data-otto-drawer': '' } : { 'data-otto-pop-center': '' })}
        className={cn(
          'fixed z-50 bg-surface-raised shadow-pop focus:outline-none',
          drawer
            ? // pb com safe-area: sem isso o rodapé do drawer fica sob o indicador
              // de home do iPhone.
              'left-0 top-0 h-full w-[85vw] max-w-[300px] border-r border-line pb-[env(safe-area-inset-bottom)]'
            : // `max-h` + `overflow-y-auto` não são refinamento: o conteúdo é
              // centralizado por translate e o Radix trava o scroll do body, então um
              // diálogo mais alto que a janela ficava com topo e rodapé fora da tela e
              // SEM como rolar — o botão Salvar simplesmente não existia para o usuário.
              // 100dvh e não 100vh pelo mesmo motivo do AppShell (barra do Safari).
              'left-1/2 top-1/2 w-[calc(100vw-2rem)] max-w-[440px] -translate-x-1/2 -translate-y-1/2 rounded-2xl border border-line p-5 max-h-[calc(100dvh-2rem)] overflow-y-auto overscroll-contain',
          className
        )}
        {...props}
      >
        {children}
        {showClose && !drawer && (
          <DialogPrimitive.Close
            aria-label="Fechar"
            className="absolute right-3 top-3 h-8 w-8 rounded-lg flex items-center justify-center text-faint hover:bg-subtle hover:text-ink transition-colors"
          >
            <X size={16} />
          </DialogPrimitive.Close>
        )}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  )
})

export function DialogTitle({ className, ...props }: React.ComponentPropsWithoutRef<typeof DialogPrimitive.Title>) {
  return <DialogPrimitive.Title className={cn('text-[15px] font-bold text-ink pr-8', className)} {...props} />
}

export function DialogDescription({
  className,
  ...props
}: React.ComponentPropsWithoutRef<typeof DialogPrimitive.Description>) {
  return (
    <DialogPrimitive.Description
      className={cn('text-[13px] text-muted leading-relaxed mt-1.5', className)}
      {...props}
    />
  )
}

export function DialogFooter({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('flex items-center justify-end gap-2 mt-5', className)} {...props} />
}
