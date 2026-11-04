"""user role e convites de equipe

Revision ID: 35537b17ba9f
Revises: d4b8f16c9a71
Create Date: 2026-07-25

Escrita à mão a partir do autogenerate. O autogenerate propôs dropar
ix_kb_documents_embedding (HNSW do pgvector), ix_messages_tenant_created e
ix_conversation_events_tenant_created: os três nascem em migrations raw
(b3f1c9a2d4e5, c7a2e5f18b90) e não estão declarados nos models, então ele os lê como
sobra. Dropar o HNSW derrubaria a performance de toda a busca do RAG. Removidos.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "35537b17ba9f"
down_revision: Union[str, Sequence[str], None] = "d4b8f16c9a71"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# create_type=False: o tipo é criado uma vez explicitamente abaixo. Sem isso o
# create_table e o add_column tentariam criá-lo cada um, e o segundo estoura com
# "type already exists".
userrole_enum = postgresql.ENUM(
    "ADMIN", "AGENT", name="userrole_enum", create_type=False
)


def upgrade() -> None:
    """Upgrade schema."""
    userrole_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "users",
        sa.Column("role", userrole_enum, server_default="AGENT", nullable=False),
    )

    # Backfill: sem isto TODO usuário existente vira AGENT e o tenant fica trancado
    # fora da própria configuração — ninguém poderia convidar, mexer na IA ou no
    # WhatsApp. O primeiro usuário de cada tenant (o que criou a conta) vira ADMIN.
    op.execute(
        """
        UPDATE users SET role = 'ADMIN'
        WHERE id IN (
            SELECT DISTINCT ON (tenant_id) id
            FROM users
            ORDER BY tenant_id, created_at ASC, id ASC
        )
        """
    )

    op.create_table(
        "user_invites",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("role", userrole_enum, server_default="AGENT", nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("invited_by", sa.UUID(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["invited_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_user_invites_tenant", "user_invites", ["tenant_id"])
    # Parcial: só um convite ABERTO por e-mail/tenant. Convidar de novo substitui o
    # pendente; convites já aceitos não bloqueiam um futuro re-convite.
    op.create_index(
        "uq_user_invites_pending",
        "user_invites",
        ["tenant_id", "email"],
        unique=True,
        postgresql_where=sa.text("accepted_at IS NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_user_invites_pending", table_name="user_invites")
    op.drop_index("ix_user_invites_tenant", table_name="user_invites")
    op.drop_table("user_invites")
    op.drop_column("users", "role")
    userrole_enum.drop(op.get_bind(), checkfirst=True)
