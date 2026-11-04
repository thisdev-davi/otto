import type { AgendaData } from '@/types/api'

/**
 * Helpers da agenda do salão. Arquivo novo do derivado — não conflita com a base.
 *
 * Datas aqui são SEMPRE a string 'AAAA-MM-DD' que o backend usa como chave do dia, nunca
 * um `Date`. `new Date('2026-08-15')` é interpretado como UTC pelo JS e, em qualquer fuso
 * a oeste de Greenwich, `getDate()` devolve 14 — o dia inteiro escorrega para trás no
 * calendário. Por isso a montagem da grade usa componentes ano/mês/dia explícitos.
 */

export const NOMES_MES = [
  'janeiro',
  'fevereiro',
  'março',
  'abril',
  'maio',
  'junho',
  'julho',
  'agosto',
  'setembro',
  'outubro',
  'novembro',
  'dezembro',
]

/**
 * Dias da semana em ISO (1=segunda … 7=domingo), na ordem em que o calendário é lido no
 * Brasil: domingo primeiro. É também o cabeçalho da grade — `curto` é o rótulo da coluna.
 */
export const DIAS_ISO: { iso: number; curto: string; longo: string }[] = [
  { iso: 7, curto: 'dom', longo: 'domingo' },
  { iso: 1, curto: 'seg', longo: 'segunda' },
  { iso: 2, curto: 'ter', longo: 'terça' },
  { iso: 3, curto: 'qua', longo: 'quarta' },
  { iso: 4, curto: 'qui', longo: 'quinta' },
  { iso: 5, curto: 'sex', longo: 'sexta' },
  { iso: 6, curto: 'sáb', longo: 'sábado' },
]

export function chaveData(ano: number, mes: number, dia: number): string {
  return `${ano}-${String(mes + 1).padStart(2, '0')}-${String(dia).padStart(2, '0')}`
}

export function hojeChave(): string {
  const agora = new Date()
  return chaveData(agora.getFullYear(), agora.getMonth(), agora.getDate())
}

/** '2026-08-15' → '15/08/2026', sem passar por `Date` (ver nota do topo). */
export function formatarData(chave: string): string {
  const [ano, mes, dia] = chave.split('-')
  return `${dia}/${mes}/${ano}`
}

/** Último dia do mês (mes é 0-indexado, como em `Date`). */
export function diasNoMes(ano: number, mes: number): number {
  return new Date(ano, mes + 1, 0).getDate()
}

/** Dia da semana (0=domingo) do primeiro dia do mês. */
export function primeiroDiaSemana(ano: number, mes: number): number {
  return new Date(ano, mes, 1).getDay()
}

export function formatarPreco(centavos: number | null): string {
  if (centavos === null || centavos === undefined) return ''
  return (centavos / 100).toLocaleString('pt-BR', {
    style: 'currency',
    currency: 'BRL',
    minimumFractionDigits: 2,
  })
}

/** Versão curta para caber na célula do calendário: 'R$ 4.200' sem centavos. */
export function precoCurto(centavos: number | null): string {
  if (centavos === null || centavos === undefined) return ''
  const reais = Math.round(centavos / 100)
  return `R$ ${reais.toLocaleString('pt-BR')}`
}

/**
 * '4.200,00' | '4200' | 'R$ 4.200' → 420000. String vazia → null (valor não cadastrado).
 *
 * Aceita o que o dono digita de verdade. `null` e `0` são coisas DIFERENTES aqui: zero é
 * um preço promocional válido; null é "ainda não sei", e é o que faz o agente mandar
 * confirmar com um atendente em vez de informar um número.
 */
export function paraCentavos(bruto: string): number | null {
  const limpo = bruto.replace(/[^\d,.-]/g, '').trim()
  if (!limpo) return null
  // Formato brasileiro: ponto é milhar, vírgula é decimal.
  const normalizado = limpo.replace(/\./g, '').replace(',', '.')
  const valor = Number(normalizado)
  if (!Number.isFinite(valor) || valor < 0) return null
  return Math.round(valor * 100)
}

/** Centavos → o texto que vai no campo de edição ('420000' → '4200,00'). */
export function paraCampo(centavos: number | null): string {
  if (centavos === null || centavos === undefined) return ''
  return (centavos / 100).toFixed(2).replace('.', ',')
}

export type EstadoDia =
  | 'sem_cadastro'
  | 'sem_preco'
  | 'livre'
  | 'pre_reservada'
  | 'confirmada'
  | 'bloqueada'
  | 'passado'

/**
 * Traduz a linha resolvida em estado visual.
 *
 * **Não recalcula disponibilidade** — lê o `motivo` que o backend resolveu. A versão
 * anterior deduzia "livre" de `preco !== null && !bloqueada`, e com a regra semanal isso
 * viraria uma segunda implementação da regra de negócio, divergindo do que o agente vê.
 */
export function estadoDoDia(linha: AgendaData | undefined): EstadoDia {
  if (!linha) return 'sem_cadastro'
  if (linha.reserva?.status === 'confirmada') return 'confirmada'
  if (linha.reserva?.status === 'pre_reservada') return 'pre_reservada'
  switch (linha.motivo) {
    case 'ok':
      return 'livre'
    case 'bloqueada':
      return 'bloqueada'
    case 'sem_preco':
      return 'sem_preco'
    case 'passado':
      return 'passado'
    default:
      return 'sem_cadastro'
  }
}

/**
 * Classes por estado. Strings LITERAIS e completas: o Tailwind lê o texto-fonte, então
 * `bg-${estado}-soft` não geraria CSS nenhum (`frontend/CLAUDE.md` §3).
 *
 * A cor separa **o que pede ação** do que não pede: verde vende, âmbar/azul têm reserva,
 * `note` é o único pendente de cadastro, e todo o resto é inerte. O motivo exato dos
 * inertes vive no rótulo — pintar quatro cinzas diferentes só faria o calendário piscar.
 */
export const CLASSE_ESTADO: Record<EstadoDia, string> = {
  sem_cadastro: 'bg-surface border-line text-faint',
  sem_preco: 'bg-note-soft border-note-line text-note-fg',
  livre: 'bg-success-soft border-success-line text-success-fg',
  pre_reservada: 'bg-warning-soft border-warning-line text-warning-fg',
  confirmada: 'bg-accent-soft border-accent-ring text-accent-fg',
  bloqueada: 'bg-subtle border-line text-faint',
  passado: 'bg-canvas border-line text-faint',
}

export const ROTULO_ESTADO: Record<EstadoDia, string> = {
  sem_cadastro: 'Sem cadastro',
  sem_preco: 'Sem valor cadastrado',
  livre: 'Livre',
  pre_reservada: 'Pré-reservada',
  confirmada: 'Reservada',
  bloqueada: 'Bloqueada',
  passado: 'Já passou',
}

/** Dia da semana em ISO (1=segunda … 7=domingo) a partir da chave 'AAAA-MM-DD'. */
export function diaSemanaIso(chave: string): number {
  const [ano, mes, dia] = chave.split('-').map(Number)
  const js = new Date(ano, mes - 1, dia).getDay() // 0=domingo
  return js === 0 ? 7 : js
}

/** Quanto falta para a pré-reserva cair. Vazio quando não há prazo. */
export function tempoRestante(expiraEm: string | null): string {
  if (!expiraEm) return ''
  const restanteMs = new Date(expiraEm).getTime() - Date.now()
  if (restanteMs <= 0) return 'expirada'
  const horas = Math.floor(restanteMs / 3_600_000)
  if (horas >= 24) return `expira em ${Math.floor(horas / 24)}d${horas % 24}h`
  if (horas >= 1) return `expira em ${horas}h`
  return `expira em ${Math.max(1, Math.floor(restanteMs / 60_000))}min`
}
