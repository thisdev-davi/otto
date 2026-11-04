#!/usr/bin/env node
// Verifica contraste WCAG dos tokens de app/globals.css, nos DOIS temas.
// Roda offline, sem navegador: os valores vêm do próprio CSS, então o gate
// acompanha qualquer mudança de paleta.
//
//   node scripts/check-contrast.mjs
//
// Sai com código 1 se algum par reprovar — é o que permite plugar em CI.
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const raiz = dirname(dirname(fileURLToPath(import.meta.url)))
const css = readFileSync(join(raiz, 'app/globals.css'), 'utf8')

/** Extrai `--nome: R G B;` de um bloco delimitado por seletor. */
function tokensDoBloco(seletor) {
  const i = css.indexOf(seletor)
  if (i === -1) throw new Error(`bloco ${seletor} não encontrado em globals.css`)
  const abre = css.indexOf('{', i)
  const fecha = css.indexOf('\n}', abre)
  const corpo = css.slice(abre, fecha)
  const out = {}
  for (const m of corpo.matchAll(/--([\w-]+):\s*(\d{1,3})\s+(\d{1,3})\s+(\d{1,3})\s*;/g)) {
    out[m[1]] = [Number(m[2]), Number(m[3]), Number(m[4])]
  }
  return out
}

const claro = tokensDoBloco(':root')
// O escuro só redefine parte dos tokens; o resto herda do :root.
const escuro = { ...claro, ...tokensDoBloco('.dark') }

const lum = ([r, g, b]) => {
  const [R, G, B] = [r, g, b].map((v) => {
    const c = v / 255
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
  })
  return 0.2126 * R + 0.7152 * G + 0.0722 * B
}
const ratio = (a, b) => {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p)
  return (x + 0.05) / (y + 0.05)
}

// [frente, fundo, mínimo]. 4.5 = texto normal (AA); 3.0 = texto grande e
// elemento gráfico. Bordas decorativas (--line) ficam de fora de propósito:
// WCAG 1.4.11 não exige contraste de divisor que não identifica componente.
const PARES = [
  // rampa de texto sobre cada superfície onde ela realmente aparece
  ...['ink', 'ink-2', 'muted', 'faint'].flatMap((t) =>
    ['canvas', 'surface', 'surface-raised', 'subtle'].map((s) => [t, s, 4.5])
  ),
  // texto sobre preenchimento sólido
  ['on-accent', 'accent', 4.5],
  ['on-success', 'success', 4.5],
  ['on-danger', 'danger', 4.5],
  ['on-accent-danger', 'accent', 4.5],
  // texto semântico sobre a variante soft e sobre as superfícies neutras
  ['accent-fg', 'accent-soft', 4.5],
  ['accent-fg', 'surface', 4.5],
  ['success-fg', 'success-soft', 4.5],
  ['success-fg', 'surface', 4.5],
  ['danger-fg', 'danger-soft', 4.5],
  ['danger-fg', 'surface', 4.5],
  ['warning-fg', 'warning-soft', 4.5],
  ['warning-fg', 'surface', 4.5],
  ['note-fg', 'note-soft', 4.5],
  ['ai-fg', 'ai-soft', 4.5],
  ['ai-fg', 'surface', 4.5],
  ['info', 'surface', 4.5],
  ['on-sky', 'sky', 4.5],
  ['bubble-bot-fg', 'bubble-bot', 4.5],
  // barras de gráfico: elemento gráfico sobre o fundo do card
  ['chart-1', 'surface', 3.0],
  ['chart-2', 'surface', 3.0],
  // avatar sempre carrega texto branco por cima
  ...[1, 2, 3, 4, 5, 6].map((n) => ['on-accent', `avatar-${n}`, 4.5]),
]

let falhas = 0
for (const [nome, tokens] of [['claro', claro], ['escuro', escuro]]) {
  console.log(`\n── tema ${nome} ─────────────────────────────`)
  for (const [fg, bg, min] of PARES) {
    if (!tokens[fg] || !tokens[bg]) {
      console.log(`  ?  ${fg} / ${bg}: token inexistente`)
      falhas++
      continue
    }
    const r = ratio(tokens[fg], tokens[bg])
    const ok = r >= min
    if (!ok) falhas++
    // Só imprime as falhas; o verde vira ruído com ~40 pares.
    if (!ok) console.log(`  FALHA  ${fg} sobre ${bg}: ${r.toFixed(2)} (mínimo ${min})`)
  }
  console.log(`  ${PARES.length} pares verificados`)
}

if (falhas) {
  console.error(`\n${falhas} par(es) reprovando o contraste mínimo.`)
  process.exit(1)
}
console.log('\nContraste OK nos dois temas.')
