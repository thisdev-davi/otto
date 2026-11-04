"""Copy local media to private S3, verify bytes, then conditionally update locators.

Dry-run by default. Stop API/workers during the final migration; keep local files and
the database backup until the new deployment has passed acceptance checks.
Run from repository root: python -m scripts.migrate_media_storage [--apply]
"""

import argparse
import asyncio
import hashlib
import mimetypes
from uuid import UUID

from sqlalchemy import text

from app.core.database import WorkerSessionLocal
from app.services import storage


async def migrate(apply: bool = False, tenant_id: UUID | None = None) -> dict[str, int]:
    counts = {"references": 0, "missing": 0, "updated": 0}
    async with WorkerSessionLocal() as db:
        # Table/column names are static, never provided by the CLI.
        for table, column, resource in (
            ("messages", "channel_metadata", "media"),
            ("contacts", "channel_metadata", "avatar"),
            ("salao_fotos", "caminho", None),
        ):
            expr = f"{column}->'{resource}'->>'path'" if resource else column
            rows = await db.execute(
                text(
                    f"SELECT id, tenant_id, {expr} AS locator, "
                    + (
                        f"{column}->'{resource}'->>'mimetype'"
                        if resource
                        else "mimetype"
                    )
                    + f" AS mimetype FROM {table} WHERE {expr} IS NOT NULL "
                    + f"AND {expr} NOT LIKE 's3://%'"
                    + (" AND tenant_id=:tenant" if tenant_id else "")
                ),
                {"tenant": tenant_id} if tenant_id else {},
            )
            for row in rows.fetchall():
                counts["references"] += 1
                if not await storage.exists(row.locator):
                    counts["missing"] += 1
                    continue
                if not apply:
                    continue
                data = await storage.read(row.locator)
                digest = hashlib.sha256(data).hexdigest()
                extension = mimetypes.guess_extension(row.mimetype or "") or ".bin"
                key = f"{UUID(str(row.tenant_id))}/migrated/{digest}{extension}"
                target = await storage.put_s3(
                    key,
                    data,
                    row.mimetype or "application/octet-stream",
                    audit_session=db,
                )
                if hashlib.sha256(await storage.read(target)).hexdigest() != digest:
                    raise RuntimeError(
                        "Object verification failed; original locator preserved"
                    )
                value = (
                    f"jsonb_set({column}, '{{{resource},path}}', to_jsonb(CAST(:new AS text)))"
                    if resource
                    else ":new"
                )
                result = await db.execute(
                    text(
                        f"UPDATE {table} SET {column} = {value} "
                        f"WHERE id = :id AND tenant_id = :tenant AND {expr} = :old"
                    ),
                    {
                        "new": target,
                        "id": row.id,
                        "tenant": row.tenant_id,
                        "old": row.locator,
                    },
                )
                counts["updated"] += result.rowcount
            # Each backend preserves the original files. A failed database commit is
            # safe to retry because content-addressed copies are idempotent.
            await db.commit()
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Copy, verify and update database locators"
    )
    parser.add_argument(
        "--tenant", type=UUID, help="Restrict migration to one verified tenant ID"
    )
    args = parser.parse_args()
    print(asyncio.run(migrate(args.apply, args.tenant)))
