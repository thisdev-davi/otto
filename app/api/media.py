"""Serve a mídia baixada de uma mensagem, autenticada por tenant."""

import os
from typing import Annotated
from uuid import UUID

from fastapi import Request, APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import decode_token
from app.core.database import get_session
from app.services import media_links
from app.services.attachments import mimetype_para_servir
from app.services import audit, media_response

router = APIRouter(prefix="/media", tags=["media"])

# auto_error=False: sem isso o HTTPBearer aborta com 401 antes de olharmos a assinatura.
_bearer = HTTPBearer(auto_error=False)

TIPO = "media"


@router.get("/{message_id}")
async def get_media(
    request: Request,
    message_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer)
    ] = None,
    exp: Annotated[int | None, Query()] = None,
    sig: Annotated[str | None, Query()] = None,
) -> Response:
    """Autentica por assinatura na URL (`<img src>`) ou por Bearer (cliente de API).

    A assinatura substituiu o `?token=<JWT>` que existia aqui. Ver
    `app/services/media_links.py` para o porquê: aquele token era credencial de sessão
    completa, e esta rota podia devolver HTML do atacante que lia a própria URL.
    """
    row = await session.execute(
        text(
            "SELECT channel_metadata->'media'->>'path' AS path, "
            "channel_metadata->'media'->>'mimetype' AS mimetype, "
            "channel_metadata->'media'->>'filename' AS filename, "
            "content_type, tenant_id "
            "FROM messages WHERE id = :mid"
        ),
        {"mid": str(message_id)},
    )
    r = row.fetchone()
    if r is None or not r.path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    if credentials is None and not sig:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not _autorizado(r.tenant_id, message_id, credentials, exp, sig):
        # 404, e não 403: quem apresenta credencial de outro tenant não deve nem saber
        # que esta mensagem existe.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    # O mimetype gravado veio do canal — quem mandou a mensagem escolheu o valor. Quem
    # decide com o que servir é o servidor.
    media_type, pode_inline = mimetype_para_servir(r.mimetype, r.content_type)

    headers = {
        "X-Content-Type-Options": "nosniff",
        # Defesa em profundidade: mesmo que um tipo executável escape da allow-list
        # acima, aqui ele não tem origem, não carrega script e não alcança rede.
        "Content-Security-Policy": "default-src 'none'; sandbox",
    }
    if not pode_inline:
        nome = r.filename or f"{message_id}"
        headers["Content-Disposition"] = f'attachment; filename="{_sanitiza(nome)}"'

    signed_access = media_links.validar(TIPO, message_id, r.tenant_id, exp, sig)
    if not signed_access and not await audit.bind_media_identity(
        session, credentials, r.tenant_id, request
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await audit.record(
        session,
        r.tenant_id,
        "media.read",
        "media",
        message_id,
        actor_type="signed_link" if signed_access else "system",
        reason="signed_resource" if signed_access else "bearer_tenant",
    )
    await session.commit()
    return await media_response.serve(r.path, media_type, headers, request)


def _autorizado(
    tenant_id,
    message_id: UUID,
    credentials: HTTPAuthorizationCredentials | None,
    exp: int | None,
    sig: str | None,
) -> bool:
    if media_links.validar(TIPO, message_id, tenant_id, exp, sig):
        return True
    if credentials is None:
        return False
    token_data = decode_token(credentials.credentials)
    return token_data is not None and str(token_data.tenant_id) == str(tenant_id)


def _sanitiza(nome: str) -> str:
    """Nome de arquivo vem do remetente: tira aspas e quebras que escapariam do header."""
    limpo = os.path.basename(nome).replace('"', "").replace("\\", "")
    limpo = "".join(c for c in limpo if c.isprintable() and c not in "\r\n")
    return limpo[:120] or "arquivo"
