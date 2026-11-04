"""Administrative, tenant-scoped, immutable audit access."""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import TokenData, require_admin
from app.core.database import AsyncSessionLocal, get_session
from app.services import audit

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditItem(BaseModel):
    id: UUID
    created_at: datetime
    actor_type: str
    actor_user_id: UUID | None
    action: str
    resource_type: str
    resource_id: str | None
    result: str
    reason: str | None
    correlation_id: UUID
    ip_network: str | None
    metadata: dict[str, Any]


class AuditPage(BaseModel):
    items: list[AuditItem]
    next_cursor: str | None


def filters(
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    actor_id: UUID | None = None,
    action: Annotated[str | None, Query(max_length=100)] = None,
    resource_type: Annotated[str | None, Query(max_length=64)] = None,
    resource_id: Annotated[str | None, Query(max_length=100)] = None,
    result: Annotated[str | None, Query(max_length=24)] = None,
) -> dict[str, Any]:
    if any(value and value.tzinfo is None for value in (date_from, date_to)):
        raise HTTPException(422, "As datas devem incluir fuso horário.")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "Intervalo de datas inválido.")
    return {
        "date_from": date_from,
        "date_to": date_to,
        "actor_id": actor_id,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "result": result,
    }


@router.get("", response_model=AuditPage)
async def list_events(
    current_user: Annotated[TokenData, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    selected: Annotated[dict[str, Any], Depends(filters)],
    cursor: Annotated[str | None, Query(max_length=300)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> AuditPage:
    try:
        statement, parameters = audit.query(
            current_user.tenant_id, **selected, cursor=cursor, limit=limit + 1
        )
    except ValueError as exc:
        raise HTTPException(422, "Cursor inválido.") from exc
    rows = (await session.execute(statement, parameters)).mappings().all()
    items = [AuditItem.model_validate(dict(row)) for row in rows[:limit]]
    next_cursor = (
        audit.encode_cursor(items[-1].created_at, items[-1].id)
        if len(rows) > limit
        else None
    )
    await audit.record(
        session,
        current_user.tenant_id,
        "audit.read",
        "audit_events",
        metadata={"limit": limit, "has_cursor": bool(cursor)},
    )
    await session.commit()
    return AuditPage(items=items, next_cursor=next_cursor)


@router.get("/export")
async def export_events(
    current_user: Annotated[TokenData, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    selected: Annotated[dict[str, Any], Depends(filters)],
) -> StreamingResponse:
    # Persist intent before returning bytes. An interrupted download is not declared successful.
    operation_id = await audit.record(
        session,
        current_user.tenant_id,
        "audit.export",
        "audit_events",
        result="accepted",
    )
    await session.commit()

    async def generate():
        count = 0
        async with AsyncSessionLocal() as export_session:
            statement, parameters = audit.query(
                current_user.tenant_id, **selected, limit=None
            )
            rows = await export_session.stream(statement, parameters)
            yield "\ufeff" + audit.csv_line(audit.CSV_COLUMNS)
            async for row in rows.mappings():
                count += 1
                yield audit.csv_line(row[column] for column in audit.CSV_COLUMNS)
            await audit.record(
                export_session,
                current_user.tenant_id,
                "audit.export.completed",
                "audit_events",
                actor_type="human",
                actor_user_id=current_user.user_id,
                metadata={"rows": count, "operation_id": str(operation_id)},
            )
            await export_session.commit()

    return StreamingResponse(
        generate(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": 'attachment; filename="otto-auditoria.csv"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
