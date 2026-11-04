import { cn } from '@/lib/utils'

interface SegmentedProps<T extends string | number> {
  value: T
  onChange: (v: T) => void
  options: readonly { value: T; label: string }[]
  /** Nome do grupo para leitor de tela ("Período", "Modo"). */
  label: string
  className?: string
}

// Escolha exclusiva entre 2–4 opções curtas (período do relatório, filtro de lista).
// Mais que isso, ou rótulo longo, é `select`.
export function Segmented<T extends string | number>({
  value,
  onChange,
  options,
  label,
  className,
}: SegmentedProps<T>) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={cn('inline-flex h-9 gap-0.5 rounded-lg bg-subtle p-0.5 coarse:h-11', className)}
    >
      {options.map((o) => {
        const ativo = o.value === value
        return (
          <button
            key={String(o.value)}
            type="button"
            role="radio"
            aria-checked={ativo}
            onClick={() => onChange(o.value)}
            className={cn(
              'rounded-md px-3 text-13 font-medium outline-none transition-colors duration-[120ms] focus-visible:ring-[3px] focus-visible:ring-accent-ring motion-reduce:transition-none',
              ativo ? 'bg-surface text-ink shadow-card' : 'text-muted hover:text-ink'
            )}
          >
            {o.label}
          </button>
        )
      })}
    </div>
  )
}
