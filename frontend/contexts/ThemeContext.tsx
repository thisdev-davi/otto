'use client'
import { createContext, useContext, useState, useCallback, useEffect, ReactNode } from 'react'

const LS_THEME = 'otto.theme'

/** 'system' segue a preferência do SO ao vivo; os outros dois a ignoram. */
export type Theme = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

interface ThemeContextValue {
  theme: Theme
  /** O que está pintado na tela agora — 'system' já resolvido. */
  resolved: ResolvedTheme
  setTheme: (t: Theme) => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

const MEDIA = '(prefers-color-scheme: dark)'

function systemTheme(): ResolvedTheme {
  if (typeof window === 'undefined') return 'light'
  return window.matchMedia(MEDIA).matches ? 'dark' : 'light'
}

// Espelha exatamente o que o script inline de app/layout.tsx faz. As duas
// implementações precisam concordar: o script pinta antes da hidratação e este
// contexto assume a partir dali — se divergirem, a tela muda sozinha no load.
function applyTheme(resolved: ResolvedTheme) {
  const root = document.documentElement
  root.classList.toggle('dark', resolved === 'dark')
  root.dataset.theme = resolved
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  // Começa em 'system' nos dois lados; o efeito abaixo lê o localStorage já no
  // cliente. Ler no useState inicial quebraria a hidratação (servidor não tem
  // localStorage), e o script inline já garante que a PINTURA está certa.
  const [theme, setThemeState] = useState<Theme>('system')
  const [resolved, setResolved] = useState<ResolvedTheme>('light')

  useEffect(() => {
    let inicial: Theme = 'system'
    try {
      const salvo = localStorage.getItem(LS_THEME)
      if (salvo === 'light' || salvo === 'dark') inicial = salvo
    } catch {
      // Safari em modo privado lança ao tocar em localStorage.
    }
    setThemeState(inicial)
    setResolved(inicial === 'system' ? systemTheme() : inicial)
  }, [])

  // Só faz sentido escutar o SO quando o usuário não escolheu nada.
  useEffect(() => {
    if (theme !== 'system') return
    const mq = window.matchMedia(MEDIA)
    const onChange = () => setResolved(mq.matches ? 'dark' : 'light')
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [theme])

  useEffect(() => {
    applyTheme(resolved)
  }, [resolved])

  const setTheme = useCallback((t: Theme) => {
    setThemeState(t)
    setResolved(t === 'system' ? systemTheme() : t)
    try {
      // 'system' REMOVE a chave em vez de gravar a palavra: assim o script
      // inline não precisa conhecer o valor, e voltar ao padrão é o estado
      // "sem preferência", que é o que ele realmente significa.
      if (t === 'system') localStorage.removeItem(LS_THEME)
      else localStorage.setItem(LS_THEME, t)
    } catch {
      // idem: sem persistência, o tema vale só para esta aba.
    }
  }, [])

  return (
    <ThemeContext.Provider value={{ theme, resolved, setTheme }}>
      {children}
    </ThemeContext.Provider>
  )
}

export function useTheme() {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme must be used within ThemeProvider')
  return ctx
}
