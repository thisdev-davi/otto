'use client'
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react'

/**
 * Avisa o atendente de mensagem nova quando ele NÃO está olhando a aba.
 *
 * Três sinais, do mais barato ao mais caro em permissão:
 *
 * 1. **Contador no título da aba** — `(3) Otto`. Não pede permissão nenhuma, sobrevive
 *    ao navegador silenciar áudio, e é o único que continua visível depois do primeiro
 *    instante. É o sinal principal, não o acessório.
 * 2. **Bip** — dois tons curtos via Web Audio. Sem arquivo de áudio de propósito: um
 *    `.mp3` seria mais um asset para servir e para o CSP liberar, e o que se quer aqui
 *    são 200ms de senóide.
 * 3. **Notificação do sistema** — só se a pessoa tiver concedido. Nunca pedimos
 *    permissão sozinhos: prompt que aparece sem o usuário ter pedido é negado na hora
 *    e a decisão fica gravada, queimando a chance de perguntar quando fizesse sentido.
 *
 * **Só dispara com a aba em segundo plano.** Quem está com a inbox aberta já vê a
 * conversa chegar na lista; bipar em cima disso seria ruído a cada mensagem.
 */

const CHAVE_PREF = 'otto.notificacoes'

/** Título base da aba, para restaurar quando zera o contador. */
const TITULO_BASE = 'Otto'

interface NotificacoesValue {
  /** A pessoa quer ser avisada? Persistido em localStorage. */
  ativo: boolean
  alternar: () => void
  /** Estado da permissão do navegador: 'default' | 'granted' | 'denied' | 'indisponivel'. */
  permissao: string
  /** Pede permissão ao navegador. Só chame a partir de um clique. */
  pedirPermissao: () => void
  /** Registra uma mensagem nova. Ignora sozinho se a aba estiver em foco. */
  notificar: (titulo: string, corpo: string) => void
}

const Ctx = createContext<NotificacoesValue | null>(null)

/** Aba realmente em segundo plano: outra aba (`hidden`) ou outra janela (sem foco). */
function emSegundoPlano(): boolean {
  if (typeof document === 'undefined') return false
  return document.hidden || !document.hasFocus()
}

export function NotificacoesProvider({ children }: { children: React.ReactNode }) {
  const [ativo, setAtivo] = useState(true)
  const [permissao, setPermissao] = useState('indisponivel')
  const [naoLidas, setNaoLidas] = useState(0)
  const audioRef = useRef<AudioContext | null>(null)

  // Preferência e permissão só existem no cliente — ler no primeiro render quebraria a
  // hidratação, porque o servidor não tem localStorage nem Notification.
  useEffect(() => {
    setAtivo(localStorage.getItem(CHAVE_PREF) !== 'off')
    if ('Notification' in window) setPermissao(Notification.permission)
  }, [])

  /**
   * O `AudioContext` nasce suspenso quando criado sem gesto do usuário — é a política
   * de autoplay. Criar no primeiro clique/tecla, que sempre acontece antes de qualquer
   * mensagem chegar, é o que garante que o primeiro bip toque de verdade em vez de ser
   * engolido em silêncio.
   */
  useEffect(() => {
    function preparar() {
      if (audioRef.current) return
      const AC = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
      if (AC) audioRef.current = new AC()
    }
    window.addEventListener('pointerdown', preparar, { once: true })
    window.addEventListener('keydown', preparar, { once: true })
    return () => {
      window.removeEventListener('pointerdown', preparar)
      window.removeEventListener('keydown', preparar)
    }
  }, [])

  // Voltar para a aba é a confirmação de que a pessoa viu — zera o contador do título.
  useEffect(() => {
    function aoVoltar() {
      if (!emSegundoPlano()) setNaoLidas(0)
    }
    document.addEventListener('visibilitychange', aoVoltar)
    window.addEventListener('focus', aoVoltar)
    return () => {
      document.removeEventListener('visibilitychange', aoVoltar)
      window.removeEventListener('focus', aoVoltar)
    }
  }, [])

  useEffect(() => {
    document.title = naoLidas > 0 ? `(${naoLidas}) ${TITULO_BASE}` : TITULO_BASE
  }, [naoLidas])

  const bipar = useCallback(() => {
    const ctx = audioRef.current
    if (!ctx) return
    // Pode ter sido suspenso depois (o navegador suspende aba em segundo plano).
    if (ctx.state === 'suspended') void ctx.resume()

    // Dois tons curtos ascendentes: mais fácil de distinguir de um som do sistema que
    // um bip único, e curto o bastante para não irritar quem recebe muita mensagem.
    const agora = ctx.currentTime
    ;[
      { hz: 880, em: 0 },
      { hz: 1175, em: 0.12 },
    ].forEach(({ hz, em }) => {
      const osc = ctx.createOscillator()
      const ganho = ctx.createGain()
      osc.type = 'sine'
      osc.frequency.value = hz
      // Rampa em vez de liga/desliga: corte seco de senóide vira um "clique" audível.
      ganho.gain.setValueAtTime(0, agora + em)
      ganho.gain.linearRampToValueAtTime(0.12, agora + em + 0.01)
      ganho.gain.exponentialRampToValueAtTime(0.001, agora + em + 0.1)
      osc.connect(ganho).connect(ctx.destination)
      osc.start(agora + em)
      osc.stop(agora + em + 0.11)
    })
  }, [])

  const notificar = useCallback(
    (titulo: string, corpo: string) => {
      if (!ativo || !emSegundoPlano()) return
      setNaoLidas((n) => n + 1)
      bipar()
      if ('Notification' in window && Notification.permission === 'granted') {
        try {
          // `tag` fixa: mensagens em rajada substituem a notificação anterior em vez de
          // empilharem cinco cartões que a pessoa fecha um a um.
          new Notification(titulo, { body: corpo, tag: 'otto-mensagem' })
        } catch {
          // Safari em alguns contextos lança aqui. O bip e o título já avisaram — e
          // não há nada que o atendente possa fazer a respeito.
        }
      }
    },
    [ativo, bipar],
  )

  const alternar = useCallback(() => {
    setAtivo((v) => {
      const novo = !v
      localStorage.setItem(CHAVE_PREF, novo ? 'on' : 'off')
      if (!novo) setNaoLidas(0)
      return novo
    })
  }, [])

  const pedirPermissao = useCallback(() => {
    if (!('Notification' in window)) return
    void Notification.requestPermission().then(setPermissao)
  }, [])

  return (
    <Ctx.Provider
      value={{ ativo, alternar, permissao, pedirPermissao, notificar }}
    >
      {children}
    </Ctx.Provider>
  )
}

export function useNotificacoes(): NotificacoesValue {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useNotificacoes fora de NotificacoesProvider')
  return ctx
}
