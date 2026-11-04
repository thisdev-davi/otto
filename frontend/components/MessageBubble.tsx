'use client'
import { Bot, FileText, Film, Image as ImageIcon, Mic, Smile, Sparkles, StickyNote, Trash2 } from 'lucide-react'
import type { Message } from '@/types/api'
import { mediaUrl } from '@/lib/api'
import { cn } from '@/lib/utils'

function formatMsgTime(dateStr: string): string {
  const d = new Date(dateStr)
  return d.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })
}

// ✓ enviada, ✓✓ entregue (apagado) / lida (cheio). Herda a cor do texto da bolha:
// a distinção é opacidade, que lê no azul do atendente e no cinza-azulado do bot.
// `falhaClass` muda por bolha: o vermelho da paleta daria 2,7:1 sobre o azul.
function StatusTicks({ status, falhaClass }: { status: Message['status']; falhaClass: string }) {
  if (!status) return null
  if (status === 'failed') return <span className={cn('font-sans font-medium', falhaClass)}>Falhou</span>
  const double = status === 'delivered' || status === 'read'
  return (
    <svg
      width={double ? 16 : 11}
      height="11"
      viewBox={double ? '0 0 16 11' : '0 0 11 11'}
      fill="none"
      aria-label={status}
      className={status === 'read' ? 'opacity-100' : 'opacity-60'}
    >
      <path d="M1 5.5L4 8.5L9.5 2.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
      {double && <path d="M6 8.5L11.5 2.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />}
    </svg>
  )
}

// Rótulo de mídia: ícone + texto. O ícone herda `currentColor`, então serve tanto
// no balão `bg-accent` (onde a cor é `on-accent`) quanto no balão claro.
function RotuloMidia({ icone: Icone, children }: { icone: typeof Mic; children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <Icone size={14} className="flex-shrink-0" />
      {children}
    </span>
  )
}

function MediaContent({ msg }: { msg: Message }) {
  // Apagada no banco de mídia: a mensagem fica no fio da conversa, o arquivo não.
  if (msg.media_removed_at) {
    const quando = new Date(msg.media_removed_at).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })
    return (
      <span className="italic opacity-70">
        <RotuloMidia icone={Trash2}>
          Mídia removida em {quando}
          {msg.media_removed_by ? ` por ${msg.media_removed_by}` : ''}
        </RotuloMidia>
      </span>
    )
  }
  const src = msg.media_url ? mediaUrl(msg.media_url) : null
  if (msg.content_type === 'audio') {
    return (
      <div className="flex flex-col gap-1">
        {src ? (
          <audio controls src={src} className="max-w-[240px]" />
        ) : (
          <span className="opacity-70">
            <RotuloMidia icone={Mic}>Áudio</RotuloMidia>
          </span>
        )}
        {msg.transcript_status === 'pending' && <span className="text-[12px] italic opacity-70">Transcrevendo…</span>}
        {msg.transcript_status === 'done' && msg.content && <span className="text-[13px]">{msg.content}</span>}
        {msg.transcript_status === 'failed' && <span className="text-[12px] italic opacity-70">Não foi possível transcrever</span>}
      </div>
    )
  }
  if (msg.content_type === 'image' || msg.content_type === 'sticker') {
    const figurinha = msg.content_type === 'sticker'
    return (
      <div className="flex flex-col gap-1">
        {src ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={src} alt={msg.content ?? (figurinha ? 'figurinha' : 'imagem')} className="max-w-[240px] rounded-lg" />
        ) : (
          <span className="opacity-70">
            {figurinha ? (
              <RotuloMidia icone={Smile}>Figurinha</RotuloMidia>
            ) : (
              <RotuloMidia icone={ImageIcon}>Imagem</RotuloMidia>
            )}
          </span>
        )}
        {msg.content && <span className="text-[13px]">{msg.content}</span>}
      </div>
    )
  }
  // documento / vídeo
  const rotulo =
    msg.content_type === 'video' ? (
      <RotuloMidia icone={Film}>Vídeo</RotuloMidia>
    ) : (
      <RotuloMidia icone={FileText}>{msg.content || 'Documento'}</RotuloMidia>
    )
  return src ? (
    <a href={src} target="_blank" rel="noreferrer" className="underline">
      {rotulo}
    </a>
  ) : (
    <span className="opacity-70">{rotulo}</span>
  )
}

export default function MessageBubble({ msg }: { msg: Message }) {
  // Nunca foi ao cliente: largura toda, fora do jogo esquerda/direita. A do bot é a
  // triagem (roxo da IA); a escrita por gente é a nota interna (amarelo).
  if (msg.is_internal) {
    const ia = msg.is_bot
    return (
      <div
        className={cn(
          'rounded-lg border px-3 py-2.5 text-13 whitespace-pre-wrap',
          ia ? 'bg-ai-soft border-ai-soft text-ai-fg' : 'bg-note-soft border-note-line text-note-fg'
        )}
      >
        <span className="inline-flex items-center gap-1.5 font-semibold">
          {ia ? <Sparkles size={13} className="flex-shrink-0" /> : <StickyNote size={13} className="flex-shrink-0" />}
          {ia ? 'Triagem da IA' : 'Nota interna'}
        </span>
        <div className="mt-0.5">{msg.content}</div>
      </div>
    )
  }

  const isOutbound = msg.direction === 'outbound'
  const isMedia = msg.content_type !== 'text'

  // Figurinha baixada vai sem bolha e menor, como no próprio WhatsApp: dentro do balão
  // ela competia com foto de verdade. Sem arquivo, cai no balão comum com o rótulo.
  if (msg.content_type === 'sticker' && msg.media_url) {
    return (
      <div className={cn('flex flex-col gap-0.5', isOutbound ? 'self-end items-end' : 'self-start items-start')}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={mediaUrl(msg.media_url)} alt="figurinha" className="w-[140px] h-auto" />
        <span className="font-mono text-2xs tabular-nums text-faint">{formatMsgTime(msg.created_at)}</span>
      </div>
    )
  }
  // Quem fala define a bolha: cliente à esquerda; atendente (azul) e bot (cinza-azulado)
  // à direita. O bot é da equipe, mas não é gente — por isso não leva o azul.
  const tipo = !isOutbound ? 'cliente' : msg.is_bot ? 'bot' : 'atendente'
  return (
    <div
      className={cn(
        'max-w-[75%] px-3 pt-2 pb-1.5 text-sm whitespace-pre-wrap',
        tipo === 'cliente' && 'self-start bg-surface text-ink border border-line rounded-[18px] rounded-bl-md',
        tipo === 'atendente' &&
          'self-end bg-gradient-to-b from-accent to-accent-hover text-on-accent shadow-accent rounded-[18px] rounded-br-md',
        tipo === 'bot' && 'self-end bg-bubble-bot text-bubble-bot-fg rounded-[18px] rounded-br-md'
      )}
    >
      {tipo === 'bot' && (
        <span className="flex items-center gap-1 mb-0.5 text-2xs font-semibold tracking-[.02em]">
          <Bot size={12} className="flex-shrink-0" />
          Agente
        </span>
      )}
      {isMedia ? <MediaContent msg={msg} /> : (msg.content ?? '')}
      <div
        className={cn(
          'flex items-center justify-end gap-1 mt-0.5 font-mono text-2xs tabular-nums',
          tipo === 'cliente' && 'text-faint'
        )}
      >
        {/* Opacidade só no horário: "Falhou" precisa do contraste cheio. */}
        <span className={cn(tipo !== 'cliente' && 'opacity-75')}>{formatMsgTime(msg.created_at)}</span>
        {isOutbound && (
          <StatusTicks
            status={msg.status}
            falhaClass={tipo === 'atendente' ? 'text-on-accent-danger' : 'text-danger-fg'}
          />
        )}
      </div>
    </div>
  )
}
