'use client'
import { createContext, useContext, useState, useCallback, useEffect, useRef, ReactNode } from 'react'
import { api } from '@/lib/api'
import { useToast } from '@/contexts/ToastContext'
import type { User } from '@/types/api'

const LS_ACCESS = 'otto.access_token'
const LS_REFRESH = 'otto.refresh_token'

interface JwtPayload {
  user_id: string
  tenant_id: string
  exp: number
  type?: string
}

function parseJwt(token: string): JwtPayload | null {
  try {
    return JSON.parse(atob(token.split('.')[1]))
  } catch {
    return null
  }
}

interface AuthContextValue {
  token: string | null
  userId: string | null
  tenantId: string | null
  ready: boolean // false até o restore/refresh inicial terminar
  me: User | null // perfil do banco (nome, e-mail, papel); null enquanto carrega
  isAdmin: boolean
  login: (email: string, password: string) => Promise<void>
  /** Entra com um par de tokens já emitido (usado pelo aceite de convite). */
  loginWithTokens: (access: string, refresh: string) => void
  logout: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  // Funciona porque o ToastProvider envolve o AuthProvider em app/layout.tsx.
  const toast = useToast()
  const [token, setToken] = useState<string | null>(null)
  const [userId, setUserId] = useState<string | null>(null)
  const [tenantId, setTenantId] = useState<string | null>(null)
  const [ready, setReady] = useState(false)
  const [me, setMe] = useState<User | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  // ref quebra a dependência circular entre agendar-refresh e refresh
  const refreshRef = useRef<() => Promise<boolean>>(async () => false)

  function clearTimer() {
    if (timer.current) {
      clearTimeout(timer.current)
      timer.current = null
    }
  }

  function scheduleRefresh(access: string) {
    clearTimer()
    const p = parseJwt(access)
    if (!p?.exp) return
    // renova ~1 min antes de expirar (mínimo 5s)
    const delay = Math.max(p.exp * 1000 - Date.now() - 60_000, 5_000)
    timer.current = setTimeout(() => void refreshRef.current(), delay)
  }

  function applyAccess(access: string) {
    const p = parseJwt(access)
    setToken(access)
    setUserId(p?.user_id ?? null)
    setTenantId(p?.tenant_id ?? null)
    localStorage.setItem(LS_ACCESS, access)
    scheduleRefresh(access)
  }

  function clearAuth() {
    clearTimer()
    setToken(null)
    setUserId(null)
    setTenantId(null)
    setMe(null)
    localStorage.removeItem(LS_ACCESS)
    localStorage.removeItem(LS_REFRESH)
  }

  refreshRef.current = async () => {
    const rt = localStorage.getItem(LS_REFRESH)
    if (!rt) {
      clearAuth()
      return false
    }
    try {
      const data = await api.auth.refresh(rt)
      localStorage.setItem(LS_REFRESH, data.refresh_token)
      applyAccess(data.access_token)
      return true
    } catch {
      clearAuth()
      return false
    }
  }

  const login = useCallback(async (email: string, password: string) => {
    const data = await api.auth.login(email, password)
    localStorage.setItem(LS_REFRESH, data.refresh_token)
    applyAccess(data.access_token)
  }, [])

  const loginWithTokens = useCallback((access: string, refresh: string) => {
    localStorage.setItem(LS_REFRESH, refresh)
    applyAccess(access)
  }, [])

  // Avisa o servidor ANTES de limpar: `POST /auth/logout` incrementa o token_version e
  // derruba os refresh tokens já emitidos. Sem essa chamada, sair da conta só apagava o
  // localStorage desta aba — o refresh copiado continuava valendo por dias.
  const logout = useCallback(() => {
    const atual = localStorage.getItem(LS_ACCESS)
    if (atual) {
      // Silêncio deliberado (§5 do CLAUDE.md): a sessão local é encerrada logo abaixo de
      // qualquer forma, e não há ação que o usuário possa tomar sobre esta falha — ele
      // pediu para sair e vai sair. Um toast de erro no logout só assustaria.
      api.auth.logout(atual).catch(() => {})
    }
    clearAuth()
  }, [])

  // Perfil do banco. O papel NÃO sai do JWT de propósito: ele não carrega role
  // (rebaixar alguém tem que valer no request seguinte, não só na renovação).
  useEffect(() => {
    if (!token) {
      setMe(null)
      return
    }
    let cancelado = false
    api.users
      .me(token)
      .then((u) => {
        if (!cancelado) setMe(u)
      })
      .catch(() => {
        if (cancelado) return
        setMe(null)
        // `me` alimenta `isAdmin`. Sem aviso, uma falha de rede fazia os menus de
        // administração sumirem da sidebar e o usuário concluía que tinha perdido a
        // permissão.
        toast.error('Não foi possível carregar seu perfil. Alguns menus podem sumir.')
      })
    return () => {
      cancelado = true
    }
  }, [token, toast])

  // restaura a sessão no load: usa o access token se válido, senão renova pelo refresh
  useEffect(() => {
    const access = localStorage.getItem(LS_ACCESS)
    const p = access ? parseJwt(access) : null
    if (access && p && p.exp * 1000 > Date.now() + 5_000) {
      applyAccess(access)
      setReady(true)
    } else if (localStorage.getItem(LS_REFRESH)) {
      refreshRef.current().finally(() => setReady(true))
    } else {
      setReady(true)
    }
    return () => clearTimer()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <AuthContext.Provider
      value={{
        token,
        userId,
        tenantId,
        ready,
        me,
        // Só esconde/mostra UI. Quem decide de verdade é o require_admin do backend.
        isAdmin: me?.role === 'ADMIN',
        login,
        loginWithTokens,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
