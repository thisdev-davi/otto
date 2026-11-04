"""Endpoints de equipe: convites por link e administração de membros.

Dois routers, porque a fronteira de acesso é diferente:
- `router` (/users/...) exige ADMIN;
- `public_router` (/invites/...) NÃO exige token — quem aceita um convite ainda não tem
  conta. O token do link é a credencial, e ele é de uso único e expira em 7 dias.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import TokenData, TokenResponse, _tokens, require_admin
from app.core.database import get_session
from app.models.enums import UserRole
from app.schemas import (
    InviteAcceptSchema,
    InviteCreatedSchema,
    InviteCreateSchema,
    InviteListSchema,
    InvitePublicSchema,
    InviteSchema,
    MemberListSchema,
    MemberSchema,
    MemberUpdateSchema,
)
from app.services import team

router = APIRouter(
    prefix="/users", tags=["team"], dependencies=[Depends(require_admin)]
)
public_router = APIRouter(prefix="/invites", tags=["team"])

# Erro de regra → status HTTP. Fora daqui tudo vira 400.
_STATUS = {
    "nao_encontrado": status.HTTP_404_NOT_FOUND,
    "convite_invalido": status.HTTP_404_NOT_FOUND,
    "ja_e_membro": status.HTTP_409_CONFLICT,
    "email_em_uso": status.HTTP_409_CONFLICT,
    "ultimo_admin": status.HTTP_409_CONFLICT,
}


def _http(exc: team.TeamError) -> HTTPException:
    return HTTPException(
        status_code=_STATUS.get(exc.code, status.HTTP_400_BAD_REQUEST),
        detail=str(exc),
    )


@router.get("/members")
async def list_members(
    current_user: Annotated[TokenData, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MemberListSchema:
    membros = await team.list_members(session, current_user.tenant_id)
    return MemberListSchema(members=[MemberSchema(**m) for m in membros])


@router.patch("/members/{user_id}")
async def update_member(
    user_id: UUID,
    body: MemberUpdateSchema,
    current_user: Annotated[TokenData, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MemberSchema:
    """Promove/rebaixa ou ativa/desativa um membro."""
    try:
        membro = await team.update_member(
            session,
            current_user.tenant_id,
            current_user.user_id,
            user_id,
            role=UserRole[body.role] if body.role else None,
            is_active=body.is_active,
        )
    except team.TeamError as exc:
        raise _http(exc)
    return MemberSchema(**membro)


@router.get("/invites")
async def list_invites(
    current_user: Annotated[TokenData, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> InviteListSchema:
    convites = await team.list_invites(session, current_user.tenant_id)
    return InviteListSchema(invites=[InviteSchema(**c) for c in convites])


@router.post("/invites", status_code=status.HTTP_201_CREATED)
async def create_invite(
    body: InviteCreateSchema,
    current_user: Annotated[TokenData, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> InviteCreatedSchema:
    """Cria o convite e devolve o token em claro — é a única vez que ele existe fora do hash."""
    try:
        convite, token = await team.create_invite(
            session,
            current_user.tenant_id,
            current_user.user_id,
            body.email,
            body.full_name,
            UserRole[body.role],
        )
    except team.TeamError as exc:
        raise _http(exc)
    return InviteCreatedSchema(invite=InviteSchema(**convite), token=token)


@router.delete("/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(
    invite_id: UUID,
    current_user: Annotated[TokenData, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    if not await team.revoke_invite(session, current_user.tenant_id, invite_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Convite não encontrado"
        )


# ─────────────────────── público (sem autenticação) ───────────────────────


@public_router.get("/{token}")
async def peek_invite(
    token: str,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> InvitePublicSchema:
    """Valida o link e diz em qual negócio a pessoa está entrando."""
    convite = await team.peek_invite(session, token)
    if convite is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Convite inválido, expirado ou já usado.",
        )
    return InvitePublicSchema(**convite)


@public_router.post("/{token}/accept")
async def accept_invite(
    token: str,
    body: InviteAcceptSchema,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenResponse:
    """Cria a conta e já devolve a sessão — sem passar pela tela de login."""
    try:
        criado = await team.accept_invite(session, token, body.password, body.full_name)
    except team.TeamError as exc:
        raise _http(exc)
    return _tokens(criado["tenant_id"], criado["user_id"])
