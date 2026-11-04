"""Storage tests use a fake S3 service and the isolated validation database only."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import boto3
import httpx
import pytest
import pytest_asyncio
from moto import mock_aws
from sqlalchemy import text

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.database_urls import async_url, sync_url
from app.main import app
from app.models.enums import ContentType
from app.services import attachments, avatars, media_links, salao_fotos, storage
from app.tasks import media as media_tasks
from scripts.migrate_media_storage import migrate


@pytest.fixture
def object_store(monkeypatch):
    monkeypatch.setattr(settings, "MEDIA_STORAGE_BACKEND", "s3")
    monkeypatch.setattr(settings, "S3_BUCKET", "otto-validation-private")
    monkeypatch.setattr(settings, "S3_REGION", "us-east-1")
    monkeypatch.setattr(settings, "S3_ENDPOINT_URL", "")
    monkeypatch.setattr(settings, "S3_ACCESS_KEY_ID", "testing")
    monkeypatch.setattr(settings, "S3_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        client = boto3.client(
            "s3",
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        client.create_bucket(Bucket=settings.S3_BUCKET)
        yield client


async def test_local_locator_survives_switch_to_s3(tmp_path, monkeypatch, object_store):
    monkeypatch.setattr(settings, "MEDIA_STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "MEDIA_DIR", str(tmp_path))
    anexo = attachments.Anexo(
        b"local-content", "sample.png", "image/png", ContentType.IMAGE
    )
    locator = await attachments.gravar(anexo, uuid4(), uuid4())
    monkeypatch.setattr(settings, "MEDIA_STORAGE_BACKEND", "s3")
    assert await storage.read(locator) == b"local-content"
    remote = await attachments.gravar(anexo, uuid4(), uuid4())
    assert remote.startswith("s3://")
    assert await storage.read(remote) == b"local-content"
    assert await storage.exists(remote)
    assert (
        object_store.get_object_acl(
            Bucket=settings.S3_BUCKET, Key=remote.split("/", 3)[3]
        )["Grants"][0]["Permission"]
        == "FULL_CONTROL"
    )
    assert (
        len(
            object_store.get_object_acl(
                Bucket=settings.S3_BUCKET, Key=remote.split("/", 3)[3]
            )["Grants"]
        )
        == 1
    )


@pytest.mark.parametrize(
    "key", ["../escape", "/absolute", "tenant/../../escape", "tenant\\escape"]
)
async def test_storage_rejects_unsafe_object_keys(key, object_store):
    with pytest.raises(ValueError):
        await storage.put(key, b"data", "image/png")


async def test_missing_and_other_bucket(object_store):
    assert not await storage.exists(f"s3://{settings.S3_BUCKET}/missing")
    with pytest.raises(FileNotFoundError):
        await storage.read(f"s3://{settings.S3_BUCKET}/missing")
    with pytest.raises(ValueError):
        await storage.read("s3://another-account/private-object")


def test_neon_driver_urls_keep_tls_and_encoded_credentials():
    original = "postgresql://user:encoded%25secret@ep-example-pooler.neon.tech/otto?sslmode=require&channel_binding=require"
    runtime = async_url(original)
    assert runtime.startswith("postgresql+asyncpg://")
    assert "ssl=require" in runtime and "channel_binding" not in runtime
    assert (
        "encoded%25secret" in runtime and "prepared_statement_cache_size=0" in runtime
    )
    direct = sync_url(original.replace("-pooler", ""))
    assert direct.startswith("postgresql+psycopg2://") and "sslmode=require" in direct


@pytest_asyncio.fixture
async def stored_resources(tmp_path):
    tid, cid, conv, mid, fid = (uuid4() for _ in range(5))
    file = tmp_path / "legacy.ogg"
    file.write_bytes(b"0123456789")
    meta = {
        "status": "stored",
        "path": str(file),
        "mimetype": "audio/ogg",
        "transcript_status": "pending",
    }
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("INSERT INTO tenants (id,name) VALUES (:id,'Storage validation')"),
            {"id": tid},
        )
        await db.execute(
            text(
                "INSERT INTO contacts (id,tenant_id,channel,external_id,channel_metadata) VALUES (:id,:tid,'WHATSAPP',:external,CAST(:meta AS jsonb))"
            ),
            {
                "id": cid,
                "tid": tid,
                "external": str(cid),
                "meta": json.dumps({"avatar": meta}),
            },
        )
        await db.execute(
            text(
                "INSERT INTO conversations (id,tenant_id,contact_id,status,unread_count) VALUES (:id,:tid,:cid,'OPEN',0)"
            ),
            {"id": conv, "tid": tid, "cid": cid},
        )
        await db.execute(
            text(
                "INSERT INTO messages (id,tenant_id,conversation_id,direction,content_type,channel,channel_metadata) VALUES (:id,:tid,:conv,'INBOUND','AUDIO','WHATSAPP',CAST(:meta AS jsonb))"
            ),
            {"id": mid, "tid": tid, "conv": conv, "meta": json.dumps({"media": meta})},
        )
        await db.execute(
            text(
                "INSERT INTO salao_fotos (id,tenant_id,caminho,mimetype,tamanho_bytes,ordem) VALUES (:id,:tid,:path,'image/png',10,0)"
            ),
            {"id": fid, "tid": tid, "path": str(file)},
        )
        await db.commit()
    yield SimpleNamespace(
        tenant=tid, contact=cid, conversation=conv, message=mid, photo=fid, local=file
    )
    async with AsyncSessionLocal() as db:
        for table in ("salao_fotos", "messages", "conversations", "contacts"):
            await db.execute(
                text(f"DELETE FROM {table} WHERE tenant_id=:tid"), {"tid": tid}
            )
        await db.execute(text("DELETE FROM tenants WHERE id=:tid"), {"tid": tid})
        await db.commit()


async def test_migration_keeps_local_then_serves_all_private_resources(
    stored_resources, object_store
):
    resource = stored_resources
    dry_run = await migrate(tenant_id=resource.tenant)
    assert dry_run["references"] >= 3 and dry_run["updated"] == 0
    result = await migrate(True, resource.tenant)
    assert result["updated"] >= 3
    assert resource.local.read_bytes() == b"0123456789"
    async with AsyncSessionLocal() as db:
        avatar = await avatars.arquivo_de(db, resource.tenant, resource.contact)
        photo = await salao_fotos.caminho_de(db, resource.tenant, resource.photo)
    assert avatar[0].startswith("s3://") and photo[0].startswith("s3://")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for kind, identity, url in (
            ("media", resource.message, f"/media/{resource.message}"),
            ("avatar", resource.contact, f"/contacts/{resource.contact}/avatar"),
            ("foto", resource.photo, f"/espaco/fotos/{resource.photo}/arquivo"),
        ):
            signed = media_links.assinar(kind, identity, resource.tenant)
            response = await client.get(f"{url}?{signed}")
            assert response.status_code == 200 and response.content == b"0123456789"
            assert response.headers["x-content-type-options"] == "nosniff"
            assert (
                response.headers["content-security-policy"]
                == "default-src 'none'; sandbox"
            )
            invalid = media_links.assinar(kind, identity, uuid4())
            assert (await client.get(f"{url}?{invalid}")).status_code in {401, 404}
        signed = media_links.assinar("media", resource.message, resource.tenant)
        response = await client.get(
            f"/media/{resource.message}?{signed}", headers={"Range": "bytes=2-5"}
        )
        assert response.status_code == 206 and response.content == b"2345"
        assert response.headers["content-range"] == "bytes 2-5/10"
        response = await client.get(
            f"/media/{resource.message}?{signed}", headers={"Range": "bytes=20-30"}
        )
        assert response.status_code == 416


async def test_transcription_reads_shared_s3_object(
    stored_resources, object_store, monkeypatch
):
    resource = stored_resources
    await migrate(True, resource.tenant)
    transcribe = AsyncMock(return_value="Mensagem transcrita")
    monkeypatch.setattr(media_tasks.transcription, "transcribe", transcribe)
    monkeypatch.setattr(media_tasks, "_dar_a_vez_ao_bot", AsyncMock())
    await media_tasks._transcribe(resource.message, resource.tenant)
    assert transcribe.await_args.args[0] == b"0123456789"
    async with AsyncSessionLocal() as db:
        text_value = (
            await db.execute(
                text("SELECT content FROM messages WHERE id=:id AND tenant_id=:tid"),
                {"id": resource.message, "tid": resource.tenant},
            )
        ).scalar_one()
    assert text_value == "Mensagem transcrita"


async def test_storage_ledger_does_not_commit_pending_business_write(
    stored_resources, object_store
):
    resource = stored_resources
    async with AsyncSessionLocal() as db:
        await db.execute(
            text("UPDATE tenants SET name='Pending change' WHERE id=:id"),
            {"id": resource.tenant},
        )
        locator = await storage.put(
            f"{resource.tenant}/{uuid4()}.png", b"object", "image/png", audit_session=db
        )
        async with AsyncSessionLocal() as observer:
            name = (
                await observer.execute(
                    text("SELECT name FROM tenants WHERE id=:id"),
                    {"id": resource.tenant},
                )
            ).scalar_one()
            results = (
                (
                    await observer.execute(
                        text(
                            "SELECT result FROM audit_events WHERE tenant_id=:id AND action LIKE 'storage.write.%' ORDER BY created_at"
                        ),
                        {"id": resource.tenant},
                    )
                )
                .scalars()
                .all()
            )
        assert name == "Storage validation"
        assert results == ["intent", "success"]
        await db.rollback()
    assert await storage.read(locator) == b"object"


async def test_audit_failure_prevents_external_storage_dispatch(
    stored_resources, object_store, monkeypatch
):
    from app.services import audit

    monkeypatch.setattr(
        audit, "record", AsyncMock(side_effect=RuntimeError("audit unavailable"))
    )
    async with AsyncSessionLocal() as db:
        with pytest.raises(RuntimeError, match="audit unavailable"):
            await storage.put(
                f"{stored_resources.tenant}/{uuid4()}.png",
                b"private",
                "image/png",
                audit_session=db,
            )
    assert object_store.list_objects_v2(Bucket=settings.S3_BUCKET)["KeyCount"] == 0


async def test_successful_upload_preserves_locator_when_outcome_audit_fails(
    object_store, monkeypatch, caplog
):
    ledger = AsyncMock(side_effect=[None, RuntimeError("private database details")])
    monkeypatch.setattr(storage, "_audit_storage_event", ledger)
    key = f"{uuid4()}/{uuid4()}.png"
    locator = await storage.put_s3(
        key, b"accepted object", "image/png", audit_session=SimpleNamespace(info={})
    )
    assert locator == f"s3://{settings.S3_BUCKET}/{key}"
    assert await storage.read(locator) == b"accepted object"
    assert [call.args[3] for call in ledger.await_args_list] == ["intent", "success"]
    assert "storage_audit_result_failed" in caplog.text
    assert "private database details" not in caplog.text and key not in caplog.text


async def test_failed_upload_preserves_provider_error_when_outcome_audit_fails(
    object_store, monkeypatch, caplog
):
    ledger = AsyncMock(side_effect=[None, RuntimeError("private database details")])
    monkeypatch.setattr(storage, "_audit_storage_event", ledger)
    provider_error = ValueError("private provider details")
    put_object = Mock(side_effect=provider_error)
    monkeypatch.setattr(
        storage, "_client", lambda: SimpleNamespace(put_object=put_object)
    )
    with pytest.raises(ValueError) as caught:
        await storage.put_s3(
            f"{uuid4()}/{uuid4()}.png",
            b"uncertain object",
            "image/png",
            audit_session=SimpleNamespace(info={}),
        )
    assert caught.value is provider_error
    put_object.assert_called_once()
    assert [call.args[3] for call in ledger.await_args_list] == ["intent", "unknown"]
    assert "storage_audit_result_failed" in caplog.text
    assert "private database details" not in caplog.text
    assert "private provider details" not in caplog.text
