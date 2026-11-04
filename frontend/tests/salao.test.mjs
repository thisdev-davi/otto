// Regra 4 do CLAUDE.md: quem decide se a data está à venda é o backend (`motivo`).
// O front só traduz — nunca deduz "livre" de `preco`.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { estadoDoDia } from '../lib/salao.ts'

const linha = (extra) => ({ data: '2026-12-12', preco: 150000, reserva: null, ...extra })

test('sem linha é sem cadastro', () => {
  assert.equal(estadoDoDia(undefined), 'sem_cadastro')
})

test('o motivo do backend manda, não o preço', () => {
  assert.equal(estadoDoDia(linha({ motivo: 'bloqueada' })), 'bloqueada')
  assert.equal(estadoDoDia(linha({ motivo: 'passado' })), 'passado')
  assert.equal(estadoDoDia(linha({ motivo: 'ok', preco: null })), 'livre')
  assert.equal(estadoDoDia(linha({ motivo: 'sem_preco', preco: null })), 'sem_preco')
})

test('reserva vem antes do motivo', () => {
  assert.equal(estadoDoDia(linha({ motivo: 'ok', reserva: { status: 'confirmada' } })), 'confirmada')
  assert.equal(
    estadoDoDia(linha({ motivo: 'ok', reserva: { status: 'pre_reservada' } })),
    'pre_reservada',
  )
})

test('motivo desconhecido não vira livre', () => {
  assert.equal(estadoDoDia(linha({ motivo: 'motivo_novo' })), 'sem_cadastro')
})
