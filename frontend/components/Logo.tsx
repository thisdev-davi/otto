import { cn } from '@/lib/utils'

/**
 * Marca do Otto. O símbolo (dois círculos) é a única peça da identidade anterior.
 *
 * - `icon`: o app icon, quadrado `accent` com raio de 30% do lado. Topo do trilho.
 * - `lockup`: símbolo + a palavra "Otto" em Space Grotesk 700. Login, convite, drawer.
 *
 * A palavra é texto vivo até existir o arquivo vetorizado. O símbolo troca de arquivo
 * com o tema: o azul vai sobre fundo claro, o negativo (branco) sobre o escuro. São dois
 * `<img>` e não um só porque o tema é por classe, e `<picture>` só lê a preferência do
 * sistema.
 */
export function Logo({
  variant = 'lockup',
  size = 'lg',
  className,
}: {
  variant?: 'icon' | 'lockup'
  /** lockup: `lg` 31px (login, convite), `sm` 20px (drawer). icon: lado em px é 40 ou 32. */
  size?: 'lg' | 'sm'
  className?: string
}) {
  if (variant === 'icon') {
    const lado = size === 'lg' ? 40 : 32
    return (
      <img
        src="/otto-app-icon.svg"
        alt=""
        width={lado}
        height={lado}
        className={cn('block', className)}
      />
    )
  }

  const grande = size === 'lg'
  const altura = grande ? 22 : 14
  const largura = Math.round((altura * 31) / 22)
  return (
    <span className={cn('inline-flex items-center', grande ? 'gap-[13px]' : 'gap-2', className)}>
      <img src="/otto-simbolo.svg" alt="" width={largura} height={altura} className="dark:hidden" />
      <img
        src="/otto-simbolo-negativo.svg"
        alt=""
        width={largura}
        height={altura}
        className="hidden dark:block"
      />
      <span
        className={cn(
          'font-display font-bold leading-none tracking-[-0.03em] text-ink',
          grande ? 'text-[31px]' : 'text-xl',
        )}
      >
        Otto
      </span>
    </span>
  )
}
