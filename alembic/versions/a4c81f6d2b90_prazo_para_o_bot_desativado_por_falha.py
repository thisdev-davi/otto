"""prazo para o bot desativado por falha de IA

Revision ID: a4c81f6d2b90
Revises: e7b3d5a91f24
Create Date: 2026-07-31

`bot_disabled` era definitivo, e servia a DUAS causas muito diferentes com o mesmo bit:
o teto de turnos pós-handoff (decisão de produto, permanente de propósito) e falhas
seguidas de IA (acidente, quase sempre transitório). Como só existia o bit, um provedor
que ficou fora do ar por dois minutos deixava a conversa sem agente para sempre — e
nada no sistema desfazia isso a não ser um humano capturar e liberar a conversa.

Foi assim que um cliente que perguntou "meu lugar foi confirmado?" nunca recebeu
resposta: o modelo devolveu um JSON cortado no teto de tokens (duas vezes), o
orquestrador contou como provedor indisponível, e o agente daquela conversa morreu.
O provedor estava de pé o tempo todo.

`bot_disabled_until` separa as duas causas:

    NULL        → desativado por DECISÃO (teto pós-handoff). Permanente, como antes.
    preenchido  → desativado por FALHA. Volta sozinho quando o prazo vencer.

Quem levanta o prazo é um UPDATE condicional em `bot_orchestrator._load_or_create_session`
(regra 2 do CLAUDE.md: dois turnos podem rodar na mesma conversa ao mesmo tempo, então a
condição vai no WHERE, nunca num `if` em Python).

Sem backfill de propósito: as linhas existentes ficam NULL, que é exatamente o estado
que elas já têm hoje — desativadas em definitivo. Ressuscitar uma delas é liberar a
conversa, como sempre foi.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4c81f6d2b90"
down_revision: Union[str, Sequence[str], None] = "e7b3d5a91f24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "bot_sessions",
        sa.Column("bot_disabled_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bot_sessions", "bot_disabled_until")
