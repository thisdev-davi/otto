"""Tenant-scoped retrieval with cosine ranking and PostgreSQL text fallback."""

import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import embeddings
from app.ai.embeddings import embed


async def _scope(db: AsyncSession, tenant_id: UUID) -> None:
    await db.execute(
        text("SELECT set_config('app.tenant_id', :tid, true)"), {"tid": str(tenant_id)}
    )


async def search(
    db: AsyncSession,
    tenant_id: UUID,
    query: str,
    api_key: str,
    provider: str,
    top_k: int = 5,
    max_distance: float | None = None,
) -> list[dict]:
    limit = min(50, max(1, top_k))
    vector = await embed(query, api_key, provider)
    if vector is None:
        return await _keyword_search(db, tenant_id, query, limit)
    await _scope(db, tenant_id)
    rows = await db.execute(
        text("""
        SELECT title, source_url, content,
               embedding <=> CAST(:qvec AS vector) AS distance
        FROM kb_documents
        WHERE tenant_id = :tenant_id
          AND embedding_model = :model AND embedding IS NOT NULL
        ORDER BY embedding <=> CAST(:qvec AS vector)
        LIMIT :top_k
    """),
        {
            "qvec": json.dumps(vector),
            "tenant_id": str(tenant_id),
            "model": embeddings.model_id(provider),
            "top_k": limit,
        },
    )
    threshold = (
        embeddings.max_distance(provider) if max_distance is None else max_distance
    )
    hits = []
    for row in rows.fetchall():
        if row.distance is not None and float(row.distance) <= threshold:
            hits.append(
                {
                    "title": row.title,
                    "source_url": row.source_url,
                    "content": row.content,
                    "distance": float(row.distance),
                }
            )
    return hits


async def _keyword_search(
    db: AsyncSession, tenant_id: UUID, query: str, top_k: int
) -> list[dict]:
    await _scope(db, tenant_id)
    rows = await db.execute(
        text("""
        WITH terms AS (
            SELECT replace(plainto_tsquery('portuguese', :q)::text, '&', '|')::tsquery AS query
        ), documents AS (
            SELECT title,source_url,content,
                   to_tsvector('portuguese', coalesce(title,'') || ' ' || content) AS document
            FROM kb_documents WHERE tenant_id = :tenant_id
        )
        SELECT title,source_url,content,ts_rank(document,terms.query) AS rank
        FROM documents CROSS JOIN terms WHERE document @@ terms.query
        ORDER BY rank DESC LIMIT :top_k
    """),
        {"q": query, "tenant_id": str(tenant_id), "top_k": top_k},
    )
    return [
        {
            "title": row.title,
            "source_url": row.source_url,
            "content": row.content,
            "distance": 0.05 / (float(row.rank) + 0.05),
        }
        for row in rows.fetchall()
    ]
