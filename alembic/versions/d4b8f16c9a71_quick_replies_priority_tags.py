"""respostas rápidas + prioridade/tags de conversa

Revision ID: d4b8f16c9a71
Revises: c7a2e5f18b90
Create Date: 2026-07-24

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "d4b8f16c9a71"
down_revision: Union[str, Sequence[str], None] = "c7a2e5f18b90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Prioridade e tags são domínio core da conversa → colunas (não channel_metadata).
    op.add_column(
        "conversations",
        sa.Column(
            "priority", sa.String(length=8), nullable=False, server_default="normal"
        ),
    )
    op.add_column(
        "conversations",
        sa.Column(
            "tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )

    op.create_table(
        "quick_replies",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("shortcut", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "shortcut", name="uq_quick_replies_tenant_shortcut"
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("quick_replies")
    op.drop_column("conversations", "tags")
    op.drop_column("conversations", "priority")
