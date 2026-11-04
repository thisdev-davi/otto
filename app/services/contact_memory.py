"""Memória de cliente entre conversas (contacts.profile).

O contexto da IA é carregado por `conversation_id` (ai_context.load_history) e a ingestão
só reaproveita conversa com `status != 'RESOLVED'` (services/ingestion.py). Logo, cliente
que volta depois de uma conversa resolvida chega numa conversa NOVA e é atendido como
desconhecido — repetindo perguntas que já respondeu.

Este módulo guarda no contato o que sobrevive à conversa:

    {
      "intake": {"nome": "Gui", "tipo_de_evento": "aniversário 15 anos"},
      "last_summary": "resumo da última triagem, gerado pela própria IA no handoff",
      "last_interaction_at": "2026-07-26T23:11:00+00:00",
      "conversations": 3
    }

Estruturado, e não as mensagens antigas: transcrição custa tokens em TODA mensagem e
arrasta negociação velha para dentro da conversa nova. O resumo já é produzido de graça
pelo agente no handoff — aqui ele deixa de morrer na nota da conversa.
"""

import json
import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger("uvicorn.error")


async def load(db: AsyncSession, tenant_id: UUID, contact_id: UUID) -> dict:
    """Perfil do contato. Dicionário vazio quando é cliente novo."""
    row = await db.execute(
        text(
            "SELECT profile FROM contacts WHERE id = :contact_id AND tenant_id = :tenant_id"
        ),
        {"contact_id": str(contact_id), "tenant_id": str(tenant_id)},
    )
    r = row.fetchone()
    return dict(r.profile or {}) if r is not None else {}


async def remember(
    db: AsyncSession,
    tenant_id: UUID,
    contact_id: UUID,
    captured: dict | None = None,
    summary: str | None = None,
) -> dict:
    """Mescla o que a IA coletou no perfil do contato. Devolve o perfil resultante.

    MESCLA, não substitui: o `captured` de um turno é o que a IA enxergou naquela
    conversa, e sobrescrever apagaria o que ela soube em atendimentos anteriores. Dado
    novo vence o antigo com a mesma chave; chave que não veio agora permanece.

    Best-effort: perder memória não pode derrubar o atendimento em curso.
    """
    try:
        perfil = await load(db, tenant_id, contact_id)
        intake = dict(perfil.get("intake") or {})
        # Valor vazio não apaga o que já se sabia — a IA às vezes devolve "" num turno.
        intake.update({k: v for k, v in (captured or {}).items() if str(v).strip()})

        perfil["intake"] = intake
        if summary and summary.strip():
            perfil["last_summary"] = summary.strip()
        perfil["last_interaction_at"] = datetime.now(timezone.utc).isoformat()

        await db.execute(
            text(
                "UPDATE contacts SET profile = CAST(:profile AS jsonb) "
                "WHERE id = :contact_id AND tenant_id = :tenant_id"
            ),
            {
                "profile": json.dumps(perfil, ensure_ascii=False),
                "contact_id": str(contact_id),
                "tenant_id": str(tenant_id),
            },
        )
        return perfil
    except Exception:
        log.exception(
            "contact_memory: falha ao gravar perfil do contato %s", contact_id
        )
        return {}


async def count_conversation(
    db: AsyncSession, tenant_id: UUID, contact_id: UUID
) -> None:
    """Incrementa o contador de atendimentos do contato. Chamado ao criar conversa."""
    try:
        await db.execute(
            text(
                "UPDATE contacts SET profile = jsonb_set(profile, '{conversations}', "
                "  to_jsonb(COALESCE((profile->>'conversations')::int, 0) + 1)) "
                "WHERE id = :contact_id AND tenant_id = :tenant_id"
            ),
            {"contact_id": str(contact_id), "tenant_id": str(tenant_id)},
        )
    except Exception:
        log.exception(
            "contact_memory: falha ao contar conversa do contato %s", contact_id
        )


def is_returning(perfil: dict) -> bool:
    """True quando já houve atendimento anterior com dado aproveitável."""
    return bool(perfil.get("intake") or perfil.get("last_summary"))
