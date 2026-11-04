"""anotações da equipe sobre o contato

Revision ID: e5a91c3d7f24
Revises: d3f70b52c8a1
Create Date: 2026-07-31

Tabela nova, nenhuma coluna alterada em `contacts`.

O lugar óbvio para uma anotação seria mais uma chave em `contacts.profile` (JSONB que já
existe e já guarda a memória da IA), e é justamente o que NÃO dá para fazer:
`contact_memory.remember()` reescreve o perfil inteiro num read-modify-write sem lock, a
cada turno do agente. Anotação escrita ali durante uma conversa ativa some sem erro.

`user_id` é `ON DELETE SET NULL`: remover um atendente da equipe não pode apagar o que a
equipe sabia dos clientes dele. A nota sobrevive sem autor.

O índice cobre a única leitura que existe — as notas de um contato, da mais nova para a
mais velha — e leva `tenant_id` na frente porque toda query de contato o carrega.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e5a91c3d7f24"
down_revision: Union[str, Sequence[str], None] = "d3f70b52c8a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "contact_notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contact_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("texto", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_contact_notes_contato",
        "contact_notes",
        ["tenant_id", "contact_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_contact_notes_contato", table_name="contact_notes")
    op.drop_table("contact_notes")
