"""Perfil do cliente: quem é, o que já contratou, o que a equipe anotou.

Nada aqui é dado novo. O perfil é a **junção** de quatro coisas que o sistema já grava e
que hoje só existem separadas — o atendente que quer saber quem é a pessoa do outro lado
precisa abrir a Agenda, procurar o telefone à mão e reler a thread:

- `contacts` — nome, telefone, canal;
- `contacts.profile` — memória da IA entre conversas (`contact_memory`);
- `conversations` — quando falou e como terminou;
- `contact_notes` — o que a equipe escreveu (único pedaço que nasceu com este módulo).

As reservas entram por composição: `_reservas` é o único ponto deste módulo que conhece
o derivado do salão, e o que ele importa de lá é uma função só (`expirar_vencidas`). É o
mesmo formato mínimo dos dois pontos que o derivado já usa para se ligar ao agente
(`CLAUDE.md` › Estrutura).

**Identidade: o perfil é do TELEFONE, não da linha em `contacts`.**

O `CLAUDE.md` põe merge de contatos fora de escopo, e isto não é merge — nada é apagado
nem reescrito. É agrupamento na leitura: `_irmaos` acha as linhas de `contacts` que
compartilham o telefone dentro do tenant, e o perfil soma o que está nelas.

Por que precisa: a identidade do contato é o endereço do canal, e esse endereço já mudou
de forma uma vez (o WhatsApp passou a mandar `<numero>@lid` no lugar do JID de telefone).
Quando isso acontece, `uq_contacts_tenant_channel_ext` cria um contato NOVO para a mesma
pessoa e o histórico dela se parte ao meio — `ingestion.py` já loga o caso justamente
porque ninguém sabia se acontecia de verdade. Agrupar na leitura resolve o sintoma sem
tocar na identidade, que é o que `ingestion.py` alerta ser prematuro.

Contato sem telefone (o `@lid` antigo, que ninguém conseguiu reparar) não agrupa com
ninguém: `_irmaos` devolve só ele. Casar por telefone NULL juntaria clientes distintos,
que é muito pior que mostrá-los separados.
"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import avatars, salao_agenda

# Teto das listas do painel. O perfil é uma tela de decisão ("seguro este sábado?"), não
# um relatório: cliente de dois anos com quarenta conversas não pode empurrar as
# anotações para fora da primeira dobra.
MAX_CONVERSAS = 10
MAX_NOTAS = 50
MAX_RESERVAS = 20

# Mesmo teto de `salao_resumo.MAX_CAMPOS`, pelo mesmo motivo: o `intake` é dicionário
# livre decidido pela persona, e um cliente falante enche a tela.
MAX_CAMPOS = 12

TAMANHO_MAX_NOTA = 2000


async def _irmaos(
    db: AsyncSession, tenant_id: UUID, contact_id: UUID
) -> tuple[list[str], dict]:
    """Os contatos que são a mesma pessoa, e a linha do contato pedido.

    Devolve `([], {})` quando o contato não é deste tenant — é o guarda de isolamento de
    todo o módulo, e por isso toda função pública começa por aqui.
    """
    row = await db.execute(
        text(
            "SELECT id, name, phone, channel, profile, channel_metadata, created_at "
            "FROM contacts WHERE id = :contact_id AND tenant_id = :tenant_id"
        ),
        {"contact_id": str(contact_id), "tenant_id": str(tenant_id)},
    )
    r = row.fetchone()
    if r is None:
        return [], {}

    alvo = {
        "contact_id": str(r.id),
        "nome": r.name,
        "telefone": r.phone,
        "canal": r.channel,
        "profile": dict(r.profile or {}),
        "avatar_url": avatars.url_de(tenant_id, r.id, dict(r.channel_metadata or {})),
        "cliente_desde": r.created_at,
    }
    if not r.phone:
        return [str(r.id)], alvo

    # `channel` entra no casamento porque telefone só identifica pessoa DENTRO de um
    # canal — e é a mesma chave que `ingestion.py` usa para detectar o contato partido.
    irmaos = await db.execute(
        text(
            "SELECT id FROM contacts "
            "WHERE tenant_id = :tenant_id AND channel = :channel AND phone = :phone"
        ),
        {"tenant_id": str(tenant_id), "channel": r.channel, "phone": r.phone},
    )
    return [str(x.id) for x in irmaos.fetchall()], alvo


async def _conversas(
    db: AsyncSession, tenant_id: UUID, contact_ids: list[str]
) -> list[dict]:
    """Histórico de atendimentos, do mais recente para o mais antigo."""
    rows = await db.execute(
        text(
            "SELECT c.id, c.status, c.priority, c.created_at, c.last_message_at, "
            "       u.full_name AS atendente_nome, u.email AS atendente_email "
            "FROM conversations c "
            "LEFT JOIN users u ON u.id = c.assigned_user_id AND u.tenant_id = c.tenant_id "
            "WHERE c.tenant_id = :tenant_id AND c.contact_id = ANY(CAST(:ids AS uuid[])) "
            "ORDER BY COALESCE(c.last_message_at, c.created_at) DESC "
            "LIMIT :limite"
        ),
        {"tenant_id": str(tenant_id), "ids": contact_ids, "limite": MAX_CONVERSAS},
    )
    return [
        {
            "conversation_id": str(r.id),
            "status": r.status,
            "priority": r.priority,
            "criada_em": r.created_at,
            "ultima_mensagem_em": r.last_message_at,
            "atendente": r.atendente_nome or r.atendente_email,
        }
        for r in rows.fetchall()
    ]


async def _notas(
    db: AsyncSession, tenant_id: UUID, contact_ids: list[str]
) -> list[dict]:
    """Anotações da equipe, da mais nova para a mais velha.

    `LEFT JOIN` em users porque `user_id` é `SET NULL`: atendente que saiu da equipe
    deixa a nota sem autor, e a nota continua valendo.
    """
    rows = await db.execute(
        text(
            "SELECT n.id, n.texto, n.created_at, n.user_id, "
            "       u.full_name AS autor_nome, u.email AS autor_email "
            "FROM contact_notes n "
            "LEFT JOIN users u ON u.id = n.user_id AND u.tenant_id = n.tenant_id "
            "WHERE n.tenant_id = :tenant_id "
            "AND n.contact_id = ANY(CAST(:ids AS uuid[])) "
            "ORDER BY n.created_at DESC LIMIT :limite"
        ),
        {"tenant_id": str(tenant_id), "ids": contact_ids, "limite": MAX_NOTAS},
    )
    return [
        {
            "id": str(r.id),
            "texto": r.texto,
            "criada_em": r.created_at,
            "autor_id": str(r.user_id) if r.user_id else None,
            "autor": r.autor_nome or r.autor_email,
        }
        for r in rows.fetchall()
    ]


async def _reservas(
    db: AsyncSession, tenant_id: UUID, contact_ids: list[str], telefone: str | None
) -> list[dict]:
    """Todos os agendamentos do cliente, ativos e encerrados, do mais recente ao antigo.

    Não reusa `salao_agenda.reservas_do_contato` porque ela responde outra pergunta: é o
    contexto do AGENTE ("sua data ainda está de pé?"), então para nas ativas e devolve
    duas. O painel quer o histórico — o sábado que caiu em março é contexto de quem
    decide se segura o de agosto. O que se reusa é o casamento por contato OU telefone,
    replicado abaixo, e a expiração preguiçosa.

    `expirar_vencidas` primeiro, e não como detalhe: sem ela um hold já vencido apareceria
    no painel como "pré-reservada" — exatamente a promessa que o sistema não pode mais
    cumprir. É a mesma cobrança na leitura que dispensa um `celery beat` no projeto.
    """
    await salao_agenda.expirar_vencidas(db, tenant_id)

    rows = await db.execute(
        text(
            "SELECT id, data, status, criada_por, expira_em, nome_cliente, telefone, "
            "       observacao, conversation_id "
            "FROM salao_reservas "
            "WHERE tenant_id = :tenant_id "
            "AND (contact_id = ANY(CAST(:ids AS uuid[])) "
            "     OR (CAST(:telefone AS text) IS NOT NULL "
            "         AND telefone = CAST(:telefone AS text))) "
            "ORDER BY data DESC LIMIT :limite"
        ),
        {
            "tenant_id": str(tenant_id),
            "ids": contact_ids,
            "telefone": telefone or None,
            "limite": MAX_RESERVAS,
        },
    )
    return [
        {
            "id": str(r.id),
            "data": r.data,
            "status": r.status,
            "criada_por": r.criada_por,
            "expira_em": r.expira_em,
            "observacao": r.observacao,
            "conversation_id": str(r.conversation_id) if r.conversation_id else None,
        }
        for r in rows.fetchall()
    ]


def _intake(perfis: list[dict]) -> dict[str, str]:
    """Une o `intake` dos contatos irmãos. O mais recente vence a chave repetida."""
    dados: dict[str, str] = {}
    for p in perfis:
        bruto = p.get("intake")
        if isinstance(bruto, dict):
            dados.update(bruto)
    # Chave de controle da persona (`_acao_*`) nunca é dado do cliente; `salao_acoes`
    # já as remove antes de persistir, mas a tela é o pior lugar para uma delas vazar.
    return {
        k: str(v)
        for k, v in list(dados.items())[:MAX_CAMPOS]
        if not k.startswith("_") and str(v).strip()
    }


async def perfil(db: AsyncSession, tenant_id: UUID, contact_id: UUID) -> dict | None:
    """O painel inteiro. `None` quando o contato não é deste tenant (ou não existe)."""
    ids, alvo = await _irmaos(db, tenant_id, contact_id)
    if not ids:
        return None

    perfis = [alvo["profile"]]
    if len(ids) > 1:
        outros = await db.execute(
            text(
                "SELECT profile FROM contacts "
                "WHERE tenant_id = :tenant_id AND id = ANY(CAST(:ids AS uuid[])) "
                "AND id <> :alvo ORDER BY created_at"
            ),
            {"tenant_id": str(tenant_id), "ids": ids, "alvo": alvo["contact_id"]},
        )
        # O contato pedido vai por ÚLTIMO: é o que está em uso agora, e na colisão de
        # chave o dado dele é o mais recente.
        perfis = [dict(x.profile or {}) for x in outros.fetchall()] + perfis

    return {
        "contact_id": alvo["contact_id"],
        "nome": alvo["nome"],
        "telefone": alvo["telefone"],
        "canal": alvo["canal"],
        "avatar_url": alvo["avatar_url"],
        "cliente_desde": alvo["cliente_desde"],
        # Quantos registros de contato esta pessoa tem. > 1 é o cliente partido que
        # `ingestion.py` loga; a tela avisa, em vez de deixar o número mentir sozinho.
        "contatos_unidos": len(ids),
        "dados_coletados": _intake(perfis),
        "ultimo_resumo": next(
            (p["last_summary"] for p in reversed(perfis) if p.get("last_summary")),
            None,
        ),
        "conversas": await _conversas(db, tenant_id, ids),
        "reservas": await _reservas(db, tenant_id, ids, alvo["telefone"]),
        "notas": await _notas(db, tenant_id, ids),
    }


async def criar_nota(
    db: AsyncSession,
    tenant_id: UUID,
    contact_id: UUID,
    user_id: UUID,
    texto: str,
) -> dict | None:
    """Grava uma anotação. `None` quando o contato não é deste tenant.

    O `tenant_id` gravado é o do token, nunca o da linha lida — a checagem de posse já
    aconteceu em `_irmaos`, e derivar o tenant do que se leu inverteria a ordem.
    """
    texto = texto.strip()[:TAMANHO_MAX_NOTA]
    if not texto:
        return None
    ids, _ = await _irmaos(db, tenant_id, contact_id)
    if not ids:
        return None

    row = await db.execute(
        text(
            "INSERT INTO contact_notes (id, tenant_id, contact_id, user_id, texto) "
            "VALUES (gen_random_uuid(), :tenant_id, :contact_id, :user_id, :texto) "
            "RETURNING id, texto, created_at"
        ),
        {
            "tenant_id": str(tenant_id),
            "contact_id": str(contact_id),
            "user_id": str(user_id),
            "texto": texto,
        },
    )
    r = row.fetchone()
    autor = await db.execute(
        text("SELECT full_name, email FROM users WHERE id = :id"),
        {"id": str(user_id)},
    )
    a = autor.fetchone()
    return {
        "id": str(r.id),
        "texto": r.texto,
        "criada_em": r.created_at,
        "autor_id": str(user_id),
        "autor": (a.full_name or a.email) if a is not None else None,
    }


async def apagar_nota(
    db: AsyncSession,
    tenant_id: UUID,
    nota_id: UUID,
    user_id: UUID,
    is_admin: bool,
) -> bool:
    """Apaga a anotação. False quando ela não existe, não é do tenant, ou não é sua.

    Quem apaga é o autor — ou um admin, que é quem responde pelo que fica escrito sobre
    os clientes. A condição vai no `WHERE` e não numa leitura anterior: é DELETE, e ler
    para depois decidir em Python é a forma de apagar a nota de outra pessoa numa corrida.
    """
    sql = "DELETE FROM contact_notes WHERE id = :id AND tenant_id = :tenant_id"
    params: dict = {"id": str(nota_id), "tenant_id": str(tenant_id)}
    if not is_admin:
        sql += " AND user_id = :user_id"
        params["user_id"] = str(user_id)
    result = await db.execute(text(sql), params)
    return result.rowcount > 0
