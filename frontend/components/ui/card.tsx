import * as React from 'react'
import { cn } from '@/lib/utils'

interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  /** `raised` para o que flutua (popover, menu) — no escuro a elevação é a
   *  superfície mais clara, não a sombra. */
  surface?: 'default' | 'raised'
  padding?: 'none' | 'sm' | 'md'
}

export function Card({ className, surface = 'default', padding = 'md', ...props }: CardProps) {
  return (
    <div
      className={cn(
        // Sem sombra: card é superfície com hairline. Só flutua o que está por cima.
        'rounded-xl border border-line',
        surface === 'raised' ? 'bg-surface-raised' : 'bg-surface',
        padding === 'sm' && 'px-4 py-3',
        padding === 'md' && 'px-5 py-4',
        className
      )}
      {...props}
    />
  )
}

export function CardTitle({ className, ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
  return <h2 className={cn('text-[15px] font-semibold text-ink', className)} {...props} />
}
