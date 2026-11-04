"""texto original das fontes da base de conhecimento

Revision ID: a3f7e1d64c28
Revises: f2d5b8c31a47
Create Date: 2026-07-26

Escrita à mão, NÃO pelo autogenerate — mesmo motivo de f2d5b8c31a47 e e1c4a7d92b60:
o autogenerate propõe dropar ix_kb_documents_embedding (HNSW do pgvector),
ix_messages_tenant_created e ix_conversation_events_tenant_created, que nascem em
migrations raw e não estão declarados nos models.

Por que a tabela: `kb_documents` guarda CHUNKS, e chunk não remonta documento. O
fatiamento usa overlap de 200 caracteres (app/ai/chunking.py), não há coluna de
índice, e todos os chunks de uma fonte entram na mesma transação com `now()` — que no
Postgres é o timestamp da transação, idêntico para todos. Sem ordem e com sobreposição,
o texto original era irrecuperável.

Consequências disso que esta tabela resolve:
1. não dava para EDITAR um documento (nada com que preencher o formulário);
2. não dava para RELER o que a IA recebeu — a base era write-only, e é ela que decide
   o que o bot fala com o cliente;
3. o marcador `stale` (e1c4a7d92b60) pedia re-ingestão de uma fonte cujo conteúdo o
   sistema não tinha — uma instrução impossível de cumprir.

Separação: `kb_sources` é a verdade (o que o usuário escreveu), `kb_documents` é o
índice vetorial derivado. Guardar o texto inteiro repetido em cada linha de chunk
seria duplicação pura.

Fontes ingeridas antes desta migration ficam sem linha aqui: seguem buscáveis e
deletáveis, mas não editáveis. Não há de onde recuperar o texto delas.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3f7e1d64c28"
down_revision: Union[str, Sequence[str], None] = "f2d5b8c31a47"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "kb_sources",
        sa.Column(
            "tenant_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        # (tenant, source) é a identidade da fonte — a mesma chave que
        # kb.ingest_document usa para apagar e reinserir os chunks.
        sa.PrimaryKeyConstraint("tenant_id", "source"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("kb_sources")
