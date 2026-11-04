"""atendimento por fluxo: bot_flows e o modo do bot

Revision ID: f1a4c62e9d38
Revises: e5a91c3d7f24
Create Date: 2026-09-02

Duas coisas, e as duas são aditivas.

**`bot_settings.modo`** decide qual cérebro atende a conversa: `agente` (o LLM, que é o
comportamento de hoje) ou `fluxo` (roteiro determinístico, sem nenhuma chamada de modelo).
O default é `agente` justamente para que a migration não mude o comportamento de nenhum
tenant existente. Combinado com o `ai_enabled` que já existe, ele forma o controle de três
estados do painel — desligado / fluxo / agente — e trocar `agente` por `fluxo` é o freio de
emergência quando o provedor de IA cai ou a fatura assusta.

**`bot_settings.horario`** é o expediente do negócio, lido pelo nó `horario` do fluxo. Não
tem nada a ver com a agenda: a agenda diz qual DATA está à venda e por quanto, e ela
recusou padrão semanal de propósito porque preço de salão é ditado por feriado. Expediente
é quando existe gente para responder — outra pergunta, outra coluna.

**`bot_flows`** guarda o grafo. Uma linha por tenant (PK = `tenant_id`, igual a
`bot_settings`), grafo inteiro em JSONB. Normalizar nós e arestas em tabelas custaria uma
migration a cada tipo de nó novo e joins num caminho que roda a cada mensagem recebida,
para comprar consultas que ninguém faz — nunca se busca "todos os nós do tipo menu".
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f1a4c62e9d38"
down_revision: Union[str, Sequence[str], None] = "e5a91c3d7f24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bot_settings",
        sa.Column(
            "modo",
            sa.String(length=16),
            nullable=False,
            server_default="agente",
        ),
    )
    op.add_column(
        "bot_settings",
        sa.Column(
            "horario",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_table(
        "bot_flows",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("nome", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "grafo",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("versao", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "gerenciado_por_arquivo",
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
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id"),
    )


def downgrade() -> None:
    op.drop_table("bot_flows")
    op.drop_column("bot_settings", "horario")
    op.drop_column("bot_settings", "modo")
