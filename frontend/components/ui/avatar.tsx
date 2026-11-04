'use client'
import { useState } from 'react'
import { cn } from '@/lib/utils'
import { mediaUrl } from '@/lib/api'

/**
 * Foto do contato, com as iniciais como fallback.
 *
 * Existia três vezes copiado (lista, cabeçalho da thread, painel do cliente) e virou
 * primitivo quando a foto do WhatsApp entrou: sem um lugar só, dois dos três teriam
 * ficado com as iniciais para sempre (`frontend/CLAUDE.md` §3).
 *
 * **As iniciais não são plano B, são o caso comum.** A maioria dos contatos esconde a
 * foto por privacidade, e aí `src` chega `null`. Também caem aqui a assinatura da URL
 * que venceu com a aba aberta e o arquivo que sumiu do disco — em nenhum dos casos o
 * atendente pode ficar com um ícone quebrado no lugar do cliente.
 */

const TAMANHOS = {
  sm: 'w-9 h-9 text-xs',
  md: 'w-[38px] h-[38px] text-[13px]',
} as const

// Paleta categórica por hash do nome. Devolve `rgb(var(--avatar-N))` e não hex: no
// formato de canais do globals.css, `var(--avatar-N)` sozinho não é cor válida.
const TOTAL_AVATARES = 6

export function corDoAvatar(name: string | null): string {
  const i = name ? name.charCodeAt(0) % TOTAL_AVATARES : 0
  return `rgb(var(--avatar-${i + 1}))`
}

export function iniciais(name: string | null): string {
  if (!name) return '?'
  return name
    .split(' ')
    .map((w) => w[0])
    .join('')
    .slice(0, 2)
    .toUpperCase()
}

interface AvatarProps {
  name: string | null
  /** Caminho JÁ ASSINADO vindo do servidor (`contact_avatar_url`), ou null. */
  src?: string | null
  size?: keyof typeof TAMANHOS
  /** `categorico` distingue conversas numa lista; `accent` é identidade única na tela. */
  tone?: 'categorico' | 'accent'
  className?: string
}

export function Avatar({
  name,
  src,
  size = 'md',
  tone = 'accent',
  className,
}: AvatarProps) {
  const [falhou, setFalhou] = useState(false)
  const base = cn(
    'rounded-full flex-shrink-0 flex items-center justify-center overflow-hidden',
    TAMANHOS[size],
    className,
  )

  if (src && !falhou) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={mediaUrl(src)}
        // Vazio de propósito: o nome do cliente está sempre ao lado, e repeti-lo aqui
        // faria o leitor de tela dizer duas vezes a mesma coisa.
        alt=""
        className={cn(base, 'object-cover bg-subtle')}
        onError={() => setFalhou(true)}
      />
    )
  }

  return (
    <div
      className={cn(base, 'font-bold text-on-accent', tone === 'accent' && 'bg-accent')}
      style={
        tone === 'categorico'
          ? { backgroundColor: corDoAvatar(name) }
          : undefined
      }
    >
      {iniciais(name)}
    </div>
  )
}
