import { clsx, type ClassValue } from 'clsx'
import { extendTailwindMerge } from 'tailwind-merge'

// Os tamanhos de fonte fora do padrão do Tailwind (tailwind.config.ts) precisam ser
// declarados: sem isso `text-13` cai no grupo de cor e `cn('text-13', 'text-ink')`
// apaga um dos dois em silêncio.
const twMerge = extendTailwindMerge({
  extend: { classGroups: { 'font-size': [{ text: ['2xs', '13', '15'] }] } },
})

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Chave do DIA local de um instante ISO — 'AAAA-MM-DD'.
 *
 * Local, e não `toISOString().slice(0,10)`: aquele devolve o dia em UTC, e uma mensagem
 * das 22h de Brasília cai no dia seguinte. O separador apareceria no meio da noite,
 * marcando uma virada de data que não aconteceu para quem está lendo.
 */
export function diaLocal(iso: string): string {
  const d = new Date(iso)
  const mes = String(d.getMonth() + 1).padStart(2, '0')
  const dia = String(d.getDate()).padStart(2, '0')
  return `${d.getFullYear()}-${mes}-${dia}`
}

/** Rótulo do separador de dia na thread: 'Hoje', 'Ontem' ou a data por extenso. */
export function rotuloDia(iso: string): string {
  const chave = diaLocal(iso)
  const hoje = new Date()
  const ontem = new Date()
  ontem.setDate(hoje.getDate() - 1)

  if (chave === diaLocal(hoje.toISOString())) return 'Hoje'
  if (chave === diaLocal(ontem.toISOString())) return 'Ontem'

  const d = new Date(iso)
  // O ano só entra quando não é o corrente: "12 de julho de 2026" na conversa de ontem
  // é ruído, mas sem ele uma thread antiga vira uma data ambígua.
  return d.toLocaleDateString('pt-BR', {
    day: 'numeric',
    month: 'long',
    ...(d.getFullYear() !== hoje.getFullYear() ? { year: 'numeric' } : {}),
  })
}
