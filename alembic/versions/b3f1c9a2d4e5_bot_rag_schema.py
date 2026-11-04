"""bot settings, bot sessions e base de conhecimento vetorial (pgvector)

Revision ID: b3f1c9a2d4e5
Revises: 8a379746f5c3
Create Date: 2026-07-23

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b3f1c9a2d4e5"
down_revision: Union[str, Sequence[str], None] = "8a379746f5c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Requer a imagem pgvector/pgvector (extensão disponível no cluster).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "bot_settings",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "ai_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("persona", sa.Text(), nullable=False, server_default=""),
        sa.Column("knowledge", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "provider", sa.String(length=32), nullable=False, server_default="stub"
        ),
        sa.Column("model", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("api_key", sa.Text(), nullable=False, server_default=""),
        sa.Column("transfer_message", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "use_rag", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id"),
    )

    op.create_table(
        "bot_sessions",
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column(
            "variables",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "ai_failures", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "post_handoff_turns",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "handoff", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "bot_disabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("conversation_id"),
    )

    op.create_table(
        "kb_documents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_kb_documents_tenant", "kb_documents", ["tenant_id"])
    # HNSW + cosseno: casa com o operador <=> usado na busca (retrieval.py).
    op.execute(
        "CREATE INDEX ix_kb_documents_embedding ON kb_documents "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    # RLS: um tenant jamais recupera conhecimento de outro. Obs.: superusuários
    # (caso do usuário 'otto' em dev) fazem bypass de RLS — por isso toda query
    # da aplicação também filtra tenant_id explicitamente (defesa em profundidade).
    op.execute("ALTER TABLE kb_documents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE kb_documents FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON kb_documents "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("kb_documents")
    op.drop_table("bot_sessions")
    op.drop_table("bot_settings")
