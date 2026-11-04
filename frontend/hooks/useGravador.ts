'use client'
import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Gravação de nota de voz pelo microfone do navegador.
 *
 * **O formato não é escolha nossa.** Cada navegador grava no container que sabe, e
 * nenhum deles grava nos três: Chrome só faz `audio/webm;codecs=opus`, Firefox faz
 * `audio/ogg;codecs=opus`, Safari faz `audio/mp4`. Como o WhatsApp só aceita ogg/opus,
 * quem converte é o ffmpeg do Evolution — por isso a lista abaixo é uma ordem de
 * PREFERÊNCIA e não um requisito, e `audio/webm` precisou entrar na allow-list do
 * servidor (`app/services/attachments.py`).
 *
 * O `MediaStream` é encerrado em toda saída — inclusive no cancelamento e no unmount.
 * Track de microfone que fica viva mantém o indicador de gravação aceso no navegador
 * depois que a pessoa já parou, o que parece (com razão) que o app continua ouvindo.
 */

const FORMATOS = [
  'audio/ogg;codecs=opus',
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/mp4',
]

/** Teto de segurança. O limite do servidor para áudio é 16 MB; 5 min não chega perto. */
const MAX_SEGUNDOS = 300

function melhorFormato(): string {
  if (typeof MediaRecorder === 'undefined') return ''
  return FORMATOS.find((f) => MediaRecorder.isTypeSupported(f)) ?? ''
}

export interface Gravador {
  gravando: boolean
  segundos: number
  /** Disponível só depois de parar; é o que se manda ao servidor. */
  arquivo: File | null
  /** Object URL para ouvir antes de enviar. */
  previewUrl: string | null
  erro: string | null
  suportado: boolean
  iniciar: () => Promise<void>
  parar: () => void
  descartar: () => void
}

export function useGravador(): Gravador {
  const [gravando, setGravando] = useState(false)
  const [segundos, setSegundos] = useState(0)
  const [arquivo, setArquivo] = useState<File | null>(null)
  const [previewUrl, setPreviewUrl] = useState<string | null>(null)
  const [erro, setErro] = useState<string | null>(null)

  const recorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const pedacosRef = useRef<Blob[]>([])
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null)
  // Cancelamento precisa ser legível DENTRO do onstop, que não enxerga o estado do
  // React. Sem isto, descartar durante a gravação ainda produzia um arquivo.
  const descartadoRef = useRef(false)

  const pararStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop())
    streamRef.current = null
    if (tickRef.current) {
      clearInterval(tickRef.current)
      tickRef.current = null
    }
  }, [])

  const descartar = useCallback(() => {
    descartadoRef.current = true
    if (recorderRef.current?.state === 'recording') recorderRef.current.stop()
    pararStream()
    setGravando(false)
    setSegundos(0)
    setArquivo(null)
    setPreviewUrl((anterior) => {
      if (anterior) URL.revokeObjectURL(anterior)
      return null
    })
    setErro(null)
  }, [pararStream])

  const parar = useCallback(() => {
    if (recorderRef.current?.state === 'recording') recorderRef.current.stop()
    setGravando(false)
  }, [])

  const iniciar = useCallback(async () => {
    setErro(null)
    const mimeType = melhorFormato()
    if (!mimeType) {
      setErro('Este navegador não grava áudio.')
      return
    }
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      // Negar o microfone é uma escolha da pessoa, não uma falha do sistema — o texto
      // diz o que fazer em vez de sugerir que algo quebrou.
      setErro('Permita o acesso ao microfone para gravar.')
      return
    }

    descartadoRef.current = false
    pedacosRef.current = []
    streamRef.current = stream

    const rec = new MediaRecorder(stream, { mimeType })
    recorderRef.current = rec
    rec.ondataavailable = (e) => {
      if (e.data.size > 0) pedacosRef.current.push(e.data)
    }
    rec.onstop = () => {
      pararStream()
      if (descartadoRef.current) return
      const blob = new Blob(pedacosRef.current, { type: mimeType })
      if (blob.size === 0) {
        setErro('Nada foi gravado.')
        return
      }
      // A extensão acompanha o container real; o servidor decide pelo mimetype, mas um
      // nome coerente é o que aparece se o envio falhar e alguém for olhar o arquivo.
      const ext = mimeType.includes('ogg')
        ? 'ogg'
        : mimeType.includes('mp4')
          ? 'm4a'
          : 'webm'
      setArquivo(
        new File([blob], `nota-de-voz-${Date.now()}.${ext}`, { type: mimeType }),
      )
      setPreviewUrl((anterior) => {
        if (anterior) URL.revokeObjectURL(anterior)
        return URL.createObjectURL(blob)
      })
    }

    rec.start()
    setGravando(true)
    setSegundos(0)
    tickRef.current = setInterval(() => {
      setSegundos((s) => {
        if (s + 1 >= MAX_SEGUNDOS) parar()
        return s + 1
      })
    }, 1000)
  }, [pararStream, parar])

  // Sair da tela no meio de uma gravação não pode deixar o microfone aberto nem vazar
  // o object URL do preview.
  useEffect(() => {
    return () => {
      if (recorderRef.current?.state === 'recording') {
        descartadoRef.current = true
        recorderRef.current.stop()
      }
      streamRef.current?.getTracks().forEach((t) => t.stop())
      if (tickRef.current) clearInterval(tickRef.current)
      setPreviewUrl((anterior) => {
        if (anterior) URL.revokeObjectURL(anterior)
        return null
      })
    }
  }, [])

  return {
    gravando,
    segundos,
    arquivo,
    previewUrl,
    erro,
    // `undefined` no SSR; a checagem roda no cliente e o botão só aparece se der.
    suportado: typeof window !== 'undefined' && typeof MediaRecorder !== 'undefined',
    iniciar,
    parar,
    descartar,
  }
}

/** 95 → '1:35'. */
export function formatarDuracao(segundos: number): string {
  const m = Math.floor(segundos / 60)
  const s = segundos % 60
  return `${m}:${String(s).padStart(2, '0')}`
}
