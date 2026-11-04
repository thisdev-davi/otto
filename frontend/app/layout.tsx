import type { Metadata, Viewport } from 'next'
import { Geist, Geist_Mono, Space_Grotesk } from 'next/font/google'
import { AuthProvider } from '@/contexts/AuthContext'
import { ThemeProvider } from '@/contexts/ThemeContext'
import { ToastProvider } from '@/contexts/ToastContext'
import './globals.css'

const geist = Geist({ subsets: ['latin'], variable: '--font-geist' })
const geistMono = Geist_Mono({ subsets: ['latin'], variable: '--font-geist-mono' })
const spaceGrotesk = Space_Grotesk({
  subsets: ['latin'],
  weight: ['500', '600', '700'],
  variable: '--font-space-grotesk',
})

export const metadata: Metadata = {
  title: 'Otto — Inbox',
  description: 'Atendimento pelo WhatsApp com equipe e agente de IA',
}

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  // Deixa o conteúdo ir até a borda em telas com notch; exige env(safe-area-inset-*)
  // no que encosta embaixo (ReplyInput, drawer).
  viewportFit: 'cover',
  // Sem maximumScale/userScalable: bloquear zoom é regressão de acessibilidade.
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#F4F5F7' },
    { media: '(prefers-color-scheme: dark)', color: '#0F1012' },
  ],
}

// Roda ANTES do primeiro paint e antes da hidratação, para o tema escuro não
// piscar branco no load. Precisa ser <script> inline no <head>: next/script, mesmo
// com beforeInteractive, não garante execução antes do paint.
//
// Mantém-se de acordo com contexts/ThemeContext.tsx — mesma chave, mesma regra.
const themeScript = `
try {
  var t = localStorage.getItem('otto.theme');
  var dark = t === 'dark' || (t !== 'light' && matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.classList.toggle('dark', dark);
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
} catch (e) {}
`

// suppressHydrationWarning nos DOIS: extensões de navegador (gerenciador de senha,
// tradutor) injetam atributos em <html> e <body> antes do React hidratar, e o React
// acusa "attributes of the server rendered HTML didn't match".
//
// Em <html> a supressão também cobre uma divergência NOSSA e deliberada: o script
// acima escreve class="dark" e data-theme antes da hidratação, então o atributo que
// o cliente encontra nunca é o que o SSR emitiu. Isso é seguro porque o SSR emite
// <html> SEM className — sem valor servido, o React não reconcilia o atributo e a
// classe do script sobrevive. Pelo mesmo motivo o tema NÃO pode ir no <body>: lá o
// React é dono do className e sobrescreveria.
//
// O escopo é UM elemento: vale para os atributos de <html> e <body> e nada mais. Não
// cascateia para os filhos, então um mismatch de verdade lá dentro continua aparecendo.
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pt-BR" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body
        className={`${geist.variable} ${geistMono.variable} ${spaceGrotesk.variable} font-sans antialiased`}
        suppressHydrationWarning
      >
        {/* Tema por fora do Auth: /login e /convite ficam fora do AuthGuard e
            também precisam ser tematizados.
            Toast entre os dois: DENTRO do tema porque usa tokens, e FORA do Auth para que o
            próprio AuthContext possa avisar e para cobrir as telas de autenticação. Não vai no
            AppShell — lá deixaria /login e /convite descobertos. */}
        <ThemeProvider>
          <ToastProvider>
            <AuthProvider>{children}</AuthProvider>
          </ToastProvider>
        </ThemeProvider>
      </body>
    </html>
  )
}
