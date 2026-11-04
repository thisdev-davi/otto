"""Galeria do espaço: o que o dono cadastra e o que o agente manda quando pedem para ver.

Num salão de festas a foto é meia venda — quem pesquisa espaço pergunta "tem foto?" antes
de perguntar preço, e quase sempre fora do horário comercial. Por isso a galeria não é
uma pasta de arquivos: é conteúdo do agente, com ordem e liga/desliga por foto.

O arquivo em disco segue a mesma convenção da mídia de conversa (`MEDIA_DIR/{tenant}/{id}`),
reusando `app/services/attachments.py` — validação, teto de tamanho e derivação de tipo
pelo mimetype já estão resolvidos lá.
"""

import json
import logging
import os
from uuid import UUID, uuid4

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.factory import get_provider
from app.channels.types import OutboundMedia, OutboundMessage
from app.models.enums import Channel, ContentType, MessageDirection, MessageStatus
from app.services import attachments, media_links, storage

log = logging.getLogger("uvicorn.error")

# Tipo assinado das URLs da galeria. Separado de "media" para que uma assinatura de foto
# não sirva para ler anexo de conversa, e vice-versa.
TIPO_LINK = "foto"

# Quantas fotos o agente manda de uma vez. Quatro mostra o espaço sem virar rajada — e
# rajada de mídia numa conexão não-oficial é justamente o padrão que arrisca ban.
MAX_ENVIO = 4

# Pausa entre as fotos, em ms. Menor que a de texto (o cliente entende que mídia demora),
# mas nunca zero: quatro imagens instantâneas denunciam robô.
DELAY_PRIMEIRA_MS = 900
DELAY_DEMAIS_MS = 1600


# ─────────────────────────── CRUD ───────────────────────────


def _serializar(r, tenant_id: UUID) -> dict:
    """Linha do banco → dict da API, com a URL do arquivo já assinada.

    A assinatura sai daqui, e não do front, porque só o servidor tem a chave. Antes o
    front montava `?token=<JWT>`, o que punha uma credencial de sessão inteira em cada
    `<img src>` da grade.
    """
    return {
        "id": str(r.id),
        "titulo": r.titulo,
        "mimetype": r.mimetype,
        "tamanho_bytes": r.tamanho_bytes,
        "ordem": r.ordem,
        "ativa": r.ativa,
        "created_at": r.created_at,
        "arquivo_url": (
            f"/espaco/fotos/{r.id}/arquivo"
            f"?{media_links.assinar(TIPO_LINK, r.id, tenant_id)}"
        ),
    }


async def listar(
    db: AsyncSession, tenant_id: UUID, apenas_ativas: bool = False
) -> list[dict]:
    sql = (
        "SELECT id, titulo, mimetype, tamanho_bytes, ordem, ativa, created_at "
        "FROM salao_fotos WHERE tenant_id = :tenant_id "
    )
    if apenas_ativas:
        sql += "AND ativa "
    sql += "ORDER BY ordem, created_at"
    rows = await db.execute(text(sql), {"tenant_id": str(tenant_id)})
    return [_serializar(r, tenant_id) for r in rows.fetchall()]


async def criar(
    db: AsyncSession, tenant_id: UUID, file: UploadFile, titulo: str | None = None
) -> dict:
    """Valida, grava em disco e cadastra uma foto. Só imagem passa.

    `attachments.validar` aceita vídeo, áudio e documento porque serve ao anexo do
    atendente; aqui o filtro é mais estreito de propósito — o agente manda isto sozinho,
    e um PDF no meio de "olha o espaço" seria constrangedor.
    """
    anexo = await attachments.validar(file)
    if anexo.content_type is not ContentType.IMAGE:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="A galeria do espaço aceita apenas imagens (JPEG, PNG ou WebP).",
        )

    foto_id = uuid4()
    caminho = await attachments.gravar(anexo, tenant_id, foto_id, audit_session=db)
    row = await db.execute(
        text(
            "INSERT INTO salao_fotos "
            "(id, tenant_id, titulo, caminho, mimetype, tamanho_bytes, ordem) "
            "SELECT :id, :tenant_id, :titulo, :caminho, :mimetype, :tamanho, "
            "  COALESCE(MAX(ordem) + 1, 0) FROM salao_fotos WHERE tenant_id = :tenant_id "
            "RETURNING id, titulo, mimetype, tamanho_bytes, ordem, ativa, created_at"
        ),
        {
            "id": str(foto_id),
            "tenant_id": str(tenant_id),
            "titulo": titulo or None,
            "caminho": caminho,
            "mimetype": anexo.mimetype,
            "tamanho": len(anexo.data),
        },
    )
    r = row.fetchone()
    return _serializar(r, tenant_id)


async def atualizar(
    db: AsyncSession,
    tenant_id: UUID,
    foto_id: UUID,
    titulo: str | None = None,
    ordem: int | None = None,
    ativa: bool | None = None,
) -> dict | None:
    row = await db.execute(
        text(
            "UPDATE salao_fotos SET "
            "titulo = COALESCE(:titulo, titulo), "
            "ordem = COALESCE(:ordem, ordem), "
            "ativa = COALESCE(:ativa, ativa) "
            "WHERE id = :id AND tenant_id = :tenant_id "
            "RETURNING id, titulo, mimetype, tamanho_bytes, ordem, ativa, created_at"
        ),
        {
            "id": str(foto_id),
            "tenant_id": str(tenant_id),
            "titulo": titulo,
            "ordem": ordem,
            "ativa": ativa,
        },
    )
    r = row.fetchone()
    if r is None:
        return None
    return _serializar(r, tenant_id)


async def remover(db: AsyncSession, tenant_id: UUID, foto_id: UUID) -> bool:
    """Tira a foto da galeria. **O arquivo em disco fica.**

    Não é descuido: as mensagens já enviadas apontam para este mesmo arquivo (ver
    `enviar_galeria`), e apagá-lo transformaria em ícone quebrado o histórico de todas as
    conversas em que a foto foi mandada. O custo de manter alguns arquivos órfãos é menor
    que o de furar a thread de um cliente.
    """
    row = await db.execute(
        text(
            "DELETE FROM salao_fotos WHERE id = :id AND tenant_id = :tenant_id "
            "RETURNING id"
        ),
        {"id": str(foto_id), "tenant_id": str(tenant_id)},
    )
    return row.fetchone() is not None


async def caminho_de(db: AsyncSession, tenant_id: UUID, foto_id: UUID) -> tuple | None:
    """(caminho, mimetype) para servir o arquivo, ou None se não existe no tenant."""
    row = await db.execute(
        text(
            "SELECT caminho, mimetype FROM salao_fotos "
            "WHERE id = :id AND tenant_id = :tenant_id"
        ),
        {"id": str(foto_id), "tenant_id": str(tenant_id)},
    )
    r = row.fetchone()
    if r is None or not await storage.exists(r.caminho):
        return None
    return r.caminho, r.mimetype


async def tenant_de(db: AsyncSession, foto_id: UUID) -> UUID | None:
    """Dono da foto, ou None. Usado para validar a assinatura da URL.

    A assinatura é feita sobre (foto, tenant), então validar exige saber o tenant antes
    de confiar em quem pede — daí a leitura sem escopo aqui. Nada além do id do dono sai
    desta função, e o acesso ao arquivo continua passando por `caminho_de`, que filtra
    por tenant.
    """
    row = await db.execute(
        text("SELECT tenant_id FROM salao_fotos WHERE id = :id"),
        {"id": str(foto_id)},
    )
    return row.scalar_one_or_none()


# ─────────────────────────── envio pelo agente ───────────────────────────


async def enviar_galeria(
    db: AsyncSession,
    tenant_id: UUID,
    conversation_id: UUID,
    conv,
    limite: int = MAX_ENVIO,
) -> int:
    """Manda as fotos ativas para o cliente e registra cada uma na thread.

    `conv` é a linha carregada por `bot_orchestrator._load_conversation` — traz canal,
    conexão, external_id do contato e instance_key.

    Sem import de Evolution: o canal é alcançado só por `get_provider` (`CLAUDE.md` §1).
    Devolve quantas fotos foram efetivamente enviadas.
    """
    rows = await db.execute(
        text(
            "SELECT id, caminho, mimetype, titulo FROM salao_fotos "
            "WHERE tenant_id = :tenant_id AND ativa "
            "ORDER BY ordem, created_at LIMIT :limite"
        ),
        {"tenant_id": str(tenant_id), "limite": limite},
    )
    fotos = rows.fetchall()
    if not fotos:
        return 0

    channel = Channel(conv.channel.lower())
    provider = get_provider(channel)
    enviadas = 0

    for indice, foto in enumerate(fotos):
        try:
            dados = await storage.read(foto.caminho)
        except OSError:
            # Arquivo sumiu do disco: pula esta e segue. Uma foto perdida não pode
            # cancelar as outras três nem derrubar o turno do agente.
            log.warning("salao_fotos: arquivo ausente para a foto %s", foto.id)
            continue

        message_id = uuid4()
        outbound = OutboundMessage(
            channel=channel,
            channel_connection_id=conv.channel_connection_id,
            to_external_id=conv.contact_external_id,
            to_phone=conv.contact_phone,
            content_type=ContentType.IMAGE,
            content="",
            media=OutboundMedia(
                data=dados,
                filename=os.path.basename(foto.caminho),
                mimetype=foto.mimetype,
            ),
            channel_metadata={"instance_key": conv.instance_key},
            delay_ms=DELAY_PRIMEIRA_MS if indice == 0 else DELAY_DEMAIS_MS,
        )
        from app.services.bot_orchestrator import _still_unassigned

        if not await _still_unassigned(db, tenant_id, conversation_id):
            break
        try:
            resultado = await provider.send(outbound)
        except Exception as exc:
            log.warning("salao_fotos: envio falhou (%s): %s", channel, exc)
            resultado = None

        sucesso = bool(resultado and resultado.success)
        enviadas += 1 if sucesso else 0
        # `path` aponta para o arquivo DA GALERIA, não para uma cópia: a mesma foto vai
        # para centenas de conversas, e duplicar o arquivo por mensagem encheria o disco
        # sem ganho nenhum. É por isso que `remover` preserva o arquivo.
        metadata = {
            "bot": True,
            "media": {
                "has_media": True,
                "status": "stored",
                "path": foto.caminho,
                "mimetype": foto.mimetype,
                "filename": os.path.basename(foto.caminho),
            },
        }
        await db.execute(
            text(
                "INSERT INTO messages "
                "(id, conversation_id, tenant_id, direction, content_type, content, "
                "external_id, channel, status, channel_metadata, created_at) "
                "VALUES (:id, :conversation_id, :tenant_id, :direction, 'IMAGE', NULL, "
                ":external_id, :channel, :status, CAST(:meta AS jsonb), clock_timestamp())"
            ),
            {
                "id": str(message_id),
                "conversation_id": str(conversation_id),
                "tenant_id": str(tenant_id),
                "direction": MessageDirection.OUTBOUND.name,
                "external_id": resultado.external_id if sucesso else None,
                "channel": conv.channel,
                "status": MessageStatus.SENT.name
                if sucesso
                else MessageStatus.FAILED.name,
                "meta": json.dumps(metadata),
            },
        )

    if enviadas:
        await db.execute(
            text(
                "UPDATE conversations SET last_message_at = now(), "
                "last_message_preview = :preview WHERE id = :conversation_id AND tenant_id = :tenant_id"
            ),
            {
                "preview": "Foto",
                "conversation_id": str(conversation_id),
                "tenant_id": str(tenant_id),
            },
        )
    return enviadas
