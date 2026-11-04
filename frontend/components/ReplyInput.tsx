'use client'
import { useState, useEffect, useRef, ClipboardEvent, KeyboardEvent } from 'react'
import { Mic, Paperclip, Square, Trash2, X } from 'lucide-react'
import { useGravador, formatarDuracao } from '@/hooks/useGravador'
import { useAuth } from '@/contexts/AuthContext'
import { api } from '@/lib/api'
import type { Message, QuickReply } from '@/types/api'
import { Popover, PopoverAnchor, PopoverContent } from '@/components/ui/popover'
import { Spinner } from '@/components/ui/spinner'
import { useToast } from '@/contexts/ToastContext'

// Espelha a allowlist de app/services/attachments.py. Divergir aqui só adianta o "não"
// para o usuário; quem decide de verdade continua sendo o servidor.
const TIPOS_ACEITOS =
  'image/jpeg,image/png,image/webp,video/mp4,video/3gpp,audio/mpeg,audio/ogg,audio/mp4,audio/aac,audio/amr,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,text/plain,application/zip'

// Extraído porque agora há dois botões de enviar: o da barra normal e o da nota de voz.
function IconeEnviar() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
      <path d="M14 2L7 9M14 2L9.5 14L7 9M14 2L2 6.5L7 9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  )
}

function tamanhoLegivel(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

interface ReplyInputProps {
  conversationId: string
  disabled: boolean
  onMessageSent: (msg: Message) => void
}

export default function ReplyInput({ conversationId, disabled, onMessageSent }: ReplyInputProps) {
  const { token } = useAuth()
  const toast = useToast()
  const [content, setContent] = useState('')
  const [sending, setSending] = useState(false)
  const [suggesting, setSuggesting] = useState(false)
  const [suggestError, setSuggestError] = useState<string | null>(null)
  const [quickReplies, setQuickReplies] = useState<QuickReply[]>([])
  const [atalhosIndisponiveis, setAtalhosIndisponiveis] = useState(false)
  const [arquivo, setArquivo] = useState<File | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [erroAnexo, setErroAnexo] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const gravador = useGravador()
  // Instante do último "digitando…" enviado. Estrangula a chamada por TEMPO e não por
  // debounce: o cliente precisa ver o indicador ENQUANTO se digita, e um debounce só
  // dispararia depois da pausa — quando a pessoa já parou.
  const ultimaPresenca = useRef(0)

  /** Avisa o canal a cada 2s de digitação contínua. A presença expira em 3s no cliente,
   *  então reenviar um pouco antes mantém o indicador aceso sem inundar a API. */
  function avisarDigitando() {
    if (!token || disabled) return
    const agora = Date.now()
    if (agora - ultimaPresenca.current < 2000) return
    ultimaPresenca.current = agora
    // Silêncio deliberado (frontend/CLAUDE.md §5): é higiene de fundo que o atendente
    // não pediu e sobre a qual não pode agir. Avisar aqui dispararia a cada tecla.
    api.conversations.typing(token, conversationId).catch(() => {})
  }

  function escolherArquivo(f: File | null) {
    setErroAnexo(null)
    setArquivo(f)
    // Só imagem ganha miniatura; o resto mostra nome e tamanho.
    setPreviewUrl((anterior) => {
      if (anterior) URL.revokeObjectURL(anterior)
      return f && f.type.startsWith('image/') ? URL.createObjectURL(f) : null
    })
  }

  function limparAnexo() {
    escolherArquivo(null)
    if (fileRef.current) fileRef.current.value = ''
  }

  // Sem isto o object URL da última imagem escolhida vaza ao trocar de conversa.
  useEffect(() => {
    return () => {
      setPreviewUrl((anterior) => {
        if (anterior) URL.revokeObjectURL(anterior)
        return null
      })
    }
  }, [])

  // Colar print é o caso de uso mais comum de anexo num atendimento.
  function handlePaste(e: ClipboardEvent<HTMLTextAreaElement>) {
    const item = Array.from(e.clipboardData.items).find((i) => i.type.startsWith('image/'))
    if (!item) return
    const f = item.getAsFile()
    if (!f) return
    e.preventDefault()
    escolherArquivo(f)
  }

  useEffect(() => {
    if (!token) return
    // Falha aqui não vira aviso no carregamento — puniria quem nunca usa `/`. Guarda o
    // erro e explica só quando a pessoa realmente abrir a lista de atalhos.
    api.quickReplies
      .list(token)
      .then((r) => setQuickReplies(r.quick_replies))
      .catch(() => setAtalhosIndisponiveis(true))
  }, [token])

  // popover de atalhos: aberto quando o campo começa com "/"
  const slashQuery = content.startsWith('/') ? content.slice(1).toLowerCase() : null
  const matches =
    slashQuery !== null
      ? quickReplies.filter(
          (q) => q.shortcut.includes(slashQuery) || q.title.toLowerCase().includes(slashQuery)
        )
      : []
  // `dispensado` permite fechar com Escape sem apagar o que foi digitado. Volta a
  // false quando a busca muda, então continuar digitando reabre a lista.
  const [dispensado, setDispensado] = useState(false)
  useEffect(() => {
    setDispensado(false)
  }, [slashQuery])
  const showPopover =
    slashQuery !== null && (matches.length > 0 || atalhosIndisponiveis) && !dispensado

  function pickQuickReply(qr: QuickReply) {
    setContent(qr.content) // substitui o atalho pelo texto pronto (editável antes de enviar)
  }

  async function handleSuggest() {
    if (!token || disabled || suggesting) return
    setSuggesting(true)
    setSuggestError(null)
    try {
      const res = await api.conversations.suggestReply(token, conversationId)
      if (res.suggestion) {
        setContent(res.suggestion) // preenche o campo; atendente revisa e envia
      } else {
        setSuggestError('IA indisponível no momento')
      }
    } catch {
      setSuggestError('Não foi possível gerar a sugestão')
    } finally {
      setSuggesting(false)
    }
  }

  /** Envia a nota de voz gravada. Separado de `handleSend`: PTT não leva legenda. */
  async function enviarNotaDeVoz() {
    if (!token || disabled || sending || !gravador.arquivo) return
    setSending(true)
    setErroAnexo(null)
    try {
      const msg = await api.conversations.sendMedia(
        token,
        conversationId,
        gravador.arquivo,
        '',
        true, // ptt: vira balão de voz no WhatsApp, não anexo de áudio
      )
      onMessageSent(msg)
      gravador.descartar()
    } catch (e) {
      // Mantém a gravação: o áudio só existe aqui, e perdê-lo obrigaria a regravar.
      setErroAnexo((e as Error).message || 'Não foi possível enviar a nota de voz')
    } finally {
      setSending(false)
    }
  }

  async function handleSend() {
    // Com anexo, o arquivo sozinho basta — a legenda é opcional.
    if (!token || disabled || sending) return
    if (!arquivo && !content.trim()) return

    const text = content.trim()
    const anexo = arquivo
    setContent('')
    setSending(true)
    setErroAnexo(null)
    try {
      const msg = anexo
        ? await api.conversations.sendMedia(token, conversationId, anexo, text)
        : await api.conversations.sendMessage(token, conversationId, text)
      onMessageSent(msg)
      if (anexo) limparAnexo()
    } catch (e) {
      // Devolve o que foi digitado e mantém o anexo escolhido: reenviar não pode
      // significar procurar o arquivo de novo.
      setContent(text)
      const msg = (e as Error).message
      if (anexo) setErroAnexo(msg || 'Não foi possível enviar o anexo')
      // Sem isto, envio de TEXTO que falha era totalmente mudo: o texto voltava ao
      // campo e nada mais — o atendente achava que tinha enviado.
      else toast.error(msg || 'Não foi possível enviar a mensagem.')
    } finally {
      setSending(false)
    }
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (showPopover && e.key === 'Escape') {
      e.preventDefault()
      setDispensado(true)
      return
    }
    if (showPopover && e.key === 'Enter' && matches.length > 0) {
      e.preventDefault()
      pickQuickReply(matches[0]) // Enter escolhe o primeiro atalho filtrado
      return
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    // pb com safe-area: com viewportFit=cover o conteúdo vai até a borda, e sem
    // isto a barra de resposta fica sob o indicador de home do iPhone.
    <div className="bg-surface border-t border-line px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] flex flex-col gap-2">
      {arquivo && (
        <div className="flex items-center gap-2.5 bg-subtle border border-line rounded-xl px-3 py-2">
          {previewUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={previewUrl}
              alt="Pré-visualização do anexo"
              className="w-10 h-10 rounded-lg object-cover flex-shrink-0"
            />
          ) : (
            <span className="w-10 h-10 rounded-lg bg-surface border border-line flex items-center justify-center flex-shrink-0">
              <Paperclip size={16} className="text-faint" />
            </span>
          )}
          <div className="flex-1 min-w-0">
            <p className="text-[12.5px] font-medium text-ink truncate">{arquivo.name}</p>
            <p className="text-[11px] text-faint">{tamanhoLegivel(arquivo.size)}</p>
          </div>
          <button
            onClick={limparAnexo}
            aria-label="Remover anexo"
            className="w-11 h-11 rounded-lg flex items-center justify-center text-faint hover:bg-surface hover:text-ink transition-colors flex-shrink-0"
          >
            <X size={16} />
          </button>
        </div>
      )}
      {erroAnexo && <span className="text-[11.5px] text-danger-fg px-1">{erroAnexo}</span>}
      {gravador.erro && (
        <span className="text-[11.5px] text-danger-fg px-1">{gravador.erro}</span>
      )}

      {/* Gravação e prévia SUBSTITUEM a barra normal. Deixar o campo de texto ativo ao
          lado de uma nota de voz pronta convida a escrever uma legenda que o WhatsApp
          não exibe em áudio — a mensagem sairia sem ela, sem avisar. */}
      {gravador.gravando || gravador.arquivo ? (
        <div className="flex items-center gap-3">
          <button
            onClick={gravador.descartar}
            disabled={sending}
            aria-label="Descartar gravação"
            className="w-11 h-11 rounded-full bg-subtle text-muted flex items-center justify-center flex-shrink-0 hover:text-danger-fg transition-colors disabled:opacity-40"
          >
            <Trash2 size={17} />
          </button>

          {gravador.gravando ? (
            <div className="flex-1 flex items-center gap-2.5 min-w-0">
              <span className="w-2.5 h-2.5 rounded-full bg-danger animate-pulse flex-shrink-0" />
              <span className="text-[13px] font-medium text-ink tabular-nums">
                {formatarDuracao(gravador.segundos)}
              </span>
              <span className="text-[12px] text-faint truncate">gravando…</span>
            </div>
          ) : (
            // eslint-disable-next-line jsx-a11y/media-has-caption
            <audio
              src={gravador.previewUrl ?? undefined}
              controls
              className="flex-1 min-w-0 h-11"
            />
          )}

          {gravador.gravando ? (
            <button
              onClick={gravador.parar}
              aria-label="Parar gravação"
              className="w-11 h-11 rounded-full bg-danger text-on-accent-danger flex items-center justify-center flex-shrink-0 transition-colors"
            >
              <Square size={15} fill="currentColor" />
            </button>
          ) : (
            <button
              onClick={enviarNotaDeVoz}
              disabled={sending}
              aria-label="Enviar nota de voz"
              className="w-11 h-11 rounded-full bg-accent text-on-accent flex items-center justify-center flex-shrink-0 shadow-accent hover:bg-accent-hover transition-colors disabled:opacity-40 disabled:cursor-not-allowed disabled:shadow-none"
            >
              {sending ? <Spinner /> : <IconeEnviar />}
            </button>
          )}
        </div>
      ) : (
      <div className="flex items-center gap-3">
      <input
        ref={fileRef}
        type="file"
        accept={TIPOS_ACEITOS}
        hidden
        onChange={(e) => escolherArquivo(e.target.files?.[0] ?? null)}
      />
      {!disabled && (
        <button
          onClick={() => fileRef.current?.click()}
          disabled={sending}
          aria-label="Anexar arquivo"
          title="Anexar imagem, áudio ou documento"
          className="w-11 h-11 rounded-full bg-subtle text-muted flex items-center justify-center flex-shrink-0 hover:text-ink transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <Paperclip size={17} />
        </button>
      )}
      {/* Só quando o navegador sabe gravar — botão que abre um erro ao ser clicado é
          pior que botão ausente. */}
      {!disabled && gravador.suportado && (
        <button
          onClick={gravador.iniciar}
          disabled={sending}
          aria-label="Gravar nota de voz"
          title="Gravar nota de voz"
          className="w-11 h-11 rounded-full bg-subtle text-muted flex items-center justify-center flex-shrink-0 hover:text-ink transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          <Mic size={17} />
        </button>
      )}
      <Popover open={showPopover} onOpenChange={(o) => !o && setDispensado(true)}>
        <PopoverAnchor asChild>
          <div className="flex-1 relative group">
            <textarea
              value={content}
              onChange={(e) => {
                setContent(e.target.value)
                if (e.target.value.trim()) avisarDigitando()
              }}
              onKeyDown={handleKeyDown}
              onPaste={handlePaste}
              disabled={disabled || sending}
              placeholder={
                disabled
                  ? 'Assuma a conversa para responder'
                  : arquivo
                    ? 'Legenda (opcional)…'
                    : 'Digite uma mensagem…'
              }
              rows={1}
              // text-base abaixo de `sm`: com fonte < 16px o iOS dá zoom ao focar,
              // e num shell overflow-hidden esse zoom não tem volta.
              className="w-full bg-subtle rounded-full px-4 py-[11px] text-base sm:text-[13px] text-ink placeholder:text-faint outline-none resize-none leading-relaxed disabled:opacity-50 disabled:cursor-not-allowed"
              style={{ minHeight: '42px', maxHeight: '120px' }}
            />
            {suggestError && (
              <span className="absolute -top-6 left-2 text-[11px] text-danger-fg">{suggestError}</span>
            )}
          </div>
        </PopoverAnchor>
        <PopoverContent
          side="top"
          align="start"
          className="max-h-[220px] p-0 overflow-hidden"
          // O foco NÃO pode sair do textarea: a lista é um autocomplete do que se
          // está digitando, não um menu à parte. Sem isto, abrir o popover tira o
          // cursor do campo e a próxima tecla se perde.
          onOpenAutoFocus={(e) => e.preventDefault()}
          onCloseAutoFocus={(e) => e.preventDefault()}
        >
          <div className="px-3 py-1.5 text-[10px] font-semibold text-faint uppercase border-b border-line">
            Respostas rápidas
          </div>
          {atalhosIndisponiveis && matches.length === 0 && (
            <div className="px-3 py-2.5 text-[11.5px] text-danger-fg leading-relaxed">
              Não foi possível carregar as respostas rápidas. Recarregue a página para
              tentar de novo.
            </div>
          )}
          <div className="max-h-[180px] overflow-y-auto">
            {matches.map((qr) => (
              <button
                key={qr.id}
                type="button"
                onClick={() => pickQuickReply(qr)}
                className="w-full text-left px-3 py-2 hover:bg-subtle transition-colors block"
              >
                <span className="text-[12px] font-semibold text-accent-fg">/{qr.shortcut}</span>
                <span className="text-[12px] text-muted ml-2">{qr.title}</span>
                <span className="block text-[11px] text-faint truncate">{qr.content}</span>
              </button>
            ))}
          </div>
        </PopoverContent>
      </Popover>
      {!disabled && (
        <button
          onClick={handleSuggest}
          disabled={sending || suggesting}
          aria-label="Sugerir resposta com IA"
          title="Sugerir resposta com IA"
          className="w-11 h-11 rounded-full bg-subtle text-ai-fg flex items-center justify-center flex-shrink-0 hover:bg-ai-soft transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {suggesting ? (
            <Spinner />
          ) : (
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path
                d="M8 1.5l1.4 3.6 3.6 1.4-3.6 1.4L8 11.5 6.6 7.9 3 6.5l3.6-1.4L8 1.5zM13 10.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7.7-1.8z"
                fill="currentColor"
              />
            </svg>
          )}
        </button>
      )}
      <button
        onClick={handleSend}
        disabled={disabled || sending || (!content.trim() && !arquivo)}
        aria-label="Enviar mensagem"
        className="w-11 h-11 rounded-full bg-accent text-on-accent flex items-center justify-center flex-shrink-0 shadow-accent hover:bg-accent-hover transition-colors disabled:opacity-40 disabled:cursor-not-allowed disabled:shadow-none"
      >
        {sending ? <Spinner /> : <IconeEnviar />}
      </button>
      </div>
      )}
    </div>
  )
}
