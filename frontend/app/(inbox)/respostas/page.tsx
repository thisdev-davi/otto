'use client'
import { useState, useEffect, useCallback } from 'react'
import { Lock, MessageSquareText, MoreHorizontal, Zap } from 'lucide-react'
import { useAuth } from '@/contexts/AuthContext'
import { useToast } from '@/contexts/ToastContext'
import { api } from '@/lib/api'
import type { QuickReply } from '@/types/api'
import PageShell from '@/components/PageShell'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Spinner } from '@/components/ui/spinner'
import { EmptyState } from '@/components/ui/empty-state'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { Hero, IconTile, List, Row, Section } from '@/components/ui/page'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogClose,
} from '@/components/ui/dialog'

// Espelha `_slugify_shortcut` do backend. Serve só para MOSTRAR o que será salvo —
// quem normaliza de verdade continua sendo o servidor. Sem esse eco, quem digita
// "/Horário" salva e vê "horário" na lista, sem entender o que aconteceu.
function previewAtalho(bruto: string): string {
  return bruto.trim().replace(/^\/+/, '').toLowerCase().slice(0, 32)
}

// Sugestões do primeiro uso. Existem porque o recurso é invisível até haver ao menos
// uma resposta: o popover `/` do campo de resposta nunca abre com a lista vazia.
const EXEMPLOS = [
  { shortcut: 'horario', title: 'Horário de atendimento', content: 'Atendemos de segunda a sexta, das 9h às 18h.' },
  { shortcut: 'endereco', title: 'Endereço', content: 'Estamos na Rua Exemplo, 123 — Centro.' },
  { shortcut: 'pagamento', title: 'Formas de pagamento', content: 'Aceitamos Pix, cartão de crédito e débito.' },
  { shortcut: 'obrigado', title: 'Agradecimento', content: 'Obrigado pelo contato! Qualquer dúvida, é só chamar.' },
]

export default function RespostasPage() {
  const { token, isAdmin } = useAuth()
  const toast = useToast()
  const [itens, setItens] = useState<QuickReply[]>([])
  const [carregando, setCarregando] = useState(true)
  const [erroLista, setErroLista] = useState<string | null>(null)

  // null = fechado; objeto = editando; 'novo' = criando.
  const [editando, setEditando] = useState<QuickReply | 'novo' | null>(null)
  const [atalho, setAtalho] = useState('')
  const [titulo, setTitulo] = useState('')
  const [conteudo, setConteudo] = useState('')
  const [salvando, setSalvando] = useState(false)
  const [erroForm, setErroForm] = useState<string | null>(null)
  const [erroAtalho, setErroAtalho] = useState<string | null>(null)

  const [removendo, setRemovendo] = useState<QuickReply | null>(null)
  const [apagando, setApagando] = useState(false)
  const [semeando, setSemeando] = useState(false)

  const carregar = useCallback(async () => {
    if (!token) return
    try {
      const r = await api.quickReplies.list(token)
      setItens(r.quick_replies)
      setErroLista(null)
    } catch (e) {
      setErroLista((e as Error).message || 'Não foi possível carregar as respostas.')
    } finally {
      setCarregando(false)
    }
  }, [token])

  useEffect(() => {
    carregar()
  }, [carregar])

  function abrirNovo() {
    setEditando('novo')
    setAtalho('')
    setTitulo('')
    setConteudo('')
    setErroForm(null)
    setErroAtalho(null)
  }

  function abrirEdicao(qr: QuickReply) {
    setEditando(qr)
    setAtalho(qr.shortcut)
    setTitulo(qr.title)
    setConteudo(qr.content)
    setErroForm(null)
    setErroAtalho(null)
  }

  async function salvar() {
    if (!token || salvando) return
    const sc = previewAtalho(atalho)
    if (!sc || !titulo.trim() || !conteudo.trim()) {
      setErroForm('Preencha atalho, título e conteúdo.')
      return
    }
    setSalvando(true)
    setErroForm(null)
    setErroAtalho(null)
    try {
      if (editando === 'novo') {
        await api.quickReplies.create(token, sc, titulo.trim(), conteudo.trim())
      } else if (editando) {
        await api.quickReplies.update(token, editando.id, sc, titulo.trim(), conteudo.trim())
      }
      setEditando(null)
      await carregar()
    } catch (e) {
      const err = e as Error & { status?: number }
      // 409 é sempre colisão de atalho — o erro pertence ao campo, não ao rodapé.
      if (err.status === 409) setErroAtalho('Esse atalho já existe.')
      else setErroForm(err.message || 'Não foi possível salvar.')
    } finally {
      setSalvando(false)
    }
  }

  async function confirmarRemocao() {
    if (!token || !removendo) return
    setApagando(true)
    try {
      await api.quickReplies.remove(token, removendo.id)
      setRemovendo(null)
      await carregar()
    } catch (e) {
      // Ação pedida, sem lugar na tela: toast (`frontend/CLAUDE.md` §5). O diálogo fica
      // aberto para tentar de novo.
      toast.error((e as Error).message || 'Não foi possível remover.')
    } finally {
      setApagando(false)
    }
  }

  async function criarExemplos() {
    if (!token || semeando) return
    setSemeando(true)
    try {
      for (const ex of EXEMPLOS) {
        await api.quickReplies.create(token, ex.shortcut, ex.title, ex.content)
      }
    } catch (e) {
      toast.error((e as Error).message || 'Não foi possível criar os exemplos.')
    } finally {
      // Recarrega mesmo na falha: os exemplos que já entraram precisam aparecer, senão a
      // tela segue vazia e o segundo clique tropeça no 409 do primeiro atalho.
      await carregar()
      setSemeando(false)
    }
  }

  // A barra que abre o popover no campo de resposta: é o que se digita, então vai em mono.
  const barra = <kbd className="font-mono font-medium text-ink">/</kbd>

  if (!isAdmin) {
    return (
      <PageShell largura="form">
        <Hero icon={Lock} title="Acesso restrito">
          Só administradores editam as respostas rápidas — elas valem para toda a equipe. Você
          continua podendo usá-las: digite {barra} no campo de resposta de qualquer conversa.
        </Hero>
      </PageShell>
    )
  }

  return (
    <>
      <PageShell largura="form" className="gap-8">
        {carregando ? (
          <div className="flex items-center justify-center gap-2 py-10 text-faint">
            <Spinner />
            <span className="text-13">Carregando…</span>
          </div>
        ) : itens.length === 0 && erroLista ? (
          // Falha ao carregar: dizer que falhou, nunca "nenhuma resposta ainda" — a tela
          // vazia levava a recriar respostas que existem (`frontend/CLAUDE.md` §5).
          <Card padding="md">
            <EmptyState
              title="Não foi possível carregar as respostas"
              description={erroLista}
              action={
                <Button size="sm" variant="secondary" onClick={carregar}>
                  Tentar de novo
                </Button>
              }
            />
          </Card>
        ) : itens.length === 0 ? (
          // Vazio, o Hero É o estado da tela: sem lista abaixo, a ação mora nele.
          <Hero
            icon={Zap}
            title="Nenhuma resposta rápida ainda"
            aside={
              <>
                <Button size="sm" onClick={abrirNovo}>
                  Criar a primeira
                </Button>
                <Button size="sm" variant="ghost" onClick={criarExemplos} disabled={semeando}>
                  {semeando ? 'Criando…' : 'Começar com exemplos'}
                </Button>
              </>
            }
          >
            Sem nenhuma cadastrada, o atalho {barra} no campo de resposta não abre. Crie a
            primeira ou comece pelos exemplos.
          </Hero>
        ) : (
          <>
            <Hero
              icon={Zap}
              title={
                <>
                  <span className="font-mono tabular-nums">{itens.length}</span>{' '}
                  {itens.length === 1 ? 'resposta rápida' : 'respostas rápidas'} para a equipe
                </>
              }
            >
              Textos prontos que o atendente insere digitando {barra} seguido do atalho, no
              campo de resposta da conversa.
            </Hero>

            <Section
              title="Atalhos"
              action={
                <Button size="sm" onClick={abrirNovo}>
                  Nova resposta
                </Button>
              }
            >
              {/* Falha ao RECARREGAR (depois de salvar ou excluir): a lista que já estava
                  na tela fica, com o aviso acima dela. */}
              {erroLista && <p className="px-1 text-13 text-danger-fg">{erroLista}</p>}
              <List>
                {itens.map((qr) => (
                  <Row
                    key={qr.id}
                    // No celular o ladrilho e o "Editar" visível saem: com os dois, o texto
                    // ficava com ~140px em 390. Editar continua no menu.
                    leading={<IconTile icon={MessageSquareText} className="hidden sm:flex" />}
                    title={
                      <>
                        <span className="font-mono">/{qr.shortcut}</span>
                        <span className="font-normal text-ink-2"> · {qr.title}</span>
                      </>
                    }
                    description={<span className="line-clamp-2">{qr.content}</span>}
                    trailing={
                      <>
                        <Button
                          size="sm"
                          variant="ghost"
                          className="hidden sm:inline-flex"
                          onClick={() => abrirEdicao(qr)}
                        >
                          Editar
                        </Button>
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon"
                              aria-label={`Mais ações para /${qr.shortcut}`}
                            >
                              <MoreHorizontal size={16} strokeWidth={1.75} aria-hidden="true" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent>
                            <DropdownMenuItem onSelect={() => abrirEdicao(qr)}>Editar</DropdownMenuItem>
                            <DropdownMenuItem destructive onSelect={() => setRemovendo(qr)}>
                              Excluir
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </>
                    }
                  />
                ))}
              </List>
            </Section>
          </>
        )}
      </PageShell>

      <Dialog open={editando !== null} onOpenChange={(o) => !o && setEditando(null)}>
        <DialogContent>
          <DialogTitle>
            {editando === 'novo' ? 'Nova resposta rápida' : 'Editar resposta rápida'}
          </DialogTitle>
          <DialogDescription>
            O atendente digita a barra e o atalho para inserir este texto.
          </DialogDescription>
          <div className="flex flex-col gap-3 mt-4">
            <Field
              label="Atalho"
              hint="sem a barra"
              htmlFor="atalho"
              error={erroAtalho ?? undefined}
            >
              <Input
                id="atalho"
                value={atalho}
                onChange={(e) => setAtalho(e.target.value)}
                placeholder="horario"
              />
              {previewAtalho(atalho) && (
                <span className="text-xs text-faint mt-1">
                  Vai ficar:{' '}
                  <strong className="font-mono font-medium text-muted">/{previewAtalho(atalho)}</strong>
                </span>
              )}
            </Field>
            <Field label="Título" hint="como aparece na lista de atalhos" htmlFor="titulo">
              <Input
                id="titulo"
                value={titulo}
                onChange={(e) => setTitulo(e.target.value)}
                placeholder="Horário de atendimento"
              />
            </Field>
            <Field label="Conteúdo" hint="o texto enviado ao cliente" htmlFor="conteudo">
              <Textarea
                id="conteudo"
                value={conteudo}
                onChange={(e) => setConteudo(e.target.value)}
                rows={4}
                placeholder="Atendemos de segunda a sexta, das 9h às 18h."
              />
            </Field>
            {erroForm && <span className="text-xs text-danger-fg">{erroForm}</span>}
          </div>
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="ghost" size="sm" disabled={salvando}>
                Cancelar
              </Button>
            </DialogClose>
            <Button size="sm" onClick={salvar} disabled={salvando}>
              {salvando ? 'Salvando…' : 'Salvar'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={removendo !== null}
        onOpenChange={(o) => !o && setRemovendo(null)}
        title="Excluir esta resposta rápida?"
        description={
          removendo
            ? `O atalho /${removendo.shortcut} deixa de aparecer para toda a equipe no campo de resposta.`
            : undefined
        }
        confirmLabel="Excluir"
        destructive
        loading={apagando}
        onConfirm={confirmarRemocao}
      />
    </>
  )
}
