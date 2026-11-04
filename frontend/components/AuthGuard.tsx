'use client'
import { useEffect } from 'react'
import { useRouter } from 'next/navigation'
import { useAuth } from '@/contexts/AuthContext'

export default function AuthGuard({ children }: { children: React.ReactNode }) {
  const { token, ready } = useAuth()
  const router = useRouter()

  useEffect(() => {
    // só redireciona depois do restore inicial (ready), pra não expulsar num F5
    if (ready && token === null) {
      router.replace('/login')
    }
  }, [ready, token, router])

  if (!ready || token === null) {
    return (
      <div className="min-h-screen supports-[height:100dvh]:min-h-[100dvh] bg-canvas flex items-center justify-center">
        <p className="text-[13px] text-muted">Carregando…</p>
      </div>
    )
  }

  return <>{children}</>
}
