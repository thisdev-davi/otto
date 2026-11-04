#!/usr/bin/env python3
"""Cria tenant e usuário admin de teste. Idempotente — pode rodar várias vezes."""

import asyncio
import uuid
import bcrypt
from sqlalchemy import text
from app.core.database import AsyncSessionLocal


EMAIL = "admin@otto.dev"
PASSWORD = "otto1234"
TENANT_NAME = "Otto Demo"


async def seed():
    async with AsyncSessionLocal() as session:
        # Tenant
        row = await session.execute(
            text("SELECT id FROM tenants WHERE name = :name LIMIT 1"),
            {"name": TENANT_NAME},
        )
        tenant = row.fetchone()
        if tenant:
            tenant_id = tenant.id
            print(f"Tenant já existe: {tenant_id}")
        else:
            tenant_id = uuid.uuid4()
            await session.execute(
                text("INSERT INTO tenants (id, name) VALUES (:id, :name)"),
                {"id": str(tenant_id), "name": TENANT_NAME},
            )
            print(f"Tenant criado: {tenant_id}")

        # Usuário
        row = await session.execute(
            text(
                "SELECT id FROM users WHERE email = :email AND tenant_id = :tid LIMIT 1"
            ),
            {"email": EMAIL, "tid": str(tenant_id)},
        )
        user = row.fetchone()
        if user:
            print(f"Usuário já existe: {EMAIL}")
        else:
            hashed = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode()
            await session.execute(
                # role ADMIN explícito e MAIÚSCULO (o enum no PostgreSQL guarda o NOME
                # do membro). Sem isto o INSERT cai no default AGENT e este usuário —
                # o único da conta — nasce sem acesso a equipe, IA, relatórios e canais.
                text(
                    "INSERT INTO users (id, tenant_id, email, hashed_password, full_name, role, is_active)"
                    " VALUES (:id, :tid, :email, :pwd, :name, 'ADMIN', true)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "tid": str(tenant_id),
                    "email": EMAIL,
                    "pwd": hashed,
                    "name": "Admin",
                },
            )
            print(f"Usuário criado: {EMAIL}")

        await session.commit()

    print("\n── Credenciais de teste ──────────────")
    print(f"  E-mail : {EMAIL}")
    print(f"  Senha  : {PASSWORD}")
    print(f"  Tenant : {TENANT_NAME}")
    print("─────────────────────────────────────")


asyncio.run(seed())
