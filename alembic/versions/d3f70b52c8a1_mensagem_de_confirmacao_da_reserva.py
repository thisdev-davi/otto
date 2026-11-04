"""mensagem de confirmação da reserva

Revision ID: d3f70b52c8a1
Revises: b6e29a0c4d17
Create Date: 2026-07-31

Confirmar uma reserva passa a AVISAR o cliente no WhatsApp — comportamento que o
`CLAUDE.md` listava explicitamente como fora de escopo ("nada na agenda fala com o
WhatsApp"), e que a mesma linha previa como feature a ser pedida. Foi pedida.

O texto é por tenant e editável, no mesmo molde de `transfer_message`: tom de mensagem é
o que cada salão mais quer diferente, e mudar tom não pode exigir deploy. Vazio cai no
`DEFAULT_CONFIRMACAO_MESSAGE`, então nenhum tenant existente precisa configurar nada
para a feature funcionar.

O envio em si NÃO é automático: quem confirma revisa o texto no diálogo antes de sair.
A coluna guarda o modelo, não a decisão de enviar.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d3f70b52c8a1"
down_revision: Union[str, Sequence[str], None] = "b6e29a0c4d17"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bot_settings",
        sa.Column(
            "confirmacao_message",
            sa.Text(),
            nullable=False,
            server_default=sa.text("''"),
        ),
    )


def downgrade() -> None:
    op.drop_column("bot_settings", "confirmacao_message")
