import { cn } from '@/lib/utils'

// `currentColor` nos dois traços: o spinner herda a cor de quem o contém, então
// não precisa saber em que fundo está — era o motivo de o SVG à mão do ReplyInput
// vir com o roxo cravado.
export function Spinner({ size = 16, className }: { size?: number; className?: string }) {
  return (
    <svg
      className={cn('animate-spin', className)}
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      role="status"
      aria-label="Carregando"
    >
      <circle cx="8" cy="8" r="6" stroke="currentColor" strokeWidth="2" strokeOpacity="0.25" />
      <path d="M14 8a6 6 0 0 0-6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  )
}
