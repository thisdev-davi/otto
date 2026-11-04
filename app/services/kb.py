"""Knowledge sources are replaced atomically only after a complete embedding batch."""

import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import embeddings
from app.ai.chunking import chunk_text
from app.ai.embeddings import embed_batch


async def _set_tenant_guc(db: AsyncSession, tenant_id: UUID) -> None:
    await db.execute(
        text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant_id)}
    )


async def ingest_document(
    db: AsyncSession,
    tenant_id: UUID,
    source: str,
    title: str,
    content: str,
    api_key: str,
    provider: str,
) -> int:
    parts = chunk_text(content)
    if not parts:
        return 0
    encoded = await embed_batch(parts, api_key, provider)
    if not encoded or len(encoded) != len(parts):
        return 0
    await _set_tenant_guc(db, tenant_id)
    values = {
        "tenant_id": str(tenant_id),
        "source": source,
        "title": title,
        "content": content,
    }
    await db.execute(
        text("""
        INSERT INTO kb_sources (tenant_id,source,title,content,updated_at)
        VALUES (:tenant_id,:source,:title,:content,now())
        ON CONFLICT (tenant_id,source) DO UPDATE
        SET title=excluded.title,content=excluded.content,updated_at=now()
    """),
        values,
    )
    await db.execute(
        text(
            "DELETE FROM kb_documents WHERE tenant_id=:tenant_id AND source_url=:source"
        ),
        values,
    )
    for part, vector in zip(parts, encoded, strict=True):
        await db.execute(
            text("""
            INSERT INTO kb_documents (id,tenant_id,source_url,title,content,embedding,embedding_model,created_at)
            VALUES (gen_random_uuid(),:tenant_id,:source,:title,:content,CAST(:embedding AS vector),:model,now())
        """),
            {
                **values,
                "content": part,
                "embedding": json.dumps(vector),
                "model": embeddings.model_id(provider),
            },
        )
    await db.commit()
    return len(parts)


async def list_sources(db: AsyncSession, tenant_id: UUID) -> list[dict]:
    await _set_tenant_guc(db, tenant_id)
    rows = await db.execute(
        text("""
        SELECT source_url, max(title) AS title, count(*) AS chunks,
               max(embedding_model) AS embedding_model, max(created_at) AS created_at
        FROM kb_documents WHERE tenant_id=:tenant_id
        GROUP BY source_url ORDER BY max(created_at) DESC
    """),
        {"tenant_id": str(tenant_id)},
    )
    return [
        dict(
            source=row.source_url,
            title=row.title,
            chunks=row.chunks,
            embedding_model=row.embedding_model,
            created_at=row.created_at,
        )
        for row in rows.fetchall()
    ]


async def delete_source(db: AsyncSession, tenant_id: UUID, source: str) -> int:
    await _set_tenant_guc(db, tenant_id)
    params = {"tenant_id": str(tenant_id), "source": source}
    removed = await db.execute(
        text(
            "DELETE FROM kb_documents WHERE tenant_id=:tenant_id AND source_url=:source RETURNING id"
        ),
        params,
    )
    count = len(removed.fetchall())
    await db.execute(
        text("DELETE FROM kb_sources WHERE tenant_id=:tenant_id AND source=:source"),
        params,
    )
    await db.commit()
    return count
