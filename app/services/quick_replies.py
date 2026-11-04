"""CRUD de respostas rápidas (canned responses) por tenant."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _slugify_shortcut(raw: str) -> str:
    return raw.strip().lstrip("/").lower()[:32]


async def list_all(db: AsyncSession, tenant_id: UUID) -> list[dict]:
    rows = await db.execute(
        text(
            "SELECT id, shortcut, title, content FROM quick_replies "
            "WHERE tenant_id = :tenant_id ORDER BY shortcut"
        ),
        {"tenant_id": str(tenant_id)},
    )
    return [
        {
            "id": str(r.id),
            "shortcut": r.shortcut,
            "title": r.title,
            "content": r.content,
        }
        for r in rows.fetchall()
    ]


async def create(
    db: AsyncSession,
    tenant_id: UUID,
    created_by: UUID,
    shortcut: str,
    title: str,
    content: str,
) -> dict | None:
    """Cria uma resposta rápida. None se o atalho já existe no tenant."""
    sc = _slugify_shortcut(shortcut)
    row = await db.execute(
        text(
            "INSERT INTO quick_replies (id, tenant_id, shortcut, title, content, created_by) "
            "VALUES (gen_random_uuid(), :tenant_id, :shortcut, :title, :content, :created_by) "
            "ON CONFLICT (tenant_id, shortcut) DO NOTHING "
            "RETURNING id"
        ),
        {
            "tenant_id": str(tenant_id),
            "shortcut": sc,
            "title": title,
            "content": content,
            "created_by": str(created_by),
        },
    )
    r = row.fetchone()
    if r is None:
        await db.rollback()
        return None
    await db.commit()
    return {"id": str(r.id), "shortcut": sc, "title": title, "content": content}


async def update(
    db: AsyncSession,
    tenant_id: UUID,
    qr_id: UUID,
    shortcut: str,
    title: str,
    content: str,
) -> dict | None:
    sc = _slugify_shortcut(shortcut)
    row = await db.execute(
        text(
            "UPDATE quick_replies SET shortcut = :shortcut, title = :title, "
            "content = :content, updated_at = now() "
            "WHERE id = :id AND tenant_id = :tenant_id RETURNING id"
        ),
        {
            "id": str(qr_id),
            "tenant_id": str(tenant_id),
            "shortcut": sc,
            "title": title,
            "content": content,
        },
    )
    r = row.fetchone()
    if r is None:
        return None
    await db.commit()
    return {"id": str(qr_id), "shortcut": sc, "title": title, "content": content}


async def delete(db: AsyncSession, tenant_id: UUID, qr_id: UUID) -> bool:
    row = await db.execute(
        text(
            "DELETE FROM quick_replies WHERE id = :id AND tenant_id = :tenant_id RETURNING id"
        ),
        {"id": str(qr_id), "tenant_id": str(tenant_id)},
    )
    deleted = row.fetchone() is not None
    if deleted:
        await db.commit()
    return deleted
