"""Banco de mídia: o que os clientes mandaram (fotos, figurinhas, vídeos, documentos, áudios).

**Só mídia RECEBIDA.** A enviada pela galeria do espaço reaproveita o mesmo arquivo em
toda mensagem (`salao_fotos.enviar_galeria`); apagar a partir de uma delas furaria o
histórico de todas as outras conversas. A recebida tem um arquivo por mensagem.

**Excluir apaga o arquivo, não a mensagem.** O histórico da conversa é append-only
(`OTTO_PROJETO.md` §6.3): a linha fica, marcada como removida, com quem e quando — senão
o "como na foto" do cliente perde a referência e some a prova de que algo foi apagado.
"""

import json
import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import audit, storage

log = logging.getLogger("uvicorn.error")


async def remover(
    db: AsyncSession, tenant_id: UUID, message_id: UUID, user_id: UUID
) -> bool:
    """Marca a mídia como removida e apaga o arquivo. False se não havia o que remover.

    O UPDATE é condicional (`status = 'stored'`), mesma regra da captura: dois cliques
    simultâneos não apagam duas vezes nem registram duas auditorias. O arquivo só sai
    DEPOIS do commit — com o banco ainda aberto, uma falha no commit deixaria a mensagem
    apontando para um arquivo que já não existe.
    """
    marca = json.dumps(
        {
            "status": "removed",
            "removed_at": datetime.now(timezone.utc).isoformat(),
            "removed_by": str(user_id),
        }
    )
    row = await db.execute(
        text(
            "UPDATE messages m SET channel_metadata = jsonb_set("
            "  m.channel_metadata, '{media}',"
            "  (m.channel_metadata->'media') - 'path' || CAST(:marca AS jsonb)) "
            "FROM (SELECT id, channel_metadata->'media'->>'path' AS path FROM messages "
            "      WHERE id = :mid AND tenant_id = :tid FOR UPDATE) antes "
            "WHERE m.id = antes.id AND m.tenant_id = :tid AND m.direction = 'INBOUND' "
            "  AND m.channel_metadata->'media'->>'status' = 'stored' "
            "RETURNING antes.path"
        ),
        {"mid": str(message_id), "tid": str(tenant_id), "marca": marca},
    )
    alvo = row.fetchone()
    if alvo is None:
        return False
    await audit.record(
        db,
        tenant_id,
        "media.delete",
        "media",
        message_id,
        actor_type="human",
        actor_user_id=user_id,
    )
    await db.commit()

    if alvo.path:
        try:
            await storage.delete(alvo.path)
        except Exception:
            # ponytail: o arquivo fica órfão e inalcançável (o caminho saiu do banco).
            # Uma varredura de órfãos por prefixo do tenant resolve se um dia importar.
            log.exception(
                "midia_recebida: arquivo da mensagem %s não foi apagado", message_id
            )
    return True
