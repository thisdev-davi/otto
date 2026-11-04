"""Tenant audit, strict metadata minimization, and coordinated 180-day retention.

Business mutations are captured by PostgreSQL triggers, not by parsing SQL in Python.
Explicit events describe semantic decisions and external operation intent/results.
No historical events are synthesized. This is evidence of actions, not LGPD certification.
"""

import asyncio
import base64
import csv
import io
import ipaddress
import json
import logging
import re
from contextvars import ContextVar
from contextlib import contextmanager
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger(__name__)
request_context: ContextVar[Any | None] = ContextVar("audit_request", default=None)
_actor_context: ContextVar[dict[str, Any] | None] = ContextVar(
    "audit_actor", default=None
)


@contextmanager
def actor_context(tenant_id: UUID, *, actor_type: str = "system") -> Iterator[None]:
    """A verified worker task establishes one actor/correlation for its whole turn."""
    if actor_type not in {"bot", "system"}:
        raise ValueError("Worker context must be bot or system")
    token = _actor_context.set(
        {
            "tenant_id": tenant_id,
            "actor_type": actor_type,
            "actor_user_id": None,
            "correlation_id": uuid4(),
            "ip_network": None,
        }
    )
    try:
        yield
    finally:
        _actor_context.reset(token)


def get_context() -> dict[str, Any]:
    request = request_context.get()
    identity = getattr(request.state, "audit_identity", None) if request else None
    if identity is None and _actor_context.get() is not None:
        return dict(_actor_context.get())
    return {
        "tenant_id": identity.tenant_id if identity else None,
        "actor_user_id": identity.user_id if identity else None,
        "actor_type": "human" if identity else "system",
        "correlation_id": getattr(request.state, "audit_correlation_id", None)
        if request
        else None,
        "ip_network": mask_ip(request.client.host)
        if request and request.client
        else None,
    }


_SAFE_CODE = re.compile(r"^[a-zA-Z0-9_./:-]{1,100}$")
_METADATA_KEYS = frozenset(
    {
        "count",
        "status",
        "status_code",
        "method",
        "endpoint",
        "mode",
        "provider",
        "error_type",
        "decision",
        "attempt",
        "accepted",
        "rows",
        "operation_id",
        "changed_fields",
        "has_cursor",
        "limit",
        "filter_count",
    }
)


def mask_ip(address: str | None) -> str | None:
    """Only the peer address; proxy headers need explicit trusted-proxy configuration."""
    if not address:
        return None
    try:
        ip = ipaddress.ip_address(address)
        return str(
            ipaddress.ip_network(f"{ip}/{24 if ip.version == 4 else 48}", strict=False)
        )
    except ValueError:
        return None


def safe_metadata(value: dict[str, Any] | None) -> dict[str, Any]:
    """Allowlist field names AND machine values; never serialize arbitrary payloads."""
    result: dict[str, Any] = {}
    for key, item in (value or {}).items():
        if key not in _METADATA_KEYS:
            continue
        if isinstance(item, bool | int) or item is None:
            result[key] = item
        elif isinstance(item, str) and _SAFE_CODE.fullmatch(item):
            result[key] = item
        elif key == "changed_fields" and isinstance(item, list):
            result[key] = [
                field
                for field in item[:100]
                if isinstance(field, str) and _SAFE_CODE.fullmatch(field)
            ]
    return result


async def bind_media_identity(
    session: AsyncSession, credentials: Any, tenant_id: UUID, request: Any
) -> bool:
    """Verify bearer membership before media access/human attribution.

    The caller must first check the resource signature. A valid signed link is its
    own access mode and must not be attributed to an incidental bearer credential.
    """
    from app.api.auth import decode_token

    identity = decode_token(credentials.credentials) if credentials else None
    if identity is None or str(identity.tenant_id) != str(tenant_id):
        return False
    version = await session.scalar(
        text(
            "SELECT token_version FROM users WHERE id=:uid AND tenant_id=:tid AND is_active=true"
        ),
        {"uid": identity.user_id, "tid": identity.tenant_id},
    )
    if version is None or version != identity.token_version:
        return False
    if not hasattr(request.state, "audit_correlation_id"):
        request.state.audit_correlation_id = uuid4()
    request.state.audit_identity = identity
    return True


async def record(
    session: AsyncSession,
    tenant_id: UUID,
    action: str,
    resource_type: str,
    resource_id: UUID | str | None = None,
    *,
    result: str = "success",
    reason: str | None = None,
    actor_type: str = "system",
    actor_user_id: UUID | None = None,
    metadata: dict[str, Any] | None = None,
    correlation_id: UUID | None = None,
    ip_network: str | None = None,
) -> UUID:
    """Insert in caller's transaction. Never commit and never swallow audit failures."""
    request = session.info.get("audit_request")
    identity = getattr(request.state, "audit_identity", None) if request else None
    context = get_context()
    if identity and actor_type == "system" and actor_user_id is None:
        actor_type = "human"
        actor_user_id = identity.user_id
    if identity and str(identity.tenant_id) != str(tenant_id):
        raise ValueError("Audit tenant does not match authenticated identity")
    if not identity and context["tenant_id"] is not None:
        if str(context["tenant_id"]) != str(tenant_id):
            raise ValueError("Audit tenant does not match worker context")
        if actor_type == "system" and actor_user_id is None:
            actor_type = context["actor_type"]
            actor_user_id = context["actor_user_id"]
    if actor_type != "human" and actor_user_id is not None:
        raise ValueError("Only verified human events may contain actor_user_id")
    if reason and not _SAFE_CODE.fullmatch(reason):
        raise ValueError("Audit reason must be a machine-readable code")
    event_id = uuid4()
    await session.execute(
        text(
            "INSERT INTO audit_events "
            "(id,tenant_id,created_at,actor_type,actor_user_id,action,resource_type,resource_id,"
            "result,reason,correlation_id,ip_network,metadata) "
            "VALUES (:id,:tenant,clock_timestamp(),:actor,:user,:action,:resource,:rid,:result,:reason,"
            ":correlation,:ip,CAST(:metadata AS jsonb))"
        ),
        {
            "id": event_id,
            "tenant": tenant_id,
            "actor": actor_type,
            "user": actor_user_id,
            "action": action,
            "resource": resource_type,
            "rid": str(resource_id) if resource_id is not None else None,
            "result": result,
            "reason": reason,
            "correlation": correlation_id
            or context["correlation_id"]
            or (
                getattr(request.state, "audit_correlation_id", None)
                if request
                else None
            )
            or uuid4(),
            "ip": ip_network
            or context["ip_network"]
            or (mask_ip(request.client.host) if request and request.client else None),
            "metadata": json.dumps(safe_metadata(metadata)),
        },
    )
    return event_id


def encode_cursor(created_at: datetime, event_id: UUID) -> str:
    return (
        base64.urlsafe_b64encode(
            json.dumps([created_at.isoformat(), str(event_id)]).encode()
        )
        .decode()
        .rstrip("=")
    )


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = json.loads(
            base64.b64decode(
                cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True
            )
        )
        if not isinstance(raw, list) or len(raw) != 2:
            raise ValueError
        timestamp = datetime.fromisoformat(raw[0])
        if timestamp.tzinfo is None:
            raise ValueError
        return timestamp.astimezone(timezone.utc), UUID(raw[1])
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid audit cursor") from exc


def query(
    tenant_id: UUID,
    *,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    actor_id: UUID | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    result: str | None = None,
    cursor: str | None = None,
    limit: int | None = 50,
) -> tuple[Any, dict[str, Any]]:
    conditions = ["tenant_id = :tenant_id"]
    params: dict[str, Any] = {"tenant_id": tenant_id}
    for key, value in {
        "date_from": date_from,
        "date_to": date_to,
        "actor_id": actor_id,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "result": result,
    }.items():
        if value is not None:
            column, operator = {
                "date_from": ("created_at", ">="),
                "date_to": ("created_at", "<="),
                "actor_id": ("actor_user_id", "="),
            }.get(key, (key, "="))
            conditions.append(f"{column} {operator} :{key}")
            params[key] = value
    if cursor:
        params["cursor_date"], params["cursor_id"] = decode_cursor(cursor)
        conditions.append("(created_at, id) < (:cursor_date, :cursor_id)")
    sql = (
        "SELECT * FROM audit_events WHERE "
        + " AND ".join(conditions)
        + " ORDER BY created_at DESC, id DESC"
    )
    if limit is not None:
        sql += " LIMIT :limit"
        params["limit"] = limit
    return text(sql), params


def csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict | list):
        value = json.dumps(value, ensure_ascii=False)
    rendered = str(value)
    # Spreadsheet programs can skip whitespace/control characters before formulas.
    if rendered.lstrip(" \t\r\n").startswith(
        ("=", "+", "-", "@")
    ) or rendered.startswith(("\t", "\r", "\n")):
        return "'" + rendered
    return rendered


CSV_COLUMNS = (
    "id",
    "created_at",
    "actor_type",
    "actor_user_id",
    "action",
    "resource_type",
    "resource_id",
    "result",
    "reason",
    "correlation_id",
    "ip_network",
    "metadata",
)


def csv_line(values: Any) -> str:
    buffer = io.StringIO(newline="")
    csv.writer(buffer).writerow([csv_value(value) for value in values])
    return buffer.getvalue()


async def cleanup_expired() -> int:
    """One transaction/instance at a time; deletion exception only covers expired rows."""
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        locked = await session.scalar(
            text("SELECT pg_try_advisory_xact_lock(1979103, 180)")
        )
        if not locked:
            return 0
        await session.execute(text("SET LOCAL otto.audit_retention = 'on'"))
        result = await session.execute(
            text(
                "DELETE FROM audit_events WHERE created_at < clock_timestamp() - interval '180 days'"
            )
        )
        await session.commit()
        return result.rowcount


async def retention_loop() -> None:
    while True:
        try:
            await cleanup_expired()
        except Exception:
            log.exception("Audit retention failed")
        await asyncio.sleep(3600)
