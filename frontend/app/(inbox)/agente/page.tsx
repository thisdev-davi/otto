'use client'
import { useState, useEffect, useCallback } from 'react'
import { AlertCircle, FileText, Power, Sparkles, Workflow, type LucideIcon } from 'lucide-react'
import Link from 'next/link'
import { useAuth } from '@/contexts/AuthContext'
import { api } from '@/lib/api'
import { useToast } from '@/contexts/ToastContext'
import type { BotModo, BotProvider, BotSettings } from '@/types/api'
import { cn } from '@/lib/utils'
import PageShell from '@/components/PageShell'
import { Button, buttonVariants } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Card } from '@/components/ui/card'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { Switch } from '@/components/ui/switch'
import { EmptyState } from '@/components/ui/empty-state'
import { Hero, IconTile, List, Section, type Tom } from '@/components/ui/page'
import KnowledgeBase from '@/components/KnowledgeBase'

const PROVIDERS: { value: BotProvider; label: string; hint: string }[] = [
  { value: 'stub', label: 'Stub (teste, sem custo)', hint: 'Respostas fixas — para testar o fluxo sem gastar API.' },
  { value: 'anthropic', label: 'Anthropic (Claude)', hint: 'Precisa de uma chave da Anthropic.' },
  { value: 'openai', label: 'OpenAI', hint: 'Precisa de uma chave da OpenAI.' },
  { value: 'groq', label: 'Groq (rápido, free tier)', hint: 'Grátis para começar — bom custo-benefício.' },
  { value: 'gemini', label: 'Google Gemini', hint: 'Endpoint compatível OpenAI da Google.' },
  { value: 'deepseek', label: 'DeepSeek', hint: 'Precisa de uma chave da DeepSeek.' },
  { value: 'xai', label: 'xAI (Grok)', hint: 'Precisa de uma chave da xAI.' },
]

type Escolha = 'desligado' | BotModo

/**
 * Um controle de TRÊS estados, não dois toggles.
 *
 * Quem atende sai de duas colunas (`ai_enabled` + `modo`), mas apresentá-las separadas
 * criaria um quarto estado sem sentido — "desligado, em modo fluxo". E é aqui que mora o
 * freio de emergência: trocar Agente de IA por Roteiro é um clique, o custo de LLM cai a
 * zero e o atendimento continua.
 */
const MODOS: { valor: Escolha; label: string; hint: string; icon: LucideIcon; tom: Tom }[] = [
  { valor: 'desligado', label: 'Desligado', hint: 'Ninguém responde sozinho. Tudo espera um atendente.', icon: Power, tom: 'neutral' },
  { valor: 'fluxo', label: 'Roteiro', hint: 'Menus e perguntas fixas. Previsível e sem custo de IA.', icon: Workflow, tom: 'accent' },
  { valor: 'agente', label: 'Agente de IA', hint: 'Conversa livre com persona e conhecimento. Precisa de chave.', icon: Sparkles, tom: 'ai' },
]

const escolhaDe = (s: BotSettings): Escolha => (s.ai_enabled ? s.modo : 'desligado')

function ModoDeAtendimento({
  form,
  patch,
}: {
  form: BotSettings
  patch: (p: Partial<BotSettings>) => void
}) {
  const escolhido = escolhaDe(form)

  function escolher(valor: Escolha) {
    if (valor === 'desligado') patch({ ai_enabled: false })
    else patch({ ai_enabled: true, modo: valor })
  }

  return (
    <Section title="Quem atende" description="Vale para toda mensagem nova que ninguém capturou.">
      {/* Rádio nativo por baixo: setas do teclado e leitor de tela de graça. */}
      <div role="radiogroup" aria-label="Quem atende" className="grid gap-2.5 sm:grid-cols-3">
        {MODOS.map((m) => {
          const ativo = escolhido === m.valor
          return (
            <label
              key={m.valor}
              className={cn(
                'relative flex cursor-pointer flex-col gap-2.5 rounded-xl border bg-surface p-4 transition-[border-color,box-shadow] duration-[120ms] motion-reduce:transition-none',
                ativo
                  ? 'border-accent ring-[3px] ring-accent-ring'
                  : 'border-line hover:border-line-strong has-[:focus-visible]:ring-[3px] has-[:focus-visible]:ring-accent-ring'
              )}
            >
              <input
                type="radio"
                name="quem-atende"
                className="sr-only"
                checked={ativo}
                onChange={() => escolher(m.valor)}
              />
              <IconTile icon={m.icon} tom={m.tom} />
              <span
                aria-hidden="true"
                className={cn(
                  'absolute right-4 top-4 h-[18px] w-[18px] rounded-full',
                  ativo ? 'border-[5px] border-accent' : 'border-[1.5px] border-line-strong'
                )}
              />
              <span className="text-sm font-semibold text-ink">{m.label}</span>
              <span className="text-13 text-muted">{m.hint}</span>
            </label>
          )
        })}
      </div>
      {form.ai_enabled && form.modo === 'fluxo' && (
        <p className="px-1 text-13 text-muted">
          {form.fluxo_publicado ? (
            <>
              O roteiro publicado está em{' '}
              <Link href="/fluxo" className="text-accent-fg underline underline-offset-2">
                Fluxo de atendimento
              </Link>
              . Persona, conhecimento e provedor abaixo ficam sem efeito neste modo.
            </>
          ) : (
            <span className="text-danger-fg">
              Ainda não há roteiro publicado nesta conta — sem ele o bot fica em silêncio.
              Publique com scripts/sincronizar_conteudo.py antes de salvar.
            </span>
          )}
        </p>
      )}
    </Section>
  )
}

const DIAS: { iso: string; nome: string }[] = [
  { iso: '1', nome: 'seg' },
  { iso: '2', nome: 'ter' },
  { iso: '3', nome: 'qua' },
  { iso: '4', nome: 'qui' },
  { iso: '5', nome: 'sex' },
  { iso: '6', nome: 'sáb' },
  { iso: '7', nome: 'dom' },
]

const PADRAO_ABRE = '09:00'
const PADRAO_FECHA = '18:00'

const campoHora = 'w-[104px] px-2 font-mono tabular-nums sm:w-[112px]'

/**
 * Expediente do negócio: quando existe gente para responder.
 *
 * Sem nada preenchido o negócio é tratado como aberto 24/7: silêncio configura sempre
 * aberto, nunca portas fechadas para sempre.
 */
function Expediente({
  form,
  patch,
}: {
  form: BotSettings
  patch: (p: Partial<BotSettings>) => void
}) {
  const dias = form.horario?.dias ?? {}

  function gravar(novos: Record<string, [string, string][]>) {
    patch({
      horario: {
        ...form.horario,
        fuso: form.horario?.fuso || 'America/Sao_Paulo',
        dias: novos,
      },
    })
  }

  function alterar(iso: string, intervalo: [string, string] | null) {
    gravar({ ...dias, [iso]: intervalo ? [intervalo] : [] })
  }

  // Copia para os dias ABERTOS: "fechado" é decisão de cada dia, e abrir o domingo
  // sem querer seria pior que ter de copiar de novo.
  const segunda = dias['1']?.[0]
  function copiarSegunda() {
    if (!segunda) return
    gravar(Object.fromEntries(Object.entries(dias).map(([iso, v]) => [iso, v?.length ? [segunda] : v])))
  }

  const configurado = Object.values(dias).some((v) => Array.isArray(v) && v.length > 0)

  return (
    <Section
      title="Horário de atendimento"
      description="Quando existe gente para responder. Fora dele, o bot avisa."
      action={
        <Button
          variant="ghost"
          size="sm"
          onClick={copiarSegunda}
          disabled={!segunda}
          aria-label="Copiar segunda para todos os dias abertos"
        >
          <span className="sm:hidden">Copiar segunda</span>
          <span className="hidden sm:inline">Copiar segunda para todos</span>
        </Button>
      }
    >
      <List>
        {DIAS.map(({ iso, nome }) => {
          const intervalo = dias[iso]?.[0]
          const aberto = !!intervalo
          return (
            <li key={iso} className="flex min-h-[52px] items-center gap-2 px-4 py-2 sm:gap-3">
              <span className="w-8 flex-none text-13 font-medium text-ink-2 sm:w-9">{nome}</span>
              <Switch
                checked={aberto}
                onCheckedChange={(v) => alterar(iso, v ? [PADRAO_ABRE, PADRAO_FECHA] : null)}
                label={`Aberto ${nome}`}
              />
              {aberto ? (
                <div className="flex items-center gap-1.5 text-13 text-faint sm:gap-2">
                  <Input
                    type="time"
                    value={intervalo[0]}
                    onChange={(e) => alterar(iso, [e.target.value, intervalo[1]])}
                    aria-label={`Abre ${nome}`}
                    className={campoHora}
                  />
                  até
                  <Input
                    type="time"
                    value={intervalo[1]}
                    onChange={(e) => alterar(iso, [intervalo[0], e.target.value])}
                    aria-label={`Fecha ${nome}`}
                    className={campoHora}
                  />
                </div>
              ) : (
                <span className="text-13 text-faint">Fechado</span>
              )}
            </li>
          )
        })}
      </List>
      {!configurado && (
        <p className="px-1 text-13 text-muted">
          Nenhum dia marcado: o negócio é tratado como <strong className="font-semibold">sempre aberto</strong>.
        </p>
      )}
    </Section>
  )
}

const HERO: Record<Escolha, { icon: LucideIcon; titulo: string; texto: string }> = {
  desligado: { icon: Power, titulo: 'Bot desligado', texto: 'Toda mensagem nova espera um atendente.' },
  fluxo: {
    icon: Workflow,
    titulo: 'Atendendo pelo roteiro',
    texto: 'Mensagens novas recebem o roteiro publicado, sem custo de IA.',
  },
  agente: { icon: Sparkles, titulo: 'Atendendo com IA', texto: 'O agente responde e encaminha o que não souber.' },
}

export default function AgentePage() {
  const { token } = useAuth()
  const toast = useToast()
  const [form, setForm] = useState<BotSettings | null>(null)
  // O que está gravado no servidor. O Hero descreve ISTO, não o rascunho ("Atendendo com
  // IA" antes de salvar seria mentira), e a barra de salvar nasce da diferença.
  const [salvo, setSalvo] = useState<BotSettings | null>(null)
  const [apiKeyInput, setApiKeyInput] = useState('') // vazio = mantém a atual
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [ping, setPing] = useState<{ ok: boolean; detail: string } | null>(null)
  const [pinging, setPinging] = useState(false)
  const [erroCarga, setErroCarga] = useState<string | null>(null)

  const load = useCallback(() => {
    if (!token) return
    setLoading(true)
    setErroCarga(null)
    api.bot.getSettings(token)
      .then((s) => {
        setForm(s)
        setSalvo(s)
      })
      .catch(() =>
        // Sem isto a tela ficava presa em "Carregando…" PARA SEMPRE: `loading` vira
        // false e `form` continua null, e o guard de render exige os dois.
        setErroCarga('Não foi possível carregar as configurações do agente.')
      )
      .finally(() => setLoading(false))
  }, [token])

  useEffect(() => { load() }, [load])

  function patch(p: Partial<BotSettings>) {
    setForm((f) => (f ? { ...f, ...p } : f))
  }

  function descartar() {
    setForm(salvo)
    setApiKeyInput('')
  }

  async function handleSave() {
    if (!token || !form || saving) return
    setSaving(true)
    try {
      const updated = await api.bot.updateSettings(token, {
        ai_enabled: form.ai_enabled,
        persona: form.persona,
        knowledge: form.knowledge,
        provider: form.provider,
        model: form.model,
        transfer_message: form.transfer_message,
        confirmacao_message: form.confirmacao_message,
        use_rag: form.use_rag,
        modo: form.modo,
        horario: form.horario,
        // só envia a chave se o campo foi preenchido; senão mantém a atual
        api_key: apiKeyInput.trim() ? apiKeyInput.trim() : null,
      })
      setForm(updated)
      setSalvo(updated)
      setApiKeyInput('')
      toast.success('Configurações salvas')
    } catch (e) {
      toast.error((e as Error).message || 'Não foi possível salvar as configurações.')
    } finally {
      setSaving(false)
    }
  }

  async function handlePing() {
    if (!token || pinging) return
    setPinging(true)
    setPing(null)
    try {
      // salva antes de testar, para o ping usar a config atual
      await handleSave()
      const res = await api.bot.ping(token)
      setPing(res)
    } catch {
      setPing({ ok: false, detail: 'Não foi possível testar a conexão.' })
    } finally {
      setPinging(false)
    }
  }

  if (erroCarga) {
    return (
      <div className="h-full flex items-center justify-center px-6">
        <EmptyState
          icon={AlertCircle}
          title={erroCarga}
          action={
            <Button size="sm" variant="secondary" onClick={load}>
              Tentar de novo
            </Button>
          }
        />
      </div>
    )
  }

  if (loading || !form || !salvo) {
    return <div className="h-full flex items-center justify-center text-13 text-faint">Carregando…</div>
  }

  const modoSalvo = escolhaDe(salvo)
  const hero = HERO[modoSalvo]
  const semRoteiro = modoSalvo === 'fluxo' && !salvo.fluxo_publicado
  const sujo = !!apiKeyInput.trim() || JSON.stringify(form) !== JSON.stringify(salvo)
  const somenteLeitura = form.conteudo_gerenciado ? 'bg-subtle text-muted' : undefined

  return (
    <PageShell largura="grade" className="gap-8">
      <Hero
        icon={hero.icon}
        title={hero.titulo}
        aside={
          <>
            <Badge className="rounded-full px-2">
              <span
                aria-hidden="true"
                className={cn('h-1.5 w-1.5 rounded-full', modoSalvo === 'desligado' ? 'bg-faint' : 'bg-success')}
              />
              {modoSalvo === 'desligado' ? 'Desligado' : 'Ligado'}
            </Badge>
            {modoSalvo === 'fluxo' && (
              <Link href="/fluxo" className={buttonVariants({ variant: 'ghost', size: 'sm' })}>
                Ver roteiro
              </Link>
            )}
          </>
        }
      >
        {semRoteiro ? (
          <span className="text-danger-fg">
            Ainda não há roteiro publicado nesta conta — sem ele o bot fica em silêncio.
          </span>
        ) : (
          hero.texto
        )}
      </Hero>

      <div className="flex min-w-0 flex-col gap-8">
        {/* Bot falso no ar: combinação que já atendeu cliente real respondendo frases
            fixas. O stub é a 1ª opção e o padrão, e _stub_turn nem recebe persona nem
            conhecimento — quem preenche as duas caixas e liga o modo não tem como
            desconfiar. Avisa, mas não bloqueia: testar o fluxo sem gastar API é
            justamente para o que o stub serve. */}
        {form.ai_enabled && form.modo === 'agente' && form.provider === 'stub' && (
          <div className="flex flex-col gap-1 rounded-xl border border-danger-line bg-danger-soft px-4 py-3.5 text-danger-fg">
            <span className="text-sm font-semibold">
              O atendimento automático está ligado com o provedor “Stub”
            </span>
            <span className="text-13">
              Seus clientes vão receber <strong>frases fixas de teste</strong>, não respostas
              de IA. A persona e o conhecimento configurados abaixo são ignorados nesse modo.
              Escolha um provedor real em “Provedor de IA” para valer.
            </span>
          </div>
        )}

        <div id="quem" className="scroll-mt-6">
          <ModoDeAtendimento form={form} patch={patch} />
        </div>

        {/* expediente — o roteiro ramifica nele antes de saudar */}
        <div id="horario" className="scroll-mt-6">
          <Expediente form={form} patch={patch} />
        </div>

        <div id="persona" className="scroll-mt-6">
          <Section title="Persona e conhecimento" description="Como o agente fala e o que ele pode afirmar.">
            <Card className="flex flex-col gap-4">
              {form.conteudo_gerenciado && (
                // Campo desabilitado sem explicação vira chamado de suporte. A frase diz o
                // arquivo e o comando, que é tudo o que falta para editar.
                <div className="flex gap-2.5 rounded-[10px] border border-note-line bg-note-soft px-3 py-2.5 text-13 text-note-fg">
                  <FileText size={16} strokeWidth={1.75} aria-hidden="true" className="mt-0.5 flex-none" />
                  <span>
                    Estes textos vêm de arquivos versionados:{' '}
                    <code className="font-mono text-xs">conteudo/persona.md</code> e{' '}
                    <code className="font-mono text-xs">conteudo/conhecimento.md</code>. Edite os
                    arquivos e rode <code className="font-mono text-xs">scripts/sincronizar_conteudo.py</code>.
                  </span>
                </div>
              )}
              <Field label="Persona" hint="Como atender: tom, postura e limites" htmlFor="ag-persona">
                <Textarea
                  id="ag-persona"
                  value={form.persona}
                  onChange={(e) => patch({ persona: e.target.value })}
                  readOnly={form.conteudo_gerenciado}
                  rows={form.conteudo_gerenciado ? 10 : 4}
                  placeholder="Ex.: Você é a Mel, assistente da Clínica Veterinária PetVida. Fale com carinho, seja objetiva…"
                  className={somenteLeitura}
                />
              </Field>
              <Field label="Conhecimento essencial" hint="Fatos curtos, sempre enviados ao modelo" htmlFor="ag-conhecimento">
                <Textarea
                  id="ag-conhecimento"
                  value={form.knowledge}
                  onChange={(e) => patch({ knowledge: e.target.value })}
                  readOnly={form.conteudo_gerenciado}
                  rows={8}
                  placeholder="Cole aqui os fatos que a IA pode usar (sobre o negócio, canais de contato e o que coletar). A IA só afirma o que estiver aqui — não inventa."
                  className={somenteLeitura}
                />
              </Field>
            </Card>
          </Section>
        </div>

        {/* base de conhecimento vetorial (RAG) */}
        <div id="docs" className="scroll-mt-6">
          {token && (
            <KnowledgeBase
              token={token}
              usarBusca={form.use_rag}
              onUsarBusca={(v) => patch({ use_rag: v })}
              embeddingsReady={form.embeddings_ready}
            />
          )}
        </div>

        <div id="provedor" className="scroll-mt-6">
          <Section title="Provedor de IA" description="Só vale no modo Agente de IA.">
            <Card className="flex flex-col gap-4">
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Provedor" htmlFor="ag-provedor">
                  <Select
                    id="ag-provedor"
                    value={form.provider}
                    onChange={(e) => patch({ provider: e.target.value as BotProvider })}
                  >
                    {PROVIDERS.map((p) => (
                      <option key={p.value} value={p.value}>{p.label}</option>
                    ))}
                  </Select>
                  <span className="text-xs text-faint">
                    {PROVIDERS.find((p) => p.value === form.provider)?.hint}
                  </span>
                </Field>
                {form.provider !== 'stub' && (
                  <Field label="Modelo" hint="Vazio usa o padrão" htmlFor="ag-modelo">
                    <Input
                      id="ag-modelo"
                      value={form.model}
                      onChange={(e) => patch({ model: e.target.value })}
                      placeholder="padrão do provedor"
                      className="font-mono"
                    />
                  </Field>
                )}
              </div>

              {form.provider !== 'stub' && (
                <Field label="Chave de API" hint="Cifrada no banco" htmlFor="ag-chave">
                  <div className="flex gap-2">
                    <Input
                      id="ag-chave"
                      type="password"
                      value={apiKeyInput}
                      onChange={(e) => setApiKeyInput(e.target.value)}
                      placeholder={form.api_key_masked ? `${form.api_key_masked} (mantém a atual)` : 'cole a chave aqui'}
                      className="min-w-0 flex-1 font-mono"
                      autoComplete="off"
                    />
                    <Button variant="secondary" onClick={handlePing} disabled={pinging}>
                      {pinging ? 'Testando…' : 'Testar conexão'}
                    </Button>
                  </div>
                  {ping && (
                    <span role="status" className={cn('text-xs font-medium', ping.ok ? 'text-success-fg' : 'text-danger-fg')}>
                      {ping.detail}
                    </span>
                  )}
                </Field>
              )}
            </Card>
          </Section>
        </div>

        <div id="handoff" className="scroll-mt-6">
          <Section title="Encaminhamento" description="O que o cliente lê quando a conversa vai para a equipe.">
            <Card>
              <Field label="Mensagem ao encaminhar" htmlFor="ag-encaminhar">
                <Input
                  id="ag-encaminhar"
                  value={form.transfer_message}
                  onChange={(e) => patch({ transfer_message: e.target.value })}
                />
              </Field>
            </Card>
          </Section>
        </div>

        <Section title="Confirmação de reserva" description="Texto revisado pela equipe antes de avisar o cliente no WhatsApp.">
          <Card>
            <Field label="Mensagem de confirmação" htmlFor="ag-confirmacao" hint="Use {nome}, {data} e {valor}">
              <Textarea id="ag-confirmacao" value={form.confirmacao_message} onChange={(e) => patch({ confirmacao_message: e.target.value })} rows={4} />
            </Field>
          </Card>
        </Section>

        {sujo && (
          <div
            role="status"
            className="sticky bottom-4 z-[5] mx-auto flex w-full max-w-[640px] items-center gap-2.5 rounded-[14px] border border-line bg-surface-raised py-2 pl-4 pr-2 shadow-pop"
          >
            <span className="flex min-w-0 flex-1 items-center gap-2 text-13 text-muted">
              <span aria-hidden="true" className="h-1.5 w-1.5 flex-none rounded-full bg-warning-fg" />
              <span className="sm:hidden">Não salvo</span>
              <span className="hidden sm:inline">Alterações não salvas</span>
            </span>
            <Button variant="ghost" onClick={descartar} disabled={saving}>
              Descartar
            </Button>
            <Button onClick={handleSave} disabled={saving}>
              {saving ? (
                'Salvando…'
              ) : (
                <>
                  <span className="sm:hidden">Salvar</span>
                  <span className="hidden sm:inline">Salvar alterações</span>
                </>
              )}
            </Button>
          </div>
        )}
      </div>
    </PageShell>
  )
}
