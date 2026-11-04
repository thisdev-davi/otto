'use client'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Download, ShieldCheck } from 'lucide-react'
import { useAuth } from '@/contexts/AuthContext'
import { useToast } from '@/contexts/ToastContext'
import { api, type AuditQuery } from '@/lib/api'
import type { AuditEntry } from '@/types/api'
import PageShell from '@/components/PageShell'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { EmptyState } from '@/components/ui/empty-state'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Hero, Section } from '@/components/ui/page'
import { Select } from '@/components/ui/select'
import { Spinner } from '@/components/ui/spinner'

const initialFilters = { from: '', to: '', actor: '', action: '', resource: '', resourceId: '', result: '' }
type Filters = typeof initialFilters

function query(filters: Filters): AuditQuery {
  return {
    date_from: filters.from ? new Date(filters.from).toISOString() : undefined,
    date_to: filters.to ? new Date(filters.to).toISOString() : undefined,
    actor_id: filters.actor.trim() || undefined,
    action: filters.action.trim() || undefined,
    resource_type: filters.resource.trim() || undefined,
    resource_id: filters.resourceId.trim() || undefined,
    result: filters.result || undefined,
  }
}

export default function AuditoriaPage() {
  const { token, isAdmin, me } = useAuth()
  const toast = useToast()
  const [draft, setDraft] = useState<Filters>(initialFilters)
  const [filters, setFilters] = useState<Filters>(initialFilters)
  const [entries, setEntries] = useState<AuditEntry[]>([])
  const [cursor, setCursor] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)
  const request = useRef(0)

  const load = useCallback(async (next?: string) => {
    if (!token || !isAdmin) return
    const id = ++request.current
    setLoading(true)
    setError(null)
    try {
      const response = await api.audit.list(token, { ...query(filters), limit: 50, cursor: next })
      if (id !== request.current) return
      setEntries((previous) => next ? [...previous, ...response.items] : response.items)
      setCursor(response.next_cursor)
    } catch (e) {
      if (id === request.current) setError((e as Error).message || 'Não foi possível carregar a auditoria.')
    } finally {
      if (id === request.current) setLoading(false)
    }
  }, [token, isAdmin, filters])

  useEffect(() => {
    void load()
    return () => { request.current += 1 }
  }, [load])

  async function exportCsv() {
    if (!token || exporting) return
    setExporting(true)
    try {
      const blob = await api.audit.export(token, query(filters))
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `otto-auditoria-${new Date().toISOString().slice(0, 10)}.csv`
      document.body.append(anchor)
      anchor.click()
      anchor.remove()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
      toast.success('Auditoria exportada')
    } catch (e) {
      toast.error((e as Error).message || 'Não foi possível exportar a auditoria.')
    } finally {
      setExporting(false)
    }
  }

  if (!me) return <div className="flex h-full items-center justify-center"><Spinner /></div>
  if (!isAdmin) return <PageShell><EmptyState icon={ShieldCheck} title="Acesso restrito" description="Somente administradores podem consultar a auditoria da conta." /></PageShell>

  function field(key: keyof Filters, label: string, type = 'text') {
    return <Field label={label} htmlFor={`audit-${key}`}><Input id={`audit-${key}`} type={type} value={draft[key]} onChange={(e) => setDraft((previous) => ({ ...previous, [key]: e.target.value }))} /></Field>
  }

  return (
    <PageShell largura="amplo" className="gap-8">
      <Hero icon={ShieldCheck} title="Atividades da conta" aside={<Button variant="secondary" onClick={exportCsv} disabled={exporting || loading}><Download size={16} />{exporting ? 'Exportando…' : 'Exportar CSV'}</Button>}>
        Registros dos últimos 180 dias. Consultas e exportações também ficam registradas.
      </Hero>
      <Section title="Filtros" description="Datas e horários no seu fuso local. Os resultados usam os filtros aplicados.">
        <Card>
          <form className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4" onSubmit={(e) => {
            e.preventDefault()
            if (draft.from && draft.to && new Date(draft.from) > new Date(draft.to)) {
              toast.error('A data inicial deve ser anterior à data final.')
              return
            }
            setEntries([])
            setCursor(null)
            setFilters({ ...draft })
          }}>
            {field('from', 'A partir de', 'datetime-local')}
            {field('to', 'Até', 'datetime-local')}
            {field('actor', 'ID do usuário')}
            {field('action', 'Ação')}
            {field('resource', 'Tipo de recurso')}
            {field('resourceId', 'ID do recurso')}
            <Field label="Resultado" htmlFor="audit-result"><Select id="audit-result" value={draft.result} onChange={(e) => setDraft((previous) => ({ ...previous, result: e.target.value }))}><option value="">Todos</option><option value="success">Sucesso</option><option value="failure">Falha</option><option value="accepted">Aceito</option><option value="denied">Negado</option><option value="pending">Pendente</option><option value="uncertain">Incerto</option></Select></Field>
            <div className="flex items-end gap-2"><Button type="submit">Aplicar filtros</Button><Button variant="ghost" type="button" onClick={() => { setDraft(initialFilters); setFilters({ ...initialFilters }); setEntries([]); setCursor(null) }}>Limpar</Button></div>
          </form>
        </Card>
      </Section>
      <Section title="Registros" description="O IP é apresentado como rede mascarada. Mensagens e credenciais não fazem parte desta trilha.">
        {error && <Card><EmptyState icon={ShieldCheck} title="Não foi possível carregar a auditoria" description={error} action={<Button variant="secondary" onClick={() => load(entries.length && cursor ? cursor : undefined)}>Tentar de novo</Button>} /></Card>}
        {!loading && !error && entries.length === 0 && <Card><EmptyState icon={ShieldCheck} title="Nenhum registro encontrado" description="Altere os filtros ou aguarde novas atividades na conta." /></Card>}
        {entries.length > 0 && <Card padding="none" className="overflow-hidden"><div className="overflow-x-auto"><table className="w-full text-left text-13"><thead className="border-b border-line bg-subtle text-xs text-muted"><tr>{['Quando', 'Ator', 'Ação e recurso', 'Resultado', 'Rede'].map((label) => <th key={label} scope="col" className="px-4 py-3 font-medium">{label}</th>)}</tr></thead><tbody className="divide-y divide-line">{entries.map((entry) => <tr key={entry.id} className="align-top"><td className="whitespace-nowrap px-4 py-3 font-mono text-xs text-muted"><time dateTime={entry.created_at}>{new Date(entry.created_at).toLocaleString('pt-BR')}</time></td><td className="px-4 py-3 text-ink-2">{({ human: 'Pessoa', user: 'Pessoa', bot: 'Bot', system: 'Sistema', anonymous: 'Não identificado' } as Record<string, string>)[entry.actor_type] || entry.actor_type}{entry.actor_user_id && <p className="mt-1 font-mono text-2xs text-faint">{entry.actor_user_id}</p>}</td><td className="min-w-[210px] px-4 py-3"><p className="font-medium text-ink">{entry.action}</p><p className="mt-1 break-all font-mono text-xs text-muted">{[entry.resource_type, entry.resource_id].filter(Boolean).join(' · ') || '—'}</p><details className="mt-2 text-xs text-muted"><summary className="cursor-pointer">Detalhes</summary><dl className="mt-2 grid gap-1"><dt>Motivo</dt><dd>{entry.reason || '—'}</dd><dt>Correlação</dt><dd className="break-all font-mono">{entry.correlation_id || '—'}</dd></dl>{Object.keys(entry.metadata || {}).length > 0 && <pre className="mt-2 max-w-[360px] whitespace-pre-wrap break-all text-2xs">{JSON.stringify(entry.metadata, null, 2)}</pre>}</details></td><td className="px-4 py-3"><Badge variant={entry.result === 'success' ? 'success' : entry.result === 'failure' || entry.result === 'denied' ? 'danger' : 'neutral'}>{({ success: 'Sucesso', failure: 'Falha', accepted: 'Aceito', denied: 'Negado', pending: 'Pendente', uncertain: 'Incerto' } as Record<string, string>)[entry.result] || entry.result}</Badge></td><td className="whitespace-nowrap px-4 py-3 font-mono text-xs text-muted">{entry.ip_network || '—'}</td></tr>)}</tbody></table></div></Card>}
        {loading && <div role="status" className="flex justify-center gap-2 py-4 text-13 text-muted"><Spinner />Carregando…</div>}
        {cursor && !loading && !error && <div className="flex justify-center"><Button variant="secondary" onClick={() => load(cursor)}>Carregar mais</Button></div>}
      </Section>
    </PageShell>
  )
}
