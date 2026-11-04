import json
import logging
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import TokenData, get_current_user
from app.channels.factory import get_provider
from app.channels.types import OutboundMedia, OutboundMessage
from app.core.database import get_session
from app.models.enums import (
    Channel,
    ContentType,
    MessageDirection,
    MessageStatus,
)
from app.schemas import (
    ConversationDetailSchema,
    ConversationListSchema,
    ConversationSchema,
    MessageSchema,
    SendMessageSchema,
    StatusUpdateSchema,
    SuggestReplySchema,
    TransferRequestSchema,
)
from app.services import (
    assignment,
    attachments,
    copilot,
    events,
    inbox_queries,
    media_links,
)
from app.services.channels import JOIN_CONEXAO
from app.services.previews import preview_for

log = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/conversations", tags=["inbox"])


async def _rota_de_envio(session: AsyncSession, tenant_id: UUID, conversation_id: UUID):
    """Conversa + contato + conexão, que é o que o provider precisa para entregar.

    A conexão vem com plano B (`channels.JOIN_CONEXAO`): reconectar o WhatsApp
    apaga a conexão antiga e a FK zera `channel_connection_id` de toda conversa que já
    existia. Com o JOIN direto que havia aqui, isso virava **404 ao responder um cliente**
    — o atendente clicava em enviar e a mensagem não ia, sem nada explicando por quê.
    """
    row = await session.execute(
        text(
            "SELECT c.id, COALESCE(cc.id, fb.id) AS channel_connection_id, "
            "ct.external_id AS contact_external_id, ct.phone AS contact_phone, "
            "COALESCE(cc.channel, fb.channel) AS channel, "
            "COALESCE(cc.credentials, fb.credentials)->>'instance' AS instance_key "
            "FROM conversations c "
            "JOIN contacts ct ON ct.id = c.contact_id "
            + JOIN_CONEXAO
            + "WHERE c.id = :conversation_id AND c.tenant_id = :tenant_id"
        ),
        {"conversation_id": str(conversation_id), "tenant_id": str(tenant_id)},
    )
    conv_row = row.fetchone()
    if conv_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return conv_row


async def _marcar_lidas_no_canal(
    session: AsyncSession, tenant_id: UUID, conversation_id: UUID
) -> None:
    """Pede ao canal o tique azul das mensagens do cliente nesta conversa.

    **Best-effort, e silencioso por desenho.** Abrir a conversa é a ação que o atendente
    pediu; o tique é um efeito secundário no aparelho de OUTRA pessoa. Um erro aqui não
    pode virar 500 numa rota que já fez o que importava (zerar o badge), nem toast numa
    tela onde não há nada a fazer a respeito.

    Só as INBOUND que ainda não estavam marcadas: `direction = 'INBOUND'` porque marcar
    a própria mensagem como lida não significa nada, e o teto vem do provider.
    """
    try:
        conv_row = await _rota_de_envio(session, tenant_id, conversation_id)
        rows = await session.execute(
            text(
                "SELECT external_id FROM messages "
                "WHERE conversation_id = :cid AND tenant_id = :tid "
                "AND direction = 'INBOUND' AND external_id IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 50"
            ),
            {"cid": str(conversation_id), "tid": str(tenant_id)},
        )
        ids = [r.external_id for r in rows.fetchall()]
        if not ids:
            return
        provider = get_provider(Channel(conv_row.channel.lower()))
        await provider.mark_read(
            conv_row.instance_key or "", conv_row.contact_external_id, ids
        )
    except Exception:
        log.warning(
            "não foi possível marcar como lida no canal a conversa %s",
            conversation_id,
            exc_info=True,
        )


@router.get("")
async def list_conversations(
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    # max_length não é cosmético: ILIKE sem índice com string gigante é DoS barato.
    search: str | None = Query(None, max_length=100),
) -> ConversationListSchema:
    conversations = await inbox_queries.get_unclaimed(
        session, current_user.tenant_id, limit=limit, offset=offset, search=search
    )
    return ConversationListSchema(
        conversations=[ConversationSchema.model_validate(c) for c in conversations],
        total=len(conversations),
        limit=limit,
        offset=offset,
    )


@router.get("/mine")
async def list_my_conversations(
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: str | None = Query(None, max_length=100),
) -> ConversationListSchema:
    conversations = await inbox_queries.get_assigned_to(
        session,
        current_user.tenant_id,
        current_user.user_id,
        limit=limit,
        offset=offset,
        search=search,
    )
    return ConversationListSchema(
        conversations=[ConversationSchema.model_validate(c) for c in conversations],
        total=len(conversations),
        limit=limit,
        offset=offset,
    )


@router.get("/all")
async def list_all_conversations(
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    search: str | None = Query(None, max_length=100),
) -> ConversationListSchema:
    conversations = await inbox_queries.get_all(
        session, current_user.tenant_id, limit=limit, offset=offset, search=search
    )
    return ConversationListSchema(
        conversations=[ConversationSchema.model_validate(c) for c in conversations],
        total=len(conversations),
        limit=limit,
        offset=offset,
    )


@router.get("/{conversation_id}")
async def get_conversation(
    conversation_id: UUID,
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    limit: int = Query(50, ge=1, le=200),
    # offset conta para TRÁS a partir da mensagem mais recente — é como a thread
    # carrega histórico antigo (ver inbox_queries.get_conversation_messages).
    offset: int = Query(0, ge=0),
) -> ConversationDetailSchema:
    conv = await inbox_queries.get_conversation_by_id(
        session, current_user.tenant_id, conversation_id
    )
    if conv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    messages = await inbox_queries.get_conversation_messages(
        session, current_user.tenant_id, conversation_id, limit=limit, offset=offset
    )
    return ConversationDetailSchema.model_validate({**conv, "messages": messages})


@router.post("/{conversation_id}/read")
async def mark_conversation_read(
    conversation_id: UUID,
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConversationSchema:
    """Zera o contador de não-lidas. Chamado ao abrir a conversa na thread.

    Sem isto o `unread_count` só crescia: `ingestion` incrementa a cada mensagem e nada
    no sistema devolvia a zero, então o badge da lista — o principal sinal de triagem do
    atendente — ficava permanentemente errado.
    """
    result = await session.execute(
        text(
            "UPDATE conversations SET unread_count = 0 "
            "WHERE id = :conversation_id AND tenant_id = :tenant_id "
            "RETURNING id"
        ),
        {
            "conversation_id": str(conversation_id),
            "tenant_id": str(current_user.tenant_id),
        },
    )
    if result.fetchone() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await session.commit()

    await _marcar_lidas_no_canal(session, current_user.tenant_id, conversation_id)

    conv = await inbox_queries.get_conversation_by_id(
        session, current_user.tenant_id, conversation_id
    )
    # A lista dos outros atendentes precisa refletir o badge zerado.
    await events.publish_once(
        current_user.tenant_id,
        events.CONVERSATION_STATUS,
        {"conversation_id": str(conversation_id), "status": conv["status"]},
    )
    return ConversationSchema.model_validate(conv)


@router.post("/{conversation_id}/typing", status_code=status.HTTP_204_NO_CONTENT)
async def send_typing(
    conversation_id: UUID,
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Mostra "digitando…" no WhatsApp do cliente. 204 mesmo quando o canal recusa.

    **Não devolve erro de propósito.** É sinalização cosmética disparada por tecla
    digitada: transformar falha em 4xx encheria o console do atendente de vermelho no
    meio da resposta, sem nada a fazer. O front chama e esquece.

    Sem checar posse da conversa: quem já pode LER a conversa (o `get_current_user` do
    tenant) não ganha nada podendo publicar presença — e exigir captura impediria o
    indicador justamente em quem ainda vai assumir para responder.
    """
    try:
        conv_row = await _rota_de_envio(
            session, current_user.tenant_id, conversation_id
        )
        provider = get_provider(Channel(conv_row.channel.lower()))
        await provider.send_presence(
            conv_row.instance_key or "", conv_row.contact_external_id
        )
    except HTTPException:
        raise  # conversa de outro tenant continua sendo 404
    except Exception:
        log.warning("presence falhou na conversa %s", conversation_id, exc_info=True)


@router.post("/{conversation_id}/claim")
async def claim_conversation(
    conversation_id: UUID,
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConversationSchema:
    success = await assignment.claim_conversation(
        session, conversation_id, current_user.user_id, current_user.tenant_id
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Conversa já capturada"
        )
    await events.publish_once(
        current_user.tenant_id,
        events.CONVERSATION_ASSIGNED,
        {
            "conversation_id": str(conversation_id),
            "assigned_user_id": str(current_user.user_id),
        },
    )
    conv = await inbox_queries.get_conversation_by_id(
        session, current_user.tenant_id, conversation_id
    )
    return ConversationSchema.model_validate(conv)


@router.post("/{conversation_id}/release")
async def release_conversation(
    conversation_id: UUID,
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConversationSchema:
    success = await assignment.release_conversation(
        session, conversation_id, current_user.user_id, current_user.tenant_id
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Não é possível liberar esta conversa",
        )
    await events.publish_once(
        current_user.tenant_id,
        events.CONVERSATION_ASSIGNED,
        {"conversation_id": str(conversation_id), "assigned_user_id": None},
    )
    conv = await inbox_queries.get_conversation_by_id(
        session, current_user.tenant_id, conversation_id
    )
    return ConversationSchema.model_validate(conv)


@router.post("/{conversation_id}/transfer")
async def transfer_conversation(
    conversation_id: UUID,
    body: TransferRequestSchema,
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConversationSchema:
    success = await assignment.transfer_conversation(
        session,
        conversation_id,
        current_user.user_id,
        body.to_user_id,
        current_user.tenant_id,
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Não é possível transferir esta conversa",
        )
    await events.publish_once(
        current_user.tenant_id,
        events.CONVERSATION_ASSIGNED,
        {
            "conversation_id": str(conversation_id),
            "assigned_user_id": str(body.to_user_id),
        },
    )
    conv = await inbox_queries.get_conversation_by_id(
        session, current_user.tenant_id, conversation_id
    )
    return ConversationSchema.model_validate(conv)


@router.post("/{conversation_id}/messages", status_code=status.HTTP_201_CREATED)
async def send_message(
    conversation_id: UUID,
    body: SendMessageSchema,
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MessageSchema:
    # Esta rota é só de texto. Antes ela ACEITAVA content_type=image, gravava a Message
    # como IMAGE e mandava o conteúdo pelo /sendText do provider — o cliente recebia o
    # nome do arquivo escrito como mensagem, e nada disso aparecia em log.
    if body.content_type is not ContentType.TEXT:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Envio de mídia usa POST /conversations/{id}/messages/media",
        )

    conv_row = await _rota_de_envio(session, current_user.tenant_id, conversation_id)

    outbound = OutboundMessage(
        channel=conv_row.channel.lower(),
        channel_connection_id=conv_row.channel_connection_id,
        to_external_id=conv_row.contact_external_id,
        to_phone=conv_row.contact_phone,
        content_type=body.content_type,
        content=body.content,
        channel_metadata={"instance_key": conv_row.instance_key},
    )
    provider = get_provider(Channel(conv_row.channel.lower()))
    send_result = await provider.send(outbound)

    external_id = send_result.external_id if send_result.success else None
    msg_status = (
        MessageStatus.SENT.name if send_result.success else MessageStatus.FAILED.name
    )

    msg_row = await session.execute(
        text(
            "INSERT INTO messages "
            "(id, conversation_id, tenant_id, direction, content_type, content, "
            "external_id, channel, status, sender_user_id, created_at) "
            "VALUES (gen_random_uuid(), :conversation_id, :tenant_id, :direction, "
            ":content_type, :content, :external_id, :channel, :status, :sender_user_id, "
            "clock_timestamp()) "
            "RETURNING id, direction, content_type, content, status, created_at"
        ),
        {
            "conversation_id": str(conversation_id),
            "tenant_id": str(current_user.tenant_id),
            "direction": MessageDirection.OUTBOUND.name,
            "content_type": body.content_type.name,
            "content": body.content,
            "external_id": external_id,
            "channel": conv_row.channel,
            "status": msg_status,
            "sender_user_id": str(current_user.user_id),
        },
    )
    msg = msg_row.fetchone()

    preview = body.content[:120]
    # first_response_at: marca a 1ª resposta humana (só se ainda não houver) — métrica de TMPR.
    await session.execute(
        text(
            "UPDATE conversations "
            "SET last_message_at = now(), last_message_preview = :preview, "
            "first_response_at = COALESCE(first_response_at, now()) "
            "WHERE id = :conversation_id"
        ),
        {"preview": preview, "conversation_id": str(conversation_id)},
    )
    await session.commit()

    await events.publish_once(
        current_user.tenant_id,
        events.MESSAGE_OUTBOUND,
        {"conversation_id": str(conversation_id), "preview": preview},
    )

    return MessageSchema(
        id=msg.id,
        direction=MessageDirection.OUTBOUND,
        content_type=body.content_type,
        content=body.content,
        status=MessageStatus.SENT if send_result.success else MessageStatus.FAILED,
        created_at=msg.created_at,
    )


@router.post("/{conversation_id}/messages/media", status_code=status.HTTP_201_CREATED)
async def send_media_message(
    conversation_id: UUID,
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    file: Annotated[UploadFile, File()],
    caption: Annotated[str, Form()] = "",
    ptt: Annotated[bool, Form()] = False,
) -> MessageSchema:
    """Envia um anexo do atendente para o cliente.

    Multipart e não JSON com base64: dobrar o tamanho do corpo para atravessar a nossa
    API só para o provider desfazer em seguida não paga nada.

    `ptt=true` marca "isto é uma nota de voz, não um arquivo de áudio" — muda o balão no
    aparelho do cliente. Quem sabe o que fazer com o fato é o provider; a rota só o
    repassa. Só vale para áudio: em imagem ou documento é ignorado.
    """
    conv_row = await _rota_de_envio(session, current_user.tenant_id, conversation_id)
    anexo = await attachments.validar(file)

    # O id sai daqui, e não de gen_random_uuid() no INSERT, porque o NOME DO ARQUIVO em
    # disco é {message_id}{ext} — a mesma convenção da mídia que chega. É o que faz
    # `GET /media/{id}` servir este anexo sem uma linha de mudança.
    message_id = uuid4()
    # Grava ANTES de enviar: se o Evolution cair, a mensagem fica registrada como
    # FAILED mas o arquivo continua no disco para reenvio, em vez de sumir.
    caminho = await attachments.gravar(
        anexo, current_user.tenant_id, message_id, audit_session=session
    )

    outbound = OutboundMessage(
        channel=conv_row.channel.lower(),
        channel_connection_id=conv_row.channel_connection_id,
        to_external_id=conv_row.contact_external_id,
        to_phone=conv_row.contact_phone,
        content_type=anexo.content_type,
        content=caption,
        media=OutboundMedia(
            data=anexo.data,
            filename=anexo.filename,
            mimetype=anexo.mimetype,
            ptt=ptt and anexo.content_type == ContentType.AUDIO,
        ),
        channel_metadata={"instance_key": conv_row.instance_key},
    )
    provider = get_provider(Channel(conv_row.channel.lower()))
    # try/except porque uma exceção de rede aqui viraria 500 e o atendente perderia o
    # anexo sem saber por quê — o mesmo cuidado que o bot_orchestrator já toma.
    try:
        send_result = await provider.send(outbound)
    except Exception:
        log.exception("falha ao enviar mídia da conversa %s", conversation_id)
        send_result = None

    sucesso = bool(send_result and send_result.success)
    msg_status = MessageStatus.SENT.name if sucesso else MessageStatus.FAILED.name

    metadata = {
        "media": {
            "has_media": True,
            "status": "stored",
            "path": caminho,
            "mimetype": anexo.mimetype,
            "filename": anexo.filename,
        }
    }
    msg_row = await session.execute(
        text(
            "INSERT INTO messages "
            "(id, conversation_id, tenant_id, direction, content_type, content, "
            "external_id, channel, status, sender_user_id, channel_metadata, created_at) "
            "VALUES (:id, :conversation_id, :tenant_id, :direction, "
            ":content_type, :content, :external_id, :channel, :status, :sender_user_id, "
            "CAST(:metadata AS jsonb), clock_timestamp()) "
            "RETURNING id, created_at"
        ),
        {
            "id": str(message_id),
            "conversation_id": str(conversation_id),
            "tenant_id": str(current_user.tenant_id),
            "direction": MessageDirection.OUTBOUND.name,
            "content_type": anexo.content_type.name,
            "content": caption or None,
            "external_id": send_result.external_id if sucesso else None,
            "channel": conv_row.channel,
            "status": msg_status,
            "sender_user_id": str(current_user.user_id),
            "metadata": json.dumps(metadata),
        },
    )
    msg = msg_row.fetchone()

    # Sem legenda, a lista mostra o mesmo rótulo de uma mídia recebida ("Imagem").
    preview = preview_for(anexo.content_type.name, caption)
    await session.execute(
        text(
            "UPDATE conversations "
            "SET last_message_at = now(), last_message_preview = :preview, "
            "first_response_at = COALESCE(first_response_at, now()) "
            "WHERE id = :conversation_id"
        ),
        {"preview": preview, "conversation_id": str(conversation_id)},
    )
    await session.commit()

    await events.publish_once(
        current_user.tenant_id,
        events.MESSAGE_OUTBOUND,
        {"conversation_id": str(conversation_id), "preview": preview},
    )

    return MessageSchema(
        id=msg.id,
        direction=MessageDirection.OUTBOUND,
        content_type=anexo.content_type,
        content=caption or None,
        status=MessageStatus.SENT if sucesso else MessageStatus.FAILED,
        created_at=msg.created_at,
        # Sem isto a bolha recém-enviada aparece vazia: a thread faz append do objeto
        # retornado e só veria a mídia depois do refresh do WebSocket.
        media_url=(
            f"/media/{message_id}"
            f"?{media_links.assinar('media', message_id, current_user.tenant_id)}"
        ),
    )


@router.post("/{conversation_id}/suggest-reply")
async def suggest_reply(
    conversation_id: UUID,
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SuggestReplySchema:
    """Copilot: sugere a próxima resposta para o atendente (nunca envia sozinho)."""
    conv = await inbox_queries.get_conversation_by_id(
        session, current_user.tenant_id, conversation_id
    )
    if conv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    suggestion = await copilot.suggest(session, current_user.tenant_id, conversation_id)
    if suggestion is None:
        return SuggestReplySchema(suggestion=None, reason="ia_indisponivel")
    return SuggestReplySchema(suggestion=suggestion)


@router.patch("/{conversation_id}/status")
async def update_conversation_status(
    conversation_id: UUID,
    body: StatusUpdateSchema,
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConversationSchema:
    from app.services import conversation_state

    try:
        changed = await conversation_state.transition(
            session,
            current_user.tenant_id,
            conversation_id,
            current_user.user_id,
            body.status.name,
        )
    except conversation_state.ConversationNotFound:
        raise HTTPException(status_code=404) from None
    except conversation_state.StateConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()

    if changed:
        await events.publish_once(
            current_user.tenant_id,
            events.CONVERSATION_STATUS,
            {"conversation_id": str(conversation_id), "status": body.status.value},
        )

    conv = await inbox_queries.get_conversation_by_id(
        session, current_user.tenant_id, conversation_id
    )
    return ConversationSchema.model_validate(conv)
