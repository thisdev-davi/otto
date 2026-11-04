'use client'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { Inbox, CalendarDays, BarChart3, Settings, LogOut } from 'lucide-react'
import { useAuth } from '@/contexts/AuthContext'
import { CHANNEL_META } from '@/lib/channels'
import ThemeToggle from '@/components/ThemeToggle'
import { cn } from '@/lib/utils'

// Todas as telas de configuração são admin-only no backend (require_admin).
const SETTINGS_PATHS = [
  '/channels',
  '/agente',
  '/fluxo',
  '/equipe',
  '/respostas',
  '/espaco',
  '/auditoria',
]

const settingsNav = [
  { label: 'WhatsApp', href: '/channels' },
  { label: 'Agente de IA', href: '/agente' },
  { label: 'Fluxo de atendimento', href: '/fluxo' },
  { label: 'Fotos do espaço', href: '/espaco' },
  { label: 'Respostas rápidas', href: '/respostas' },
  { label: 'Equipe', href: '/equipe' },
  { label: 'Auditoria', href: '/auditoria' },
]

function initials(name: string | null): string {
  if (!name) return 'ME'
  return name.split(' ').map((w) => w[0]).join('').slice(0, 2).toUpperCase()
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-[10.5px] font-bold uppercase tracking-[0.1em] text-faint px-2 mb-1.5 mt-1">
      {children}
    </p>
  )
}

export default function Sidebar({ className }: { className?: string }) {
  const pathname = usePathname()
  // `me` vem do AuthContext (/users/me). Antes esta tela baixava a EQUIPE INTEIRA
  // e procurava a si mesma na lista, só para escrever um nome no rodapé.
  const { me, isAdmin, logout } = useAuth()

  const isSettings = SETTINGS_PATHS.some((p) => pathname.startsWith(p))
  // Relatórios e Configurações são admin-only. Esconder é conveniência: quem digitar
  // a URL na mão leva 403 do backend de qualquer jeito.
  const primaryNav = [
    { label: 'Inbox', href: '/inbox', icon: Inbox, active: pathname.startsWith('/inbox') },
    { label: 'Agenda', href: '/agenda', icon: CalendarDays, active: pathname.startsWith('/agenda') },
    ...(isAdmin
      ? [
          { label: 'Relatórios', href: '/relatorios', icon: BarChart3, active: pathname.startsWith('/relatorios') },
          { label: 'Configurações', href: '/channels', icon: Settings, active: isSettings },
        ]
      : []),
  ]

  return (
    <aside
      className={cn(
        'w-[238px] flex-shrink-0 bg-surface border-r border-line flex flex-col h-full',
        className
      )}
    >
      {/* marca */}
      <div className="flex items-center gap-2.5 px-4 h-[60px] flex-shrink-0">
        <div className="w-8 h-8 rounded-[10px] bg-accent flex items-center justify-center flex-shrink-0">
          <div className="relative w-4 h-[11px]">
            <div className="absolute left-0 top-0 w-[11px] h-[11px] rounded-full bg-on-accent" />
            <div className="absolute right-0 top-0 w-[11px] h-[11px] rounded-full bg-on-accent mix-blend-screen opacity-70" />
          </div>
        </div>
        <span className="text-[17px] font-extrabold text-ink">Otto</span>
      </div>

      {/* navegação principal */}
      <nav className="flex flex-col gap-0.5 px-3 mt-1">
        {primaryNav.map((item) => {
          const isActive = item.active
          const Icon = item.icon
          return (
            <Link
              key={item.href}
              href={item.href}
              className={cn(
                'flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13.5px] transition-colors',
                isActive
                  ? 'bg-accent-soft text-accent-fg font-bold'
                  : 'text-muted font-medium hover:bg-canvas hover:text-ink'
              )}
            >
              <Icon size={17} strokeWidth={isActive ? 2.4 : 2} />
              {item.label}
            </Link>
          )
        })}
      </nav>

      {/* contexto */}
      <div className="px-3 mt-5 flex-1 overflow-y-auto">
        {isSettings ? (
          <>
            <SectionLabel>Configurações</SectionLabel>
            <div className="flex flex-col gap-0.5">
              {settingsNav.map((item) => {
                const isActive = pathname.startsWith(item.href)
                return (
                  <Link
                    key={item.href}
                    href={item.href}
                    className={cn(
                      'rounded-lg px-2.5 py-2 text-[13px] transition-colors',
                      isActive
                        ? 'bg-accent-soft text-accent-fg font-semibold'
                        : 'text-muted font-medium hover:bg-canvas hover:text-ink'
                    )}
                  >
                    {item.label}
                  </Link>
                )
              })}
            </div>
          </>
        ) : (
          <>
            <SectionLabel>Canais</SectionLabel>
            <div className="flex flex-col gap-0.5">
              {Object.values(CHANNEL_META).map((ch) => (
                <div
                  key={ch.label}
                  className="flex items-center gap-2.5 px-2.5 py-1.5 text-[13px] font-medium text-muted"
                >
                  <span className={cn('w-2 h-2 rounded-full flex-shrink-0', ch.dotClass)} />
                  {ch.label}
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      {/* usuário */}
      <div className="border-t border-line p-2.5 flex-shrink-0 flex flex-col gap-1.5">
        <ThemeToggle />
        <div className="flex items-center gap-2.5 px-1.5 py-1.5 group">
          <div className="w-8 h-8 rounded-full bg-accent text-on-accent flex items-center justify-center text-[12px] font-bold flex-shrink-0">
            {initials(me?.full_name ?? me?.email ?? null)}
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-[13px] font-semibold text-ink truncate">
              {me?.full_name || me?.email || 'Atendente'}
            </p>
            {me?.email && <p className="text-[11px] text-faint truncate">{me.email}</p>}
          </div>
          <button
            onClick={logout}
            title="Sair"
            className="w-11 h-11 rounded-lg flex items-center justify-center text-faint hover:bg-canvas hover:text-muted transition-colors flex-shrink-0"
          >
            <LogOut size={15} />
          </button>
        </div>
      </div>
    </aside>
  )
}
