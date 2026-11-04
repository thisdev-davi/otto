"""Resumo de uma reserva: o que o dono precisa saber antes de confirmar ou cancelar.

Existe porque a pré-reserva chega à Agenda sem contexto. O card diz "Carlos Souza,
15/08, expira em 2d" — e a decisão de segurar ou soltar um sábado depende de coisas que
estão na conversa: para quantas pessoas é a festa, que tipo de evento, o que o agente já
prometeu. Sem isto o dono abre a Inbox, procura o telefone à mão e lê a thread inteira.

**Nenhuma coluna nova.** Tudo o que vai aqui já é gravado pelo turno do agente:

- `bot_sessions.variables->'intake'` — o `captured` da conversa, campos que a persona pediu;
- `contacts.profile->'intake'` — o mesmo, promovido a memória do contato (`contact_memory`),
  e que sobrevive à conversa ter sido resolvida;
- a última **nota interna** da thread — onde `_build_intake_note` grava o resumo da triagem
  e `salao_acoes._nota` grava o texto da pré-reserva com prazo.

A ordem de preferência do intake é sessão → contato: a sessão é desta conversa e é a mais
recente; o perfil do contato pode trazer dado de um atendimento antigo, que ainda ajuda
mas não pode sobrescrever o que o cliente acabou de dizer.
"""

import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import salao_agenda

log = logging.getLogger("uvicorn.error")

# Quantos campos de intake vão para a tela. O dicionário é livre (a persona decide as
# chaves), então um cliente falante pode encher isto — e um painel de decisão com trinta
# linhas deixa de ser um painel de decisão.
MAX_CAMPOS = 12


async def _intake(
    db: AsyncSession,
    tenant_id: UUID,
    conversation_id: UUID | None,
    contact_id: UUID | None,
) -> dict[str, str]:
    """Dados coletados pelo agente, da sessão desta conversa e da memória do contato."""
    dados: dict[str, str] = {}
    if contact_id:
        row = await db.execute(
            text(
                "SELECT profile -> 'intake' AS intake FROM contacts "
                "WHERE id = :contact_id AND tenant_id = :tenant_id"
            ),
            {"contact_id": str(contact_id), "tenant_id": str(tenant_id)},
        )
        r = row.fetchone()
        if r is not None and isinstance(r.intake, dict):
            dados.update(r.intake)
    if conversation_id:
        row = await db.execute(
            text(
                "SELECT variables -> 'intake' AS intake FROM bot_sessions "
                "WHERE conversation_id = :conversation_id AND tenant_id = :tenant_id"
            ),
            {"conversation_id": str(conversation_id), "tenant_id": str(tenant_id)},
        )
        r = row.fetchone()
        if r is not None and isinstance(r.intake, dict):
            dados.update(r.intake)  # a sessão vence: é o que o cliente disse agora

    # Chave de controle da persona (`_acao_*`) nunca é dado do cliente. `salao_acoes.extrair`
    # já as remove antes de persistir, mas filtrar de novo aqui é barato e a tela é o pior
    # lugar para uma delas vazar.
    return {
        k: str(v)
        for k, v in list(dados.items())[:MAX_CAMPOS]
        if not k.startswith("_") and str(v).strip()
    }


async def _ultima_nota(
    db: AsyncSession, tenant_id: UUID, conversation_id: UUID
) -> str | None:
    """A nota interna mais recente da conversa — resumo da triagem ou texto da pré-reserva."""
    row = await db.execute(
        text(
            "SELECT content FROM messages "
            "WHERE conversation_id = :conversation_id AND tenant_id = :tenant_id "
            "AND channel_metadata ->> 'internal' = 'true' "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        {"conversation_id": str(conversation_id), "tenant_id": str(tenant_id)},
    )
    r = row.fetchone()
    return r.content if r is not None else None


async def de_reserva(
    db: AsyncSession, tenant_id: UUID, reserva_id: UUID
) -> dict | None:
    """Monta o resumo. `None` quando a reserva não é deste tenant (ou não existe)."""
    row = await db.execute(
        text(
            "SELECT r.id, r.data, r.status, r.criada_por, r.expira_em, r.nome_cliente, "
            "       r.telefone, r.observacao, r.created_at, r.conversation_id, "
            "       r.contact_id, c.name AS contato_nome, c.phone AS contato_telefone "
            "FROM salao_reservas r "
            "LEFT JOIN contacts c ON c.id = r.contact_id AND c.tenant_id = r.tenant_id "
            "WHERE r.id = :id AND r.tenant_id = :tenant_id"
        ),
        {"id": str(reserva_id), "tenant_id": str(tenant_id)},
    )
    r = row.fetchone()
    if r is None:
        return None

    situacao = (await salao_agenda.resolver(db, tenant_id, [r.data])).get(r.data)
    nota = (
        await _ultima_nota(db, tenant_id, r.conversation_id)
        if r.conversation_id
        else None
    )
    return {
        "reserva_id": str(r.id),
        "data": r.data,
        "status": r.status,
        "criada_por": r.criada_por,
        "expira_em": r.expira_em,
        "criada_em": r.created_at,
        # O nome da reserva é uma cópia do momento em que ela nasceu; o do contato é o
        # atual. Mostrar os dois evita o "quem é essa pessoa?" quando o contato foi
        # renomeado depois — e é por isso que a cópia existe em `salao_reservas`.
        "nome_cliente": r.nome_cliente,
        "contato_nome": r.contato_nome,
        # Mesma preferência sessão→contato do resto do módulo: a cópia da reserva primeiro,
        # o contato como rede. É o que faz uma reserva antiga — criada quando o telefone
        # ainda não era gravado — voltar a exibir número assim que o contato aprender o
        # dele, sem precisar de backfill.
        "telefone": r.telefone or r.contato_telefone,
        "observacao": r.observacao,
        "preco_centavos": situacao["preco_centavos"] if situacao else None,
        "conversation_id": str(r.conversation_id) if r.conversation_id else None,
        # Necessário para montar o aviso de confirmação: é o contato que diz se existe
        # alguém a quem avisar (reserva lançada à mão no painel não tem).
        "contact_id": str(r.contact_id) if r.contact_id else None,
        "dados_coletados": await _intake(
            db, tenant_id, r.conversation_id, r.contact_id
        ),
        "nota_interna": nota,
    }
