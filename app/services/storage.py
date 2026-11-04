"""Private media storage, independent of the process that receives or serves a file.

The persisted locator selects the backend on reads, not the current environment.
Old local paths therefore keep working while new files move to object storage.
No signed bucket URLs escape this service: application routes authorize every read.
"""

import asyncio
import logging
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from urllib.parse import urlsplit

from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import settings

log = logging.getLogger(__name__)


def _client() -> Any:
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL or None,
        region_name=settings.S3_REGION,
        aws_access_key_id=settings.S3_ACCESS_KEY_ID or None,
        aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY or None,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": settings.S3_ADDRESSING_STYLE},
            connect_timeout=5,
            read_timeout=30,
            retries={"max_attempts": 0, "mode": "standard"},
        ),
    )


def _key(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError("Invalid media object key")
    return str(path)


def _s3_location(locator: str) -> tuple[str, str]:
    parsed = urlsplit(locator)
    if parsed.scheme != "s3" or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("Invalid media storage locator")
    # A locator never grants access to another configured bucket.
    if parsed.netloc != settings.S3_BUCKET:
        raise ValueError("Media bucket differs from configured bucket")
    return parsed.netloc, _key(parsed.path.lstrip("/"))


def is_remote(locator: str) -> bool:
    return locator.startswith("s3://")


def _put_local(key: str, data: bytes) -> str:
    destination = Path(settings.MEDIA_DIR).resolve() / _key(key)
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Readers never observe a partially written file.
    fd, temporary = tempfile.mkstemp(dir=destination.parent, prefix=".upload-")
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return str(destination)


async def put(
    key: str, data: bytes, mimetype: str, *, audit_session: AsyncSession | None = None
) -> str:
    key = _key(key)
    if settings.MEDIA_STORAGE_BACKEND == "local":
        return await asyncio.to_thread(_put_local, key, data)
    return await put_s3(key, data, mimetype, audit_session=audit_session)


async def put_s3(
    key: str, data: bytes, mimetype: str, *, audit_session: AsyncSession | None = None
) -> str:
    """Explicit target used by the migration script even while writes remain local."""
    key = _key(key)
    if not settings.S3_BUCKET:
        raise ValueError("S3_BUCKET is required")
    operation_id = uuid4()
    tenant_id = UUID(key.split("/", 1)[0]) if audit_session is not None else None
    if audit_session is not None:
        await _audit_storage_event(audit_session, tenant_id, operation_id, "intent")
    try:
        await asyncio.to_thread(
            _client().put_object,
            Bucket=settings.S3_BUCKET,
            Key=key,
            Body=data,
            ContentType=mimetype,
            # ACL intentionally omitted: compatible providers may disable ACLs.
        )
    except Exception:
        if audit_session is not None:
            await _audit_storage_outcome(
                audit_session, tenant_id, operation_id, "unknown"
            )
        # No automatic retry: the remote service may already have stored the object.
        raise
    if audit_session is not None:
        await _audit_storage_outcome(audit_session, tenant_id, operation_id, "success")
    return f"s3://{settings.S3_BUCKET}/{key}"


async def _audit_storage_outcome(
    origin: AsyncSession, tenant_id: UUID, operation_id: UUID, result: str
) -> None:
    try:
        await _audit_storage_event(origin, tenant_id, operation_id, result)
    except Exception:
        # Dispatch already happened. Preserve the provider's known success/original
        # error, and leave the committed intent as evidence for reconciliation.
        # Never log exception text: drivers can include credentials or object keys.
        log.error(
            "storage_audit_result_failed operation_id=%s result=%s",
            operation_id,
            result,
        )


async def _audit_storage_event(
    origin: AsyncSession, tenant_id: UUID, operation_id: UUID, result: str
) -> None:
    from app.core.database import WorkerSessionLocal
    from app.services import audit

    # The external-operation ledger is independent of the pending business write.
    # Never commit the caller's transaction simply because an upload succeeded.
    async with WorkerSessionLocal() as ledger:
        ledger.info.update({"audit_request": origin.info.get("audit_request")})
        await audit.record(
            ledger,
            tenant_id,
            "storage.write.intent" if result == "intent" else "storage.write.result",
            "media",
            operation_id,
            result=result,
            reason="storage_error" if result == "unknown" else None,
            correlation_id=operation_id,
        )
        await ledger.commit()


def _read_remote(locator: str) -> bytes:
    bucket, key = _s3_location(locator)
    try:
        response = _client().get_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {
            "NoSuchKey",
            "404",
            "NotFound",
        }:
            raise FileNotFoundError("Media object does not exist") from exc
        raise
    body = response["Body"]
    try:
        return body.read()
    finally:
        body.close()


async def read(locator: str) -> bytes:
    if is_remote(locator):
        return await asyncio.to_thread(_read_remote, locator)
    return await asyncio.to_thread(Path(locator).read_bytes)


async def exists(locator: str) -> bool:
    if not is_remote(locator):
        return await asyncio.to_thread(Path(locator).is_file)
    bucket, key = _s3_location(locator)
    try:
        await asyncio.to_thread(_client().head_object, Bucket=bucket, Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {
            "NoSuchKey",
            "404",
            "NotFound",
        }:
            return False
        raise
    return True
