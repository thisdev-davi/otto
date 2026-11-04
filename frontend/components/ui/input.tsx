import * as React from 'react'
import { cn } from '@/lib/utils'

// Base compartilhada por Input, Textarea e Select. Existia como a constante
// `inputCls`, copiada literalmente em três telas.
//
// `text-base sm:text-sm` não é escolha estética: o iOS dá zoom automático ao focar
// qualquer campo com fonte < 16px, e num shell `overflow-hidden` esse zoom não tem
// volta. Abaixo de `sm` o campo é 16px; do tablet para cima volta a 14.
// Foco = borda accent + anel de 3px, sem outline do navegador.
export const fieldBase =
  'w-full px-3 py-2 border border-line-strong rounded-lg text-base sm:text-sm text-ink bg-surface shadow-card outline-none placeholder:text-faint transition-[border-color,box-shadow] duration-[120ms] motion-reduce:transition-none focus:border-accent focus:ring-[3px] focus:ring-accent/15 disabled:opacity-50 disabled:cursor-not-allowed'

export type InputProps = React.InputHTMLAttributes<HTMLInputElement>

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  function Input({ className, ...props }, ref) {
    return <input ref={ref} className={cn(fieldBase, className)} {...props} />
  }
)
