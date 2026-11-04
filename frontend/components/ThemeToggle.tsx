'use client'
import { Sun, Moon, Monitor } from 'lucide-react'
import { useTheme, type Theme } from '@/contexts/ThemeContext'
import { cn } from '@/lib/utils'

// Três estados e não um interruptor: com dois, "seguir o sistema" vira um estado
// que dá para sair mas não para voltar.
const OPCOES: { value: Theme; label: string; Icon: typeof Sun }[] = [
  { value: 'light', label: 'Tema claro', Icon: Sun },
  { value: 'dark', label: 'Tema escuro', Icon: Moon },
  { value: 'system', label: 'Seguir o sistema', Icon: Monitor },
]

export default function ThemeToggle({ className }: { className?: string }) {
  const { theme, setTheme } = useTheme()

  return (
    <div
      role="radiogroup"
      aria-label="Tema"
      className={cn('flex items-center gap-0.5 rounded-lg bg-canvas p-0.5', className)}
    >
      {OPCOES.map(({ value, label, Icon }) => {
        const ativo = theme === value
        return (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={ativo}
            title={label}
            onClick={() => setTheme(value)}
            className={cn(
              'flex-1 h-8 rounded-[6px] flex items-center justify-center transition-colors',
              ativo
                ? 'bg-surface text-accent-fg shadow-card'
                : 'text-faint hover:text-ink'
            )}
          >
            <Icon size={14} strokeWidth={ativo ? 2.4 : 2} />
            <span className="sr-only">{label}</span>
          </button>
        )
      })}
    </div>
  )
}
