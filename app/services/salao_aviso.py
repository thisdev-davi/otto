"""Aviso ao cliente quando a equipe confirma a reserva dele.

**Este é o único ponto em que a agenda fala com o WhatsApp**, e por muito tempo não
existiu de propósito — o `CLAUDE.md` listava "confirmar reserva não envia mensagem ao
cliente" nos limites de escopo, dizendo que mudar isso seria feature nova e precisaria
ser pedida. Foi pedida. O que a regra protegia continua valendo e está preservado aqui:

- O envio **nunca é automático**. Quem confirma revisa o texto antes de sair; a rota só
  envia se receber a mensagem no corpo. Confirmar sem avisar continua possível.
- Quem decide o texto é o tenant (`bot_settings.confirmacao_message`), não o modelo. Não
  há LLM neste caminho: a informação precisa estar exata, e é a única coisa que o cliente
  quer saber.
- O envio passa por `get_provider()` como todo o resto (regra 1) — nada de Evolution aqui.

A mensagem é **persistida na conversa**. Não é detalhe: sem isso o atendente não vê o que
o cliente recebeu, e o agente, que lê o histórico, contradiria o aviso na mensagem
seguinte.
"""

import json
import logging
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.factory import get_provider
from app.channels.types import OutboundMessage
from app.models.enums import Channel, ContentType, MessageDirection, MessageStatus
from app.services import salao_agenda
from app.services.channels import JOIN_CONEXAO
from app.services.bot_settings import load_bot_config, render_confirmacao

log = logging.getLogger("uvicorn.error")


async def _destino(db: AsyncSession, tenant_id: UUID, reserva: dict):
    """Para quem enviar e em qual conversa registrar. `None` quando não há como avisar.

    A conversa NÃO é necessariamente a que originou a reserva: aquela costuma estar
    resolvida (foi exatamente o caso que motivou tudo isto), e enterrar o aviso numa
    thread fechada faz o atendente não ver o que o cliente recebeu. Usa-se a conversa mais
    recente do contato, com a da reserva como reserva técnica.
    """
    if not reserva.get("contact_id"):
        return None
    row = await db.execute(
        text(
            "SELECT c.id AS conversation_id, "
            "       COALESCE(cc.id, fb.id) AS channel_connection_id, "
            "       ct.external_id AS contact_external_id, ct.phone AS contact_phone, "
            "       ct.name AS contact_name, "
            "       COALESCE(cc.channel, fb.channel) AS channel, "
            "       COALESCE(cc.credentials, fb.credentials)->>'instance' AS instance_key "
            "FROM conversations c "
            "JOIN contacts ct ON ct.id = c.contact_id "
            + JOIN_CONEXAO
            + "WHERE c.tenant_id = :tenant_id AND c.contact_id = :contact_id "
            "  AND COALESCE(cc.id, fb.id) IS NOT NULL "
            "ORDER BY c.last_message_at DESC NULLS LAST, c.created_at DESC LIMIT 1"
        ),
        {"tenant_id": str(tenant_id), "contact_id": reserva["contact_id"]},
    )
    return row.fetchone()


async def montar_mensagem(
    db: AsyncSession, tenant_id: UUID, reserva: dict
) -> str | None:
    """Texto do aviso já preenchido, ou `None` quando não há a quem enviar.

    Renderizado **no servidor** de propósito. Se o front montasse o texto, existiriam
    duas implementações da mesma frase e a divergência apareceria como mensagem errada no
    WhatsApp de um cliente — a mesma classe de erro que a regra 4 descreve para a agenda.
    """
    destino = await _destino(db, tenant_id, reserva)
    if destino is None:
        return None

    bot = await load_bot_config(db, tenant_id)
    situacao = (await salao_agenda.resolver(db, tenant_id, [reserva["data"]])).get(
        reserva["data"]
    )
    preco = situacao["preco_centavos"] if situacao else None
    return render_confirmacao(
        bot.confirmacao_message,
        {
            # Nome da reserva primeiro (é o que o cliente disse chamar-se), nome do
            # contato como rede. Só o primeiro nome: "Oi, Lucas!" e não o nome inteiro
            # do perfil do WhatsApp.
            "nome": (reserva.get("nome_cliente") or destino.contact_name or "").split(
                " "
            )[0],
            "data": reserva["data"].strftime("%d/%m/%Y"),
            "valor": salao_agenda.formatar_preco(preco) if preco else "",
        },
    )


async def enviar_confirmacao(
    db: AsyncSession, tenant_id: UUID, reserva: dict, mensagem: str
) -> bool:
    """Envia o aviso e registra na thread. `False` quando não saiu.

    **Não é chamada dentro da transação da confirmação**, e isso é deliberado: uma falha
    aqui não pode desfazer a confirmação. A data está mesmo segurada — reverter seria
    mentir para a agenda e para quem já viu a reserva confirmada na tela. Quem chama
    reporta o `False` ao atendente, que reenvia pela Inbox.

    Não há trava contra aviso duplicado aqui de propósito: quem já garante isso é o
    `UPDATE` condicional de `salao_agenda.confirmar`, que só devolve a reserva para a
    transação que de fato virou o status. Inventar uma segunda trava seria duplicar a
    regra em dois lugares.
    """
    texto = (mensagem or "").strip()
    if not texto:
        return False
    destino = await _destino(db, tenant_id, reserva)
    if destino is None:
        return False

    canal = Channel[destino.channel]
    outbound = OutboundMessage(
        channel=canal,
        channel_connection_id=destino.channel_connection_id,
        to_external_id=destino.contact_external_id,
        to_phone=destino.contact_phone,
        content_type=ContentType.TEXT,
        content=texto,
        channel_metadata={"instance_key": destino.instance_key},
    )
    try:
        resultado = await get_provider(canal).send(outbound)
    except Exception as exc:
        log.warning("salao_aviso: envio da confirmação falhou: %s", exc)
        resultado = None

    sucesso = bool(resultado and resultado.success)
    await db.execute(
        text(
            "INSERT INTO messages "
            "(id, conversation_id, tenant_id, direction, content_type, content, "
            "external_id, channel, status, channel_metadata, created_at) "
            "VALUES (:id, :conversation_id, :tenant_id, :direction, 'TEXT', :content, "
            ":external_id, :channel, :status, CAST(:meta AS jsonb), clock_timestamp())"
        ),
        {
            "id": str(uuid4()),
            "conversation_id": str(destino.conversation_id),
            "tenant_id": str(tenant_id),
            "direction": MessageDirection.OUTBOUND.name,
            "content": texto,
            "external_id": resultado.external_id if sucesso else None,
            "channel": destino.channel,
            "status": MessageStatus.SENT.name if sucesso else MessageStatus.FAILED.name,
            # Registrada mesmo quando o envio falha: a tentativa faz parte do histórico
            # que o atendente precisa ver antes de decidir reenviar na mão.
            "meta": json.dumps({"aviso_confirmacao": True}),
        },
    )
    return sucesso
