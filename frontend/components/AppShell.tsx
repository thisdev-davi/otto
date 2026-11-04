'use client'
import { usePathname } from 'next/navigation'
import TopBar from '@/components/TopBar'
import { Rail, TabBar, ConfigNav, emConfiguracoes } from '@/components/Rail'
import { InboxSearchProvider } from '@/contexts/InboxSearchContext'
import { NotificacoesProvider } from '@/contexts/NotificacoesContext'

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname()
  const config = emConfiguracoes(pathname)

  return (
    // 100dvh e não 100vh: no Safari do iPhone o 100vh ignora a barra de endereço,
    // e como o container é overflow-hidden o campo de resposta ficava embaixo do
    // chrome do navegador, inalcançável. `supports-` mantém o h-screen de reserva.
    // NotificacoesProvider por fora de tudo: o botão de silenciar mora na TopBar e quem
    // dispara o aviso é a InboxView, que são irmãos — mesmo motivo do InboxSearchProvider.
    <NotificacoesProvider>
    <div className="flex h-screen supports-[height:100dvh]:h-[100dvh] overflow-hidden bg-canvas">
      <Rail className="hidden lg:flex" />

      {/* min-w-0 deixa o conteúdo encolher abaixo da largura natural dos filhos;
          sem isso a thread empurra o layout e reintroduz scroll horizontal. */}
      <div className="flex flex-col flex-1 overflow-hidden min-w-0">
        {/* O campo de busca fica na TopBar e a lista dentro de `children`: irmãos.
            O contexto é o que os liga sem clonar elemento de server component. */}
        <InboxSearchProvider>
          <TopBar />
          {config && <ConfigNav className="lg:hidden" />}
          {/* No desktop o menu de configurações mora dentro do conteúdo, ao lado da página;
              cada página rola sozinha (PageShell), então ele fica parado sem `sticky`. */}
          <main className="flex-1 overflow-hidden lg:flex">
            {config && <ConfigNav className="hidden lg:flex" />}
            <div className="h-full min-w-0 flex-1">{children}</div>
          </main>
        </InboxSearchProvider>
        <TabBar className="lg:hidden" />
      </div>
    </div>
    </NotificacoesProvider>
  )
}
