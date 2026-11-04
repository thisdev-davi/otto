import json
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.types import NormalizedInbound
from app.core.config import settings
from app.models.enums import MessageDirection
from app.services import avatars, contact_memory, events
from app.services.previews import preview_for

log = logging.getLogger("uvicorn.error")


async def _encerrar_se_inativa(db: AsyncSession, tenant_id, conversation_id) -> bool:
    """Encerra a conversa parada há tempo demais. `True` quando encerrou.

    Só escreve — não publica nem loga. O encerramento vale apenas se `ingest` chegar ao
    commit: se a mensagem que o disparou for um evento duplicado, a transação inteira
    volta atrás, e anunciar antes disso seria avisar o front de algo que não aconteceu.

    **Por que existe.** O estado do atendimento envelhece mal. Quem parou de responder no
    meio de "para qual data você está pensando?" e volta três horas depois dizendo "oi"
    caía no "Desculpa, não entendi" — e na segunda tentativa ia para a fila da equipe.
    Do lado do cliente não houve erro nenhum: ele só não sabia que a conversa dele
    continuava aberta, parada numa pergunta. Encerrar devolve o começo do roteiro.

    **Preguiçoso, nunca agendado.** Não há `celery beat` no projeto e não se introduz um
    para isto (mesma decisão de `salao_agenda.expirar_vencidas`): a conversa vence quando
    a próxima mensagem chega, que é o único momento em que a resposta importa.

    **As três condições vão no WHERE, não em `if` no Python.** Duas mensagens coladas
    entrariam juntas aqui, e a decisão de encerrar é disputada como qualquer outra
    (`CLAUDE.md` §2): quem encerra é o UPDATE condicional, e só ele.

    O que a cláusula protege, em ordem de gravidade:

    - **`assigned_user_id IS NULL`** — atendente no comando não perde a conversa dele
      porque o cliente demorou para responder.
    - **`NOT EXISTS (... bs.handoff)`** — conversa que já foi encaminhada pertence à fila
      da equipe. Encerrá-la a sumiria da fila com o cliente ainda esperando, que é
      exatamente o oposto do que esta função existe para evitar.
    - **`last_message_at`** — a inatividade é medida pela última mensagem de qualquer
      lado, e não pela criação da conversa.
    """
    limite = settings.CONVERSA_INATIVA_SEGUNDOS
    if limite <= 0:
        return False

    fechou = await db.execute(
        text(
            "UPDATE conversations SET status = 'RESOLVED', resolved_at = now(), closure_reason = 'inactivity' "
            "WHERE id = :cid AND tenant_id = :tid "
            "  AND status != 'RESOLVED' "
            "  AND assigned_user_id IS NULL "
            "  AND last_message_at IS NOT NULL "
            "  AND last_message_at < now() - make_interval(secs => :limite) "
            "  AND NOT EXISTS ("
            "    SELECT 1 FROM bot_sessions bs "
            "    WHERE bs.conversation_id = conversations.id AND bs.handoff"
            "  ) "
            "RETURNING id"
        ),
        {"cid": str(conversation_id), "tid": str(tenant_id), "limite": float(limite)},
    )
    if fechou.fetchone() is None:
        return False

    # Mudança de estado e evento na MESMA transação (`CLAUDE.md` §2). `actor_user_id`
    # nulo é a convenção do sistema para "ninguém fez isso, o sistema fez" — a mesma de
    # `messages.sender_user_id` nas mensagens do bot.
    await db.execute(
        text(
            "INSERT INTO conversation_events "
            "(id, conversation_id, tenant_id, action, actor_user_id, created_at) "
            "VALUES (gen_random_uuid(), :cid, :tid, 'RESOLVED', NULL, now())"
        ),
        {"cid": str(conversation_id), "tid": str(tenant_id)},
    )
    return True


async def ingest(
    db: AsyncSession,
    redis_client,
    normalized: NormalizedInbound,
    connection_id: str | None = None,
) -> dict | None:
    """Persiste a mensagem inbound. Retorna {tenant_id, conversation_id} da mensagem
    nova, ou None quando o evento é duplicado (no-op).

    `connection_id` vem da rota, resolvido pelo `webhook_id` secreto da URL. Quando ele
    existe, é ELE que decide o tenant, e o `instance` do corpo vira apenas um dado a
    conferir. O caminho sem `connection_id` é a rota legada, mantida um ciclo — ali o
    tenant ainda sai do corpo, que é exatamente a fraqueza que o `webhook_id` corrige.
    """
    # 1. Resolve ChannelConnection — pelo id autenticado, ou (legado) pelo instance_key.
    if connection_id is not None:
        row = await db.execute(
            text(
                "SELECT id, tenant_id, credentials->>'instance' AS instance "
                "FROM channel_connections "
                "WHERE id = :cid AND channel = :channel AND is_active = true"
            ),
            {"cid": connection_id, "channel": normalized.channel.name},
        )
        conn_row = row.fetchone()
        # A instância do corpo tem de ser a da conexão autenticada. Divergiu, alguém está
        # entregando evento de outra instância nesta URL — descarta em vez de gravar no
        # tenant errado.
        if conn_row is not None and normalized.instance_key != conn_row.instance:
            log.error(
                "Ingestão descartada: webhook da conexão %s trouxe instance=%r, mas a "
                "conexão é da instância %r.",
                connection_id,
                normalized.instance_key,
                conn_row.instance,
            )
            return None
    else:
        row = await db.execute(
            text(
                "SELECT id, tenant_id FROM channel_connections "
                "WHERE channel = :channel "
                "AND credentials->>'instance' = :instance_key "
                "AND is_active = true "
                "LIMIT 1"
            ),
            {
                "channel": normalized.channel.name,
                "instance_key": normalized.instance_key,
            },
        )
        conn_row = row.fetchone()

    if conn_row is None:
        # Sem conexão ativa a mensagem se perde de qualquer jeito — o `raise` daqui só
        # trocava um log legível por um traceback de Celery. Loga alto, nomeando a
        # instância e o que fazer, e devolve o mesmo no-op usado para evento duplicado.
        log.error(
            "Ingestão descartada: nenhuma ChannelConnection ativa para instance_key=%r "
            "(canal %s). A mensagem NÃO foi salva. Reconecte o WhatsApp em Configurações "
            "› WhatsApp, ou reative a conexão.",
            normalized.instance_key,
            normalized.channel.name,
        )
        return None
    channel_connection_id = conn_row[0]
    tenant_id = conn_row[1]

    # 2. Upsert Contact
    #
    # `COALESCE(EXCLUDED.phone, contacts.phone)`: mensagem que chega SEM telefone não pode
    # apagar um telefone já conhecido. O canal nem sempre informa (ver `_telefone` em
    # channels/whatsapp.py), e "não sei agora" não é "não tem".
    #
    # O `WHERE` precisa do segundo ramo. Com só a comparação de nome — que é como era — o
    # UPDATE não roda quando o nome não mudou, e o nome quase nunca muda: nenhum contato
    # que já existia ganharia telefone, para sempre. O teste do caminho feliz (contato
    # novo) passaria mesmo assim, porque ali o INSERT resolve.
    await db.execute(
        text(
            "INSERT INTO contacts "
            "(id, tenant_id, channel, external_id, name, phone, created_at) "
            "VALUES (gen_random_uuid(), :tenant_id, :channel, :external_id, :name, "
            ":phone, now()) "
            "ON CONFLICT (tenant_id, channel, external_id) DO UPDATE "
            "SET name = EXCLUDED.name, "
            "    phone = COALESCE(EXCLUDED.phone, contacts.phone) "
            "WHERE contacts.name IS DISTINCT FROM EXCLUDED.name "
            "   OR (EXCLUDED.phone IS NOT NULL "
            "       AND contacts.phone IS DISTINCT FROM EXCLUDED.phone)"
        ),
        {
            "tenant_id": str(tenant_id),
            "channel": normalized.channel.name,
            "external_id": normalized.contact_external_id,
            "name": normalized.contact_name,
            "phone": normalized.contact_phone,
        },
    )

    contact_row = await db.execute(
        text(
            "SELECT id, channel_metadata FROM contacts "
            "WHERE tenant_id = :tenant_id AND channel = :channel AND external_id = :external_id"
        ),
        {
            "tenant_id": str(tenant_id),
            "channel": normalized.channel.name,
            "external_id": normalized.contact_external_id,
        },
    )
    contato = contact_row.fetchone()
    contact_id = contato.id

    # Foto de perfil: quem decide é aqui, quem enfileira é `tasks/ingest.py` — mesma
    # divisão da mídia inbound, e o motivo é o mesmo (`services/` não conhece Celery).
    # Não há `celery beat` no projeto e a leitura é o único momento em que alguém olha
    # para este contato, então é ela que dispara. `desatualizado` cobre o contato novo e
    # a foto trocada há mais de uma semana.
    avatar_desatualizado = avatars.desatualizado(dict(contato.channel_metadata or {}))

    # Mesma pessoa em dois contatos? Só instrumenta — não decide nada.
    #
    # A identidade do contato é o endereço do canal, e esse endereço já mudou de forma uma
    # vez: o WhatsApp passou a mandar `<numero>@lid` no lugar do JID de telefone. Se a
    # forma mudar de novo (relink, outro caminho de mensagem), `uq_contacts_tenant_channel_ext`
    # cria um segundo contato DE VERDADE, e perfil, memória e reservas do cliente se
    # partem ao meio sem ninguém perceber.
    #
    # Unificar identidade por telefone é tentador e prematuro: hoje ninguém sabe se o
    # cenário acontece de fato. Este log é o dado que falta para decidir — e enquanto ele
    # não disparar, mexer na identidade é especulação com risco de quebrar o envio.
    if normalized.contact_phone:
        gemeos = await db.execute(
            text(
                "SELECT external_id FROM contacts "
                "WHERE tenant_id = :tenant_id AND channel = :channel "
                "AND phone = :phone AND id <> :contact_id LIMIT 3"
            ),
            {
                "tenant_id": str(tenant_id),
                "channel": normalized.channel.name,
                "phone": normalized.contact_phone,
                "contact_id": str(contact_id),
            },
        )
        outros = [r[0] for r in gemeos.fetchall()]
        if outros:
            log.error(
                "Contato duplicado: telefone %s aparece em %r e também em %r (tenant %s). "
                "A mesma pessoa está partida em mais de um contato — perfil, memória e "
                "reservas não se enxergam.",
                normalized.contact_phone,
                normalized.contact_external_id,
                outros,
                tenant_id,
            )

    # 3. Find or create Conversation (OPEN ou PENDING mais recente)
    conv_row = await db.execute(
        text(
            "SELECT id FROM conversations "
            "WHERE tenant_id = :tenant_id AND contact_id = :contact_id "
            "AND status != 'RESOLVED' "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"tenant_id": str(tenant_id), "contact_id": str(contact_id)},
    )
    conversation_id = conv_row.scalar_one_or_none()

    # Conversa parada há muito tempo é encerrada aqui, e a mensagem que acabou de chegar
    # abre uma NOVA. Ver `_encerrar_se_inativa`.
    encerrada = None
    if conversation_id is not None and await _encerrar_se_inativa(
        db, tenant_id, conversation_id
    ):
        encerrada, conversation_id = conversation_id, None

    if conversation_id is None:
        new_conv = await db.execute(
            text(
                "INSERT INTO conversations "
                "(id, tenant_id, contact_id, channel_connection_id, status, unread_count, created_at) "
                "VALUES (gen_random_uuid(), :tenant_id, :contact_id, :channel_connection_id, "
                "'OPEN', 0, now()) "
                "RETURNING id"
            ),
            {
                "tenant_id": str(tenant_id),
                "contact_id": str(contact_id),
                "channel_connection_id": str(channel_connection_id),
            },
        )
        conversation_id = new_conv.scalar_one()
        # Conversa NOVA para um contato que já existia = cliente voltando. O contador
        # alimenta a memória em contacts.profile.
        await contact_memory.count_conversation(db, tenant_id, contact_id)

    # 4. Insert Message — idempotente via ON CONFLICT DO NOTHING.
    # channel_metadata guarda o payload bruto do canal (necessário p/ baixar mídia depois).
    preview = preview_for(normalized.content_type.name, normalized.content)
    msg_result = await db.execute(
        text(
            "INSERT INTO messages "
            "(id, conversation_id, tenant_id, direction, content_type, content, "
            "external_id, channel, channel_metadata, created_at) "
            "VALUES (gen_random_uuid(), :conversation_id, :tenant_id, :direction, "
            ":content_type, :content, :external_id, :channel, CAST(:channel_metadata AS jsonb), :created_at) "
            "ON CONFLICT (channel, external_id) DO NOTHING "
            "RETURNING id"
        ),
        {
            "conversation_id": str(conversation_id),
            "tenant_id": str(tenant_id),
            "direction": MessageDirection.INBOUND.name,
            "content_type": normalized.content_type.name,
            "content": normalized.content,
            "external_id": normalized.external_id,
            "channel": normalized.channel.name,
            "channel_metadata": json.dumps(normalized.channel_metadata or {}),
            "created_at": normalized.timestamp,
        },
    )
    message_id = msg_result.scalar_one_or_none()

    # Mensagem duplicada — no-op DE VERDADE: rollback, nunca commit. Até aqui esta
    # transação pode ter encerrado a conversa parada e aberto outra vazia, e gravar isso
    # deixava uma reentrega (o backlog que o WhatsApp devolve ao reconectar) fechar a
    # conversa do cliente e plantar uma conversa sem mensagem na fila.
    if message_id is None:
        await db.rollback()
        return None

    # 5. Update Conversation desnormalizado
    await db.execute(
        text(
            "UPDATE conversations SET "
            "last_message_at = :ts, "
            "last_message_preview = :preview, "
            "unread_count = unread_count + 1 "
            "WHERE id = :conversation_id"
        ),
        {
            "ts": normalized.timestamp,
            "preview": preview,
            "conversation_id": str(conversation_id),
        },
    )

    await db.commit()

    # 6. Publicar evento tipado no Redis após commit bem-sucedido
    if encerrada is not None:
        log.info(
            "conversa %s encerrada por %ss de silêncio; a mensagem nova abre outra",
            encerrada,
            settings.CONVERSA_INATIVA_SEGUNDOS,
        )
        await events.publish(
            redis_client,
            tenant_id,
            events.CONVERSATION_STATUS,
            {"conversation_id": str(encerrada), "status": "resolved"},
        )
    await events.publish(
        redis_client,
        tenant_id,
        events.MESSAGE_INBOUND,
        {
            "conversation_id": str(conversation_id),
            "preview": preview,
            "channel": normalized.channel.name,
            # Para a notificação do sistema conseguir dizer DE QUEM é a mensagem. Sem
            # isto o cartão do navegador diz só "Nova mensagem", e o atendente tem de
            # voltar à aba para descobrir se valia a pena voltar à aba.
            "contact_name": normalized.contact_name or normalized.contact_phone,
        },
    )

    return {
        "tenant_id": tenant_id,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "contact_id": contact_id,
        "avatar_desatualizado": avatar_desatualizado,
    }
