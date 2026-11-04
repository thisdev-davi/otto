"""Gestão de ChannelConnection — listar, criar (WhatsApp) e remover.

O núcleo não conhece providers concretos: o provisionamento/estado vem sempre
via `ChannelProvider` obtido por `factory.get_provider()`.
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import Channel

# Conexão de uma conversa, COM plano B — e o plano B não é luxo.
#
# `conversations.channel_connection_id` tem FK `ON DELETE SET NULL`, e reconectar o
# WhatsApp APAGA a conexão anterior. Toda conversa que já existia fica com a coluna nula,
# e como as rotas de envio resolviam a conexão por um `JOIN` nessa coluna, o resultado era
# mudez total: o bot desistia sem motivo registrado e o atendente levava 404 ao tentar
# responder o cliente. Observado no banco depois de um conectar/desconectar real.
#
# `create_connection` religa as órfãs assim que a conexão nova nasce; este LATERAL cobre a
# janela entre uma coisa e outra, e o caso de alguém religar por fora do painel.
#
# Espera que a query já tenha `conversations c` e `contacts ct` no FROM, e expõe `cc`
# (a conexão ligada) e `fb` (o plano B). Quem usa lê com COALESCE(cc.x, fb.x).
JOIN_CONEXAO = (
    "LEFT JOIN channel_connections cc "
    "  ON cc.id = c.channel_connection_id AND cc.tenant_id = c.tenant_id "
    "LEFT JOIN LATERAL ("
    "  SELECT * FROM channel_connections f "
    "  WHERE f.tenant_id = c.tenant_id AND f.channel = ct.channel "
    "    AND f.is_active = true ORDER BY f.created_at DESC LIMIT 1"
    ") fb ON cc.id IS NULL "
)


async def list_connections(session: AsyncSession, tenant_id: UUID) -> list[dict]:
    result = await session.execute(
        text(
            "SELECT id, channel, is_active, created_at, "
            "COALESCE(credentials->>'instance', credentials->>'username') AS label "
            "FROM channel_connections WHERE tenant_id = :tid ORDER BY created_at DESC"
        ),
        {"tid": str(tenant_id)},
    )
    rows = result.fetchall()
    return [
        {
            "id": r.id,
            "channel": Channel(r.channel.lower()),
            "label": r.label,
            "is_active": r.is_active,
            "created_at": r.created_at,
        }
        for r in rows
    ]


async def get_connection_credentials(
    session: AsyncSession, tenant_id: UUID, connection_id: UUID
) -> tuple[Channel, dict] | None:
    """Retorna (canal, credentials) de uma conexão do tenant, ou None."""
    result = await session.execute(
        text(
            "SELECT channel, credentials FROM channel_connections "
            "WHERE id = :id AND tenant_id = :tid"
        ),
        {"id": str(connection_id), "tid": str(tenant_id)},
    )
    row = result.fetchone()
    if row is None:
        return None
    return Channel(row.channel.lower()), row.credentials


async def resolve_by_webhook_id(
    session: AsyncSession, channel: Channel, webhook_id: str
) -> dict | None:
    """Conexão ativa dona deste `webhook_id`, ou None.

    É esta função que decide o tenant de um evento inbound. Antes, quem decidia era o
    campo `instance` do CORPO do webhook — dado de quem enviava a requisição —, o que
    permitia escolher em qual tenant a mensagem forjada ia cair. Aqui o tenant sai de um
    segredo de 256 bits que só o Evolution recebeu, por conexão.

    O `LIMIT 1` é seguro porque `webhook_id` é `token_urlsafe(32)`; ainda assim quem
    chama confere a instância do corpo contra `instance` devolvido aqui.
    """
    if not webhook_id:
        return None
    result = await session.execute(
        text(
            "SELECT id, tenant_id, credentials->>'instance' AS instance "
            "FROM channel_connections "
            "WHERE channel = :channel "
            "AND credentials->>'webhook_id' = :webhook_id "
            "AND is_active = true "
            "LIMIT 1"
        ),
        {"channel": channel.name, "webhook_id": webhook_id},
    )
    row = result.fetchone()
    if row is None:
        return None
    return {"id": row.id, "tenant_id": row.tenant_id, "instance": row.instance}


async def whatsapp_instance_exists(session: AsyncSession, instance: str) -> bool:
    """Instância já usada por ALGUM tenant.

    A checagem era por tenant, e isso não bastava: o nome da instância é global no
    Evolution, que é um só para toda a plataforma. Dois tenants com o mesmo `instance`
    disputavam a mesma instância lá, e a ingestão legada resolvia a conexão por esse
    campo sem ordenação — a mensagem de um salão podia cair na inbox do outro.

    O índice único parcial em `channel_connections` é quem garante isto de verdade sob
    concorrência; esta consulta existe para dar 409 com mensagem legível antes do erro
    de integridade.
    """
    result = await session.execute(
        text(
            "SELECT 1 FROM channel_connections "
            "WHERE channel = :channel AND credentials->>'instance' = :instance "
            "LIMIT 1"
        ),
        {"channel": Channel.WHATSAPP.name, "instance": instance},
    )
    return result.fetchone() is not None


async def create_connection(
    session: AsyncSession, tenant_id: UUID, channel: Channel, credentials: dict
) -> dict:
    import json

    result = await session.execute(
        text(
            "INSERT INTO channel_connections (id, tenant_id, channel, credentials, is_active) "
            "VALUES (gen_random_uuid(), :tid, :channel, CAST(:creds AS jsonb), true) "
            "RETURNING id, created_at"
        ),
        {
            "tid": str(tenant_id),
            "channel": channel.name,
            "creds": json.dumps(credentials),
        },
    )
    row = result.fetchone()
    # Reconectar o WhatsApp APAGA a conexão antiga, e a FK de `conversations` é
    # ON DELETE SET NULL: toda conversa que existia fica com `channel_connection_id`
    # nulo. Como as três rotas de envio (bot, atendente e galeria) resolvem a conexão
    # por essa coluna com JOIN, o efeito é mudez total naquelas conversas — nem o bot
    # nem o humano conseguem responder, e a única pista é um 404 ou um silêncio.
    #
    # Religar aqui é o conserto no lugar certo: quem acabou de criar a conexão é a única
    # parte do sistema que sabe qual é a nova.
    await religar_conversas_orfas(session, tenant_id, channel, row.id)
    await session.commit()
    return {"id": row.id, "created_at": row.created_at}


async def religar_conversas_orfas(
    session: AsyncSession, tenant_id: UUID, channel: Channel, connection_id
) -> int:
    """Aponta para `connection_id` as conversas do canal que ficaram sem conexão.

    Só toca em quem está com a coluna NULA — conversa ligada a outra conexão ativa não é
    reatribuída. Devolve quantas foram religadas.
    """
    result = await session.execute(
        text(
            "UPDATE conversations c SET channel_connection_id = :conn "
            "FROM contacts ct "
            "WHERE ct.id = c.contact_id AND c.tenant_id = :tid "
            "  AND c.channel_connection_id IS NULL AND ct.channel = :channel"
        ),
        {"conn": str(connection_id), "tid": str(tenant_id), "channel": channel.name},
    )
    return result.rowcount or 0


async def delete_connection(
    session: AsyncSession, tenant_id: UUID, connection_id: UUID
) -> bool:
    result = await session.execute(
        text(
            "DELETE FROM channel_connections "
            "WHERE id = :id AND tenant_id = :tid RETURNING id"
        ),
        {"id": str(connection_id), "tid": str(tenant_id)},
    )
    deleted = result.fetchone() is not None
    await session.commit()
    return deleted
