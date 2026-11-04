'use client'
import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { Inbox, CalendarDays, BarChart3, Settings2, LogOut, Sun, Moon, Monitor, Check, Menu } from 'lucide-react'
import { useAuth } from '@/contexts/AuthContext'
import { useTheme, type Theme } from '@/contexts/ThemeContext'
import { Logo } from '@/components/Logo'
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'

// Navegação do shell, no desenho do design system "Otto": trilho de 64px no desktop,
// barra inferior no celular. O `Sidebar.tsx` antigo fica no repositório sem uso: é o
// arquivo que o upstream mais altera, e apagá-lo viraria conflito em todo merge.

// Todas as telas de configuração são admin-only no backend (require_admin).
const CONFIG = [
  {
    grupo: 'Atendimento',
    itens: [
      { label: 'WhatsApp', href: '/channels' },
      { label: 'Bot e IA', href: '/agente' },
      { label: 'Fluxo de atendimento', href: '/fluxo' },
      { label: 'Respostas rápidas', href: '/respostas' },
      { label: 'Fotos do espaço', href: '/espaco' },
    ],
  },
  { grupo: 'Conta', itens: [{ label: 'Equipe', href: '/equipe' }, { label: 'Auditoria', href: '/auditoria' }] },
]

export function emConfiguracoes(pathname: string) {
  return CONFIG.some((g) => g.itens.some((i) => pathname.startsWith(i.href)))
}

function useNavPrincipal() {
  const pathname = usePathname()
  const { isAdmin } = useAuth()
  // Relatórios e Configurações são admin-only. Esconder é conveniência: quem digitar
  // a URL na mão leva 403 do backend de qualquer jeito.
  return [
    { label: 'Inbox', href: '/inbox', Icon: Inbox, ativo: pathname.startsWith('/inbox') },
    { label: 'Agenda', href: '/agenda', Icon: CalendarDays, ativo: pathname.startsWith('/agenda') },
    ...(isAdmin
      ? [
          { label: 'Relatórios', href: '/relatorios', Icon: BarChart3, ativo: pathname.startsWith('/relatorios') },
          { label: 'Configurações', href: '/channels', Icon: Settings2, ativo: emConfiguracoes(pathname) },
        ]
      : []),
  ]
}

function iniciais(nome: string | null): string {
  if (!nome) return 'ME'
  return nome.split(' ').map((p) => p[0]).join('').slice(0, 2).toUpperCase()
}

const TEMAS: { value: Theme; label: string; Icon: typeof Sun }[] = [
  { value: 'light', label: 'Tema claro', Icon: Sun },
  { value: 'dark', label: 'Tema escuro', Icon: Moon },
  { value: 'system', label: 'Seguir o sistema', Icon: Monitor },
]

/** Conta, tema e sair. O mesmo menu no avatar do trilho e no "Mais" da barra do celular. */
function MenuConta({ children, lado }: { children: React.ReactNode; lado: 'right' | 'top' }) {
  const { me, logout } = useAuth()
  const { theme, setTheme } = useTheme()
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>{children}</DropdownMenuTrigger>
      <DropdownMenuContent side={lado} align="end" className="w-60">
        <div className="px-2.5 py-2">
          <p className="text-13 font-semibold text-ink truncate">{me?.full_name || me?.email || 'Atendente'}</p>
          {me?.email && <p className="text-xs text-faint truncate">{me.email}</p>}
        </div>
        <DropdownMenuSeparator />
        <DropdownMenuLabel>Tema</DropdownMenuLabel>
        {TEMAS.map(({ value, label, Icon }) => (
          // Item de menu e não o ThemeToggle: botão comum dentro de um menu Radix fica
          // fora da navegação por setas.
          <DropdownMenuItem
            key={value}
            role="menuitemradio"
            aria-checked={theme === value}
            onSelect={(e) => {
              e.preventDefault()
              setTheme(value)
            }}
          >
            <Icon size={16} strokeWidth={1.75} className="text-muted" />
            <span className="flex-1">{label}</span>
            {theme === value && <Check size={16} className="text-accent-fg" />}
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem destructive onSelect={logout}>
          <LogOut size={16} strokeWidth={1.75} />
          Sair
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/** Trilho de 56px do desktop. */
export function Rail({ className }: { className?: string }) {
  const nav = useNavPrincipal()
  const { me } = useAuth()
  return (
    <nav
      aria-label="Principal"
      className={cn('w-14 flex-shrink-0 flex-col items-center gap-1.5 py-3 bg-surface border-r border-line', className)}
    >
      <Link href="/inbox" aria-label="Otto, início" className="mb-5 rounded-[10px]">
        <Logo variant="icon" size="sm" />
      </Link>
      {nav.map(({ label, href, Icon, ativo }) => (
        <Link
          key={href}
          href={href}
          aria-label={label}
          aria-current={ativo ? 'page' : undefined}
          className={cn(
            // 36px como todo controle; 44px só no toque. O hover é um sussurro, não um bloco.
            'group relative w-9 h-9 coarse:w-11 coarse:h-11 rounded-lg flex items-center justify-center outline-none transition-colors duration-[120ms] focus-visible:ring-[3px] focus-visible:ring-accent-ring motion-reduce:transition-none',
            ativo ? 'bg-accent-soft text-accent-fg' : 'text-muted hover:bg-subtle hover:text-ink',
          )}
        >
          <Icon size={18} strokeWidth={1.75} />
          {/* Dica no hover e no foco. O nome acessível já vem do aria-label. */}
          <span
            aria-hidden="true"
            className="pointer-events-none absolute left-full ml-2 z-50 whitespace-nowrap rounded-md bg-ink px-2 py-1 text-xs font-medium text-surface opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100"
          >
            {label}
          </span>
        </Link>
      ))}
      <span className="flex-1" />
      <MenuConta lado="right">
        <button
          aria-label="Conta, tema e sair"
          className="rounded-full outline-none transition-opacity duration-[120ms] hover:opacity-85 focus-visible:ring-[3px] focus-visible:ring-accent-ring coarse:p-1.5 motion-reduce:transition-none"
        >
          <span className="w-8 h-8 rounded-full bg-accent text-on-accent flex items-center justify-center text-xs font-semibold">
            {iniciais(me?.full_name ?? me?.email ?? null)}
          </span>
        </button>
      </MenuConta>
    </nav>
  )
}

/** Barra inferior do celular: os mesmos itens do trilho, sem o logo, e "Mais". */
export function TabBar({ className }: { className?: string }) {
  const nav = useNavPrincipal()
  const item = 'flex-1 h-14 flex flex-col items-center justify-center gap-0.5 text-2xs font-medium transition-colors'
  return (
    <nav
      aria-label="Principal"
      className={cn(
        'flex-shrink-0 border-t border-line bg-surface/85 backdrop-blur pb-[env(safe-area-inset-bottom)]',
        className,
      )}
    >
      <div className="flex">
        {nav.map(({ label, href, Icon, ativo }) => (
          <Link
            key={href}
            href={href}
            aria-current={ativo ? 'page' : undefined}
            className={cn(item, ativo ? 'text-accent-fg' : 'text-muted')}
          >
            <Icon size={20} strokeWidth={1.75} />
            {label}
          </Link>
        ))}
        <MenuConta lado="top">
          <button className={cn(item, 'text-muted')}>
            <Menu size={20} strokeWidth={1.75} />
            Mais
          </button>
        </MenuConta>
      </div>
    </nav>
  )
}

/**
 * Sub-navegação das telas de configuração. No desktop é uma coluna sem borda DENTRO da área
 * de conteúdo, com o ativo neutro: o azul de item ativo é do trilho, e repeti-lo aqui punha
 * dois "você está aqui" competindo. No celular, faixa rolável sob a TopBar.
 */
export function ConfigNav({ className }: { className?: string }) {
  const pathname = usePathname()
  return (
    <nav
      aria-label="Configurações"
      className={cn(
        'flex-shrink-0 flex gap-1 overflow-x-auto px-3 py-2 bg-surface border-b border-line',
        'lg:flex-col lg:gap-0.5 lg:w-[216px] lg:overflow-y-auto lg:py-6 lg:pl-8 lg:pr-0 lg:bg-transparent lg:border-b-0',
        className,
      )}
    >
      {CONFIG.map(({ grupo, itens }, i) => (
        <div key={grupo} className="contents">
          <p
            className={cn(
              'hidden lg:block px-2.5 pb-1.5 text-2xs font-semibold uppercase tracking-[0.04em] text-faint',
              i === 0 ? 'pt-0' : 'pt-[18px]',
            )}
          >
            {grupo}
          </p>
          {itens.map(({ label, href }) => {
            const ativo = pathname.startsWith(href)
            return (
              <Link
                key={href}
                href={href}
                aria-current={ativo ? 'page' : undefined}
                className={cn(
                  'flex-shrink-0 flex items-center h-8 coarse:h-11 px-2.5 rounded-lg text-13 font-medium whitespace-nowrap',
                  'transition-colors duration-[120ms] motion-reduce:transition-none',
                  ativo
                    ? 'bg-surface text-ink shadow-[inset_0_0_0_1px_rgb(var(--line))]'
                    : 'text-muted hover:bg-subtle hover:text-ink',
                )}
              >
                {label}
              </Link>
            )
          })}
        </div>
      ))}
    </nav>
  )
}
