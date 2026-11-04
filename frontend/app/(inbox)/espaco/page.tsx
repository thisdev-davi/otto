'use client'
import { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowDown, ArrowUp, ImageIcon, Upload } from 'lucide-react'
import { useAuth } from '@/contexts/AuthContext'
import { useToast } from '@/contexts/ToastContext'
import { api } from '@/lib/api'
import type { FotoEspaco } from '@/types/api'
import { Hero } from '@/components/ui/page'
import { Badge } from '@/components/ui/badge'
import PageShell from '@/components/PageShell'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { Spinner } from '@/components/ui/spinner'
import { cn } from '@/lib/utils'

// Quantas o agente manda por conversa. Espelha `salao_fotos.MAX_ENVIO` no backend — quem
// decide de verdade é o servidor; aqui é só o aviso de qual foto vai sair.
const MAX_ENVIO = 4

export default function EspacoPage() {
  const { token, isAdmin } = useAuth()
  const toast = useToast()
  const inputArquivo = useRef<HTMLInputElement>(null)

  const [fotos, setFotos] = useState<FotoEspaco[]>([])
  const [carregando, setCarregando] = useState(true)
  const [erroPainel, setErroPainel] = useState<string | null>(null)
  const [enviando, setEnviando] = useState(false)
  const [ocupado, setOcupado] = useState(false)
  const [removendo, setRemovendo] = useState<FotoEspaco | null>(null)

  const carregar = useCallback(async () => {
    if (!token) return
    try {
      const r = await api.espaco.listFotos(token)
      setFotos(r.fotos)
      setErroPainel(null)
    } catch (e) {
      setErroPainel((e as Error).message || 'Não foi possível carregar as fotos.')
    } finally {
      setCarregando(false)
    }
  }, [token])

  useEffect(() => {
    carregar()
  }, [carregar])

  async function subir(arquivos: FileList | null) {
    if (!token || !arquivos?.length || enviando) return
    setEnviando(true)
    try {
      // Em série, não em paralelo: a `ordem` de cada foto é calculada no servidor a
      // partir do MAX(ordem) atual, e quatro uploads simultâneos disputariam o mesmo
      // número — a galeria sairia embaralhada.
      for (const arquivo of Array.from(arquivos)) {
        await api.espaco.upload(token, arquivo, '')
      }
      await carregar()
    } catch (e) {
      toast.error((e as Error).message || 'Não foi possível enviar a foto.')
    } finally {
      setEnviando(false)
      if (inputArquivo.current) inputArquivo.current.value = ''
    }
  }

  async function alternarAtiva(foto: FotoEspaco) {
    if (!token || ocupado) return
    setOcupado(true)
    try {
      await api.espaco.updateFoto(token, foto.id, { ativa: !foto.ativa })
      await carregar()
    } catch (e) {
      toast.error((e as Error).message || 'Não foi possível atualizar a foto.')
    } finally {
      setOcupado(false)
    }
  }

  async function mover(indice: number, direcao: -1 | 1) {
    if (!token || ocupado) return
    const alvo = indice + direcao
    if (alvo < 0 || alvo >= fotos.length) return
    setOcupado(true)
    try {
      // Troca as duas posições. Usa a posição na lista (e não `ordem`) como verdade:
      // ordens repetidas ou com buracos, herdadas de qualquer erro anterior, se
      // resolvem sozinhas na primeira reordenação.
      await api.espaco.updateFoto(token, fotos[indice].id, { ordem: alvo })
      await api.espaco.updateFoto(token, fotos[alvo].id, { ordem: indice })
      await carregar()
    } catch (e) {
      toast.error((e as Error).message || 'Não foi possível reordenar.')
    } finally {
      setOcupado(false)
    }
  }

  async function confirmarRemocao() {
    if (!token || !removendo || ocupado) return
    setOcupado(true)
    try {
      await api.espaco.removeFoto(token, removendo.id)
      setRemovendo(null)
      await carregar()
    } catch (e) {
      toast.error((e as Error).message || 'Não foi possível remover a foto.')
    } finally {
      setOcupado(false)
    }
  }

  if (!isAdmin) {
    return (
      <div className="h-full flex items-center justify-center px-8">
        <div className="max-w-[420px] text-center flex flex-col gap-2">
          <p className="text-[15px] font-semibold text-ink">Acesso restrito</p>
          <p className="text-[13px] text-faint leading-relaxed">
            Só administradores gerenciam as fotos do espaço — elas são enviadas ao cliente
            pelo agente, em nome do salão.
          </p>
        </div>
      </div>
    )
  }

  const ativas = fotos.filter((f) => f.ativa)

  return (
    <>
      <PageShell largura="grade" className="gap-6">
        <Hero icon={ImageIcon} title="O espaço em fotos">Organize a galeria que o agente envia aos clientes pelo WhatsApp.</Hero>
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <p className="text-[13px] text-faint leading-relaxed max-w-[52ch]">
            Estas são as fotos que <strong className="text-muted">o agente envia</strong>{' '}
            quando o cliente pede para ver o espaço — as {MAX_ENVIO} primeiras ativas, nesta
            ordem, uma vez por conversa.
          </p>
          <div className="flex-shrink-0">
            <input
              ref={inputArquivo}
              type="file"
              accept="image/jpeg,image/png,image/webp"
              multiple
              className="hidden"
              onChange={(e) => subir(e.target.files)}
            />
            <Button
              size="sm"
              onClick={() => inputArquivo.current?.click()}
              disabled={enviando}
            >
              <Upload size={15} />
              {enviando ? 'Enviando…' : 'Adicionar fotos'}
            </Button>
          </div>
        </div>

        {ativas.length > MAX_ENVIO && (
          <Card padding="sm" className="bg-note-soft border-note-line">
            <p className="text-[12.5px] text-note-fg leading-relaxed">
              Há {ativas.length} fotos ativas, mas o agente envia só as {MAX_ENVIO}{' '}
              primeiras. Reordene para escolher quais.
            </p>
          </Card>
        )}

        {erroPainel ? (
          <Card padding="md">
            <EmptyState
              icon={ImageIcon}
              title="Não foi possível carregar as fotos"
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
            <span className="text-[12.5px]">Carregando…</span>
          </div>
        ) : fotos.length === 0 ? (
          <EmptyState
            icon={ImageIcon}
            title="Nenhuma foto do espaço ainda"
            description="Sem foto cadastrada o agente não tem o que mostrar: quem pergunta 'tem foto?' é encaminhado para um atendente, mesmo de madrugada."
            action={
              <Button size="sm" onClick={() => inputArquivo.current?.click()}>
                Adicionar a primeira
              </Button>
            }
          />
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            {fotos.map((foto, indice) => (
              <Card
                key={foto.id}
                padding="none"
                className="overflow-hidden flex flex-col"
              >
                <div className="relative aspect-[4/3] bg-subtle">
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img
                    src={api.espaco.fotoUrl(foto)}
                    alt={foto.titulo || `Foto ${indice + 1} do espaço`}
                    className={cn(
                      'w-full h-full object-cover',
                      !foto.ativa && 'opacity-40 grayscale'
                    )}
                  />
                  {indice < MAX_ENVIO && foto.ativa && (
                    <span className="absolute top-1.5 left-1.5">
                      <Badge variant="ai">o agente envia</Badge>
                    </span>
                  )}
                  {!foto.ativa && (
                    <span className="absolute top-1.5 left-1.5">
                      <Badge variant="neutral">desativada</Badge>
                    </span>
                  )}
                </div>
                <div className="flex items-center justify-between gap-1 px-1.5 py-1.5">
                  <div className="flex items-center gap-0.5">
                    <Button
                      size="icon"
                      variant="ghost"
                      aria-label="Mover para cima"
                      disabled={indice === 0 || ocupado}
                      onClick={() => mover(indice, -1)}
                    >
                      <ArrowUp size={15} />
                    </Button>
                    <Button
                      size="icon"
                      variant="ghost"
                      aria-label="Mover para baixo"
                      disabled={indice === fotos.length - 1 || ocupado}
                      onClick={() => mover(indice, 1)}
                    >
                      <ArrowDown size={15} />
                    </Button>
                  </div>
                  <div className="flex items-center gap-0.5">
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={ocupado}
                      onClick={() => alternarAtiva(foto)}
                    >
                      {foto.ativa ? 'Desativar' : 'Ativar'}
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      disabled={ocupado}
                      onClick={() => setRemovendo(foto)}
                    >
                      Excluir
                    </Button>
                  </div>
                </div>
              </Card>
            ))}
          </div>
        )}
      </PageShell>

      <ConfirmDialog
        open={removendo !== null}
        onOpenChange={(o) => !o && setRemovendo(null)}
        title="Excluir esta foto?"
        description="Ela sai da galeria e o agente deixa de enviá-la. As conversas em que ela já foi mandada continuam mostrando a imagem."
        confirmLabel="Excluir"
        destructive
        loading={ocupado}
        onConfirm={confirmarRemocao}
      />
    </>
  )
}
