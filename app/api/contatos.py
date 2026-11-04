"""Endpoints do perfil do cliente: quem é, o que já contratou, o que a equipe anotou.

Tudo aqui é de qualquer atendente, sem a assimetria de `salao.py`/`quick_replies.py`.
O motivo: lá o que se protege é **copy do negócio** (preço, fotos, persona) — texto que
sai para o cliente e que um erro publica. Anotação sobre cliente é o oposto: é interna,
nunca vai para o WhatsApp, e serve justamente a quem está atendendo. Exigir admin para
escrever "pediu desconto" garantiria só que ninguém escreveria.

A exceção é apagar, que é do autor ou de um admin — ver `contatos.apagar_nota`.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Request, APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import TokenData, decode_token, get_current_user
from app.core.database import get_session
from app.models.enums import ContentType, UserRole
from app.schemas import (
    ContactNoteInSchema,
    ContactNoteSchema,
    ContactPerfilSchema,
)
from app.services import avatars, contatos, media_links
from app.services.attachments import mimetype_para_servir
from app.services import audit, media_response

# `auto_error=False`: a rota do avatar aceita assinatura na URL, então "sem header" é um
# caso válido, não um 403 automático.
_bearer = HTTPBearer(auto_error=False)

router = APIRouter(prefix="/contacts", tags=["contacts"])


@router.get("/{contact_id}")
async def obter_perfil(
    contact_id: UUID,
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ContactPerfilSchema:
    """O painel do cliente. 404 quando o contato não é deste tenant.

    404 e não 403 de propósito: distinguir "não existe" de "existe mas é de outro" conta
    a quem perguntou que o contato existe em algum lugar da plataforma.
    """
    dados = await contatos.perfil(session, current_user.tenant_id, contact_id)
    if dados is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Contato não encontrado."
        )
    # `expirar_vencidas` roda dentro do perfil e escreve: sem o commit, uma pré-reserva
    # vencida seria remarcada a cada abertura do painel.
    await session.commit()
    return ContactPerfilSchema(**dados)


@router.get("/{contact_id}/avatar")
async def baixar_avatar(
    request: Request,
    contact_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer)
    ] = None,
    exp: Annotated[int | None, Query()] = None,
    sig: Annotated[str | None, Query()] = None,
) -> Response:
    """Foto de perfil do contato, autorizada por assinatura na URL ou por Bearer.

    Mesmo desenho da foto do espaço (`api/salao.py::baixar_foto`) e pelo mesmo motivo:
    `<img src>` não manda header `Authorization`, e a resposta para isso não pode ser o
    JWT na query string (`services/media_links.py`).

    O tenant sai do TOKEN de quem pede, nunca da URL: quem tem assinatura só abre a foto
    do contato assinado, e quem tem Bearer só abre foto do próprio tenant.
    """
    dono = await avatars.tenant_de(session, contact_id)
    if dono is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if not _pode_ver_avatar(dono, contact_id, credentials, exp, sig):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    encontrado = await avatars.arquivo_de(session, dono, contact_id)
    if encontrado is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    caminho, mimetype = encontrado
    # O mimetype foi validado contra a allow-list no download (`whatsapp.fetch_avatar`),
    # e passa aqui pelo MESMO filtro da mídia de conversa: existe uma regra só de "com
    # que Content-Type servir arquivo" (`CLAUDE.md` regra 6), não uma por origem.
    media_type, pode_inline = mimetype_para_servir(mimetype, ContentType.IMAGE.name)
    headers = {
        "Cache-Control": f"private, max-age={avatars.CACHE_SEGUNDOS}",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
    }
    if not pode_inline:
        headers["Content-Disposition"] = "attachment"
    signed_access = media_links.validar(avatars.TIPO_LINK, contact_id, dono, exp, sig)
    if not signed_access and not await audit.bind_media_identity(
        session, credentials, dono, request
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await audit.record(
        session,
        dono,
        "media.read",
        "avatar",
        contact_id,
        actor_type="signed_link" if signed_access else "system",
        reason="signed_resource" if signed_access else "bearer_tenant",
    )
    await session.commit()
    return await media_response.serve(caminho, media_type, headers, request)


def _pode_ver_avatar(
    dono: UUID,
    contact_id: UUID,
    credentials: HTTPAuthorizationCredentials | None,
    exp: int | None,
    sig: str | None,
) -> bool:
    if media_links.validar(avatars.TIPO_LINK, contact_id, dono, exp, sig):
        return True
    if credentials is None:
        return False
    token_data = decode_token(credentials.credentials)
    return token_data is not None and token_data.tenant_id == dono


@router.post("/{contact_id}/notes", status_code=status.HTTP_201_CREATED)
async def criar_nota(
    contact_id: UUID,
    body: ContactNoteInSchema,
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ContactNoteSchema:
    nota = await contatos.criar_nota(
        session,
        current_user.tenant_id,
        contact_id,
        current_user.user_id,
        body.texto,
    )
    if nota is None:
        # Duas causas, uma resposta: contato de outro tenant (404 pelo mesmo motivo do
        # GET) e texto vazio depois do strip. O 400 seria mais preciso no segundo caso,
        # mas a tela já bloqueia o botão com o campo vazio.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Contato não encontrado ou anotação vazia.",
        )
    await session.commit()
    return ContactNoteSchema(**nota)


@router.delete("/{contact_id}/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
async def apagar_nota(
    contact_id: UUID,
    note_id: UUID,
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Apaga a anotação. Autor apaga a sua; admin apaga qualquer uma.

    O papel vem do banco e não do JWT, pelo mesmo motivo de `auth.require_admin`: o
    refresh token vive dias e se renova sozinho, então papel embutido no token só valeria
    quando ele expirasse.

    **`contact_id` fica só na URL, fora do `WHERE` — e é de propósito.** O painel agrupa
    contatos irmãos (mesmo telefone), então a nota listada sob o contato A pode estar
    gravada no contato B. Casar os dois aqui faria o botão de apagar falhar exatamente
    para o cliente que teve o endereço do canal trocado. Quem autoriza é o par
    `tenant_id` + posse, que já é suficiente.
    """
    row = await session.execute(
        text(
            "SELECT role FROM users "
            "WHERE id = :user_id AND tenant_id = :tenant_id AND is_active = true"
        ),
        {
            "user_id": str(current_user.user_id),
            "tenant_id": str(current_user.tenant_id),
        },
    )
    is_admin = row.scalar_one_or_none() == UserRole.ADMIN.name

    apagou = await contatos.apagar_nota(
        session, current_user.tenant_id, note_id, current_user.user_id, is_admin
    )
    if not apagou:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Anotação não encontrada ou não é sua.",
        )
    await session.commit()
