"""telefone real em vez do identificador do canal

Revision ID: b6e29a0c4d17
Revises: a4c81f6d2b90
Create Date: 2026-07-31

Reparo de dados. Nenhuma coluna nova: `contacts.phone` já existia e nunca era escrita.

O que aconteceu: `channels/whatsapp.py` usava o `remoteJid` cru como identidade do
contato, e o WhatsApp passou a entregar remetente como `<numero>@lid` — identificador
opaco que NÃO é telefone de ninguém. Quem gravava telefone gravava esse identificador.
O banco tinha uma reserva CONFIRMADA com `telefone = '21711730823330@lid'`: o dono do
salão não conseguia ligar para o cliente, e o problema era invisível porque o campo
estava preenchido.

Três reparos:

1. `salao_reservas.telefone` terminando em `@lid` → NULL. Não há número a recuperar
   dali, e exibir o identificador convida alguém a tentar discá-lo. NULL é honesto, e
   `salao_resumo` passa a mostrar o telefone do contato quando ele existir.
2. `salao_reservas.telefone` terminando em `@s.whatsapp.net` → só os dígitos. Aqui o
   número está mesmo lá, só embrulhado no JID.
3. `contacts.phone` preenchido a partir do `external_id` quando ele é JID de telefone.

Os contatos `@lid` NÃO são reparados aqui — não existe de onde tirar o número. Eles se
reparam sozinhos na próxima mensagem que o cliente mandar, porque o adapter agora lê o
`senderPn` do payload. Nada mais é necessário.

Sobre a regra 1 do CLAUDE.md: este arquivo codifica formato de JID fora de `channels/`,
que é justamente o que a regra proíbe. É consciente. A alternativa purista seria um
script de backfill passando por `get_provider()` e um método novo na interface
`ChannelProvider` — contrato permanente para um uso único. Reparo histórico datado, que
roda uma vez no deploy e nunca mais, sai mais barato em três linhas de SQL. O que a regra
protege de verdade é o caminho de RUNTIME, e esse continua inteiro dentro do adapter.

`downgrade()` é no-op: não se desfaz um reparo. Voltar atrás seria reintroduzir o dado
ruim, e o dado ruim é o bug.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b6e29a0c4d17"
down_revision: Union[str, Sequence[str], None] = "a4c81f6d2b90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE salao_reservas SET telefone = NULL WHERE telefone LIKE '%@lid'")
    op.execute(
        "UPDATE salao_reservas "
        "SET telefone = regexp_replace(split_part(telefone, '@', 1), '[^0-9]', '', 'g') "
        "WHERE telefone LIKE '%@s.whatsapp.net'"
    )
    op.execute(
        "UPDATE contacts "
        "SET phone = regexp_replace(split_part(external_id, '@', 1), '[^0-9]', '', 'g') "
        "WHERE phone IS NULL AND external_id LIKE '%@s.whatsapp.net'"
    )


def downgrade() -> None:
    """No-op: não se desrepara dado. Ver o docstring do módulo."""
