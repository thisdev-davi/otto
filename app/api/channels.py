import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import TokenData, get_current_user, require_admin
from app.channels.factory import get_provider
from app.core.database import get_session
from app.models.enums import Channel
from app.schemas import (
    ChannelConnectionListSchema,
    ChannelConnectionSchema,
    ChannelStatusSchema,
    CreateWhatsAppConnectionSchema,
)
from app.services import channels as channels_service

log = logging.getLogger("uvicorn.error")

# Admin-only no router inteiro: parear ou remover a instância derruba o WhatsApp do
# negócio todo. O Sidebar não consome estes endpoints (usa CHANNEL_META estático), então
# trancar aqui não quebra a navegação do atendente — só a página de Configurações.
router = APIRouter(
    prefix="/channels", tags=["channels"], dependencies=[Depends(require_admin)]
)


@router.get("")
async def list_channels(
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChannelConnectionListSchema:
    rows = await channels_service.list_connections(session, current_user.tenant_id)
    return ChannelConnectionListSchema(
        connections=[ChannelConnectionSchema.model_validate(r) for r in rows]
    )


@router.post("/whatsapp", status_code=status.HTTP_201_CREATED)
async def connect_whatsapp(
    body: CreateWhatsAppConnectionSchema,
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChannelConnectionSchema:
    # O formato já foi validado pelo schema (`^[A-Za-z0-9_-]{3,64}$`), que é o que impede
    # este valor de virar outra rota na API do Evolution. Aqui não cabe mais checagem.
    instance = body.instance

    if await channels_service.whatsapp_instance_exists(session, instance):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Já existe uma conexão com essa instância",
        )

    provider = get_provider(Channel.WHATSAPP)
    try:
        credentials = await provider.provision({"instance": instance})
    except Exception as exc:  # falha de provisionamento no Evolution
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Falha ao provisionar: {exc}",
        )

    created = await channels_service.create_connection(
        session, current_user.tenant_id, Channel.WHATSAPP, credentials
    )
    return ChannelConnectionSchema(
        id=created["id"],
        channel=Channel.WHATSAPP,
        label=instance,
        is_active=True,
        created_at=created["created_at"],
    )


@router.get("/{connection_id}/status")
async def channel_status(
    connection_id: UUID,
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChannelStatusSchema:
    found = await channels_service.get_connection_credentials(
        session, current_user.tenant_id, connection_id
    )
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    channel, credentials = found
    provider = get_provider(channel)
    result = await provider.get_status(credentials)
    return ChannelStatusSchema(
        state=result.state, qr_code=result.qr_code, detail=result.detail
    )


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(
    connection_id: UUID,
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    found = await channels_service.get_connection_credentials(
        session, current_user.tenant_id, connection_id
    )
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    channel, credentials = found
    provider = get_provider(channel)
    try:
        await provider.deprovision(credentials)
    except Exception:
        # Best-effort: a conexão local sai de qualquer forma, mas a instância que sobrou
        # no provider precisa aparecer no log para ser removida à mão.
        log.warning(
            "deprovision falhou para a conexão %s", connection_id, exc_info=True
        )
    await channels_service.delete_connection(
        session, current_user.tenant_id, connection_id
    )
