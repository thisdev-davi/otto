"""Tenant audit and explicit conversation closure reason.

Revision ID: a6b7c8d9e0f1
Revises: a3f70b18c9d2
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a6b7c8d9e0f1"
down_revision = "a3f70b18c9d2"
branch_labels = None
depends_on = None

# Database triggers are intentional: both raw conditional SQL and ORM writes are
# audited in the same transaction, including worker tasks and bulk operations.
TRIGGER_SQL = r"""
CREATE FUNCTION otto_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    old_data jsonb := '{}'::jsonb;
    new_data jsonb := '{}'::jsonb;
    row_data jsonb;
    changed jsonb;
    actor text;
BEGIN
    IF TG_OP <> 'INSERT' THEN old_data := to_jsonb(OLD); END IF;
    IF TG_OP <> 'DELETE' THEN new_data := to_jsonb(NEW); END IF;
    row_data := CASE WHEN TG_OP = 'DELETE' THEN old_data ELSE new_data END;
    SELECT coalesce(jsonb_agg(k ORDER BY k), '[]'::jsonb) INTO changed
      FROM jsonb_object_keys(old_data || new_data) AS keys(k)
      WHERE old_data->k IS DISTINCT FROM new_data->k;
    IF TG_OP = 'UPDATE' AND changed = '[]'::jsonb THEN RETURN NULL; END IF;
    actor := coalesce(nullif(current_setting('otto.actor_type', true), ''), 'system');
    INSERT INTO audit_events
        (id, tenant_id, created_at, actor_type, actor_user_id, action,
         resource_type, resource_id, result, correlation_id, ip_network, metadata)
    VALUES
        (gen_random_uuid(), (row_data->>'tenant_id')::uuid, clock_timestamp(), actor,
         nullif(current_setting('otto.actor_user_id', true), '')::uuid,
         'db.' || lower(TG_OP), TG_TABLE_NAME,
         coalesce(row_data->>'id', row_data->>'conversation_id', row_data->>'tenant_id'),
         'success', coalesce(nullif(current_setting('otto.correlation_id', true), '')::uuid,
                             gen_random_uuid()),
         nullif(current_setting('otto.ip_network', true), ''),
         jsonb_build_object('changed_fields', changed));
    RETURN NULL;
END;
$$;
CREATE FUNCTION otto_audit_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE'
       AND current_setting('otto.audit_retention', true) = 'on'
       AND OLD.created_at < clock_timestamp() - interval '180 days' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'Audit events are append-only';
END;
$$;
CREATE TRIGGER audit_events_immutable BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION otto_audit_immutable();
DO $$
DECLARE item record;
BEGIN
    FOR item IN SELECT table_name FROM information_schema.columns
        WHERE table_schema = 'public' AND column_name = 'tenant_id'
          AND table_name <> 'audit_events'
    LOOP
        EXECUTE format('CREATE TRIGGER otto_audit_changes AFTER INSERT OR UPDATE OR DELETE ON %I '
                       'FOR EACH ROW EXECUTE FUNCTION otto_audit_mutation()', item.table_name);
    END LOOP;
END;
$$;
"""


def upgrade() -> None:
    op.add_column(
        "conversations", sa.Column("closure_reason", sa.String(24), nullable=True)
    )
    op.execute(
        "UPDATE conversations SET closure_reason = 'legacy_unknown' WHERE status = 'RESOLVED'"
    )
    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("actor_type", sa.String(24), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(100), nullable=True),
        sa.Column("result", sa.String(24), server_default="success", nullable=False),
        sa.Column("reason", sa.String(100), nullable=True),
        sa.Column("correlation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ip_network", sa.String(64), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_audit_tenant_created_id", "audit_events", ["tenant_id", "created_at", "id"]
    )
    op.create_index("ix_audit_created", "audit_events", ["created_at"])
    op.execute(TRIGGER_SQL)


def downgrade() -> None:
    op.execute("DROP FUNCTION otto_audit_mutation() CASCADE")
    op.execute("DROP FUNCTION otto_audit_immutable() CASCADE")
    op.drop_table("audit_events")
    op.drop_column("conversations", "closure_reason")
