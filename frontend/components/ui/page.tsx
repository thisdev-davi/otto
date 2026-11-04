import * as React from 'react'
import type { LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Card } from '@/components/ui/card'

/*
 * O vocabulário das telas de conteúdo (Relatórios, Agente, Fluxo, Equipe, Respostas,
 * WhatsApp). Toda tela é montada com as mesmas cinco peças, e é isso que faz o app
 * parecer um produto só:
 *
 *   Hero      — o estado da tela numa frase, leve. No máximo um por tela, no topo.
 *   Section   — rótulo em caixa alta + conteúdo. Substitui o "card com título em negrito".
 *   Stat      — número em Geist Mono, sempre `ink`. Cor de número não é informação.
 *   List/Row  — lista em card sem padding, linhas separadas por hairline, ícone em ladrilho.
 *   IconTile  — o ladrilho 36px de ícone, com tom semântico.
 *
 * Referência visual: `app/(inbox)/channels/page.tsx` (a tela de WhatsApp).
 */

// Tons fechados e literais: o Tailwind lê o texto-fonte (`frontend/CLAUDE.md` §3).
const TOM = {
  neutral: 'bg-subtle text-muted',
  accent: 'bg-accent-soft text-accent-fg',
  ai: 'bg-ai-soft text-ai-fg',
  success: 'bg-success-soft text-success-fg',
  warning: 'bg-warning-soft text-warning-fg',
  danger: 'bg-danger-soft text-danger-fg',
} as const

export type Tom = keyof typeof TOM

export function IconTile({
  icon: Icon,
  tom = 'neutral',
  className,
}: {
  icon: LucideIcon
  tom?: Tom
  className?: string
}) {
  return (
    <span className={cn('flex h-9 w-9 flex-none items-center justify-center rounded-[9px]', TOM[tom], className)}>
      <Icon size={18} strokeWidth={1.75} aria-hidden="true" />
    </span>
  )
}

/**
 * O estado da tela numa frase. Leve de propósito: card com hairline, título de 15px e
 * uma linha de apoio. O `sky` fica só no ladrilho — o bloco inteiro em `sky` com título
 * de 24px pesava no meio da tela e foi recusado na prévia.
 */
export function Hero({
  icon: Icon,
  title,
  children,
  aside,
  className,
}: {
  icon?: LucideIcon
  title: React.ReactNode
  children?: React.ReactNode
  /** À direita no desktop, embaixo no celular: a ação ou o número que resume a tela. */
  aside?: React.ReactNode
  className?: string
}) {
  return (
    <section
      className={cn(
        'flex flex-wrap items-center gap-3.5 rounded-xl border border-line bg-surface px-4 py-3.5',
        className
      )}
    >
      {Icon && (
        <span className="flex h-9 w-9 flex-none items-center justify-center rounded-[9px] bg-sky text-on-sky">
          <Icon size={18} strokeWidth={1.75} aria-hidden="true" />
        </span>
      )}
      <div className="min-w-[240px] flex-1">
        <h2 className="text-15 font-semibold text-ink">{title}</h2>
        {children && <div className="mt-0.5 text-13 text-muted">{children}</div>}
      </div>
      {aside && <div className="flex flex-none flex-wrap items-center gap-2">{aside}</div>}
    </section>
  )
}

/** Bloco da tela: rótulo em caixa alta, descrição opcional e uma ação à direita. */
export function Section({
  title,
  description,
  action,
  className,
  children,
}: {
  title: string
  description?: React.ReactNode
  action?: React.ReactNode
  className?: string
  children: React.ReactNode
}) {
  return (
    <section className={cn('flex flex-col gap-2.5', className)}>
      <div className="flex items-end justify-between gap-3 px-1">
        <div className="min-w-0">
          <h3 className="text-2xs font-semibold uppercase tracking-[0.04em] text-faint">{title}</h3>
          {description && <p className="mt-0.5 text-13 text-muted">{description}</p>}
        </div>
        {action && <div className="flex flex-none items-center gap-2">{action}</div>}
      </div>
      {children}
    </section>
  )
}

/** Uma métrica. O número é dado: Geist Mono, tabular, `ink`. */
export function Stat({
  label,
  value,
  hint,
  icon,
  tom,
  className,
}: {
  label: string
  value: string
  hint?: string
  /** Ícone em ladrilho ao lado do rótulo — o tom vai NELE, nunca no número. */
  icon?: LucideIcon
  tom?: Tom
  className?: string
}) {
  return (
    <Card className={cn('flex flex-col gap-2', className)}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-13 font-medium text-muted">{label}</span>
        {icon && <IconTile icon={icon} tom={tom} className="h-7 w-7 rounded-md [&>svg]:h-4 [&>svg]:w-4" />}
      </div>
      <span className="font-mono text-2xl font-medium tabular-nums tracking-[-0.02em] text-ink">{value}</span>
      {hint && <span className="text-xs text-faint">{hint}</span>}
    </Card>
  )
}

/** Lista em card: sem padding, linhas separadas por hairline. */
export function List({ className, ...props }: React.HTMLAttributes<HTMLUListElement>) {
  return (
    <Card padding="none" className="overflow-hidden">
      <ul className={cn('divide-y divide-line', className)} {...props} />
    </Card>
  )
}

/** Linha de lista: ladrilho, título, descrição e o que vier à direita (badge, menu, switch). */
export function Row({
  icon,
  tom,
  leading,
  title,
  description,
  trailing,
  className,
}: {
  icon?: LucideIcon
  tom?: Tom
  /** No lugar do ladrilho, quando a linha é de uma pessoa (avatar). */
  leading?: React.ReactNode
  title: React.ReactNode
  description?: React.ReactNode
  trailing?: React.ReactNode
  className?: string
}) {
  return (
    <li className={cn('flex min-h-[60px] items-center gap-3 px-4 py-2.5', className)}>
      {leading ?? (icon && <IconTile icon={icon} tom={tom} />)}
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium text-ink">{title}</div>
        {description && <div className="text-13 text-muted">{description}</div>}
      </div>
      {trailing && <div className="flex flex-none items-center gap-2">{trailing}</div>}
    </li>
  )
}
