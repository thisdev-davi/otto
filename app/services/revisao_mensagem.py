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
from datetime import datetime, timedelta, timezone
from uuid import UUID

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


# Janelas do próprio WhatsApp: fora delas ele recusa a edição e o "apagar para todos".
# O front espelha estes números só para esconder o botão; quem decide é o servidor.
JANELA_EDICAO = timedelta(minutes=15)
JANELA_APAGAR = timedelta(hours=48)


class RevisaoRecusada(Exception):
    """Por que o atendente não pode editar ou apagar esta mensagem — texto para a tela."""

    def __init__(self, status: int, motivo: str) -> None:
        super().__init__(motivo)
        self.status = status
        self.motivo = motivo


async def alvo_do_atendente(
    db: AsyncSession,
    tenant_id: UUID,
    user_id: UUID,
    conversation_id: UUID,
    message_id: UUID,
    acao: str,
):
    """A mensagem que o atendente quer revisar, ou `RevisaoRecusada`.

    Só a PRÓPRIA mensagem (`sender_user_id`): nem a do bot, nem a de um colega. E só a
    que chegou ao canal (`external_id`): sem ele não há o que editar no aparelho do
    cliente.
    """
    row = await db.execute(
        text(
            "SELECT external_id, content_type, created_at, "
            "  channel_metadata->'revisao'->>'apagada_em' AS apagada_em "
            "FROM messages WHERE id = :mid AND conversation_id = :cid "
            "AND tenant_id = :tid AND direction = 'OUTBOUND' AND sender_user_id = :uid"
        ),
        {
            "mid": str(message_id),
            "cid": str(conversation_id),
            "tid": str(tenant_id),
            "uid": str(user_id),
        },
    )
    alvo = row.fetchone()
    if alvo is None:
        raise RevisaoRecusada(404, "Mensagem não encontrada entre as suas.")
    if not alvo.external_id:
        raise RevisaoRecusada(409, "Esta mensagem não chegou ao WhatsApp.")
    if alvo.apagada_em:
        raise RevisaoRecusada(409, "Esta mensagem já foi apagada.")
    if acao == "editada" and alvo.content_type != "TEXT":
        raise RevisaoRecusada(422, "Só mensagem de texto pode ser editada.")
    janela = JANELA_EDICAO if acao == "editada" else JANELA_APAGAR
    if datetime.now(timezone.utc) - alvo.created_at > janela:
        prazo = "15 minutos" if acao == "editada" else "48 horas"
        raise RevisaoRecusada(
            409, f"O WhatsApp só permite isso até {prazo} depois do envio."
        )
    return alvo


async def marcar_pelo_otto(
    db: AsyncSession,
    tenant_id: UUID,
    user_id: UUID,
    message_id: UUID,
    acao: str,
    novo: str | None = None,
) -> bool:
    """Grava a revisão que o canal já aceitou. Chamado DEPOIS do sucesso no provider.

    A ordem é a do aviso de confirmação do salão: efeito externo primeiro, banco
    depois. Gravar antes deixaria marcada como apagada uma mensagem que o WhatsApp
    recusou apagar — e o cliente continuaria lendo o que a equipe acha que sumiu.
    """
    # ponytail: duas edições simultâneas da mesma mensagem vão as duas ao WhatsApp e a
    # última vence nos dois lados. Uma trava por mensagem resolve se isso aparecer.
    alvo = (
        "WHERE id = :mid AND tenant_id = :tid AND sender_user_id = :uid "
        "AND NOT (COALESCE(channel_metadata->'revisao', '{}') ? 'apagada_em') "
    )
    params: dict = {"mid": str(message_id), "tid": str(tenant_id), "uid": str(user_id)}
    if acao == "apagada":
        sql = _SQL_APAGADA
        params["marca"] = json.dumps(
            {"apagada_em": _agora(), "apagada_por": str(user_id)}
        )
    else:
        sql = _SQL_EDITADA
        params.update(novo=novo, agora=_agora())
    row = await db.execute(text(sql + alvo + "RETURNING id"), params)
    return row.fetchone() is not None
