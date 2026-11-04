"""conteúdo do agente gerenciado por arquivo

Revision ID: d5e9a1c73b48
Revises: a91c3f7e5d20
Create Date: 2026-07-29

Marca o tenant cujo persona/knowledge vem de `conteudo/*.md`, gravado por
`scripts/sincronizar_conteudo.py`. Com a flag ligada, `PUT /bot/settings` recusa alterar
esses dois campos e a tela os mostra em leitura.

Existe porque, sem ela, os dois lados se sobrescreveriam em silêncio: o dono editaria a
persona no painel e a próxima sincronização apagaria a edição — ou o contrário.

Default `false`: quem nunca rodou o sincronizador continua editando pelo painel, como
sempre.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "d5e9a1c73b48"
down_revision: Union[str, Sequence[str], None] = "a91c3f7e5d20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "bot_settings",
        sa.Column(
            "conteudo_gerenciado",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("bot_settings", "conteudo_gerenciado")
