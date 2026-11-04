#!/usr/bin/env python3
"""Cria a ChannelConnection de teste do WhatsApp. Idempotente — pode rodar várias vezes."""

import asyncio
import os
import uuid
from sqlalchemy import text
from app.core.database import AsyncSessionLocal

TENANT_NAME = "Otto Demo"


async def seed_channels() -> None:
    evolution_instance: str = os.environ.get("EVOLUTION_INSTANCE", "otto-test")

    async with AsyncSessionLocal() as session:
        # Busca tenant
        row = await session.execute(
            text("SELECT id FROM tenants WHERE name = :name LIMIT 1"),
            {"name": TENANT_NAME},
        )
        tenant = row.fetchone()
        if not tenant:
            print("Tenant 'Otto Demo' não encontrado. Rode seed.py primeiro.")
            return

        tenant_id: str = str(tenant.id)

        # ── WhatsApp ───────────────────────────────────────────────────────────
        row = await session.execute(
            text(
                "SELECT id FROM channel_connections"
                " WHERE tenant_id = :tid AND channel = 'WHATSAPP' LIMIT 1"
            ),
            {"tid": tenant_id},
        )
        existing_wa = row.fetchone()
        if existing_wa:
            print(f"ChannelConnection WhatsApp já existe: {existing_wa.id}")
        else:
            wa_id = str(uuid.uuid4())
            await session.execute(
                text(
                    "INSERT INTO channel_connections (id, tenant_id, channel, credentials, is_active)"
                    " VALUES (:id, :tid, 'WHATSAPP', CAST(:creds AS jsonb), true)"
                ),
                {
                    "id": wa_id,
                    "tid": tenant_id,
                    "creds": f'{{"instance": "{evolution_instance}"}}',
                },
            )
            print(f"ChannelConnection WhatsApp criado: {wa_id}")

        await session.commit()


asyncio.run(seed_channels())
