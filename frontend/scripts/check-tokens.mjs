#!/usr/bin/env node
// Impede que a dívida de cor volte: nenhum hex e nenhuma cor da paleta padrão do
// Tailwind fora de app/globals.css.
//
//   node scripts/check-tokens.mjs
//
// Por que grep próprio e não eslint-plugin-tailwindcss: a regra `no-arbitrary-value`
// de lá também marcaria os ~200 `text-[13px]` legítimos do projeto, e o ruído faria
// o gate ser desligado na primeira semana.
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join, relative } from 'node:path'

const raiz = dirname(dirname(fileURLToPath(import.meta.url)))
const DIRS = ['app', 'components', 'lib', 'contexts', 'hooks']

// app/layout.tsx precisa de hex literal em `themeColor`: é valor de <meta>, não
// classe CSS — o navegador pinta a barra de endereço antes de qualquer CSS existir.
const ISENTOS = new Set(['app/layout.tsx'])

const REGRAS = [
  {
    nome: 'hex em classe utilitária',
    re: /\b(?:bg|text|border|ring|shadow|fill|stroke|from|via|to|decoration|outline|divide|accent|caret)(?:-[a-z]+)?-\[#[0-9A-Fa-f]{3,8}\]/g,
    dica: 'use um token (bg-surface, text-muted, border-line…) — os valores moram em app/globals.css',
  },
  {
    nome: 'paleta padrão do Tailwind',
    // Sem tom, só white/black são da paleta: `bg-sky` sozinho é o token da marca,
    // `bg-sky-200` é o Tailwind.
    re: /\b(?:bg|text|border|ring|divide|from|via|to)-(?:white|black|(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-\d{2,3})\b/g,
    dica: 'a paleta do Otto é semântica; branco puro é `on-accent`, cinza é `muted`/`faint`',
  },
  {
    nome: 'cor em style={{}}',
    re: /style=\{\{[^}]*(?:color|background)[^}]*#[0-9A-Fa-f]{3,8}/g,
    dica: 'cor dinâmica usa rgb(var(--token)), nunca hex literal',
  },
]

// Tira comentários antes de checar: este arquivo e vários outros CITAM classes
// antigas em comentário para explicar a regra, e isso não pode virar falha.
function semComentarios(src) {
  return src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1')
}

function* arquivos(dir) {
  for (const nome of readdirSync(dir)) {
    const p = join(dir, nome)
    if (statSync(p).isDirectory()) yield* arquivos(p)
    else if (/\.tsx?$/.test(nome)) yield p
  }
}

let falhas = 0
for (const d of DIRS) {
  let base
  try {
    base = join(raiz, d)
    statSync(base)
  } catch {
    continue
  }
  for (const arq of arquivos(base)) {
    const rel = relative(raiz, arq)
    if (ISENTOS.has(rel)) continue
    const linhas = semComentarios(readFileSync(arq, 'utf8')).split('\n')
    linhas.forEach((linha, i) => {
      for (const regra of REGRAS) {
        regra.re.lastIndex = 0
        const achados = linha.match(regra.re)
        if (achados) {
          falhas++
          console.error(`${rel}:${i + 1}  ${regra.nome}: ${[...new Set(achados)].join(', ')}`)
          console.error(`    → ${regra.dica}`)
        }
      }
    })
  }
}

if (falhas) {
  console.error(`\n${falhas} ocorrência(s) fora da camada de tokens.`)
  process.exit(1)
}
console.log('Cores: tudo por token.')
