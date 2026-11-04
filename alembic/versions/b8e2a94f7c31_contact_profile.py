"""memoria de cliente entre conversas

Revision ID: b8e2a94f7c31
Revises: a3f7e1d64c28
Create Date: 2026-07-26

Escrita à mão, NÃO pelo autogenerate — mesmo motivo das anteriores: o autogenerate propõe
dropar ix_kb_documents_embedding (HNSW do pgvector), ix_messages_tenant_created e
ix_conversation_events_tenant_created, que nascem em migrations raw e não estão nos models.

Por que a coluna: o contexto da IA é carregado por conversation_id
(app/services/ai_context.py) e a ingestão só reaproveita conversa com status != 'RESOLVED'.
Conversa resolvida + cliente de volta = conversa nova = a IA o atende como desconhecido e
repete tudo que ele já respondeu. Era latente enquanto nada era resolvido; com o botão
Resolver na thread virou o caso comum.

JSONB e não colunas fixas: o que se coleta muda por negócio — um espaço de festas guarda
tipo de evento e número de convidados, uma clínica guardaria outra coisa. Mesma decisão já
tomada em bot_sessions.variables.

Não vai em contacts.channel_metadata: aquele campo é para dado específico de canal (regra
do CLAUDE.md), e memória de cliente não é.

NOT NULL com default '{}': evita o ramo "perfil é NULL" em todo lugar que lê.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b8e2a94f7c31"
down_revision: Union[str, Sequence[str], None] = "a3f7e1d64c28"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "contacts",
        sa.Column(
            "profile",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("contacts", "profile")
