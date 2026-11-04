"""Gestão de equipe: convites por link e administração de membros.

Convite por link (e não por e-mail) é decisão de produto: o projeto não tem SMTP, e o
público-alvo manda o link por WhatsApp mesmo. O admin gera, copia e envia por onde
quiser; quem recebe define a própria senha — ninguém transporta senha em texto.

O banco guarda só o SHA-256 do token. O link em claro aparece uma única vez, na resposta
de criação: vazamento da tabela `user_invites` não dá acesso a ninguém.
"""

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID

import bcrypt
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import UserRole

INVITE_TTL_DAYS = 7
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class TeamError(Exception):
    """Erro de regra de negócio da equipe — a rota traduz para o HTTP certo."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def normalize_email(email: str) -> str:
    e = (email or "").strip().lower()
    if not _EMAIL_RE.match(e):
        raise TeamError("email_invalido", "E-mail inválido.")
    return e


def hash_token(raw_token: str) -> str:
    """SHA-256 hex. Não é senha de usuário: é um segredo de 256 bits gerado por nós,
    então não precisa de KDF lento — bcrypt aqui só custaria latência."""
    return hashlib.sha256(raw_token.encode()).hexdigest()


async def create_invite(
    db: AsyncSession,
    tenant_id: UUID,
    invited_by: UUID,
    email: str,
    full_name: str | None,
    role: UserRole,
) -> tuple[dict, str]:
    """Cria (ou substitui) o convite pendente do e-mail. Devolve (convite, token_cru)."""
    email = normalize_email(email)

    existe = await db.execute(
        text(
            "SELECT 1 FROM users WHERE tenant_id = :t AND lower(email) = :e AND is_active = true"
        ),
        {"t": str(tenant_id), "e": email},
    )
    if existe.scalar_one_or_none() is not None:
        raise TeamError("ja_e_membro", "Esse e-mail já faz parte da equipe.")

    # Reconvite substitui o pendente em vez de acumular tokens válidos soltos por aí
    # (o índice parcial uq_user_invites_pending também impediria o segundo).
    await db.execute(
        text(
            "DELETE FROM user_invites "
            "WHERE tenant_id = :t AND lower(email) = :e AND accepted_at IS NULL"
        ),
        {"t": str(tenant_id), "e": email},
    )

    raw_token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=INVITE_TTL_DAYS)
    row = await db.execute(
        text(
            "INSERT INTO user_invites "
            "(id, tenant_id, email, full_name, role, token_hash, invited_by, expires_at) "
            "VALUES (gen_random_uuid(), :t, :e, :n, :r, :h, :by, :exp) "
            "RETURNING id, email, full_name, role, expires_at, created_at"
        ),
        {
            "t": str(tenant_id),
            "e": email,
            "n": (full_name or "").strip() or None,
            "r": role.name,
            "h": hash_token(raw_token),
            "by": str(invited_by),
            "exp": expires_at,
        },
    )
    r = row.fetchone()
    await db.commit()
    return (
        {
            "id": r.id,
            "email": r.email,
            "full_name": r.full_name,
            "role": r.role,
            "expires_at": r.expires_at,
            "created_at": r.created_at,
        },
        raw_token,
    )


async def list_invites(db: AsyncSession, tenant_id: UUID) -> list[dict]:
    """Convites ainda não aceitos, inclusive expirados (o admin vê e reenvia)."""
    rows = await db.execute(
        text(
            "SELECT id, email, full_name, role, expires_at, created_at "
            "FROM user_invites WHERE tenant_id = :t AND accepted_at IS NULL "
            "ORDER BY created_at DESC"
        ),
        {"t": str(tenant_id)},
    )
    return [
        {
            "id": r.id,
            "email": r.email,
            "full_name": r.full_name,
            "role": r.role,
            "expires_at": r.expires_at,
            "created_at": r.created_at,
        }
        for r in rows.fetchall()
    ]


async def revoke_invite(db: AsyncSession, tenant_id: UUID, invite_id: UUID) -> bool:
    result = await db.execute(
        text(
            "DELETE FROM user_invites "
            "WHERE id = :id AND tenant_id = :t AND accepted_at IS NULL RETURNING id"
        ),
        {"id": str(invite_id), "t": str(tenant_id)},
    )
    apagou = result.fetchone() is not None
    await db.commit()
    return apagou


async def peek_invite(db: AsyncSession, raw_token: str) -> dict | None:
    """Dados públicos do convite, para a tela de aceite. None se inválido/expirado.

    Devolve o nome do negócio para a pessoa saber onde está entrando — e nada além
    disso: nem quem convidou, nem a lista da equipe.
    """
    row = await db.execute(
        text(
            "SELECT i.email, i.full_name, i.role, t.name AS tenant_name "
            "FROM user_invites i JOIN tenants t ON t.id = i.tenant_id "
            "WHERE i.token_hash = :h AND i.accepted_at IS NULL AND i.expires_at > now()"
        ),
        {"h": hash_token(raw_token)},
    )
    r = row.fetchone()
    if r is None:
        return None
    return {
        "email": r.email,
        "full_name": r.full_name,
        "role": r.role,
        "tenant_name": r.tenant_name,
    }


async def accept_invite(
    db: AsyncSession, raw_token: str, password: str, full_name: str | None
) -> dict:
    """Consome o convite e cria o usuário. Devolve {user_id, tenant_id}.

    O UPDATE que marca `accepted_at` carrega a condição no WHERE (mesma disciplina da
    captura de conversa): dois cliques no botão só podem criar um usuário. Quem perde
    a corrida não encontra o convite aberto e recebe o mesmo erro de link já usado.
    """
    if len(password or "") < 8:
        raise TeamError("senha_curta", "A senha precisa ter ao menos 8 caracteres.")

    consumido = await db.execute(
        text(
            "UPDATE user_invites SET accepted_at = now() "
            "WHERE token_hash = :h AND accepted_at IS NULL AND expires_at > now() "
            "RETURNING id, tenant_id, email, full_name, role"
        ),
        {"h": hash_token(raw_token)},
    )
    inv = consumido.fetchone()
    if inv is None:
        raise TeamError("convite_invalido", "Convite inválido, expirado ou já usado.")

    nome = (full_name or "").strip() or inv.full_name
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    try:
        criado = await db.execute(
            text(
                "INSERT INTO users (id, tenant_id, email, hashed_password, full_name, role, is_active) "
                "VALUES (gen_random_uuid(), :t, :e, :p, :n, :r, true) "
                "RETURNING id, tenant_id"
            ),
            {
                "t": str(inv.tenant_id),
                "e": inv.email,
                "p": hashed,
                "n": nome,
                "r": inv.role,
            },
        )
    except IntegrityError:
        # uq_users_tenant_email: alguém já criou esse e-mail entre o convite e o aceite.
        # Rollback devolve o convite ao estado pendente — o admin resolve por fora.
        await db.rollback()
        raise TeamError("email_em_uso", "Já existe uma conta com esse e-mail.")

    u = criado.fetchone()
    await db.commit()
    return {"user_id": u.id, "tenant_id": u.tenant_id}


async def list_members(db: AsyncSession, tenant_id: UUID) -> list[dict]:
    rows = await db.execute(
        text(
            "SELECT id, email, full_name, role, is_active, created_at FROM users "
            "WHERE tenant_id = :t ORDER BY is_active DESC, full_name NULLS LAST, email"
        ),
        {"t": str(tenant_id)},
    )
    return [
        {
            "id": r.id,
            "email": r.email,
            "full_name": r.full_name,
            "role": r.role,
            "is_active": r.is_active,
            "created_at": r.created_at,
        }
        for r in rows.fetchall()
    ]


async def update_member(
    db: AsyncSession,
    tenant_id: UUID,
    actor_id: UUID,
    user_id: UUID,
    role: UserRole | None = None,
    is_active: bool | None = None,
) -> dict:
    """Muda papel e/ou ativação de um membro, sem deixar o tenant sem administrador."""
    alvo = await db.execute(
        text("SELECT role, is_active FROM users WHERE id = :id AND tenant_id = :t"),
        {"id": str(user_id), "t": str(tenant_id)},
    )
    atual = alvo.fetchone()
    if atual is None:
        raise TeamError("nao_encontrado", "Usuário não encontrado.")

    vira_nao_admin = role is not None and role != UserRole.ADMIN
    vira_inativo = is_active is False
    perde_admin = atual.role == UserRole.ADMIN.name and (vira_nao_admin or vira_inativo)

    if perde_admin:
        # Sem esta trava o negócio se tranca fora da própria conta: ninguém para
        # convidar, mexer na IA ou reconectar o WhatsApp, e nenhuma tela de suporte
        # para desfazer. Vale inclusive quando o admin faz isso consigo mesmo.
        restantes = await db.execute(
            text(
                "SELECT count(*) FROM users "
                "WHERE tenant_id = :t AND role = 'ADMIN' AND is_active = true AND id <> :id"
            ),
            {"t": str(tenant_id), "id": str(user_id)},
        )
        if (restantes.scalar_one() or 0) == 0:
            raise TeamError(
                "ultimo_admin",
                "Este é o último administrador ativo. Promova outra pessoa antes.",
            )

    if user_id == actor_id and vira_inativo:
        raise TeamError("auto_desativacao", "Você não pode desativar a si mesmo.")

    campos = []
    params: dict = {"id": str(user_id), "t": str(tenant_id)}
    if role is not None:
        campos.append("role = :r")
        params["r"] = role.name
    if is_active is not None:
        campos.append("is_active = :a")
        params["a"] = is_active
    if not campos:
        raise TeamError("nada_para_mudar", "Nada para alterar.")

    row = await db.execute(
        text(
            f"UPDATE users SET {', '.join(campos)} "
            "WHERE id = :id AND tenant_id = :t "
            "RETURNING id, email, full_name, role, is_active, created_at"
        ),
        params,
    )
    r = row.fetchone()
    await db.commit()
    return {
        "id": r.id,
        "email": r.email,
        "full_name": r.full_name,
        "role": r.role,
        "is_active": r.is_active,
        "created_at": r.created_at,
    }
