import AppShell from '@/components/AppShell'
import AuthGuard from '@/components/AuthGuard'

// Continua server component de propósito: o estado do drawer mora no AppShell.
export default function InboxLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGuard>
      <AppShell>{children}</AppShell>
    </AuthGuard>
  )
}
