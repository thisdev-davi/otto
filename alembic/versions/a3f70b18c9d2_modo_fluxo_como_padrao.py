"""conta nova nasce em modo fluxo

Revision ID: a3f70b18c9d2
Revises: f1a4c62e9d38
Create Date: 2026-09-08

Troca **só o DEFAULT** da coluna `bot_settings.modo`, de `agente` para `fluxo`.

Nenhuma linha existente é tocada, e isso é o ponto: quem já contratou o agente de IA
continua no agente. O que muda é o ponto de partida de quem chega depois — conta nova
atende de forma previsível e sem custo de modelo, e ligar a IA passa a ser uma decisão
consciente de quem contrata, em vez de um padrão herdado.

O motivo é operacional e não estético: com `agente` como padrão, uma conta criada e
esquecida (uma demonstração, um teste) respondia com LLM contra a persona vazia e gastava
token antes de alguém revisar o conteúdo. `fluxo` erra para o lado barato.

Reversível sem perda: o downgrade devolve o default anterior, e também sem tocar em dado.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "a3f70b18c9d2"
down_revision: Union[str, None] = "f1a4c62e9d38"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE bot_settings ALTER COLUMN modo SET DEFAULT 'fluxo'")


def downgrade() -> None:
    op.execute("ALTER TABLE bot_settings ALTER COLUMN modo SET DEFAULT 'agente'")
