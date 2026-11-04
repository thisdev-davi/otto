'use client'
import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { FileText, Film, Images, Mic, Search } from 'lucide-react'
import { useAuth } from '@/contexts/AuthContext'
import { useToast } from '@/contexts/ToastContext'
import { api, mediaUrl } from '@/lib/api'
import type { MidiaRecebida, TipoMidia } from '@/types/api'
import { Hero } from '@/components/ui/page'
import PageShell from '@/components/PageShell'
import { Button, buttonVariants } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Spinner } from '@/components/ui/spinner'

// Espelha o `limite` padrão de `GET /media`: página cheia = pode haver mais.
const PAGINA = 60

const TIPOS: { value: TipoMidia; label: string }[] = [
  { value: 'image', label: 'Fotos' },
  { value: 'sticker', label: 'Figurinhas' },
  { value: 'video', label: 'Vídeos' },
  { value: 'document', label: 'Documentos' },
  { value: 'audio', label: 'Áudios' },
]

function quando(iso: string): string {
  return new Date(iso).toLocaleString('pt-BR', {
    day: '2-digit',
    month: '2-digit',
    year: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function Miniatura({ item }: { item: MidiaRecebida }) {
  const src = mediaUrl(item.media_url)
  if (item.content_type === 'image' || item.content_type === 'sticker') {
    return (
      <a href={src} target="_blank" rel="noreferrer" className="block aspect-square bg-subtle">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={src}
          alt={item.legenda || `Enviada por ${item.contato || 'cliente'}`}
          loading="lazy"
          className={item.content_type === 'sticker' ? 'w-full h-full object-contain p-3' : 'w-full h-full object-cover'}
        />
      </a>
    )
  }
  if (item.content_type === 'audio') {
    return (
      <div className="aspect-square bg-subtle flex flex-col items-center justify-center gap-3 p-3 text-muted">
        <Mic size={28} strokeWidth={1.5} />
        <audio controls preload="none" src={src} className="w-full" />
      </div>
    )
  }
  const Icone = item.content_type === 'video' ? Film : FileText
  return (
    <a
      href={src}
      target="_blank"
      rel="noreferrer"
      className="aspect-square bg-subtle flex flex-col items-center justify-center gap-2 p-3 text-muted hover:text-ink"
    >
      <Icone size={32} strokeWidth={1.5} />
      <span className="text-xs text-center line-clamp-2 break-all">
        {item.legenda || (item.content_type === 'video' ? 'Vídeo' : 'Documento')}
      </span>
    </a>
  )
}

export default function MidiaPage() {
  const { token, isAdmin } = useAuth()
  const toast = useToast()

  const [tipo, setTipo] = useState<TipoMidia>('image')
  const [rascunho, setRascunho] = useState('')
  const [busca, setBusca] = useState('')
  const [itens, setItens] = useState<MidiaRecebida[]>([])
  const [temMais, setTemMais] = useState(false)
  const [carregando, setCarregando] = useState(true)
  const [carregandoMais, setCarregandoMais] = useState(false)
  const [erroPainel, setErroPainel] = useState<string | null>(null)
  const [removendo, setRemovendo] = useState<MidiaRecebida | null>(null)
  const [ocupado, setOcupado] = useState(false)

  const carregar = useCallback(async () => {
    if (!token) return
    setCarregando(true)
    try {
      const r = await api.midias.listar(token, { tipo, busca })
      setItens(r.itens)
      setTemMais(r.itens.length === PAGINA)
      setErroPainel(null)
    } catch (e) {
      setErroPainel((e as Error).message || 'Não foi possível carregar as mídias.')
    } finally {
      setCarregando(false)
    }
  }, [token, tipo, busca])

  useEffect(() => {
    carregar()
  }, [carregar])

  async function carregarMais() {
    if (!token || carregandoMais) return
    setCarregandoMais(true)
    try {
      const r = await api.midias.listar(token, { tipo, busca, offset: itens.length })
      setItens((atual) => [...atual, ...r.itens])
      setTemMais(r.itens.length === PAGINA)
    } catch (e) {
      // Ação pedida, sem lugar na tela: o que já estava carregado continua visível.
      toast.error((e as Error).message || 'Não foi possível carregar mais.')
    } finally {
      setCarregandoMais(false)
    }
  }

  async function confirmarRemocao() {
    if (!token || !removendo || ocupado) return
    setOcupado(true)
    try {
      await api.midias.excluir(token, removendo.message_id)
      setItens((atual) => atual.filter((i) => i.message_id !== removendo.message_id))
      setRemovendo(null)
    } catch (e) {
      toast.error((e as Error).message || 'Não foi possível excluir a mídia.')
    } finally {
      setOcupado(false)
    }
  }

  const rotuloTipo = TIPOS.find((t) => t.value === tipo)?.label.toLowerCase() ?? 'mídias'

  return (
    <>
      <PageShell largura="grade" className="gap-6">
        <Hero icon={Images} title="Mídia recebida">
          Fotos, figurinhas, vídeos, documentos e áudios que os clientes mandaram pelo WhatsApp.
        </Hero>

        <div className="flex flex-col sm:flex-row gap-2">
          <Select
            aria-label="Tipo de mídia"
            value={tipo}
            onChange={(e) => setTipo(e.target.value as TipoMidia)}
            className="sm:w-44"
          >
            {TIPOS.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </Select>
          <form
            className="flex flex-1 gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              setBusca(rascunho.trim())
            }}
          >
            <Input
              aria-label="Buscar por cliente"
              placeholder="Nome ou telefone do cliente"
              value={rascunho}
              onChange={(e) => setRascunho(e.target.value)}
              maxLength={80}
            />
            <Button type="submit" variant="secondary" size="icon" aria-label="Buscar">
              <Search size={16} />
            </Button>
          </form>
        </div>

        {erroPainel ? (
          <Card padding="md">
            <EmptyState
              icon={Images}
              title="Não foi possível carregar as mídias"
              description={erroPainel}
              action={
                <Button size="sm" variant="secondary" onClick={carregar}>
                  Tentar de novo
                </Button>
              }
            />
          </Card>
        ) : carregando ? (
          <div className="flex items-center justify-center gap-2 py-10 text-faint">
            <Spinner />
            <span className="text-13">Carregando…</span>
          </div>
        ) : itens.length === 0 ? (
          <EmptyState
            icon={Images}
            title={busca ? 'Nada encontrado' : `Sem ${rotuloTipo} por aqui`}
            description={
              busca
                ? 'Nenhum cliente com esse nome ou telefone mandou mídia deste tipo.'
                : 'Tudo o que os clientes mandarem pelo WhatsApp aparece aqui, do mais novo para o mais antigo.'
            }
          />
        ) : (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
              {itens.map((item) => (
                <Card key={item.message_id} padding="none" className="overflow-hidden flex flex-col">
                  <Miniatura item={item} />
                  <div className="flex flex-col gap-0.5 px-2.5 pt-2">
                    <p className="text-13 font-semibold text-ink truncate">
                      {item.contato || item.telefone || 'Cliente'}
                    </p>
                    <p className="font-mono text-2xs tabular-nums text-faint">{quando(item.created_at)}</p>
                  </div>
                  <div className="flex items-center justify-between gap-1 px-1.5 py-1.5">
                    <Link
                      href={`/inbox?c=${item.conversation_id}`}
                      className={buttonVariants({ size: 'sm', variant: 'ghost' })}
                    >
                      Ver conversa
                    </Link>
                    {isAdmin && (
                      <Button size="sm" variant="ghost" disabled={ocupado} onClick={() => setRemovendo(item)}>
                        Excluir
                      </Button>
                    )}
                  </div>
                </Card>
              ))}
            </div>
            {temMais && (
              <div className="flex justify-center">
                <Button variant="secondary" size="sm" onClick={carregarMais} disabled={carregandoMais}>
                  {carregandoMais ? 'Carregando…' : 'Carregar mais'}
                </Button>
              </div>
            )}
          </>
        )}
      </PageShell>

      <ConfirmDialog
        open={removendo !== null}
        onOpenChange={(o) => !o && setRemovendo(null)}
        title="Excluir esta mídia?"
        description="O arquivo é apagado e não pode ser recuperado. A mensagem continua na conversa, marcada como “mídia removida”, com o seu nome e a data."
        confirmLabel="Excluir"
        destructive
        loading={ocupado}
        onConfirm={confirmarRemocao}
      />
    </>
  )
}
