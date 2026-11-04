"""modelo de embedding gravado por chunk

Revision ID: e1c4a7d92b60
Revises: 35537b17ba9f
Create Date: 2026-07-26

Escrita à mão, NÃO pelo autogenerate. Pelo mesmo motivo documentado em 35537b17ba9f:
o autogenerate propõe dropar ix_kb_documents_embedding (HNSW do pgvector),
ix_messages_tenant_created e ix_conversation_events_tenant_created, porque os três
nascem em migrations raw e não estão declarados nos models. Dropar o HNSW derrubaria
a performance de toda a busca do RAG.

Por que a coluna: openai (text-embedding-3-small) e gemini (gemini-embedding-001)
produzem os mesmos 1536 valores, então o banco aceita comparar vetores dos dois sem
reclamar — e devolve vizinhos sem sentido. Guardando o modelo, retrieval.search filtra
por ele e uma troca de provedor degrada para "não achei" em vez de responder errado.

Nullable de propósito: chunks anteriores a esta migration não têm como saber seu
modelo. Eles ficam invisíveis para a busca (o filtro exige igualdade) até serem
re-ingeridos, que é justamente o comportamento correto — não dá para afirmar de que
modelo vieram. Na prática não há nenhum: kb_documents está vazia.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1c4a7d92b60"
down_revision: Union[str, Sequence[str], None] = "35537b17ba9f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "kb_documents",
        sa.Column("embedding_model", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("kb_documents", "embedding_model")
