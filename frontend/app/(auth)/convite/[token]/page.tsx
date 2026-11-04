'use client'
import { useEffect, useState, type FormEvent } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { useAuth } from '@/contexts/AuthContext'
import { api } from '@/lib/api'
import type { InvitePublic } from '@/types/api'
import { Button } from '@/components/ui/button'
import { Field } from '@/components/ui/field'
import { Input } from '@/components/ui/input'

const SENHA_MINIMA = 8

export default function ConvitePage() {
  const params = useParams<{ token: string }>()
  const router = useRouter()
  const { loginWithTokens } = useAuth()
  const inviteToken = params?.token ?? ''

  const [convite, setConvite] = useState<InvitePublic | null>(null)
  const [carregando, setCarregando] = useState(true)
  const [invalido, setInvalido] = useState(false)

  const [nome, setNome] = useState('')
  const [senha, setSenha] = useState('')
  const [confirma, setConfirma] = useState('')
  const [enviando, setEnviando] = useState(false)
  const [erro, setErro] = useState<string | null>(null)

  useEffect(() => {
    if (!inviteToken) return
    api.invites
      .peek(inviteToken)
      .then((c) => {
        setConvite(c)
        setNome(c.full_name || '')
      })
      .catch(() => setInvalido(true))
      .finally(() => setCarregando(false))
  }, [inviteToken])

  async function aceitar(e: FormEvent) {
    e.preventDefault()
    if (enviando) return
    if (senha.length < SENHA_MINIMA) {
      setErro(`A senha precisa ter ao menos ${SENHA_MINIMA} caracteres.`)
      return
    }
    if (senha !== confirma) {
      setErro('As senhas não conferem.')
      return
    }
    setEnviando(true)
    setErro(null)
    try {
      const tokens = await api.invites.accept(inviteToken, senha, nome.trim() || undefined)
      // O aceite já devolve a sessão — entra direto, sem passar pelo login.
      loginWithTokens(tokens.access_token, tokens.refresh_token)
      router.replace('/inbox')
    } catch (e) {
      setErro((e as Error).message || 'Não foi possível concluir o cadastro.')
      setEnviando(false)
    }
  }

  if (carregando) {
    return <p className="text-13 text-faint">Verificando convite…</p>
  }

  if (invalido || !convite) {
    return (
      <>
        <h1 className="text-xl font-semibold tracking-[-0.01em] text-ink mb-1">Convite inválido</h1>
        <p className="text-13 text-muted">
          Este link já foi usado, expirou ou foi revogado. Peça um novo a quem administra a
          conta.
        </p>
      </>
    )
  }

  return (
    <>
      <h1 className="text-xl font-semibold tracking-[-0.01em] text-ink mb-1">
        Você foi convidado para {convite.tenant_name}
      </h1>
      <p className="text-13 text-muted mb-6">
        Entrando como <strong className="font-medium text-ink">{convite.email}</strong> —{' '}
        {convite.role === 'ADMIN' ? 'administrador' : 'atendente'}. Defina uma senha para criar
        seu acesso.
      </p>

      <form onSubmit={aceitar} className="flex flex-col gap-4">
        <Field label="Seu nome" htmlFor="nome">
          <Input id="nome" autoComplete="name" value={nome} onChange={(e) => setNome(e.target.value)} />
        </Field>
        <Field label="Senha" hint={`mínimo ${SENHA_MINIMA} caracteres`} htmlFor="senha">
          <Input
            id="senha"
            type="password"
            autoComplete="new-password"
            value={senha}
            onChange={(e) => setSenha(e.target.value)}
          />
        </Field>
        <Field label="Repita a senha" htmlFor="confirma">
          <Input
            id="confirma"
            type="password"
            autoComplete="new-password"
            value={confirma}
            onChange={(e) => setConfirma(e.target.value)}
          />
        </Field>

        {erro && (
          <p role="alert" className="text-xs font-medium text-danger-fg">
            {erro}
          </p>
        )}

        <Button type="submit" disabled={enviando} className="w-full mt-2">
          {enviando ? 'Criando acesso…' : 'Criar meu acesso'}
        </Button>
      </form>
    </>
  )
}
