'use client'
import { createContext, useContext, useState, useEffect, ReactNode } from 'react'
import { usePathname } from 'next/navigation'

interface InboxSearchValue {
  /** O que está escrito no campo — atualiza a cada tecla. */
  query: string
  setQuery: (q: string) => void
  /** O que vai para a API. É este que a lista observa. */
  queryDebounced: string
}

const InboxSearchContext = createContext<InboxSearchValue | null>(null)

const DEBOUNCE_MS = 300

/**
 * Estado da busca da inbox.
 *
 * Vive num contexto acima do `<main>` por um motivo concreto: o campo mora na TopBar e
 * a lista mora na página, e as duas são IRMÃS dentro do AppShell — não há prop a
 * passar entre elas sem clonar elemento de um server component.
 *
 * O debounce também mora aqui, e não na lista. `InboxView` remonta a ConversationList
 * (via `key` com `listRefreshKey`) a cada evento de WebSocket, ou seja, a cada mensagem
 * que chega no tenant. Um timer lá dentro seria reiniciado por mensagem de terceiro,
 * fazendo a busca "engasgar" de forma intermitente e impossível de reproduzir.
 */
export function InboxSearchProvider({ children }: { children: ReactNode }) {
  const [query, setQuery] = useState('')
  const [queryDebounced, setQueryDebounced] = useState('')
  const pathname = usePathname()

  // A busca sobrevive à troca de fila (/inbox, /inbox/mine, /inbox/unclaimed) — é o
  // gesto de "onde essa pessoa está?". Mas sair da inbox zera: voltar de Relatórios e
  // encontrar a lista filtrada por um termo antigo parece a fila ter esvaziado.
  useEffect(() => {
    if (!pathname.startsWith('/inbox')) {
      setQuery('')
      setQueryDebounced('')
    }
  }, [pathname])

  useEffect(() => {
    // Limpar é imediato: esperar 300ms para a fila voltar parece travamento.
    if (query === '') {
      setQueryDebounced('')
      return
    }
    const t = setTimeout(() => setQueryDebounced(query), DEBOUNCE_MS)
    return () => clearTimeout(t)
  }, [query])

  return (
    <InboxSearchContext.Provider value={{ query, setQuery, queryDebounced }}>
      {children}
    </InboxSearchContext.Provider>
  )
}

export function useInboxSearch() {
  const ctx = useContext(InboxSearchContext)
  if (!ctx) throw new Error('useInboxSearch must be used within InboxSearchProvider')
  return ctx
}
