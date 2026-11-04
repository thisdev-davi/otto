"""métricas de atendimento: first_response_at, resolved_at e índices

Revision ID: c7a2e5f18b90
Revises: b3f1c9a2d4e5
Create Date: 2026-07-24

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c7a2e5f18b90"
down_revision: Union[str, Sequence[str], None] = "b3f1c9a2d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "conversations",
        sa.Column("first_response_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Índices para as agregações de relatório (varredura por tenant + janela de tempo).
    op.create_index(
        "ix_messages_tenant_created", "messages", ["tenant_id", "created_at"]
    )
    op.create_index(
        "ix_conversation_events_tenant_created",
        "conversation_events",
        ["tenant_id", "created_at"],
    )

    # Backfill de dados históricos.
    # first_response_at = 1ª mensagem OUTBOUND humana (com sender_user_id, não-bot, não-interna).
    op.execute(
        """
        UPDATE conversations c SET first_response_at = sub.first_at
        FROM (
            SELECT conversation_id, MIN(created_at) AS first_at
            FROM messages
            WHERE direction = 'OUTBOUND'
              AND sender_user_id IS NOT NULL
              AND COALESCE(channel_metadata->>'internal', 'false') != 'true'
              AND COALESCE(channel_metadata->>'bot', 'false') != 'true'
            GROUP BY conversation_id
        ) sub
        WHERE c.id = sub.conversation_id
        """
    )
    # resolved_at = último evento RESOLVED da conversa (para conversas hoje resolvidas).
    op.execute(
        """
        UPDATE conversations c SET resolved_at = sub.resolved_at
        FROM (
            SELECT conversation_id, MAX(created_at) AS resolved_at
            FROM conversation_events
            WHERE action = 'RESOLVED'
            GROUP BY conversation_id
        ) sub
        WHERE c.id = sub.conversation_id AND c.status = 'RESOLVED'
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "ix_conversation_events_tenant_created", table_name="conversation_events"
    )
    op.drop_index("ix_messages_tenant_created", table_name="messages")
    op.drop_column("conversations", "resolved_at")
    op.drop_column("conversations", "first_response_at")
