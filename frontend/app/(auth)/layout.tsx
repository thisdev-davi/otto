import ThemeToggle from '@/components/ThemeToggle'
import { Logo } from '@/components/Logo'

// Moldura de /login e /convite: painel de marca à esquerda, cartão à direita.
// No painel vai o app icon, que é igual nos dois temas; o lockup troca de arquivo
// com o tema e por isso fica no cartão.
// O painel é `sky` nos dois temas, e por isso os círculos usam `on-accent` (branco
// fixo) e o texto usa `on-sky`: nada ali pode seguir o tema. Abaixo de `lg` o
// painel some para o formulário vir primeiro.
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen supports-[height:100dvh]:min-h-[100dvh] bg-canvas lg:grid lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
      <section className="relative hidden overflow-hidden bg-sky text-on-sky lg:flex lg:flex-col lg:p-12">
        {/* O símbolo em grande: dois círculos iguais, sobrepostos como no ícone. */}
        <div
          aria-hidden="true"
          className="absolute -bottom-48 -left-48 h-[520px] w-[520px] rounded-full bg-on-accent/35"
        />
        <div
          aria-hidden="true"
          className="absolute -bottom-48 left-[22px] h-[520px] w-[520px] rounded-full bg-on-accent/50"
        />
        <div className="relative max-w-[420px]">
          <Logo variant="icon" className="mb-10" />
          <p className="font-display text-3xl font-semibold tracking-[-0.025em]">
            Toda conversa num lugar só
          </p>
          <p className="mt-3 text-15">
            A equipe captura a conversa, e ninguém responde o mesmo cliente duas vezes.
          </p>
        </div>
      </section>

      <main className="relative flex min-h-screen supports-[height:100dvh]:min-h-[100dvh] items-center justify-center p-4">
        <ThemeToggle className="absolute top-4 right-4 w-28" />
        <div className="w-full max-w-[380px] rounded-2xl border border-line bg-surface p-6 sm:p-8">
          <Logo className="mb-8" />
          {children}
        </div>
      </main>
    </div>
  )
}
