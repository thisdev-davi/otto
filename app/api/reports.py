"""Endpoints de relatórios (dashboard) e listagem de usuários do tenant."""

from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import TokenData, get_current_user, require_admin
from app.core.database import get_session
from app.schemas import (
    AgentStatSchema,
    ReportAgentsSchema,
    ReportBotSchema,
    ReportOverviewSchema,
    ReportVolumeSchema,
    UserListSchema,
    UserSchema,
    VolumePointSchema,
)
from app.services import reports

# Dois routers de propósito. Relatório é admin-only (a equipe não vê o desempenho de
# uns aos outros), mas /users NÃO pode entrar nessa guarda: listar os colegas é o que
# alimenta o seletor de transferência, e transferir conversa não é gerenciar equipe.
router = APIRouter(tags=["reports"], dependencies=[Depends(require_admin)])
users_router = APIRouter(tags=["users"])


def _window(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    """Janela padrão: últimos 30 dias (inclusive)."""
    to = date_to or date.today()
    frm = date_from or (to - timedelta(days=29))
    return frm, to


DateFrom = Annotated[date | None, Query()]
DateTo = Annotated[date | None, Query()]


@router.get("/reports/overview")
async def report_overview(
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    date_from: DateFrom = None,
    date_to: DateTo = None,
) -> ReportOverviewSchema:
    frm, to = _window(date_from, date_to)
    data = await reports.overview(session, current_user.tenant_id, frm, to)
    return ReportOverviewSchema(**data)


@router.get("/reports/volume")
async def report_volume(
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    date_from: DateFrom = None,
    date_to: DateTo = None,
) -> ReportVolumeSchema:
    frm, to = _window(date_from, date_to)
    points = await reports.volume(session, current_user.tenant_id, frm, to)
    return ReportVolumeSchema(points=[VolumePointSchema(**p) for p in points])


@router.get("/reports/agents")
async def report_agents(
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    date_from: DateFrom = None,
    date_to: DateTo = None,
) -> ReportAgentsSchema:
    frm, to = _window(date_from, date_to)
    rows = await reports.agents(session, current_user.tenant_id, frm, to)
    return ReportAgentsSchema(agents=[AgentStatSchema(**a) for a in rows])


@router.get("/reports/bot")
async def report_bot(
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    date_from: DateFrom = None,
    date_to: DateTo = None,
) -> ReportBotSchema:
    frm, to = _window(date_from, date_to)
    data = await reports.bot(session, current_user.tenant_id, frm, to)
    return ReportBotSchema(**data)


@users_router.get("/users/me")
async def get_me(
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserSchema:
    """Quem sou eu, com o papel — é o que o front usa para montar a navegação.

    Existe porque o Sidebar baixava a lista inteira de usuários só para achar o próprio
    nome: uma requisição que devolve a equipe toda para exibir um chip.
    """
    row = await session.execute(
        text(
            "SELECT id, full_name, email, role FROM users "
            "WHERE id = :user_id AND tenant_id = :tenant_id"
        ),
        {
            "user_id": str(current_user.user_id),
            "tenant_id": str(current_user.tenant_id),
        },
    )
    r = row.fetchone()
    if r is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado"
        )
    return UserSchema(id=r.id, full_name=r.full_name, email=r.email, role=r.role)


@users_router.get("/users")
async def list_users(
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserListSchema:
    """Usuários (atendentes) do tenant — usado no dashboard e em transferências."""
    rows = await session.execute(
        text(
            "SELECT id, full_name, email, role FROM users "
            "WHERE tenant_id = :tenant_id AND is_active = true ORDER BY full_name NULLS LAST, email"
        ),
        {"tenant_id": str(current_user.tenant_id)},
    )
    return UserListSchema(
        users=[
            UserSchema(id=r.id, full_name=r.full_name, email=r.email, role=r.role)
            for r in rows.fetchall()
        ]
    )
