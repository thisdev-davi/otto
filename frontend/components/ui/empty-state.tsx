import * as React from 'react'
import type { LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'

interface EmptyStateProps {
  icon?: LucideIcon
  title: string
  description?: string
  action?: React.ReactNode
  className?: string
}

export function EmptyState({ icon: Icon, title, description, action, className }: EmptyStateProps) {
  return (
    // Diz o que falta e o que fazer: ícone em accent-soft, título, descrição.
    <div className={cn('flex flex-col items-center justify-center gap-1 px-6 py-12 text-center', className)}>
      {Icon && (
        <div className="mb-2 w-10 h-10 rounded-[10px] bg-accent-soft text-accent-fg flex items-center justify-center">
          <Icon size={20} strokeWidth={1.75} aria-hidden="true" />
        </div>
      )}
      <p className="text-sm font-semibold text-ink">{title}</p>
      {description && <p className="text-13 text-muted max-w-xs">{description}</p>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  )
}
