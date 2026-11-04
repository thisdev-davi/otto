#!/usr/bin/env python3
"""Cria uma conta nova: tenant + primeiro administrador.

Este é o único caminho de provisionamento do Otto. Não há rota HTTP que crie Tenant:
o convite por link (/equipe) só faz crescer uma conta que já existe, então o primeiro
usuário de cada negócio nasce aqui.

    python3 scripts/criar_conta.py --nome "Clínica Pet Vida" --email dono@petvida.com

Sem --senha, gera uma forte e imprime. NÃO é destrutivo: se o tenant ou o e-mail já
existirem, aborta sem tocar em nada (ao contrário de seed_test_account.py, que apaga
e recria de propósito). Rodar duas vezes por engano não apaga a conta de um cliente.
"""

import argparse
import asyncio
import secrets
import string
import sys
import uuid
from pathlib import Path

import bcrypt
from sqlalchemy import text

# Este script vive em scripts/, então a raiz do projeto não entra no sys.path sozinha
# (ao contrário de seed.py, que está na raiz). Sem isto, `python3 scripts/criar_conta.py`
# quebra com ModuleNotFoundError: no module named 'app'.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database import AsyncSessionLocal  # noqa: E402

# Mesmo mínimo que services/team.py exige de quem aceita convite.
MIN_SENHA = 8


def gerar_senha(tamanho: int = 14) -> str:
    """Senha aleatória legível: sem caracteres que se confundem ao ditar por telefone."""
    alfabeto = (string.ascii_letters + string.digits).translate(
        str.maketrans("", "", "lIO01")
    )
    return "".join(secrets.choice(alfabeto) for _ in range(tamanho))


def normalizar_email(email: str) -> str:
    return email.strip().lower()


async def criar_conta(
    nome_tenant: str, email: str, senha: str, nome_completo: str
) -> int:
    async with AsyncSessionLocal() as db:
        existente = (
            await db.execute(
                text("SELECT id FROM tenants WHERE lower(name) = lower(:n) LIMIT 1"),
                {"n": nome_tenant},
            )
        ).fetchone()
        if existente:
            print(
                f"ERRO: já existe uma conta chamada {nome_tenant!r} ({existente.id}).",
                file=sys.stderr,
            )
            print(
                "      Para adicionar alguém a ela, use o convite por link em "
                "Configurações › Equipe.",
                file=sys.stderr,
            )
            return 1

        # A unicidade no banco é (tenant_id, email), então este e-mail poderia existir
        # em OUTRA conta sem conflitar. Checamos global mesmo assim: o login busca por
        # e-mail sem filtrar tenant (app/api/auth.py), então dois usuários com o mesmo
        # e-mail em contas diferentes derrubariam o acesso de um deles.
        duplicado = (
            await db.execute(
                text(
                    "SELECT u.id, t.name AS tenant FROM users u "
                    "JOIN tenants t ON t.id = u.tenant_id WHERE u.email = :e LIMIT 1"
                ),
                {"e": email},
            )
        ).fetchone()
        if duplicado:
            print(
                f"ERRO: o e-mail {email} já é usado na conta {duplicado.tenant!r}.",
                file=sys.stderr,
            )
            print(
                "      O login busca por e-mail sem distinguir a conta, então reusá-lo "
                "trancaria um dos dois. Use outro e-mail.",
                file=sys.stderr,
            )
            return 1

        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        hashed = bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()

        await db.execute(
            text("INSERT INTO tenants (id, name) VALUES (:i, :n)"),
            {"i": str(tenant_id), "n": nome_tenant},
        )
        await db.execute(
            # role ADMIN explícito e MAIÚSCULO: a coluna tem default AGENT e o enum no
            # PostgreSQL guarda o NOME do membro, não o valor. Sem isto o dono nasce
            # sem acesso a equipe, IA, relatórios e canais — uma conta sem nenhum
            # administrador, que não se conserta nem por dentro.
            text(
                "INSERT INTO users (id, tenant_id, email, hashed_password, full_name, "
                "role, is_active) VALUES (:i, :t, :e, :p, :n, 'ADMIN', true)"
            ),
            {
                "i": str(user_id),
                "t": str(tenant_id),
                "e": email,
                "p": hashed,
                "n": nome_completo,
            },
        )
        await db.commit()

    print("\n── Conta criada ──────────────────────────────")
    print(f"  Empresa : {nome_tenant}")
    print(f"  E-mail  : {email}")
    print(f"  Senha   : {senha}")
    print("  Papel   : ADMIN")
    print("──────────────────────────────────────────────")
    print("Entregue estas credenciais ao dono e peça para ele trocar a senha.")
    print("Ele convida o resto da equipe em Configurações › Equipe.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Cria um tenant e seu primeiro administrador.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--nome", required=True, help="nome da empresa (tenant)")
    p.add_argument("--email", required=True, help="e-mail de login do administrador")
    p.add_argument("--senha", help=f"senha (mín. {MIN_SENHA}); omitida = gerada")
    p.add_argument(
        "--nome-completo", help="nome da pessoa (default: o nome da empresa)"
    )
    args = p.parse_args()

    nome_tenant = args.nome.strip()
    email = normalizar_email(args.email)
    if not nome_tenant:
        print("ERRO: --nome não pode ser vazio.", file=sys.stderr)
        return 1
    if "@" not in email or email.startswith("@") or email.endswith("@"):
        print(f"ERRO: {args.email!r} não parece um e-mail.", file=sys.stderr)
        return 1

    senha = args.senha or gerar_senha()
    if len(senha) < MIN_SENHA:
        print(
            f"ERRO: a senha precisa de pelo menos {MIN_SENHA} caracteres.",
            file=sys.stderr,
        )
        return 1

    return asyncio.run(
        criar_conta(nome_tenant, email, senha, args.nome_completo or nome_tenant)
    )


if __name__ == "__main__":
    raise SystemExit(main())
