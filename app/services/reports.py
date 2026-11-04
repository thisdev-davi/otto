"""Métricas de atendimento — agregações SQL sob demanda por tenant e janela de tempo.

Sem tabela de snapshot: o volume de um pequeno negócio cabe numa varredura indexada
(ix_messages_tenant_created, ix_conversation_events_tenant_created). tenant_id entra
em toda query. Datas agregadas no fuso America/Sao_Paulo.
"""

from datetime import date
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

TZ = "America/Sao_Paulo"


def _params(tenant_id: UUID, date_from: date, date_to: date) -> dict:
    return {"tenant_id": str(tenant_id), "date_from": date_from, "date_to": date_to}


async def overview(
    db: AsyncSession, tenant_id: UUID, date_from: date, date_to: date
) -> dict:
    """KPIs de topo: estado atual das filas + resolução/resposta no período."""
    # Estado atual (não depende da janela).
    now_row = await db.execute(
        text(
            "SELECT "
            "  COUNT(*) FILTER (WHERE status != 'RESOLVED') AS open_count, "
            "  COUNT(*) FILTER (WHERE status != 'RESOLVED' AND assigned_user_id IS NULL) AS unclaimed_count "
            "FROM conversations WHERE tenant_id = :tenant_id"
        ),
        {"tenant_id": str(tenant_id)},
    )
    now = now_row.fetchone()

    # Métricas do período (segundos médios convertidos no SELECT).
    period_row = await db.execute(
        text(
            "SELECT "
            "  COUNT(*) FILTER (WHERE resolved_at IS NOT NULL) AS resolved_count, "
            "  AVG(EXTRACT(EPOCH FROM (resolved_at - created_at))) "
            "    FILTER (WHERE resolved_at IS NOT NULL) AS avg_resolution_seconds, "
            "  AVG(EXTRACT(EPOCH FROM (first_response_at - created_at))) "
            "    FILTER (WHERE first_response_at IS NOT NULL) AS avg_first_response_seconds "
            "FROM conversations "
            "WHERE tenant_id = :tenant_id "
            "  AND created_at::date >= :date_from AND created_at::date <= :date_to"
        ),
        _params(tenant_id, date_from, date_to),
    )
    period = period_row.fetchone()

    def _secs(v):
        return int(v) if v is not None else None

    return {
        "open_count": now.open_count or 0,
        "unclaimed_count": now.unclaimed_count or 0,
        "resolved_count": period.resolved_count or 0,
        "avg_resolution_seconds": _secs(period.avg_resolution_seconds),
        "avg_first_response_seconds": _secs(period.avg_first_response_seconds),
    }


async def volume(
    db: AsyncSession, tenant_id: UUID, date_from: date, date_to: date
) -> list[dict]:
    """Mensagens por dia × canal × direção (para gráfico de barras)."""
    rows = await db.execute(
        text(
            "SELECT (created_at AT TIME ZONE :tz)::date AS day, "
            "  LOWER(channel::text) AS channel, LOWER(direction::text) AS direction, COUNT(*) AS total "
            "FROM messages "
            "WHERE tenant_id = :tenant_id "
            "  AND (created_at AT TIME ZONE :tz)::date >= :date_from "
            "  AND (created_at AT TIME ZONE :tz)::date <= :date_to "
            "GROUP BY day, channel, direction ORDER BY day"
        ),
        {**_params(tenant_id, date_from, date_to), "tz": TZ},
    )
    return [
        {
            "day": r.day.isoformat(),
            "channel": r.channel,
            "direction": r.direction,
            "total": r.total,
        }
        for r in rows.fetchall()
    ]


async def agents(
    db: AsyncSession, tenant_id: UUID, date_from: date, date_to: date
) -> list[dict]:
    """Desempenho por atendente: capturadas, resolvidas, mensagens enviadas."""
    rows = await db.execute(
        text(
            "SELECT u.id AS user_id, u.full_name, u.email, "
            "  COUNT(*) FILTER (WHERE e.action = 'CLAIMED') AS claimed, "
            "  COUNT(*) FILTER (WHERE e.action = 'RESOLVED') AS resolved "
            "FROM users u "
            "LEFT JOIN conversation_events e ON e.actor_user_id = u.id "
            "  AND e.tenant_id = :tenant_id "
            "  AND e.created_at::date >= :date_from AND e.created_at::date <= :date_to "
            "WHERE u.tenant_id = :tenant_id "
            "GROUP BY u.id, u.full_name, u.email "
            "ORDER BY resolved DESC, claimed DESC"
        ),
        _params(tenant_id, date_from, date_to),
    )
    agent_rows = rows.fetchall()

    # Mensagens enviadas por atendente (não-internas, humanas) no período.
    msg_rows = await db.execute(
        text(
            "SELECT sender_user_id, COUNT(*) AS sent FROM messages "
            "WHERE tenant_id = :tenant_id AND direction = 'OUTBOUND' "
            "  AND sender_user_id IS NOT NULL "
            "  AND created_at::date >= :date_from AND created_at::date <= :date_to "
            "GROUP BY sender_user_id"
        ),
        _params(tenant_id, date_from, date_to),
    )
    sent_by_user = {str(r.sender_user_id): r.sent for r in msg_rows.fetchall()}

    return [
        {
            "user_id": str(r.user_id),
            "name": r.full_name or r.email,
            "claimed": r.claimed or 0,
            "resolved": r.resolved or 0,
            "messages_sent": sent_by_user.get(str(r.user_id), 0),
        }
        for r in agent_rows
    ]


async def bot(
    db: AsyncSession, tenant_id: UUID, date_from: date, date_to: date
) -> dict:
    """Atuação do agente de IA: conversas triadas, encaminhamentos e falhas no período."""
    row = await db.execute(
        text(
            "SELECT "
            "  COUNT(*) AS triaged, "
            "  COUNT(*) FILTER (WHERE handoff) AS handoffs, "
            "  COUNT(*) FILTER (WHERE bot_disabled AND NOT handoff) AS failures "
            "FROM bot_sessions "
            "WHERE tenant_id = :tenant_id "
            "  AND updated_at::date >= :date_from AND updated_at::date <= :date_to"
        ),
        _params(tenant_id, date_from, date_to),
    )
    r = row.fetchone()

    # Conversas resolvidas sem nenhum humano ter assumido (bot resolveu sozinho).
    solo_row = await db.execute(
        text(
            "SELECT COUNT(*) AS resolved_by_bot FROM conversations c "
            "JOIN bot_sessions b ON b.conversation_id = c.id "
            "WHERE c.tenant_id = :tenant_id AND c.resolved_at IS NOT NULL "
            "  AND c.status = 'RESOLVED' AND c.closure_reason = 'bot' "
            "  AND NOT EXISTS (SELECT 1 FROM conversation_events e "
            "    WHERE e.conversation_id=c.id AND e.tenant_id=c.tenant_id "
            "      AND e.action='CLAIMED') "
            "  AND c.resolved_at::date >= :date_from AND c.resolved_at::date <= :date_to"
        ),
        _params(tenant_id, date_from, date_to),
    )
    solo = solo_row.fetchone()

    inactive = await db.scalar(
        text(
            "SELECT count(*) FROM conversations WHERE tenant_id=:tenant_id "
            "AND status='RESOLVED' AND closure_reason='inactivity' "
            "AND resolved_at::date BETWEEN :date_from AND :date_to"
        ),
        _params(tenant_id, date_from, date_to),
    )
    return {
        "closed_by_inactivity": inactive or 0,
        "triaged": r.triaged or 0,
        "handoffs": r.handoffs or 0,
        "failures": r.failures or 0,
        "resolved_by_bot": solo.resolved_by_bot or 0,
    }
