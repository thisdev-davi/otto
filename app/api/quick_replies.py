"""Endpoints de respostas rápidas (canned responses).

Leitura para todo atendente, escrita só para ADMIN — e por isso a guarda é POR ROTA e
não `dependencies` no router: o GET alimenta o autocomplete `/atalho` do campo de
resposta, que é ferramenta de quem atende.

A assimetria não é burocracia. Resposta rápida é copy padrão do negócio (preço, horário,
política de troca), disparada por toda a equipe — mesma classe de risco de editar a
persona da IA, que já é admin-only. É também o que permite a tela viver em Configurações,
seção inteiramente admin-only no backend.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import TokenData, get_current_user, require_admin
from app.core.database import get_session
from app.schemas import (
    QuickReplyInSchema,
    QuickReplyListSchema,
    QuickReplySchema,
)
from app.services import quick_replies

router = APIRouter(prefix="/quick-replies", tags=["quick-replies"])


@router.get("")
async def list_quick_replies(
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> QuickReplyListSchema:
    rows = await quick_replies.list_all(session, current_user.tenant_id)
    return QuickReplyListSchema(quick_replies=[QuickReplySchema(**r) for r in rows])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_quick_reply(
    body: QuickReplyInSchema,
    current_user: Annotated[TokenData, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> QuickReplySchema:
    created = await quick_replies.create(
        session,
        current_user.tenant_id,
        current_user.user_id,
        body.shortcut,
        body.title,
        body.content,
    )
    if created is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Atalho já existe"
        )
    return QuickReplySchema(**created)


@router.put("/{qr_id}")
async def update_quick_reply(
    qr_id: UUID,
    body: QuickReplyInSchema,
    current_user: Annotated[TokenData, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> QuickReplySchema:
    updated = await quick_replies.update(
        session, current_user.tenant_id, qr_id, body.shortcut, body.title, body.content
    )
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return QuickReplySchema(**updated)


@router.delete("/{qr_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_quick_reply(
    qr_id: UUID,
    current_user: Annotated[TokenData, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    deleted = await quick_replies.delete(session, current_user.tenant_id, qr_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
