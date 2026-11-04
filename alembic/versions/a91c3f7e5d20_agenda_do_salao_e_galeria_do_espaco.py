"""agenda do salão, reservas e galeria do espaço

Revision ID: a91c3f7e5d20
Revises: b8e2a94f7c31
Create Date: 2026-07-28

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a91c3f7e5d20"
down_revision: Union[str, Sequence[str], None] = "b8e2a94f7c31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "salao_datas",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("data", sa.Date(), nullable=False),
        # NULL = valor não cadastrado. O agente trata como desconhecimento, não como zero.
        sa.Column("preco_centavos", sa.Integer(), nullable=True),
        sa.Column(
            "bloqueada", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("observacao", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "data", name="uq_salao_datas_tenant_data"),
    )

    op.create_table(
        "salao_reservas",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("data", sa.Date(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="pre_reservada",
        ),
        sa.Column("contact_id", sa.UUID(), nullable=True),
        sa.Column("conversation_id", sa.UUID(), nullable=True),
        sa.Column(
            "criada_por", sa.String(length=8), nullable=False, server_default="humano"
        ),
        sa.Column("criada_por_user_id", sa.UUID(), nullable=True),
        sa.Column("expira_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("nome_cliente", sa.String(length=255), nullable=True),
        sa.Column("telefone", sa.String(length=50), nullable=True),
        sa.Column("observacao", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["criada_por_user_id"], ["users.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── A peça central: overbooking impossível por construção. ──
    # PARCIAL de propósito: cobre só os status que OCUPAM a data. Cancelada e expirada
    # continuam no banco como histórico e não impedem uma reserva nova no mesmo dia.
    # É o equivalente, para a agenda, do UPDATE condicional da captura de conversa: quem
    # arbitra dois clientes disputando a mesma data é o banco, não o Python.
    op.create_index(
        "uq_salao_reservas_data_ativa",
        "salao_reservas",
        ["tenant_id", "data"],
        unique=True,
        postgresql_where=sa.text("status IN ('pre_reservada', 'confirmada')"),
    )
    op.create_index(
        "ix_salao_reservas_tenant_data", "salao_reservas", ["tenant_id", "data"]
    )

    op.create_table(
        "salao_fotos",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("titulo", sa.String(length=255), nullable=True),
        sa.Column("caminho", sa.Text(), nullable=False),
        sa.Column("mimetype", sa.String(length=64), nullable=False),
        sa.Column(
            "tamanho_bytes", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("ordem", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "ativa", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_salao_fotos_tenant", "salao_fotos", ["tenant_id", "ordem"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_salao_fotos_tenant", table_name="salao_fotos")
    op.drop_table("salao_fotos")
    op.drop_index("ix_salao_reservas_tenant_data", table_name="salao_reservas")
    op.drop_index("uq_salao_reservas_data_ativa", table_name="salao_reservas")
    op.drop_table("salao_reservas")
    op.drop_table("salao_datas")
