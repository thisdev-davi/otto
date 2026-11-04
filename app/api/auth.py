from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

import bcrypt as _bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import ratelimit
from app.core.config import settings
from app.core.database import get_session
from app.models.enums import UserRole

security = HTTPBearer()

router = APIRouter(tags=["auth"])


class TokenData(BaseModel):
    tenant_id: UUID
    user_id: UUID
    # Versão da sessão no momento da emissão. Conferida no /auth/refresh contra o banco:
    # é o que permite logout e troca de senha derrubarem tokens já emitidos.
    token_version: int = 0


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class PasswordChangeRequest(BaseModel):
    senha_atual: str
    nova_senha: str


def _encode(
    tenant_id: UUID, user_id: UUID, token_type: str, expires: datetime, tv: int = 0
) -> str:
    data = {
        "tenant_id": str(tenant_id),
        "user_id": str(user_id),
        "type": token_type,
        "tv": tv,
        "exp": expires,
    }
    return jwt.encode(data, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_access_token(tenant_id: UUID, user_id: UUID, tv: int = 0) -> str:
    expires = datetime.now(timezone.utc) + timedelta(
        minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    return _encode(tenant_id, user_id, "access", expires, tv)


def create_refresh_token(tenant_id: UUID, user_id: UUID, tv: int = 0) -> str:
    expires = datetime.now(timezone.utc) + timedelta(
        days=settings.REFRESH_TOKEN_EXPIRE_DAYS
    )
    return _encode(tenant_id, user_id, "refresh", expires, tv)


def _tokens(tenant_id: UUID, user_id: UUID, tv: int = 0) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(tenant_id, user_id, tv),
        refresh_token=create_refresh_token(tenant_id, user_id, tv),
    )


def decode_token(token: str, expected_type: str = "access") -> TokenData | None:
    """Valida o JWT e devolve o TokenData, ou None se inválido. Sem levantar exceção.

    `expected_type` garante que um refresh token não seja aceito onde se espera um
    access token (get_current_user, WebSocket) e vice-versa. Usado pela dependência
    HTTP, pelo handshake do WebSocket e pelo endpoint de refresh.
    """
    try:
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        if payload.get("type", "access") != expected_type:
            return None
        tenant_id_str = payload.get("tenant_id")
        user_id_str = payload.get("user_id")
        if not tenant_id_str or not user_id_str:
            return None
        return TokenData(
            tenant_id=UUID(tenant_id_str),
            user_id=UUID(user_id_str),
            token_version=int(payload.get("tv", 0)),
        )
    except (JWTError, ValueError, TypeError):
        return None


async def get_current_user(
    request: Request,
    credentials: Annotated[object, Depends(security)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenData:
    token_data = decode_token(credentials.credentials)
    if token_data is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # A valid signature is insufficient after deactivation, logout or password change.
    # This query also proves actor membership before attributing tenant audit events.
    version = await session.scalar(
        text(
            "SELECT token_version FROM users WHERE id = :uid AND tenant_id = :tid AND is_active = true"
        ),
        {"uid": token_data.user_id, "tid": token_data.tenant_id},
    )
    if version is None or version != token_data.token_version:
        if version is not None:
            from app.services import audit

            # Membership is known, but a revoked credential proves no current actor.
            await audit.record(
                session,
                token_data.tenant_id,
                "auth.denied",
                "users",
                token_data.user_id,
                actor_type="anonymous",
                result="failure",
                reason="session_revoked",
            )
            await session.commit()
        raise HTTPException(
            status_code=401, detail="Sessão encerrada. Entre novamente."
        )
    request.state.audit_identity = token_data
    return token_data


async def require_admin(
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenData:
    """Exige papel ADMIN. Lê o papel do banco, não do token.

    O papel NÃO entra no JWT de propósito: o access token vive minutos, mas o refresh
    vive dias e se renova sozinho — rebaixar alguém a atendente só faria efeito
    quando o refresh expirasse. Lendo do banco, a mudança vale no próximo request.
    O custo é uma query, e só em rotas de administração: as rotas quentes da inbox
    (fila, thread, envio) não passam por aqui.
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
    role = row.scalar_one_or_none()
    if role != UserRole.ADMIN.name:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Ação restrita a administradores.",
        )
    return current_user


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    body: RefreshRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenResponse:
    """Troca um refresh token válido por um novo par (access + refresh, rotacionado)."""
    token_data = decode_token(body.refresh_token, expected_type="refresh")
    if token_data is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token inválido ou expirado",
        )
    # O usuário ainda existe e está ativo? Sem esta checagem, desativar alguém não o
    # expulsa: ele seguiria rotacionando o próprio refresh até ele expirar, porque o
    # endpoint só reemitia a partir do payload do token. Desativar tem que valer agora.
    row = await session.execute(
        text(
            "SELECT token_version FROM users "
            "WHERE id = :user_id AND tenant_id = :tenant_id AND is_active = true"
        ),
        {"user_id": str(token_data.user_id), "tenant_id": str(token_data.tenant_id)},
    )
    atual = row.scalar_one_or_none()
    if atual is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário inativo ou removido",
        )
    # Logout ou troca de senha incrementam `token_version`. Um refresh emitido antes disso
    # morre aqui — é a única forma de revogar um token que ainda não expirou.
    if atual != token_data.token_version:
        from app.services import audit

        await audit.record(
            session,
            token_data.tenant_id,
            "auth.refresh",
            "users",
            token_data.user_id,
            actor_type="anonymous",
            result="failure",
            reason="session_revoked",
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sessão encerrada. Entre novamente.",
        )
    from app.services import audit

    await audit.record(
        session,
        token_data.tenant_id,
        "auth.refresh",
        "users",
        token_data.user_id,
        actor_type="human",
        actor_user_id=token_data.user_id,
    )
    await session.commit()
    return _tokens(token_data.tenant_id, token_data.user_id, atual)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Encerra a sessão: invalida TODOS os refresh tokens deste usuário.

    Não existia rota de logout — o front apagava o `localStorage` e o refresh token
    continuava válido por 30 dias na mão de quem o tivesse copiado. Sair da conta tem que
    valer no servidor, não só na aba.

    O access token corrente ainda vive até expirar (no máximo
    ACCESS_TOKEN_EXPIRE_MINUTES). É a mesma troca que `require_admin` documenta: checar o
    banco a cada request nas rotas quentes da inbox sairia caro.
    """
    await session.execute(
        text(
            "UPDATE users SET token_version = token_version + 1 "
            "WHERE id = :user_id AND tenant_id = :tenant_id"
        ),
        {
            "user_id": str(current_user.user_id),
            "tenant_id": str(current_user.tenant_id),
        },
    )
    await session.commit()


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordChangeRequest,
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    """Troca a própria senha e derruba as outras sessões.

    Não havia como trocar de senha em lugar nenhum do sistema: quem suspeitasse que a
    senha vazou não tinha ação disponível — nem trocar, nem expulsar quem já estava
    dentro. Por isso o incremento de `token_version` aqui não é detalhe: é o que torna a
    troca de senha uma resposta a incidente, e não só uma mudança de cadastro.
    """
    if len(body.nova_senha or "") < 8:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A senha precisa ter ao menos 8 caracteres.",
        )

    row = await session.execute(
        text(
            "SELECT hashed_password FROM users "
            "WHERE id = :user_id AND tenant_id = :tenant_id AND is_active = true"
        ),
        {
            "user_id": str(current_user.user_id),
            "tenant_id": str(current_user.tenant_id),
        },
    )
    atual = row.scalar_one_or_none()
    if atual is None or not _bcrypt.checkpw(body.senha_atual.encode(), atual.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Senha atual incorreta."
        )

    novo_hash = _bcrypt.hashpw(body.nova_senha.encode(), _bcrypt.gensalt()).decode()
    await session.execute(
        text(
            "UPDATE users SET hashed_password = :hash, "
            "token_version = token_version + 1 "
            "WHERE id = :user_id AND tenant_id = :tenant_id"
        ),
        {
            "hash": novo_hash,
            "user_id": str(current_user.user_id),
            "tenant_id": str(current_user.tenant_id),
        },
    )
    await session.commit()


@router.post("/token", response_model=TokenResponse)
async def login(
    request: Request,
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TokenResponse:
    # Dois baldes: por IP (um atacante varrendo muitas contas) e por e-mail (muitos IPs
    # contra uma conta só). Antes não havia nenhum — e cada tentativa custa um bcrypt de
    # custo 12 no event loop, então o endpoint era ao mesmo tempo oráculo de senha e
    # negação de serviço barata.
    origem = ratelimit.ip_do_cliente(request)
    alvo = (form_data.username or "").strip().lower()[:120]
    await ratelimit.exigir(
        f"login:ip:{origem}",
        settings.RATE_LIMIT_LOGIN,
        settings.RATE_LIMIT_LOGIN_JANELA,
    )
    await ratelimit.exigir(
        f"login:user:{alvo}",
        settings.RATE_LIMIT_LOGIN,
        settings.RATE_LIMIT_LOGIN_JANELA,
    )

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciais inválidas",
        headers={"WWW-Authenticate": "Bearer"},
    )
    # `ORDER BY created_at` e não `LIMIT 1` nu: o e-mail só é único DENTRO do tenant
    # (uq_users_tenant_email), então a mesma pessoa em dois salões dava duas linhas e qual
    # delas voltava era decisão do planejador — login intermitente, ou entrando no tenant
    # errado. Ordenado, ao menos é determinístico: a conta mais antiga é a que responde.
    result = await session.execute(
        text(
            "SELECT id, tenant_id, hashed_password, token_version"
            " FROM users WHERE email = :email AND is_active = true"
            " ORDER BY created_at LIMIT 1"
        ),
        {"email": form_data.username},
    )
    row = result.fetchone()
    if row is None:
        raise credentials_exception
    if not _bcrypt.checkpw(form_data.password.encode(), row.hashed_password.encode()):
        from app.services import audit

        await audit.record(
            session,
            row.tenant_id,
            "auth.login",
            "users",
            result="failure",
            reason="invalid_credentials",
            actor_type="anonymous",
        )
        await session.commit()
        raise credentials_exception
    from app.services import audit

    identity = TokenData(
        tenant_id=row.tenant_id, user_id=row.id, token_version=row.token_version
    )
    request.state.audit_identity = identity
    await audit.record(
        session,
        row.tenant_id,
        "auth.login",
        "users",
        row.id,
        actor_type="human",
        actor_user_id=row.id,
    )
    await session.commit()
    return _tokens(tenant_id=row.tenant_id, user_id=row.id, tv=row.token_version)
