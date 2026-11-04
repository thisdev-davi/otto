'use client'
import { useState, useEffect, useCallback } from 'react'
import { FileText } from 'lucide-react'
import { api } from '@/lib/api'
import type { KbSource, RagAnswer } from '@/types/api'
import { useToast } from '@/contexts/ToastContext'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { List, Row, Section } from '@/components/ui/page'


function fmtDate(iso: string | null): string {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })
  } catch {
    return ''
  }
}

/**
 * Janela de diagnóstico do RAG — leitura apenas.
 *
 * Os documentos vêm de `conteudo/base/*.md` e entram por `scripts/sincronizar_conteudo.py`.
 * Esta tela já teve formulário de adicionar/editar e botão de remover; eles saíram junto com
 * as rotas de escrita, porque escrever por aqui durava até a próxima sincronização
 * sobrescrever tudo — sem aviso nenhum. Pelo mesmo motivo o "Enviar documento" da prancha
 * não existe aqui.
 *
 * O que sobra não tem substituto por arquivo: o selo "Reindexar" (uma fonte embedada com
 * outro modelo existe no banco e é invisível para a busca) e o testador, que é o único lugar
 * do produto onde se pergunta como um cliente e se vê o que a busca recuperou.
 */
export default function KnowledgeBase({
  token,
  usarBusca,
  onUsarBusca,
  embeddingsReady,
}: {
  token: string
  /** `use_rag` do rascunho da página: o interruptor mora aqui, o estado e o salvar lá. */
  usarBusca: boolean
  onUsarBusca: (v: boolean) => void
  embeddingsReady: boolean
}) {
  const toast = useToast()
  const [sources, setSources] = useState<KbSource[]>([])
  const [loading, setLoading] = useState(true)

  // testador de busca
  const [question, setQuestion] = useState('')
  const [testing, setTesting] = useState(false)
  const [result, setResult] = useState<RagAnswer | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const loadSources = useCallback(async () => {
    try {
      const res = await api.bot.listKbDocuments(token)
      setSources(res.sources)
    } catch {
      // Era silencioso "porque a lista simplesmente não atualiza" — mas quem acabou de
      // sincronizar fica olhando uma lista que nunca cresce, sem saber se a sincronização
      // falhou ou se a tela é que não carregou.
      toast.error('Não foi possível carregar as fontes da base de conhecimento.')
    } finally {
      setLoading(false)
    }
  }, [token, toast])

  useEffect(() => {
    loadSources()
  }, [loadSources])

  async function handleTest() {
    if (testing) return
    const q = question.trim()
    if (!q) return
    setTesting(true)
    setResult(null)
    setActionError(null)
    try {
      setResult(await api.bot.ragAnswer(token, q))
    } catch (e) {
      setActionError((e as Error).message || 'Falha ao testar a busca.')
    } finally {
      setTesting(false)
    }
  }

  return (
    <Section
      title="Documentos"
      description="Catálogos e perguntas frequentes longas. A cada mensagem o agente busca só os trechos relevantes."
      action={
        <Button variant="ghost" size="sm" onClick={loadSources}>
          Atualizar
        </Button>
      }
    >
      <div className="flex gap-2.5 rounded-[10px] border border-note-line bg-note-soft px-3 py-2.5 text-13 text-note-fg">
        <FileText size={16} strokeWidth={1.75} aria-hidden="true" className="mt-0.5 flex-none" />
        <span>
          Os documentos vêm de <code className="font-mono text-xs">conteudo/base/*.md</code>. Crie
          ou edite um arquivo lá e rode{' '}
          <code className="font-mono text-xs">scripts/sincronizar_conteudo.py</code>. O nome do
          arquivo é o nome do documento.
        </span>
      </div>

      {/* Indexar exige gerar o vetor de cada trecho. Sem chave, quem falha é a
          sincronização — avisamos aqui porque é nesta tela que se percebe a lista vazia. */}
      {!embeddingsReady && (
        <div className="flex flex-col gap-1 rounded-[10px] border border-line bg-subtle px-3 py-2.5">
          <span className="text-13 font-semibold text-ink">
            Falta a chave de embeddings para indexar documentos
          </span>
          <span className="text-13 text-muted">
            Indexar exige gerar um vetor para cada trecho do texto. Escolha o provedor{' '}
            <strong className="font-semibold text-ink">Gemini</strong> ou{' '}
            <strong className="font-semibold text-ink">OpenAI</strong> e informe a chave
            abaixo — a mesma chave serve para conversar e para indexar. Como alternativa,
            defina <code className="font-mono text-xs">EMBEDDINGS_API_KEY</code> no servidor.
            Documentos já indexados continuam funcionando normalmente.
          </span>
        </div>
      )}

      <List>
        <Row
          title="Usar busca nos documentos"
          description="Desligada, o agente usa só o conhecimento essencial."
          trailing={<Switch checked={usarBusca} onCheckedChange={onUsarBusca} label="Usar busca nos documentos" />}
        />
        {loading ? (
          <li className="px-4 py-3 text-13 text-faint">Carregando…</li>
        ) : sources.length === 0 ? (
          <li className="px-4 py-3 text-13 text-faint">
            Nenhum documento indexado. Rode a sincronização de conteúdo.
          </li>
        ) : (
          sources.map((s) => (
            <Row
              key={s.source ?? s.title ?? ''}
              icon={FileText}
              title={s.title || s.source || 'sem título'}
              description={
                <>
                  <span className="font-mono tabular-nums">{s.chunks}</span> trecho{s.chunks === 1 ? '' : 's'}
                  {s.created_at && (
                    <>
                      {' · enviado em '}
                      <span className="font-mono tabular-nums">{fmtDate(s.created_at)}</span>
                    </>
                  )}
                  {/* Dizer QUAL arquivo é a informação que falta para agir — é lá que se edita. */}
                  {s.source && <span className="hidden sm:inline"> · <span className="font-mono">base/{s.source}.md</span></span>}
                  {s.stale && ' · indexada com outro modelo, sincronize de novo para valer'}
                </>
              }
              trailing={
                // Indexada com outro modelo de embeddings: a busca compara vetores do modelo
                // ATUAL, então esta fonte existe mas é invisível. Sem o selo ela apareceria
                // na lista, contaria trechos e nunca seria encontrada.
                s.stale ? <Badge variant="warning">Reindexar</Badge> : <Badge variant="success">Indexado</Badge>
              }
            />
          ))
        )}
      </List>

      <Card className="flex flex-col gap-2">
        <Field label="Testar busca" htmlFor="kb-teste">
          <span className="-mt-1 text-xs text-faint">
            Pergunte como um cliente perguntaria — veja a resposta e de quais documentos ela veio.
          </span>
          <div className="flex gap-2">
            <Input
              id="kb-teste"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleTest()
              }}
              placeholder="Ex.: quanto custa a consulta?"
              className="min-w-0 flex-1"
            />
            <Button variant="secondary" onClick={handleTest} disabled={testing || !question.trim()}>
              {testing ? 'Testando…' : 'Testar'}
            </Button>
          </div>
        </Field>
        {actionError && <span className="text-xs font-medium text-danger-fg">{actionError}</span>}
        {result && (
          <div className="mt-1 flex flex-col gap-2 rounded-lg bg-subtle px-3 py-3">
            <p className="whitespace-pre-wrap text-13 text-ink">{result.answer}</p>
            {result.sources.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {result.sources.map((src, i) => (
                  <Badge key={i} className="bg-surface">
                    {src.title || src.source_url || 'trecho'}
                    <span className="font-mono text-faint">{Math.round(src.score * 100)}%</span>
                  </Badge>
                ))}
              </div>
            )}
          </div>
        )}
      </Card>
    </Section>
  )
}
