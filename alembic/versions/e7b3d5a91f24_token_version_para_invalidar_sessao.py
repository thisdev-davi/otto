"""token_version para invalidar sessão

Revision ID: e7b3d5a91f24
Revises: c9f4a7b21e63
Create Date: 2026-07-30

Não havia como invalidar um refresh token. Ele valia 30 dias, se rotacionava sozinho a
cada uso e nada no sistema o revogava: não existia logout, não existia troca de senha, e
o `/auth/refresh` reemitia o par lendo só o payload do próprio token. Um refresh vazado
era acesso durável — e o dono não tinha nenhuma ação disponível para cortá-lo.

`token_version` fecha isso com uma checagem só: o número entra no JWT quando ele é
emitido, e o `/auth/refresh` compara com o valor do banco. Incrementar a coluna derruba
todos os refresh tokens daquele usuário na hora — é o que fazem logout e troca de senha.

Não cobre o access token, que continua valendo até expirar (até 60 min). É a troca
consciente que o `require_admin` já documenta: as rotas quentes da inbox não podem pagar
uma query por request.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "e7b3d5a91f24"
down_revision: Union[str, Sequence[str], None] = "c9f4a7b21e63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "token_version", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
