'use client'
import { useState, useEffect, useCallback } from 'react'
import Link from 'next/link'
import {
  AlertCircle,
  AlertTriangle,
  ArrowLeftRight,
  ArrowRight,
  Bot,
  CheckCircle2,
  Clock,
  MessageCircle,
} from 'lucide-react'
import { useAuth } from '@/contexts/AuthContext'
import { api } from '@/lib/api'
import type { ReportOverview, ReportVolume, ReportAgents, ReportBot } from '@/types/api'
import PageShell from '@/components/PageShell'
import { Button, buttonVariants } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { EmptyState } from '@/components/ui/empty-state'
import { Segmented } from '@/components/ui/segmented'
import { Avatar } from '@/components/ui/avatar'
import { Hero, List, Row, Section, Stat } from '@/components/ui/page'
import VolumeChart from '@/components/reports/VolumeChart'

function fmtDuration(seconds: number | null): string {
  if (seconds == null) return '—'
  if (seconds < 60) return `${seconds}s`
  const m = Math.round(seconds / 60)
  if (m < 60) return `${m}min`
  const h = Math.floor(m / 60)
  return `${h}h${m % 60 ? ` ${m % 60}min` : ''}`
}

function isoDaysAgo(n: number): string {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return d.toISOString().slice(0, 10)
}

const ddmm = (iso: string) => `${iso.slice(8, 10)}/${iso.slice(5, 7)}`

const RANGES = [
  { label: '7 dias', value: 6 },
  { label: '30 dias', value: 29 },
] as const

const NUMERO = 'font-mono text-15 font-medium tabular-nums text-ink'

export default function RelatoriosPage() {
  const { token } = useAuth()
  const [rangeDays, setRangeDays] = useState<number>(29)
  const [overview, setOverview] = useState<ReportOverview | null>(null)
  const [volume, setVolume] = useState<ReportVolume | null>(null)
  const [agents, setAgents] = useState<ReportAgents | null>(null)
  const [bot, setBot] = useState<ReportBot | null>(null)
  const [loading, setLoading] = useState(true)
  const [erro, setErro] = useState<string | null>(null)

  const load = useCallback(() => {
    if (!token) return
    setLoading(true)
    setErro(null)
    const from = isoDaysAgo(rangeDays)
    Promise.all([
      api.reports.overview(token, from),
      api.reports.volume(token, from),
      api.reports.agents(token, from),
      api.reports.bot(token, from),
    ])
      .then(([o, v, a, b]) => {
        setOverview(o)
        setVolume(v)
        setAgents(a)
        setBot(b)
      })
      .catch(() =>
        // Promise.all de 4 chamadas: uma falha derruba as quatro e a página fica só
        // com o seletor de período. O erro precisa ficar NA página.
        setErro('Não foi possível carregar os relatórios.')
      )
      .finally(() => setLoading(false))
  }, [token, rangeDays])

  useEffect(() => { load() }, [load])

  // O mesmo `from` que vai para a API, um dia por coluna até hoje.
  const dias = Array.from({ length: rangeDays + 1 }, (_, k) => isoDaysAgo(rangeDays - k))
  const pronto = !loading && !erro && overview && volume && agents && bot

  return (
    <PageShell largura="amplo" className="gap-8">
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <span className="font-mono text-13 tabular-nums text-muted">
            {ddmm(dias[0])} – {ddmm(dias[dias.length - 1])}
          </span>
          <Segmented label="Período" value={rangeDays} onChange={setRangeDays} options={RANGES} />
        </div>

        {pronto && (
          <Hero
            icon={CheckCircle2}
            title={`${overview.resolved_count} ${overview.resolved_count === 1 ? 'conversa resolvida' : 'conversas resolvidas'} em ${rangeDays + 1} dias`}
            aside={
              <Link href="/inbox" className={buttonVariants({ variant: 'ghost', size: 'sm' })}>
                Abrir a fila
                <ArrowRight size={16} aria-hidden="true" />
              </Link>
            }
          >
            {overview.avg_first_response_seconds == null ? (
              'Nenhuma primeira resposta no período.'
            ) : (
              <>
                Primeira resposta em{' '}
                <span className="font-mono tabular-nums">{fmtDuration(overview.avg_first_response_seconds)}</span>, em média.
              </>
            )}
          </Hero>
        )}
      </div>

      {loading && <p className="text-13 text-faint">Carregando…</p>}

      {!loading && erro && (
        <EmptyState
          icon={AlertCircle}
          title={erro}
          action={
            <Button size="sm" variant="secondary" onClick={load}>
              Tentar de novo
            </Button>
          }
        />
      )}

      {pronto && (
        <>
          <Section title="Agora">
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <Stat label="Abertas" value={String(overview.open_count)} hint="conversas em andamento" icon={MessageCircle} />
              <Stat
                label="Esperando atendente"
                value={String(overview.unclaimed_count)}
                hint="ninguém capturou ainda"
                icon={AlertCircle}
                tom="warning"
              />
              <Stat
                label="1ª resposta"
                value={fmtDuration(overview.avg_first_response_seconds)}
                hint="média no período"
                icon={Clock}
              />
              <Stat
                label="Resolução"
                value={fmtDuration(overview.avg_resolution_seconds)}
                hint="média no período"
                icon={CheckCircle2}
              />
            </div>
          </Section>

          <Section
            title="Volume de mensagens"
            description="Por dia, recebidas e enviadas"
            action={
              <div className="flex gap-4 text-xs text-muted">
                <span className="flex items-center gap-1.5">
                  <i className="h-2.5 w-2.5 rounded-[3px] bg-chart-2" aria-hidden="true" />
                  Recebidas
                </span>
                <span className="flex items-center gap-1.5">
                  <i className="h-2.5 w-2.5 rounded-[3px] bg-chart-1" aria-hidden="true" />
                  Enviadas
                </span>
              </div>
            }
          >
            <Card className="px-5 pb-3 pt-5">
              <VolumeChart points={volume.points} days={dias} />
            </Card>
          </Section>

          <div className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)] lg:gap-6">
            <Section title="Agente de IA">
              <Card padding="none" className="overflow-hidden">
                <ul className="divide-y divide-line">
                  <Row icon={Bot} tom="ai" title="Conversas triadas" trailing={<span className={NUMERO}>{bot.triaged}</span>} />
                  <Row icon={ArrowLeftRight} title="Encaminhadas à equipe" trailing={<span className={NUMERO}>{bot.handoffs}</span>} />
                  <Row icon={CheckCircle2} tom="success" title="Resolvidas pelo bot" trailing={<span className={NUMERO}>{bot.resolved_by_bot}</span>} />
                  <Row icon={Clock} title="Encerradas por inatividade" description="Não entram como resolução pelo bot." trailing={<span className={NUMERO}>{bot.closed_by_inactivity}</span>} />
                  <Row icon={AlertTriangle} tom="danger" title="Falhas de IA" trailing={<span className={NUMERO}>{bot.failures}</span>} />
                </ul>
                {bot.triaged > 0 && <Fatia resolvidas={bot.resolved_by_bot} triadas={bot.triaged} />}
              </Card>
            </Section>

            <Section title="Equipe" description="Ordenada por conversas resolvidas">
              {agents.agents.length === 0 ? (
                <Card>
                  <p className="text-13 text-muted">Ninguém da equipe atendeu no período.</p>
                </Card>
              ) : (
                <Equipe agents={agents.agents} />
              )}
            </Section>
          </div>
        </>
      )}
    </PageShell>
  )
}

function Fatia({ resolvidas, triadas }: { resolvidas: number; triadas: number }) {
  const pct = Math.round((resolvidas / triadas) * 100)
  return (
    <div className="flex flex-col gap-2 border-t border-line px-4 py-3.5">
      <div className="flex justify-between text-13">
        <span className="text-muted">Resolvidas sem gente</span>
        <span className="font-mono font-medium tabular-nums text-ink">{pct}%</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-subtle">
        <div className="h-full bg-success-fg" style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

function Equipe({ agents }: { agents: ReportAgents['agents'] }) {
  const ordem = [...agents].sort((a, b) => b.resolved - a.resolved)
  const topo = ordem[0].resolved
  return (
    <List>
      {ordem.map((a, i) => (
        <Row
          key={a.user_id}
          leading={
            <>
              <span className="w-5 flex-none text-center font-mono text-xs tabular-nums text-faint">{i + 1}</span>
              <Avatar name={a.name} tone="categorico" size="sm" className="h-8 w-8" />
            </>
          }
          title={a.name}
          description={
            <>
              <span className="font-mono tabular-nums">{a.claimed}</span> capturadas ·{' '}
              <span className="font-mono tabular-nums">{a.messages_sent.toLocaleString('pt-BR')}</span> mensagens
            </>
          }
          trailing={
            <div className="flex w-[72px] flex-col items-end gap-1 sm:w-[120px]">
              <span className={NUMERO}>{a.resolved}</span>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-subtle">
                <div className="h-full bg-chart-1" style={{ width: topo ? `${(a.resolved / topo) * 100}%` : '0%' }} />
              </div>
            </div>
          }
        />
      ))}
    </List>
  )
}
