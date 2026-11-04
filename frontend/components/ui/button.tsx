import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

// As variantes precisam ser strings LITERAIS: o Tailwind lê o texto-fonte, então
// classe montada por template (`bg-${cor}`) não gera CSS nenhum.
const buttonVariants = cva(
  // 36px e raio 8. Clique "afunda" 1,5% em 120ms; quem pediu menos
  // movimento ao sistema não ganha nem a escala nem a transição.
  'inline-flex items-center justify-center gap-1.5 rounded-lg font-medium whitespace-nowrap border-0 cursor-pointer outline-none transition-[background-color,border-color,color,filter,transform] duration-[120ms] enabled:active:scale-[.985] motion-reduce:transition-none motion-reduce:enabled:active:scale-100 focus-visible:ring-[3px] focus-visible:ring-accent-ring disabled:opacity-40 disabled:cursor-not-allowed',
  {
    variants: {
      variant: {
        // O ÚNICO gradiente do app: accent → accent-hover, duas paradas que o gate de
        // contraste já mede com on-accent. Sem `bg-accent` junto: o twMerge trata cor e
        // imagem de fundo como conflito e apagava a cor, deixando o botão transparente.
        primary:
          'bg-gradient-to-b from-accent to-accent-hover text-on-accent shadow-accent hover:brightness-105 active:brightness-95',
        secondary: 'bg-surface text-ink border border-line-strong shadow-card hover:bg-subtle',
        destructive: 'bg-danger text-on-danger shadow-accent hover:bg-danger-hover',
        success: 'bg-success text-on-success shadow-success hover:brightness-105 active:brightness-95',
        ghost: 'bg-transparent text-muted hover:bg-subtle hover:text-ink',
      },
      // Altura fixa no desktop; no toque (`coarse:`) o alvo sobe para 44px, porque 32px
      // erra com frequência sob o dedo.
      size: {
        sm: 'h-8 px-2.5 text-13 coarse:min-h-11',
        md: 'h-9 px-3.5 text-sm coarse:min-h-11',
        icon: 'h-9 w-9 p-0 coarse:min-h-11 coarse:min-w-11',
      },
    },
    defaultVariants: { variant: 'primary', size: 'md' },
  }
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

export function Button({ className, variant, size, ...props }: ButtonProps) {
  return <button className={cn(buttonVariants({ variant, size }), className)} {...props} />
}

export { buttonVariants }
