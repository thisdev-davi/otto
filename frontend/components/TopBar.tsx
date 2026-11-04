'use client'
import { useState } from 'react'
import { usePathname } from 'next/navigation'
import { Search, X, Bell, BellOff } from 'lucide-react'
import { useInboxSearch } from '@/contexts/InboxSearchContext'
import { useNotificacoes } from '@/contexts/NotificacoesContext'
import { cn } from '@/lib/utils'

const TITLES: { match: string; title: string; subtitle: string }[] = [
  { match: '/relatorios', title: 'Relatórios', subtitle: 'Desempenho do atendimento' },
  { match: '/agente', title: 'Bot e IA', subtitle: 'Atendimento automático do bot' },
  { match: '/equipe', title: 'Equipe', subtitle: 'Quem atende e o que cada um pode fazer' },
  {
    match: '/respostas',
    title: 'Respostas rápidas',
    subtitle: 'Textos prontos que o atendente insere digitando /',
  },
  { match: '/channels', title: 'Configurações', subtitle: 'Canais do atendimento' },
  {
    match: '/fluxo',
    title: 'Fluxo de atendimento',
    subtitle: 'O roteiro que o bot segue quando a IA está desligada',
  },
  { match: '/agenda', title: 'Agenda', subtitle: 'Datas, valores e reservas do salão' },
  { match: '/espaco', title: 'Fotos do espaço', subtitle: 'Galeria enviada pelo agente' },
  { match: '/auditoria', title: 'Auditoria', subtitle: 'Atividades e segurança da conta' },
  { match: '/inbox', title: 'Inbox', subtitle: 'Atendimentos da sua conta' },
]

// Rota sem entrada em TITLES cai aqui. Antes o fallback era `TITLES[2]`, ou seja,
// uma rota nova sem registro aparecia como "Equipe" — errado e silencioso.
const TITULO_PADRAO = { match: '', title: 'Otto', subtitle: 'Atendimento' }

function CampoBusca({ className }: { className?: string }) {
  const { query, setQuery } = useInboxSearch()
  return (
    <div
      className={cn(
        'flex items-center gap-2 px-3 h-9 bg-canvas rounded-lg border border-transparent focus-within:border-line transition-colors',
        className
      )}
    >
      <Search size={15} className="text-faint flex-shrink-0" />
      <input
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Buscar conversas…"
        // text-base abaixo de sm: campo com fonte < 16px faz o iOS dar zoom ao focar.
        className="bg-transparent outline-none text-base sm:text-[13px] text-ink placeholder:text-faint w-full"
      />
      {query && (
        <button
          onClick={() => setQuery('')}
          aria-label="Limpar busca"
          className="text-faint hover:text-ink transition-colors flex-shrink-0"
        >
          <X size={14} />
        </button>
      )}
    </div>
  )
}

/** Liga/desliga o aviso de mensagem nova e, de quebra, é onde se concede a permissão. */
function BotaoNotificacoes() {
  const { ativo, alternar, permissao, pedirPermissao } = useNotificacoes()
  // Só oferece o upgrade para notificação do sistema quando ainda dá — 'denied' é
  // decisão da pessoa no navegador e não há como reverter daqui.
  const podePedir = ativo && permissao === 'default'

  return (
    <button
      onClick={podePedir ? pedirPermissao : alternar}
      aria-label={
        podePedir
          ? 'Permitir notificações do sistema'
          : ativo
            ? 'Silenciar avisos de mensagem'
            : 'Ativar avisos de mensagem'
      }
      title={
        podePedir
          ? 'Permitir notificações do sistema (o bip já está ligado)'
          : ativo
            ? 'Avisos ligados — clique para silenciar'
            : 'Avisos silenciados — clique para ligar'
      }
      className={cn(
        'h-9 w-9 coarse:h-11 coarse:w-11 flex items-center justify-center rounded-lg transition-colors duration-[120ms] flex-shrink-0',
        ativo ? 'text-muted hover:bg-subtle hover:text-ink' : 'text-faint hover:bg-subtle',
      )}
    >
      {ativo ? <Bell size={16} strokeWidth={1.75} /> : <BellOff size={16} strokeWidth={1.75} />}
    </button>
  )
}

export default function TopBar() {
  const pathname = usePathname()
  const ctx = TITLES.find((t) => pathname.startsWith(t.match)) ?? TITULO_PADRAO
  // Em telas estreitas a busca não cabe ao lado do título; vira uma linha própria.
  const [buscaAberta, setBuscaAberta] = useState(false)
  // Só a inbox tem o que buscar. Nas outras telas o campo sempre foi decoração —
  // agora que ele funciona, deixá-lo lá seria uma promessa funcional falsa.
  const temBusca = pathname.startsWith('/inbox')

  return (
    <header className="flex-shrink-0 bg-surface/85 backdrop-blur border-b border-line z-10">
      <div className="flex items-center gap-3 px-4 sm:px-6 h-[60px]">
        <div className="min-w-0">
          {/* h1 de página é Space Grotesk (design system Otto, régua §3). */}
          <h1 className="font-display text-15 font-semibold tracking-[-0.01em] text-ink leading-tight truncate">{ctx.title}</h1>
          <p className="text-2xs text-faint leading-tight truncate">{ctx.subtitle}</p>
        </div>

        {/* O `ml-auto` vive no agrupador, e não em cada filho: o sino aparece em TODA
            tela, mas a busca só na inbox — com o empurrão nos filhos, a tela sem busca
            deixava o sino colado no título. */}
        <div className="flex items-center gap-1 ml-auto">
          {temBusca && (
            <>
              <CampoBusca className="hidden md:flex w-[260px] max-w-[38vw]" />
              <button
                onClick={() => setBuscaAberta((v) => !v)}
                aria-label={buscaAberta ? 'Fechar busca' : 'Buscar'}
                aria-expanded={buscaAberta}
                className="md:hidden h-9 w-9 coarse:h-11 coarse:w-11 flex items-center justify-center rounded-lg text-muted hover:bg-subtle hover:text-ink transition-colors flex-shrink-0"
              >
                {buscaAberta ? <X size={16} strokeWidth={1.75} /> : <Search size={16} strokeWidth={1.75} />}
              </button>
            </>
          )}
          <BotaoNotificacoes />
        </div>
      </div>

      {temBusca && buscaAberta && (
        <div className="md:hidden px-4 pb-3">
          <CampoBusca />
        </div>
      )}
    </header>
  )
}
