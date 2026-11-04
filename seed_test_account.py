#!/usr/bin/env python3
"""Cria uma conta de teste completa com dados de exemplo para explorar o sistema.

Login: teste@otto.com / teste1234   (tenant "Otto Teste")
Idempotente: apaga e recria o tenant 'Otto Teste' a cada execução.
"""

import asyncio
import json
import uuid

import bcrypt
from sqlalchemy import text

from app.core.database import AsyncSessionLocal

TENANT = "Otto Teste"
EMAIL = "teste@otto.com"
PASSWORD = "teste1234"


async def seed() -> None:
    async with AsyncSessionLocal() as db:
        # ── zera o tenant de teste (recomeça limpo) ─────────────────────────────
        old = (
            await db.execute(
                text("SELECT id FROM tenants WHERE name = :n"), {"n": TENANT}
            )
        ).fetchall()
        for row in old:
            tid = str(row.id)
            for tb in (
                "messages",
                "bot_sessions",
                "conversation_events",
                "quick_replies",
                "conversations",
                "contacts",
                "channel_connections",
                "bot_settings",
                "users",
            ):
                await db.execute(
                    text(f"DELETE FROM {tb} WHERE tenant_id = :t"), {"t": tid}
                )
            await db.execute(text("DELETE FROM tenants WHERE id = :t"), {"t": tid})
        await db.commit()

        tenant_id = uuid.uuid4()
        user_id = uuid.uuid4()
        conn_id = uuid.uuid4()
        pw = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode()

        await db.execute(
            text("INSERT INTO tenants (id, name) VALUES (:i, :n)"),
            {"i": str(tenant_id), "n": TENANT},
        )
        await db.execute(
            # role ADMIN explícito: a coluna tem default AGENT, e sem isto o dono da
            # conta de teste nasceria sem acesso a equipe, IA, relatórios e canais —
            # um tenant sem nenhum administrador, que nem por dentro se conserta.
            text(
                "INSERT INTO users (id, tenant_id, email, hashed_password, full_name, role, is_active) "
                "VALUES (:i, :t, :e, :p, :n, 'ADMIN', true)"
            ),
            {
                "i": str(user_id),
                "t": str(tenant_id),
                "e": EMAIL,
                "p": pw,
                "n": "Você (teste)",
            },
        )
        await db.execute(
            text(
                "INSERT INTO channel_connections (id, tenant_id, channel, credentials, is_active) "
                "VALUES (:i, :t, 'WHATSAPP', CAST(:c AS jsonb), true)"
            ),
            {"i": str(conn_id), "t": str(tenant_id), "c": '{"instance": "otto-teste"}'},
        )

        # ── contatos + conversas de exemplo ─────────────────────────────────────
        # (contato, dias_atras, capturada?, resolvida?, prioridade, mensagens[(dir, texto)])
        cenarios = [
            (
                "Mariana Alves",
                "5511990001111",
                0,
                False,
                False,
                "normal",
                [
                    (
                        "in",
                        "Oi! Vi o anúncio de vocês, ainda tem vaga pra essa semana?",
                    ),
                ],
            ),
            (
                "Carlos Souza",
                "5511990002222",
                0,
                True,
                False,
                "high",
                [
                    ("in", "Preciso de ajuda urgente com meu pedido"),
                    (
                        "out",
                        "Claro, Carlos! Já estou olhando aqui. Qual o número do pedido?",
                    ),
                    ("in", "É o 4821"),
                ],
            ),
            (
                "Fernanda Lima",
                "5511990003333",
                1,
                True,
                False,
                "normal",
                [
                    ("in", "Bom dia, queria saber o valor da consulta"),
                    (
                        "out",
                        "Bom dia, Fernanda! A consulta fica R$ 120. Posso te ajudar a agendar?",
                    ),
                    ("in", "Perfeito, pode ser sexta"),
                ],
            ),
            (
                "João Pedro",
                "5511990004444",
                2,
                True,
                True,
                "normal",
                [
                    ("in", "Obrigado pelo atendimento de ontem!"),
                    ("out", "Nós que agradecemos, João! Qualquer coisa é só chamar 😊"),
                ],
            ),
            (
                "Beatriz Rocha",
                "5511990005555",
                0,
                False,
                False,
                "normal",
                [
                    ("in", "vocês entregam no centro?"),
                ],
            ),
        ]

        for nome, phone, dias, capturada, resolvida, prio, msgs in cenarios:
            contact_id = uuid.uuid4()
            conv_id = uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO contacts (id, tenant_id, channel, external_id, name, phone) "
                    "VALUES (:i, :t, 'WHATSAPP', :e, :n, :ph)"
                ),
                {
                    "i": str(contact_id),
                    "t": str(tenant_id),
                    "e": f"{phone}@s.whatsapp.net",
                    "n": nome,
                    "ph": phone,
                },
            )
            last = msgs[-1][1]
            await db.execute(
                text(
                    "INSERT INTO conversations "
                    "(id, tenant_id, contact_id, channel_connection_id, status, assigned_user_id, "
                    " priority, tags, first_response_at, resolved_at, last_message_at, last_message_preview, "
                    " unread_count, created_at) "
                    "VALUES (:i, :t, :c, :cc, :st, :au, :pr, CAST(:tg AS jsonb), :fr, :rv, "
                    " now() - (:d || ' days')::interval + interval '2 hours', :prev, :un, "
                    " now() - (:d || ' days')::interval)"
                ),
                {
                    "i": str(conv_id),
                    "t": str(tenant_id),
                    "c": str(contact_id),
                    "cc": str(conn_id),
                    "st": "RESOLVED"
                    if resolvida
                    else ("PENDING" if capturada else "OPEN"),
                    "au": str(user_id) if capturada else None,
                    "pr": prio,
                    "tg": json.dumps(["prioridade_alta"] if prio == "high" else []),
                    "fr": None,
                    "rv": None,
                    "d": str(dias),
                    "prev": last[:120],
                    "un": 0 if capturada else sum(1 for m in msgs if m[0] == "in"),
                },
            )
            # first_response_at / resolved_at coerentes
            if any(m[0] == "out" for m in msgs):
                await db.execute(
                    text(
                        "UPDATE conversations SET first_response_at = created_at + interval '4 minutes' WHERE id=:i"
                    ),
                    {"i": str(conv_id)},
                )
            if resolvida:
                await db.execute(
                    text(
                        "UPDATE conversations SET resolved_at = created_at + interval '35 minutes' WHERE id=:i"
                    ),
                    {"i": str(conv_id)},
                )
                await db.execute(
                    text(
                        "INSERT INTO conversation_events (id, conversation_id, tenant_id, action, actor_user_id, created_at) "
                        "VALUES (gen_random_uuid(), :c, :t, 'RESOLVED', :u, now() - (:d||' days')::interval)"
                    ),
                    {
                        "c": str(conv_id),
                        "t": str(tenant_id),
                        "u": str(user_id),
                        "d": str(dias),
                    },
                )
            if capturada:
                await db.execute(
                    text(
                        "INSERT INTO conversation_events (id, conversation_id, tenant_id, action, actor_user_id, created_at) "
                        "VALUES (gen_random_uuid(), :c, :t, 'CLAIMED', :u, now() - (:d||' days')::interval)"
                    ),
                    {
                        "c": str(conv_id),
                        "t": str(tenant_id),
                        "u": str(user_id),
                        "d": str(dias),
                    },
                )
            for j, (direction, texto) in enumerate(msgs):
                await db.execute(
                    text(
                        "INSERT INTO messages (id, conversation_id, tenant_id, direction, content_type, content, "
                        " channel, external_id, status, sender_user_id, created_at) "
                        "VALUES (gen_random_uuid(), :c, :t, :dir, 'TEXT', :txt, 'WHATSAPP', :ext, :stt, :snd, "
                        " now() - (:d||' days')::interval + (:j || ' minutes')::interval)"
                    ),
                    {
                        "c": str(conv_id),
                        "t": str(tenant_id),
                        "dir": "INBOUND" if direction == "in" else "OUTBOUND",
                        "txt": texto,
                        "ext": str(uuid.uuid4()),
                        "stt": None if direction == "in" else "READ",
                        "snd": str(user_id) if direction == "out" else None,
                        "d": str(dias),
                        "j": str(j),
                    },
                )

        # ── respostas rápidas de exemplo ────────────────────────────────────────
        for sc, ti, co in [
            (
                "horario",
                "Horário de atendimento",
                "Nosso horário é de segunda a sexta, das 9h às 18h. 😊",
            ),
            (
                "obrigado",
                "Agradecimento",
                "Obrigado pelo contato! Precisando, é só chamar.",
            ),
            (
                "pix",
                "Pagamento via PIX",
                "Nossa chave PIX é contato@empresa.com. Assim que cair, confirmo por aqui!",
            ),
        ]:
            await db.execute(
                text(
                    "INSERT INTO quick_replies (id, tenant_id, shortcut, title, content, created_by) "
                    "VALUES (gen_random_uuid(), :t, :s, :ti, :c, :u)"
                ),
                {"t": str(tenant_id), "s": sc, "ti": ti, "c": co, "u": str(user_id)},
            )

        # ── bot desligado por padrão (você liga/ninclui no painel) ──────────────
        await db.execute(
            text(
                "INSERT INTO bot_settings (tenant_id, ai_enabled, provider) VALUES (:t, false, 'stub')"
            ),
            {"t": str(tenant_id)},
        )

        await db.commit()
        print("Conta de teste criada!")
        print(f"  Login: {EMAIL}")
        print(f"  Senha: {PASSWORD}")
        print(
            "  5 conversas de exemplo (não atribuídas, capturadas, 1 resolvida, 1 prioridade alta)"
        )
        print("  3 respostas rápidas (/horario, /obrigado, /pix)")
        print("  bot desligado (ligue e ninche em Configurações > Agente de IA)")


asyncio.run(seed())
