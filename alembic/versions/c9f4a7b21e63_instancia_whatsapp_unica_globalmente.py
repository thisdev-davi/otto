"""instância de WhatsApp única globalmente

Revision ID: c9f4a7b21e63
Revises: d5e9a1c73b48
Create Date: 2026-07-30

O nome da instância é global no Evolution — há um único servidor Evolution para toda a
plataforma. A unicidade, porém, só era checada dentro do tenant (`whatsapp_instance_exists`),
e não havia constraint nenhuma no banco.

Dois tenants com o mesmo `instance` disputavam a MESMA instância no Evolution, e a
ingestão resolvia a conexão por `credentials->>'instance'` com `LIMIT 1` sem `ORDER BY`:
qual tenant recebia a mensagem virava detalhe do plano de execução do Postgres. Ou seja,
mensagem de um salão podendo aparecer na inbox de outro.

Índice PARCIAL (só `channel = 'WHATSAPP'`) porque o campo `instance` não existe nas
credenciais de outros canais — um índice cheio recusaria linhas com `NULL` repetido em
alguns planos e não diz nada sobre canais que não usam esse conceito.

Se o upgrade falhar por duplicata pré-existente, a query que mostra o conflito é:

    SELECT credentials->>'instance', count(*), array_agg(tenant_id)
    FROM channel_connections WHERE channel = 'WHATSAPP'
    GROUP BY 1 HAVING count(*) > 1;
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9f4a7b21e63"
down_revision: Union[str, Sequence[str], None] = "d5e9a1c73b48"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX uq_channel_connections_whatsapp_instance "
        "ON channel_connections ((credentials->>'instance')) "
        "WHERE channel = 'WHATSAPP'"
    )
    # Busca do webhook_id na ingestão: sem índice, cada evento vira seq scan na tabela.
    op.execute(
        "CREATE INDEX ix_channel_connections_webhook_id "
        "ON channel_connections ((credentials->>'webhook_id')) "
        "WHERE credentials ? 'webhook_id'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_channel_connections_webhook_id")
    op.execute("DROP INDEX IF EXISTS uq_channel_connections_whatsapp_instance")
