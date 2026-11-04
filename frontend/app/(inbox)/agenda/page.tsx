'use client'
import { useCallback, useEffect, useMemo, useState } from 'react'
import Link from 'next/link'
import { CalendarDays, ChevronLeft, ChevronRight } from 'lucide-react'
import { useAuth } from '@/contexts/AuthContext'
import { useToast } from '@/contexts/ToastContext'
import { api } from '@/lib/api'
import {
  CLASSE_ESTADO,
  DIAS_ISO,
  NOMES_MES,
  ROTULO_ESTADO,
  chaveData,
  diaSemanaIso,
  diasNoMes,
  estadoDoDia,
  formatarData,
  formatarPreco,
  hojeChave,
  paraCampo,
  paraCentavos,
  precoCurto,
  primeiroDiaSemana,
  tempoRestante,
  type EstadoDia,
} from '@/lib/salao'
import type { AgendaData, Reserva, ReservaDetalhe } from '@/types/api'
import { Hero } from '@/components/ui/page'
import { Badge } from '@/components/ui/badge'
import PageShell from '@/components/PageShell'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogTitle,
} from '@/components/ui/dialog'
import { EmptyState } from '@/components/ui/empty-state'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Spinner } from '@/components/ui/spinner'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'

// A ordem da legenda espelha a leitura da grade: o que se procura primeiro é o que
// ainda dá para vender.
const LEGENDA: EstadoDia[] = [
  'livre',
  'pre_reservada',
  'confirmada',
  'sem_preco',
  'bloqueada',
  'sem_cadastro',
]

export default function AgendaPage() {
  const { token, isAdmin } = useAuth()
  const toast = useToast()

  const agora = new Date()
  const [ano, setAno] = useState(agora.getFullYear())
  const [mes, setMes] = useState(agora.getMonth())

  const [datas, setDatas] = useState<AgendaData[]>([])
  const [reservas, setReservas] = useState<Reserva[]>([])
  const [carregando, setCarregando] = useState(true)
  const [erroPainel, setErroPainel] = useState<string | null>(null)

  const [diaAberto, setDiaAberto] = useState<string | null>(null)
  const [campoPreco, setCampoPreco] = useState('')
  const [campoBloqueada, setCampoBloqueada] = useState(false)
  const [campoObs, setCampoObs] = useState('')
  const [salvando, setSalvando] = useState(false)
  const [erroDia, setErroDia] = useState<string | null>(null)

  const [loteAberto, setLoteAberto] = useState(false)
  const [loteDe, setLoteDe] = useState('')
  const [loteAte, setLoteAte] = useState('')
  const [loteDias, setLoteDias] = useState<number[]>([6])
  const [lotePreco, setLotePreco] = useState('')
  const [loteSalvando, setLoteSalvando] = useState(false)
  const [erroLote, setErroLote] = useState<string | null>(null)

  const [cancelando, setCancelando] = useState<Reserva | null>(null)
  const [processandoReserva, setProcessandoReserva] = useState(false)

  const [resumo, setResumo] = useState<ReservaDetalhe | null>(null)
  const [resumoCarregando, setResumoCarregando] = useState(false)
  const [erroResumo, setErroResumo] = useState<string | null>(null)

  // Confirmar dispara mensagem no WhatsApp de um cliente REAL, e não dá para desfazer.
  // Por isso o texto é mostrado e fica editável antes de sair — quem confirma lê o que o
  // cliente vai receber. `avisoTexto` guarda a edição daquele envio; o modelo em si mora
  // em Agente e não é alterado aqui.
  const [confirmando, setConfirmando] = useState<Reserva | null>(null)
  const [avisoTexto, setAvisoTexto] = useState('')
  const [podeAvisar, setPodeAvisar] = useState(false)
  const [avisoCarregando, setAvisoCarregando] = useState(false)

  // Guarda a DATA, não um booleano: o diálogo do dia fecha ao abrir este (nunca dois
  // Dialog modais ao mesmo tempo), então `diaAberto` já não serve de referência.
  const [reservarData, setReservarData] = useState<string | null>(null)
  const [reservaNome, setReservaNome] = useState('')
  const [reservaTelefone, setReservaTelefone] = useState('')
  const [reservaObs, setReservaObs] = useState('')
  const [reservaConfirmar, setReservaConfirmar] = useState(true)
  const [erroReserva, setErroReserva] = useState<string | null>(null)

  // Seleção múltipla na grade: é o que substitui o padrão semanal. Feriado e data
  // comemorativa formam conjuntos IRREGULARES, que nenhum filtro de dia da semana pega —
  // então o jeito de cadastrá-los em bloco é apontar as datas uma a uma e agir de uma vez.
  const [selecao, setSelecao] = useState<string[]>([])
  const [selSalvando, setSelSalvando] = useState(false)
  // Modo explícito, e não só Ctrl+clique: no celular não existe tecla modificadora, então
  // sem este botão a seleção múltipla simplesmente não teria como começar no telefone.
  const [modoSelecao, setModoSelecao] = useState(false)
  // Editor de valores: uma linha por data marcada, cada uma com o SEU valor. Um campo
  // único para todas resolve "todo sábado 3.500" e falha em "25/12 e 31/12 têm preços
  // diferentes" — que é o caso de feriado, o mais caro de errar.
  const [valoresAberto, setValoresAberto] = useState(false)
  const [valores, setValores] = useState<Record<string, string>>({})
  const [valorTodas, setValorTodas] = useState('')

  const primeiroDoMes = chaveData(ano, mes, 1)
  const ultimoDoMes = chaveData(ano, mes, diasNoMes(ano, mes))

  const carregar = useCallback(async () => {
    if (!token) return
    try {
      const [agenda, listaReservas] = await Promise.all([
        api.agenda.list(token, primeiroDoMes, ultimoDoMes),
        api.agenda.listReservas(token),
      ])
      setDatas(agenda.datas)
      setReservas(listaReservas.reservas)
      setErroPainel(null)
    } catch (e) {
      // Falha de conteúdo principal vira estado do painel, com "Tentar de novo"
      // (`frontend/CLAUDE.md` §5). O que já estava na tela permanece: mostrar um mês
      // vazio faria o dono acreditar que a agenda foi apagada.
      setErroPainel((e as Error).message || 'Não foi possível carregar a agenda.')
    } finally {
      setCarregando(false)
    }
  }, [token, primeiroDoMes, ultimoDoMes])

  useEffect(() => {
    carregar()
  }, [carregar])

  const porData = useMemo(() => {
    const mapa = new Map<string, AgendaData>()
    for (const d of datas) mapa.set(d.data, d)
    return mapa
  }, [datas])

  const celulas = useMemo(() => {
    const vazias = primeiroDiaSemana(ano, mes)
    const total = diasNoMes(ano, mes)
    const lista: (string | null)[] = Array(vazias).fill(null)
    for (let dia = 1; dia <= total; dia++) lista.push(chaveData(ano, mes, dia))
    return lista
  }, [ano, mes])

  const preReservas = useMemo(
    () => reservas.filter((r) => r.status === 'pre_reservada'),
    [reservas]
  )

  const hoje = hojeChave()

  function irPara(delta: number) {
    const proximo = new Date(ano, mes + delta, 1)
    setAno(proximo.getFullYear())
    setMes(proximo.getMonth())
  }

  function abrirDia(chave: string) {
    const linha = porData.get(chave)
    setDiaAberto(chave)
    setCampoPreco(paraCampo(linha?.preco_centavos ?? null))
    setCampoBloqueada(linha?.bloqueada ?? false)
    setCampoObs(linha?.observacao ?? '')
    setErroDia(null)
  }

  async function salvarDia() {
    if (!token || !diaAberto || salvando) return
    setSalvando(true)
    setErroDia(null)
    try {
      await api.agenda.setDate(token, diaAberto, {
        preco_centavos: paraCentavos(campoPreco),
        bloqueada: campoBloqueada,
        observacao: campoObs.trim() || null,
      })
      setDiaAberto(null)
      await carregar()
    } catch (e) {
      setErroDia((e as Error).message || 'Não foi possível salvar esta data.')
    } finally {
      setSalvando(false)
    }
  }

  async function abrirResumo(reserva: Reserva) {
    if (!token) return
    // O diálogo do dia fecha antes: dois Dialog modais empilhados disputam o
    // `pointer-events` que o Radix põe no body, e fechar os dois no mesmo tick deixa a
    // página inteira sem receber clique.
    setDiaAberto(null)
    setResumo(null)
    setErroResumo(null)
    setResumoCarregando(true)
    try {
      setResumo(await api.agenda.reservaResumo(token, reserva.id))
    } catch (e) {
      setErroResumo((e as Error).message || 'Não foi possível carregar o resumo.')
    } finally {
      setResumoCarregando(false)
    }
  }

  function abrirReservar(chave: string) {
    setDiaAberto(null)
    setReservaNome('')
    setReservaTelefone('')
    setReservaObs('')
    setReservaConfirmar(true)
    setErroReserva(null)
    setReservarData(chave)
  }

  async function criarReserva() {
    if (!token || !reservarData || processandoReserva) return
    setProcessandoReserva(true)
    setErroReserva(null)
    try {
      await api.agenda.createReserva(token, {
        data: reservarData,
        nome_cliente: reservaNome.trim() || null,
        telefone: reservaTelefone.trim() || null,
        observacao: reservaObs.trim() || null,
        confirmar: reservaConfirmar,
      })
      toast.success(
        reservaConfirmar
          ? `Data ${formatarData(reservarData)} reservada.`
          : `Data ${formatarData(reservarData)} segurada por 48 horas.`
      )
      setReservarData(null)
      await carregar()
    } catch (e) {
      setErroReserva((e as Error).message || 'Não foi possível reservar esta data.')
    } finally {
      setProcessandoReserva(false)
    }
  }

  function alternarSelecao(chave: string) {
    setSelecao((atual) =>
      atual.includes(chave) ? atual.filter((c) => c !== chave) : [...atual, chave]
    )
  }

  /** Marca de uma vez todos os dias do mês que caem neste dia da semana. */
  function selecionarDiaDaSemana(iso: number) {
    const doMes = celulas.filter(
      (c): c is string => c !== null && diaSemanaIso(c) === iso && c >= hoje
    )
    const todosJaMarcados = doMes.every((c) => selecao.includes(c))
    setSelecao((atual) =>
      todosJaMarcados
        ? atual.filter((c) => !doMes.includes(c))
        : [...new Set([...atual, ...doMes])]
    )
  }

  function abrirValores() {
    const atual: Record<string, string> = {}
    for (const chave of selecao) {
      atual[chave] = paraCampo(porData.get(chave)?.preco_centavos ?? null)
    }
    setValores(atual)
    setValorTodas('')
    setValoresAberto(true)
  }

  /** Grava cada data com o SEU valor. Em série: são poucas datas e o servidor é o mesmo,
      mas um lote paralelo que falha no meio deixa metade aplicada sem dizer quais. */
  async function salvarValores() {
    if (!token || selSalvando) return
    setSelSalvando(true)
    let feitas = 0
    try {
      for (const chave of [...selecao].sort()) {
        const atual = porData.get(chave)
        await api.agenda.setDate(token, chave, {
          preco_centavos: paraCentavos(valores[chave] ?? ''),
          bloqueada: atual?.bloqueada ?? false,
          observacao: atual?.observacao ?? null,
        })
        feitas++
      }
      toast.success(feitas === 1 ? '1 data salva.' : `${feitas} datas salvas.`)
      setValoresAberto(false)
      limparSelecao()
      await carregar()
    } catch (e) {
      toast.error(
        `${(e as Error).message || 'Falha ao salvar.'}${feitas ? ` ${feitas} data(s) já haviam sido salvas.` : ''}`
      )
      await carregar()
    } finally {
      setSelSalvando(false)
    }
  }

  /** Bloqueio/desbloqueio em massa — sem valor envolvido, então não passa pelo editor. */
  async function bloquearSelecao(bloquear: boolean) {
    if (!token || !selecao.length || selSalvando) return
    setSelSalvando(true)
    let feitas = 0
    try {
      for (const chave of [...selecao].sort()) {
        const atual = porData.get(chave)
        await api.agenda.setDate(token, chave, {
          preco_centavos: atual?.preco_centavos ?? null,
          bloqueada: bloquear,
          observacao: atual?.observacao ?? null,
        })
        feitas++
      }
      toast.success(
        `${feitas} data(s) ${bloquear ? 'bloqueada(s)' : 'desbloqueada(s)'}.`
      )
      limparSelecao()
      await carregar()
    } catch (e) {
      toast.error(
        `${(e as Error).message || 'Falha ao aplicar.'}${feitas ? ` ${feitas} data(s) já haviam sido salvas.` : ''}`
      )
      await carregar()
    } finally {
      setSelSalvando(false)
    }
  }

  function limparSelecao() {
    setSelecao([])
    setModoSelecao(false)
  }

  async function salvarLote() {
    if (!token || loteSalvando) return
    if (!loteDe || !loteAte) {
      setErroLote('Informe o início e o fim do intervalo.')
      return
    }
    if (loteAte < loteDe) {
      setErroLote('O fim do intervalo é anterior ao início.')
      return
    }
    setLoteSalvando(true)
    setErroLote(null)
    try {
      const r = await api.agenda.bulk(token, {
        de: loteDe,
        ate: loteAte,
        dias_da_semana: loteDias,
        preco_centavos: paraCentavos(lotePreco),
        // null e não false: mandar `false` desbloquearia toda data bloqueada da faixa,
        // que é como a manutenção do dia 18 voltava a ser vendida sozinha.
        bloqueada: null,
      })
      setLoteAberto(false)
      toast.success(
        r.datas_afetadas === 1
          ? '1 data atualizada.'
          : `${r.datas_afetadas} datas atualizadas.`
      )
      await carregar()
    } catch (e) {
      setErroLote((e as Error).message || 'Não foi possível aplicar o lote.')
    } finally {
      setLoteSalvando(false)
    }
  }

  /** Abre o diálogo de confirmação já com o aviso pronto para revisão. */
  async function pedirConfirmacao(reserva: Reserva) {
    if (!token || processandoReserva) return
    // Nunca dois Dialog modais empilhados — mesma razão de `abrirResumo`.
    setDiaAberto(null)
    setConfirmando(reserva)
    setAvisoTexto('')
    setPodeAvisar(false)
    setAvisoCarregando(true)
    try {
      const detalhe = await api.agenda.reservaResumo(token, reserva.id)
      setPodeAvisar(detalhe.pode_avisar)
      setAvisoTexto(detalhe.mensagem_confirmacao ?? '')
    } catch {
      // Sem o texto ainda dá para confirmar — só não dá para avisar. O diálogo diz isso
      // em vez de fingir que a opção existe.
      setPodeAvisar(false)
    } finally {
      setAvisoCarregando(false)
    }
  }

  async function confirmarReserva(avisar: boolean) {
    if (!token || !confirmando || processandoReserva) return
    const reserva = confirmando
    setProcessandoReserva(true)
    try {
      const r = await api.agenda.confirmReserva(
        token,
        reserva.id,
        avisar ? avisoTexto : null
      )
      setConfirmando(null)
      toast.success(`Reserva de ${formatarData(reserva.data)} confirmada.`)
      // A confirmação valeu; o aviso é que não saiu. Dizer as duas coisas, porque a ação
      // seguinte do atendente depende disso — ele vai reenviar pela Inbox.
      if (avisar && !r.aviso_enviado) {
        toast.error('A reserva foi confirmada, mas o aviso não saiu. Avise pela Inbox.')
      }
      await carregar()
    } catch (e) {
      toast.error((e as Error).message || 'Não foi possível confirmar a reserva.')
    } finally {
      setProcessandoReserva(false)
    }
  }

  /** Abre a confirmação SEMPRE com o diálogo do dia já fechado — ver `abrirResumo`. */
  function pedirCancelamento(reserva: Reserva) {
    setDiaAberto(null)
    setCancelando(reserva)
  }

  async function cancelarReserva() {
    if (!token || !cancelando || processandoReserva) return
    setProcessandoReserva(true)
    try {
      await api.agenda.cancelReserva(token, cancelando.id)
      toast.success(`Data ${formatarData(cancelando.data)} liberada.`)
      setCancelando(null)
      await carregar()
    } catch (e) {
      toast.error((e as Error).message || 'Não foi possível cancelar a reserva.')
    } finally {
      setProcessandoReserva(false)
    }
  }

  const linhaAberta = diaAberto ? porData.get(diaAberto) : undefined
  const reservaAberta = diaAberto
    ? reservas.find((r) => r.data === diaAberto) ?? null
    : null

  return (
    <>
      <PageShell largura="grade" className="gap-6">
        <Hero icon={CalendarDays} title="Datas e reservas">Valores e disponibilidade consultados pelo agente. A confirmação de cada reserva fica com a equipe.</Hero>
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <p className="text-[13px] text-faint leading-relaxed max-w-[52ch] hidden sm:block">
            É daqui que o agente tira data e valor. Clique numa data para editá-la, ou
            marque várias para aplicar o mesmo valor de uma vez. Data sem valor o agente
            trata como desconhecida e manda confirmar com um atendente — nunca chuta.
          </p>
          {isAdmin && (
            <div className="flex items-center gap-2 w-full sm:w-auto sm:flex-shrink-0">
              <Button
                size="sm"
                variant="secondary"
                onClick={() => setLoteAberto(true)}
                className="flex-1 sm:flex-none"
              >
                Preencher intervalo
              </Button>
              <Button
                size="sm"
                variant={modoSelecao ? 'primary' : 'secondary'}
                onClick={() => (modoSelecao ? limparSelecao() : setModoSelecao(true))}
                className="flex-1 sm:flex-none"
              >
                {modoSelecao ? 'Sair da seleção' : 'Selecionar datas'}
              </Button>
            </div>
          )}
        </div>

        {/* navegação do mês */}
        <div className="flex items-center justify-between gap-2">
          <Button
            size="icon"
            variant="ghost"
            aria-label="Mês anterior"
            onClick={() => irPara(-1)}
          >
            <ChevronLeft size={18} />
          </Button>
          <span className="font-display text-base font-semibold text-ink capitalize">
            {NOMES_MES[mes]} de {ano}
          </span>
          <Button
            size="icon"
            variant="ghost"
            aria-label="Próximo mês"
            onClick={() => irPara(1)}
          >
            <ChevronRight size={18} />
          </Button>
        </div>

        {erroPainel ? (
          <Card padding="md">
            <EmptyState
              icon={CalendarDays}
              title="Não foi possível carregar a agenda"
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
        ) : (
          <Card padding="sm">
            {/* Cabeçalho clicável: marcar "sáb" pega todos os sábados futuros do mês.
                É o que substitui o filtro de dia da semana do preenchimento em lote, sem
                tirar a liberdade de desmarcar um deles depois. */}
            <div className="grid grid-cols-7 gap-0.5 sm:gap-1 mb-1">
              {DIAS_ISO.map((d) =>
                isAdmin ? (
                  <button
                    key={d.iso}
                    onClick={() => selecionarDiaDaSemana(d.iso)}
                    title={`Marcar todos os ${d.longo}s deste mês`}
                    className="text-[10.5px] font-bold uppercase tracking-wide text-faint text-center py-1 rounded hover:bg-subtle hover:text-ink transition-colors"
                  >
                    {d.curto}
                  </button>
                ) : (
                  <span
                    key={d.iso}
                    className="text-[10.5px] font-bold uppercase tracking-wide text-faint text-center py-1"
                  >
                    {d.curto}
                  </span>
                )
              )}
            </div>
            <div className="grid grid-cols-7 gap-0.5 sm:gap-1">
              {celulas.map((chave, i) =>
                chave === null ? (
                  <div key={`vazio-${i}`} />
                ) : (
                  <button
                    key={chave}
                    onClick={(e) =>
                      // Fora do modo de seleção, clique abre a data. Ctrl/Cmd/Shift é o
                      // atalho de quem está no teclado; o botão "Selecionar datas" é o
                      // caminho do celular, que não tem tecla modificadora nenhuma.
                      isAdmin && (modoSelecao || e.ctrlKey || e.metaKey || e.shiftKey)
                        ? alternarSelecao(chave)
                        : abrirDia(chave)
                    }
                    aria-pressed={selecao.includes(chave)}
                    className={cn(
                      // `aspect-square` com um piso: a célula era só `min-h-[58px]`, e
                      // numa largura de 900px o mês virava sete colunas largas e baixas,
                      // sem parecer um calendário. O piso mantém o alvo de toque no
                      // celular, onde a coluna é estreita.
                      // 44px é o alvo de toque mínimo (`frontend/CLAUDE.md` §4); no
                      // celular a coluna tem ~46px, então o piso é o que manda ali.
                      'aspect-square min-h-[44px] sm:min-h-[52px] rounded-md sm:rounded-lg border p-0.5 sm:p-1 flex flex-col items-start justify-between text-left transition-colors hover:border-line-strong',
                      CLASSE_ESTADO[estadoDoDia(porData.get(chave))],
                      selecao.includes(chave) &&
                        'ring-2 ring-offset-1 ring-accent ring-offset-surface'
                    )}
                    aria-label={`${formatarData(chave)} — ${
                      ROTULO_ESTADO[estadoDoDia(porData.get(chave))]
                    }`}
                  >
                    <span className="text-[11px] sm:text-[12px] font-bold leading-none">
                      {Number(chave.slice(8))}
                    </span>
                    {/* Preço quando a data está à venda ou já foi vendida (aí o dono quer
                        ver por quanto saiu). Numa data bloqueada ou passada o valor fica
                        oculto: mostrá-lo sugeriria que ela está sendo oferecida. */}
                    <span className="text-[8.5px] sm:text-[9.5px] font-semibold leading-tight truncate w-full">
                      {porData.get(chave)?.livre || porData.get(chave)?.reserva
                        ? precoCurto(porData.get(chave)?.preco_centavos ?? null)
                        : ''}
                    </span>
                  </button>
                )
              )}
            </div>

            <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 mt-3 pt-3 border-t border-line">
              {LEGENDA.map((estado) => (
                <span key={estado} className="flex items-center gap-1.5">
                  <span
                    className={cn(
                      'w-3 h-3 rounded border',
                      CLASSE_ESTADO[estado].split(' ').slice(0, 2).join(' ')
                    )}
                  />
                  <span className="text-[11px] text-faint">{ROTULO_ESTADO[estado]}</span>
                </span>
              ))}
              {isAdmin && !modoSelecao && (
                <span className="text-[11px] text-faint sm:ml-auto">
                  Use “Selecionar datas” para editar várias de uma vez.
                </span>
              )}
            </div>
          </Card>
        )}

        {/* Barra da seleção: aparece só quando há datas marcadas, e some ao aplicar.
            É o caminho de cadastro em bloco para conjunto irregular — os feriados de um
            ano, que não têm nenhuma regularidade de dia da semana. */}
        {isAdmin && modoSelecao && (
          <Card
            padding="sm"
            className="flex flex-col sm:flex-row sm:items-center gap-2 sticky bottom-3 z-10 shadow-card-lg"
          >
            <span className="text-[12.5px] font-semibold text-ink">
              {selecao.length === 0
                ? 'Toque nas datas que quer editar'
                : selecao.length === 1
                  ? '1 data marcada'
                  : `${selecao.length} datas marcadas`}
            </span>
            <div className="flex flex-wrap items-center gap-2 sm:ml-auto">
              <Button
                size="sm"
                disabled={selSalvando || !selecao.length}
                onClick={abrirValores}
              >
                Definir valores
              </Button>
              <Button
                size="sm"
                variant="secondary"
                disabled={selSalvando || !selecao.length}
                onClick={() => bloquearSelecao(true)}
              >
                Bloquear
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={selSalvando || !selecao.length}
                onClick={() => bloquearSelecao(false)}
              >
                Desbloquear
              </Button>
              <Button size="sm" variant="ghost" onClick={limparSelecao}>
                Sair
              </Button>
            </div>
          </Card>
        )}

        {/* fila de trabalho que o recurso cria */}
        {preReservas.length > 0 && (
          <div className="flex flex-col gap-2">
            <p className="text-[10.5px] font-bold uppercase tracking-[0.1em] text-faint px-1">
              Pré-reservas aguardando decisão
            </p>
            <p className="text-[12px] text-faint px-1 leading-relaxed -mt-1">
              O agente segura a data por 48 horas quando o cliente demonstra intenção de
              fechar; ele nunca confirma. Passado o prazo sem decisão, a data volta a ser
              oferecida automaticamente.
            </p>
            {preReservas.map((r) => (
              <Card key={r.id} padding="sm" className="flex items-center gap-3 flex-wrap">
                <div className="flex-1 min-w-0 flex flex-col gap-0.5">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-[13px] font-semibold text-ink">
                      {formatarData(r.data)}
                    </span>
                    <Badge variant="warning">{tempoRestante(r.expira_em)}</Badge>
                    {r.criada_por === 'bot' && <Badge variant="ai">agente</Badge>}
                  </div>
                  <span className="text-[12px] text-muted truncate">
                    {r.nome_cliente || 'sem nome'}
                    {r.telefone ? ` · ${r.telefone}` : ''}
                  </span>
                </div>
                <div className="flex items-center gap-1 flex-shrink-0 w-full sm:w-auto">
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={processandoReserva}
                    onClick={() => abrirResumo(r)}
                    className="flex-1 sm:flex-none"
                  >
                    Ver resumo
                  </Button>
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={processandoReserva}
                    onClick={() => pedirConfirmacao(r)}
                    className="flex-1 sm:flex-none"
                  >
                    Confirmar
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={processandoReserva}
                    onClick={() => pedirCancelamento(r)}
                    className="flex-1 sm:flex-none"
                  >
                    Cancelar
                  </Button>
                </div>
              </Card>
            ))}
          </div>
        )}
      </PageShell>

      {/* ── um dia ── */}
      <Dialog open={diaAberto !== null} onOpenChange={(o) => !o && setDiaAberto(null)}>
        <DialogContent>
          <DialogTitle>{diaAberto ? formatarData(diaAberto) : ''}</DialogTitle>
          <DialogDescription>
            {ROTULO_ESTADO[estadoDoDia(linhaAberta)]}
            {linhaAberta?.preco_centavos !== null &&
            linhaAberta?.preco_centavos !== undefined
              ? ` · ${formatarPreco(linhaAberta.preco_centavos)}`
              : ''}
          </DialogDescription>

          {reservaAberta && (
            <Card padding="sm" className="mt-4 flex flex-col gap-1.5">
              <div className="flex items-center gap-2 flex-wrap">
                <Badge
                  variant={reservaAberta.status === 'confirmada' ? 'accent' : 'warning'}
                >
                  {reservaAberta.status === 'confirmada'
                    ? 'Reservada'
                    : tempoRestante(reservaAberta.expira_em) || 'Pré-reservada'}
                </Badge>
                {reservaAberta.criada_por === 'bot' && <Badge variant="ai">agente</Badge>}
              </div>
              <span className="text-[12.5px] text-ink font-medium">
                {reservaAberta.nome_cliente || 'Cliente sem nome'}
              </span>
              {reservaAberta.telefone && (
                <span className="text-[12px] text-faint">{reservaAberta.telefone}</span>
              )}
              <div className="flex items-center gap-1 mt-1 flex-wrap">
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={processandoReserva}
                  onClick={() => abrirResumo(reservaAberta)}
                >
                  Ver resumo
                </Button>
                {reservaAberta.status === 'pre_reservada' && (
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={processandoReserva}
                    onClick={() => pedirConfirmacao(reservaAberta)}
                  >
                    Confirmar
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={processandoReserva}
                  onClick={() => pedirCancelamento(reservaAberta)}
                >
                  Cancelar reserva
                </Button>
              </div>
            </Card>
          )}

          {diaAberto && diaAberto < hoje ? (
            // A causa nº 1 de "salvei o valor e não ficou Livre": o calendário abre no mês
            // corrente, onde quase todo dia já passou. Antes a tela aceitava o valor e a
            // célula seguia cinza, sem dizer nada.
            <p className="text-[12.5px] text-faint mt-4 leading-relaxed">
              Esta data já passou, então ela não volta a ficar disponível nem aparece para
              o agente. Cadastrar valor aqui não muda isso.
            </p>
          ) : isAdmin ? (
            <div className="flex flex-col gap-3 mt-4">
              <Field
                label="Valor da data"
                hint="em reais; vazio = o agente não informa valor nesta data"
                htmlFor="preco"
              >
                <Input
                  id="preco"
                  inputMode="decimal"
                  value={campoPreco}
                  onChange={(e) => setCampoPreco(e.target.value)}
                  placeholder="4200,00"
                />
              </Field>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={campoBloqueada}
                  onChange={(e) => setCampoBloqueada(e.target.checked)}
                  className="w-4 h-4 accent-accent"
                />
                <span className="text-[12.5px] text-ink">
                  Bloquear esta data{' '}
                  <span className="text-faint">— some da disponibilidade do agente</span>
                </span>
              </label>
              <Field label="Observação" hint="interna; o cliente nunca vê" htmlFor="obs">
                <Textarea
                  id="obs"
                  rows={2}
                  value={campoObs}
                  onChange={(e) => setCampoObs(e.target.value)}
                  placeholder="ex.: manutenção do ar-condicionado"
                />
              </Field>
              {erroDia && <span className="text-[12px] text-danger-fg">{erroDia}</span>}
            </div>
          ) : (
            <p className="text-[12.5px] text-faint mt-4 leading-relaxed">
              Só administradores editam valor e bloqueio. Você continua podendo reservar,
              confirmar e cancelar.
            </p>
          )}

          {/* Fechar por telefone é o caso mais comum do salão, e não era possível pela
              tela: a rota existe e é aberta a qualquer atendente de propósito. */}
          {!reservaAberta && diaAberto && diaAberto >= hoje && (
            <Button
              size="sm"
              variant="secondary"
              className="mt-4 w-full"
              onClick={() => abrirReservar(diaAberto)}
            >
              Reservar esta data
            </Button>
          )}

          <DialogFooter>
            <Button size="sm" variant="ghost" onClick={() => setDiaAberto(null)}>
              Fechar
            </Button>
            {isAdmin && diaAberto && diaAberto >= hoje && (
              <Button size="sm" onClick={salvarDia} disabled={salvando}>
                {salvando ? 'Salvando…' : 'Salvar'}
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── resumo da reserva ── */}
      <Dialog
        open={resumo !== null || resumoCarregando || erroResumo !== null}
        onOpenChange={(o) => {
          if (!o) {
            setResumo(null)
            setErroResumo(null)
          }
        }}
      >
        <DialogContent className="max-w-[480px]">
          <DialogTitle>
            {resumo ? formatarData(resumo.data) : 'Resumo da reserva'}
          </DialogTitle>
          <DialogDescription>
            {resumo
              ? `${ROTULO_ESTADO[resumo.status === 'confirmada' ? 'confirmada' : 'pre_reservada']}${
                  resumo.preco_centavos !== null
                    ? ` · ${formatarPreco(resumo.preco_centavos)}`
                    : ''
                }`
              : 'O que o agente coletou nesta conversa.'}
          </DialogDescription>

          {resumoCarregando && (
            <div className="flex items-center gap-2 py-8 justify-center text-faint">
              <Spinner />
              <span className="text-[12.5px]">Carregando…</span>
            </div>
          )}

          {erroResumo && (
            <p className="text-[12.5px] text-danger-fg mt-4">{erroResumo}</p>
          )}

          {resumo && (
            <div className="flex flex-col gap-3 mt-4">
              <div className="flex flex-col gap-0.5">
                <span className="text-[13px] font-semibold text-ink">
                  {resumo.nome_cliente || resumo.contato_nome || 'Cliente sem nome'}
                </span>
                {resumo.telefone && (
                  <span className="text-[12px] text-faint">{resumo.telefone}</span>
                )}
                {/* O nome da reserva é cópia do momento em que ela nasceu; se o contato
                    foi renomeado depois, mostrar os dois evita o "quem é essa pessoa?". */}
                {resumo.contato_nome &&
                  resumo.nome_cliente &&
                  resumo.contato_nome !== resumo.nome_cliente && (
                    <span className="text-[12px] text-faint">
                      hoje salvo como {resumo.contato_nome}
                    </span>
                  )}
                {resumo.expira_em && (
                  <span className="text-[12px] text-warning-fg">
                    {tempoRestante(resumo.expira_em)}
                  </span>
                )}
              </div>

              {Object.keys(resumo.dados_coletados).length > 0 ? (
                <div className="flex flex-col gap-1">
                  <p className="text-[10.5px] font-bold uppercase tracking-[0.1em] text-faint">
                    Coletado pelo agente
                  </p>
                  <Card padding="sm" className="flex flex-col gap-1">
                    {Object.entries(resumo.dados_coletados).map(([campo, valor]) => (
                      <div key={campo} className="flex gap-2 text-[12.5px]">
                        <span className="text-faint capitalize flex-shrink-0">
                          {campo.replace(/_/g, ' ')}
                        </span>
                        <span className="text-ink font-medium">{valor}</span>
                      </div>
                    ))}
                  </Card>
                </div>
              ) : (
                <p className="text-[12px] text-faint leading-relaxed">
                  O agente não coletou nenhum dado nesta conversa. O que ele pergunta é
                  definido na persona, em Configurações › Agente de IA.
                </p>
              )}

              {resumo.nota_interna && (
                <div className="flex flex-col gap-1">
                  <p className="text-[10.5px] font-bold uppercase tracking-[0.1em] text-faint">
                    Última nota interna
                  </p>
                  <Card padding="sm">
                    <p className="text-[12.5px] text-ink leading-relaxed whitespace-pre-wrap">
                      {resumo.nota_interna}
                    </p>
                  </Card>
                </div>
              )}

              {resumo.observacao && (
                <p className="text-[12px] text-faint leading-relaxed">
                  Observação: {resumo.observacao}
                </p>
              )}
            </div>
          )}

          <DialogFooter>
            {resumo?.conversation_id && (
              <Link
                href={`/inbox?c=${resumo.conversation_id}`}
                className="mr-auto text-[12.5px] font-semibold text-accent-fg hover:underline"
              >
                Abrir a conversa
              </Link>
            )}
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                setResumo(null)
                setErroResumo(null)
              }}
            >
              Fechar
            </Button>
            {resumo?.status === 'pre_reservada' && (
              <Button
                size="sm"
                disabled={processandoReserva}
                onClick={() => {
                  const alvo = reservas.find((r) => r.id === resumo.reserva_id)
                  setResumo(null)
                  if (alvo) pedirConfirmacao(alvo)
                }}
              >
                Confirmar
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── confirmar a reserva (e avisar o cliente) ──
           O texto aparece ANTES de sair porque isto manda mensagem no WhatsApp de um
           cliente real e não há como desfazer. Confirmar sem avisar continua sendo um
           clique — o aviso é decisão de quem confirma, não default do sistema. */}
      <Dialog open={confirmando !== null} onOpenChange={(o) => !o && setConfirmando(null)}>
        <DialogContent>
          <DialogTitle>
            Confirmar {confirmando ? formatarData(confirmando.data) : ''}
          </DialogTitle>
          <DialogDescription>
            {podeAvisar
              ? 'A reserva passa a ocupar a data. Revise o aviso antes de enviá-lo ao cliente.'
              : 'A reserva passa a ocupar a data.'}
          </DialogDescription>

          {avisoCarregando ? (
            <div className="flex justify-center py-6">
              <Spinner />
            </div>
          ) : podeAvisar ? (
            <Field label="Mensagem para o cliente" htmlFor="aviso-confirmacao">
              <Textarea
                id="aviso-confirmacao"
                rows={5}
                value={avisoTexto}
                onChange={(e) => setAvisoTexto(e.target.value)}
              />
              <p className="mt-1.5 text-[12px] text-faint">
                Vai para o WhatsApp do cliente e fica registrada na conversa. O texto
                padrão se edita em Agente.
              </p>
            </Field>
          ) : (
            <p className="text-[13px] text-muted">
              Esta reserva não tem conversa de WhatsApp ligada a ela, então não há para
              quem enviar o aviso — avise o cliente pelo canal em que vocês falaram.
            </p>
          )}

          <DialogFooter>
            <Button
              size="sm"
              variant="ghost"
              disabled={processandoReserva}
              onClick={() => setConfirmando(null)}
            >
              Cancelar
            </Button>
            <Button
              size="sm"
              variant="secondary"
              disabled={processandoReserva}
              onClick={() => confirmarReserva(false)}
            >
              Confirmar sem avisar
            </Button>
            {podeAvisar && (
              <Button
                size="sm"
                disabled={processandoReserva || !avisoTexto.trim()}
                onClick={() => confirmarReserva(true)}
              >
                Confirmar e avisar
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── reservar uma data ── */}
      <Dialog
        open={reservarData !== null}
        onOpenChange={(o) => !o && setReservarData(null)}
      >
        <DialogContent>
          <DialogTitle>
            Reservar {reservarData ? formatarData(reservarData) : ''}
          </DialogTitle>
          <DialogDescription>
            Para quem fecha por telefone ou pessoalmente. O agente para de oferecer esta
            data imediatamente.
          </DialogDescription>
          <div className="flex flex-col gap-3 mt-4">
            <Field label="Nome do cliente" htmlFor="reserva-nome">
              <Input
                id="reserva-nome"
                value={reservaNome}
                onChange={(e) => setReservaNome(e.target.value)}
                placeholder="Fernanda Lima"
              />
            </Field>
            <Field label="Telefone" hint="opcional" htmlFor="reserva-telefone">
              <Input
                id="reserva-telefone"
                inputMode="tel"
                value={reservaTelefone}
                onChange={(e) => setReservaTelefone(e.target.value)}
                placeholder="5511990001111"
              />
            </Field>
            <Field label="Observação" hint="interna; o cliente nunca vê" htmlFor="reserva-obs">
              <Textarea
                id="reserva-obs"
                rows={2}
                value={reservaObs}
                onChange={(e) => setReservaObs(e.target.value)}
                placeholder="ex.: sinal pago em dinheiro, contrato pendente"
              />
            </Field>
            <label className="flex items-start gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={!reservaConfirmar}
                onChange={(e) => setReservaConfirmar(!e.target.checked)}
                className="w-4 h-4 accent-accent mt-0.5 flex-shrink-0"
              />
              <span className="text-[12.5px] text-ink">
                Só segurar por 48 horas{' '}
                <span className="text-faint">
                  — a data volta a ser oferecida sozinha se ninguém confirmar
                </span>
              </span>
            </label>
            {erroReserva && (
              <span className="text-[12px] text-danger-fg">{erroReserva}</span>
            )}
          </div>
          <DialogFooter>
            <Button size="sm" variant="ghost" onClick={() => setReservarData(null)}>
              Cancelar
            </Button>
            <Button size="sm" onClick={criarReserva} disabled={processandoReserva}>
              {processandoReserva
                ? 'Salvando…'
                : reservaConfirmar
                  ? 'Reservar'
                  : 'Segurar data'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── valores, um por data marcada ── */}
      <Dialog open={valoresAberto} onOpenChange={setValoresAberto}>
        <DialogContent className="max-w-[460px]">
          <DialogTitle>
            {selecao.length === 1
              ? 'Valor da data'
              : `Valores de ${selecao.length} datas`}
          </DialogTitle>
          <DialogDescription>
            Cada data com o seu valor. Deixar em branco tira o valor: o agente passa a
            tratar a data como desconhecida e manda confirmar com um atendente.
          </DialogDescription>

          {selecao.length > 1 && (
            <div className="flex items-end gap-2 mt-4 pb-3 border-b border-line">
              <Field label="Preencher todas com" htmlFor="valor-todas" className="flex-1">
                <Input
                  id="valor-todas"
                  inputMode="decimal"
                  value={valorTodas}
                  onChange={(e) => setValorTodas(e.target.value)}
                  placeholder="4200,00"
                />
              </Field>
              <Button
                size="sm"
                variant="secondary"
                onClick={() =>
                  setValores(Object.fromEntries(selecao.map((c) => [c, valorTodas])))
                }
              >
                Preencher
              </Button>
            </div>
          )}

          <div className="flex flex-col gap-1.5 mt-3 max-h-[46vh] overflow-y-auto">
            {[...selecao].sort().map((chave) => (
              <div key={chave} className="flex items-center gap-2">
                <span className="text-[12.5px] text-ink w-[112px] flex-shrink-0">
                  {formatarData(chave)}
                  <span className="text-faint ml-1">
                    {DIAS_ISO.find((d) => d.iso === diaSemanaIso(chave))?.curto}
                  </span>
                </span>
                <Input
                  inputMode="decimal"
                  aria-label={`Valor de ${formatarData(chave)}`}
                  value={valores[chave] ?? ''}
                  onChange={(e) =>
                    setValores((atual) => ({ ...atual, [chave]: e.target.value }))
                  }
                  placeholder="4200,00"
                  className="flex-1"
                />
              </div>
            ))}
          </div>

          <DialogFooter>
            <Button size="sm" variant="ghost" onClick={() => setValoresAberto(false)}>
              Cancelar
            </Button>
            <Button size="sm" onClick={salvarValores} disabled={selSalvando}>
              {selSalvando ? 'Salvando…' : 'Salvar'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* ── lote ── */}
      <Dialog open={loteAberto} onOpenChange={setLoteAberto}>
        <DialogContent>
          <DialogTitle>Preencher intervalo</DialogTitle>
          <DialogDescription>
            Aplica o mesmo valor a um intervalo contínuo, opcionalmente só a certos dias
            da semana. Para datas soltas — feriados, por exemplo — marque-as direto no
            calendário. Datas com reserva ativa não são alteradas, e datas bloqueadas
            continuam bloqueadas.
          </DialogDescription>
          <div className="flex flex-col gap-3 mt-4">
            <div className="flex gap-3">
              <Field label="De" htmlFor="lote-de" className="flex-1">
                <Input
                  id="lote-de"
                  type="date"
                  value={loteDe}
                  onChange={(e) => setLoteDe(e.target.value)}
                />
              </Field>
              <Field label="Até" htmlFor="lote-ate" className="flex-1">
                <Input
                  id="lote-ate"
                  type="date"
                  value={loteAte}
                  onChange={(e) => setLoteAte(e.target.value)}
                />
              </Field>
            </div>
            <Field label="Dias da semana" hint="nenhum marcado = todos os dias">
              <div className="flex flex-wrap gap-1.5">
                {DIAS_ISO.map((d) => {
                  const marcado = loteDias.includes(d.iso)
                  return (
                    <button
                      key={d.iso}
                      type="button"
                      aria-pressed={marcado}
                      onClick={() =>
                        setLoteDias((atual) =>
                          marcado
                            ? atual.filter((x) => x !== d.iso)
                            : [...atual, d.iso]
                        )
                      }
                      className={cn(
                        'px-3 min-h-[38px] rounded-lg border text-[12px] font-semibold transition-colors',
                        marcado
                          ? 'bg-accent-soft border-accent-ring text-accent-fg'
                          : 'bg-surface border-line text-muted hover:bg-canvas'
                      )}
                    >
                      {d.curto}
                    </button>
                  )
                })}
              </div>
            </Field>
            <Field
              label="Valor"
              hint="em reais; vazio deixa as datas sem valor"
              htmlFor="lote-preco"
            >
              <Input
                id="lote-preco"
                inputMode="decimal"
                value={lotePreco}
                onChange={(e) => setLotePreco(e.target.value)}
                placeholder="4200,00"
              />
            </Field>
            {erroLote && <span className="text-[12px] text-danger-fg">{erroLote}</span>}
          </div>
          <DialogFooter>
            <Button size="sm" variant="ghost" onClick={() => setLoteAberto(false)}>
              Cancelar
            </Button>
            <Button size="sm" onClick={salvarLote} disabled={loteSalvando}>
              {loteSalvando ? 'Aplicando…' : 'Aplicar'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={cancelando !== null}
        onOpenChange={(o) => !o && setCancelando(null)}
        title="Cancelar esta reserva?"
        description={
          cancelando
            ? `A data ${formatarData(cancelando.data)} volta a ser oferecida pelo agente imediatamente.`
            : undefined
        }
        confirmLabel="Cancelar reserva"
        cancelLabel="Voltar"
        destructive
        loading={processandoReserva}
        onConfirm={cancelarReserva}
      />
    </>
  )
}
