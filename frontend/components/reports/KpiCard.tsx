import { Card } from '@/components/ui/card'
import { cn } from '@/lib/utils'

// A cor do número é um TIPO FECHADO, não string livre de hex. Além de eliminar o
// style={{}} (que não seguiria o tema), fecha a porta para cada chamador inventar
// um tom novo — era `accent?: string` recebendo #16A34A, #0EA5E9, #7C3AED...
export type KpiTone = 'accent' | 'success' | 'danger' | 'ai' | 'info'

// Classes literais: o Tailwind lê texto-fonte, então `text-${tone}` não geraria CSS.
const TONE_CLASS: Record<KpiTone, string> = {
  accent: 'text-accent-fg',
  success: 'text-success-fg',
  danger: 'text-danger-fg',
  ai: 'text-ai-fg',
  info: 'text-info',
}

interface KpiCardProps {
  label: string
  value: string
  hint?: string
  tone?: KpiTone
}

export default function KpiCard({ label, value, hint, tone = 'accent' }: KpiCardProps) {
  return (
    <Card className="flex flex-col gap-1">
      <span className="text-[12px] font-semibold text-muted">{label}</span>
      <span className={cn('text-[26px] font-extrabold leading-tight', TONE_CLASS[tone])}>
        {value}
      </span>
      {hint && <span className="text-[11px] text-faint">{hint}</span>}
    </Card>
  )
}
