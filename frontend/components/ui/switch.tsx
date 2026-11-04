import { cn } from '@/lib/utils'

interface SwitchProps {
  checked: boolean
  onCheckedChange: (v: boolean) => void
  /** Nome acessível. Obrigatório: o switch não tem texto próprio. */
  label: string
  disabled?: boolean
  className?: string
}

// O Switch do design system (pílula 36×20). Existia à mão em /agente como `Toggle`,
// sem role nem aria-checked — leitor de tela ouvia só "botão".
export function Switch({ checked, onCheckedChange, label, disabled, className }: SwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
      className={cn(
        'relative h-5 w-9 flex-none rounded-full outline-none transition-colors duration-[120ms] focus-visible:ring-[3px] focus-visible:ring-accent-ring disabled:cursor-not-allowed disabled:opacity-40 motion-reduce:transition-none',
        checked ? 'bg-accent' : 'bg-line-strong',
        className
      )}
    >
      <span
        aria-hidden="true"
        className={cn(
          'absolute top-0.5 h-4 w-4 rounded-full bg-on-accent shadow-card transition-[left] duration-[120ms] motion-reduce:transition-none',
          checked ? 'left-[18px]' : 'left-0.5'
        )}
      />
    </button>
  )
}
