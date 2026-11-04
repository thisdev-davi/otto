import { cn } from '@/lib/utils'

/**
 * Casca das telas de conteúdo: rolagem própria, largura máxima e respiro.
 *
 * Existia copiada à mão em sete telas, e por isso divergiu: 720, 820, 860 e 900px — e a
 * de Canais tinha `max-width` **sem** `mx-auto`, então a página inteira ficava colada na
 * borda esquerda em qualquer monitor largo. Relatórios não tinha largura nenhuma.
 *
 * Não serve para a Inbox: lá o layout é mestre-detalhe de duas colunas com rolagem
 * interna em cada uma, e uma largura máxima no meio quebraria a thread.
 */
const LARGURA = {
  /** Formulários e listas de uma coluna — a leitura cansa antes dos 720px. */
  form: 'max-w-[720px]',
  /** Grades e calendários, que precisam de mais respiro horizontal. */
  grade: 'max-w-[900px]',
  /** Painéis com cartões lado a lado (relatórios). */
  amplo: 'max-w-[1100px]',
} as const

export type LarguraPagina = keyof typeof LARGURA

export default function PageShell({
  largura = 'form',
  className,
  children,
}: {
  // União fechada, não `string`: o Tailwind lê o texto-fonte, então uma classe montada
  // por template não geraria CSS (`frontend/CLAUDE.md` §3).
  largura?: LarguraPagina
  className?: string
  children: React.ReactNode
}) {
  return (
    <div className="h-full overflow-y-auto">
      <div
        className={cn(
          LARGURA[largura],
          'mx-auto px-4 sm:px-6 lg:px-8 py-6 flex flex-col gap-5',
          className
        )}
      >
        {children}
      </div>
    </div>
  )
}
