"""Mensagem apagada ou editada depois de entregue — pelo cliente ou pela equipe.

**Nada é apagado do banco.** A linha fica e ganha `channel_metadata.revisao`:
`{apagada_em, apagada_por, editada_em, original}`. Apagar do Otto o que o cliente apagou
no WhatsApp tiraria da equipe o que ele chegou a dizer (uma data, um valor combinado); o
que importa é ela ver que ele voltou atrás. `original` guarda o texto da PRIMEIRA versão:
reeditar não o sobrescreve.

`apagada_por` é `"canal"` quando o aviso veio do WhatsApp, ou o id do atendente quando
a ação saiu do Otto — é o que permite à thread dizer "por Fulano".
"""

import json
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Os dois UPDATEs compartilham o alvo: o par (canal, id externo) DENTRO do tenant da
# conexão autenticada pela URL do webhook — nunca só o par, que vem do corpo (regra 5).
_ALVO_DO_CANAL = (
    "WHERE channel = :channel AND external_id = :ext "
    "AND tenant_id = (SELECT tenant_id FROM channel_connections WHERE id = :cid) "
)

_SQL_APAGADA = (
    "UPDATE messages SET channel_metadata = COALESCE(channel_metadata, '{}') || "
    # A marca que já existe vence: o eco do WhatsApp de um apagar feito pelo Otto não
    # pode trocar "por Fulano" por "pelo canal".
    "  jsonb_build_object('revisao', CAST(:marca AS jsonb) || "
    "    COALESCE(channel_metadata->'revisao', '{}')) "
)

_SQL_EDITADA = (
    "UPDATE messages SET content = :novo, "
    "  channel_metadata = COALESCE(channel_metadata, '{}') || jsonb_build_object("
    "    'revisao', COALESCE(channel_metadata->'revisao', '{}') || jsonb_build_object("
    "      'editada_em', CAST(:agora AS text),"
    "      'original', COALESCE(channel_metadata->'revisao'->>'original', content))) "
)


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat()


async def aplicar_do_canal(
    db: AsyncSession,
    connection_id: str,
    channel: str,
    external_id: str,
    acao: str,
    novo: str | None = None,
) -> dict | None:
    """Revisão avisada pelo canal. None quando a mensagem não é nossa conhecida."""
    params = {"channel": channel, "ext": external_id, "cid": connection_id}
    if acao == "apagada":
        sql = _SQL_APAGADA
        params["marca"] = json.dumps({"apagada_em": _agora(), "apagada_por": "canal"})
    else:
        sql = _SQL_EDITADA
        params.update(novo=novo, agora=_agora())
    row = await db.execute(
        text(sql + _ALVO_DO_CANAL + "RETURNING id, tenant_id, conversation_id"), params
    )
    alvo = row.fetchone()
    return dict(alvo._mapping) if alvo else None
