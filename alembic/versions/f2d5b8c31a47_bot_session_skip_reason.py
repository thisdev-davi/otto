"""motivo do último silêncio do bot, por conversa

Revision ID: f2d5b8c31a47
Revises: e1c4a7d92b60
Create Date: 2026-07-26

Escrita à mão, NÃO pelo autogenerate — mesmo motivo de e1c4a7d92b60 e 35537b17ba9f:
o autogenerate propõe dropar ix_kb_documents_embedding (HNSW do pgvector),
ix_messages_tenant_created e ix_conversation_events_tenant_created, que nascem em
migrations raw e não estão declarados nos models.

Por que a coluna: o orquestrador tem seis saídas antecipadas (não é texto, IA desligada,
mensagem antiga, conversa capturada, sessão desativada, LLM indisponível) e todas faziam
`return` em silêncio. Na prática isso significava "liguei o toggle e o bot não respondeu"
sem nenhuma forma de descobrir qual das seis foi — nem no log, nem na UI. Guardar o
motivo é o que transforma essa classe inteira de suporte numa consulta.

Nullable de propósito: NULL = "o bot nunca deixou de responder nesta conversa". É
diferente de 'ok', que significa "a última passagem terminou em resposta enviada".
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2d5b8c31a47"
down_revision: Union[str, Sequence[str], None] = "e1c4a7d92b60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "bot_sessions",
        sa.Column("skip_reason", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("bot_sessions", "skip_reason")
