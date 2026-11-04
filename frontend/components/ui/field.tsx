import * as React from 'react'
import { cn } from '@/lib/utils'

interface FieldProps {
  label: string
  /** Complemento em texto fraco, na mesma linha do rótulo. */
  hint?: string
  /** Mensagem de erro; troca a cor da borda de quem estiver dentro. */
  error?: string
  htmlFor?: string
  className?: string
  children: React.ReactNode
}

// Absorve o helper `Field` que existia solto em /agente e o par label+input
// repetido em /equipe, /login e no convite.
export function Field({ label, hint, error, htmlFor, className, children }: FieldProps) {
  return (
    <div className={cn('flex flex-col gap-1.5', className)}>
      <label htmlFor={htmlFor} className="text-13 font-medium text-ink">
        {label}
        {hint && <span className="font-normal text-faint"> — {hint}</span>}
      </label>
      {children}
      {error && <span className="text-xs text-danger-fg">{error}</span>}
    </div>
  )
}
