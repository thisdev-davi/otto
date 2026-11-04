"""Leitura, publicação e — o que importa — o AVANÇO ATÔMICO do nó do fluxo.

O grafo é lido inteiro por turno (SELECT por chave primária) e publicado inteiro.

**A parte que não pode ser simplificada é `avancar`.** Dois turnos rodam juntos na mesma
conversa o tempo todo: o projeto não tem debounce (cada mensagem dispara seu próprio turno,
ver `ai_context.py`) e ainda existe a re-tentativa agendada de `tasks/bot.py`. Ler
`variables.fluxo.no`, decidir o destino em Python e salvar é a MESMA corrida das regras 2 e
3 do `CLAUDE.md`, num campo que ninguém suspeita — o sintoma seria o cliente recebendo o
menu duas vezes, ou pulando uma pergunta.

A condição vai no WHERE, e quem arbitra é o banco.
"""

import json
import logging
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = logging.getLogger("uvicorn.error")


async def carregar(db: AsyncSession, tenant_id: UUID) -> dict | None:
    """`{'grafo', 'versao', 'nome', 'gerenciado_por_arquivo'}` ou None se não há fluxo."""
    row = await db.execute(
        text(
            "SELECT nome, grafo, versao, gerenciado_por_arquivo "
            "FROM bot_flows WHERE tenant_id = :tenant_id"
        ),
        {"tenant_id": str(tenant_id)},
    )
    r = row.fetchone()
    if r is None:
        return None
    return {
        "nome": r.nome or "",
        "grafo": dict(r.grafo or {}),
        "versao": r.versao,
        "gerenciado_por_arquivo": r.gerenciado_por_arquivo,
    }


async def salvar(
    db: AsyncSession,
    tenant_id: UUID,
    grafo: dict,
    nome: str = "",
    gerenciado_por_arquivo: bool | None = None,
) -> int:
    """Publica o grafo e devolve a versão nova. NÃO valida — quem valida é quem chama.

    `versao` sobe a cada publicação e vai para `bot_sessions.variables.fluxo.versao`: é o
    que permite a nota interna dizer em qual versão a conversa estava quando um nó sumiu.
    """
    gerenciado = bool(gerenciado_por_arquivo)
    row = await db.execute(
        text(
            "INSERT INTO bot_flows (tenant_id, nome, grafo, versao, gerenciado_por_arquivo) "
            "VALUES (:tenant_id, :nome, CAST(:grafo AS jsonb), 1, :gerenciado) "
            "ON CONFLICT (tenant_id) DO UPDATE SET "
            "nome = EXCLUDED.nome, grafo = EXCLUDED.grafo, "
            "versao = bot_flows.versao + 1, "
            "gerenciado_por_arquivo = COALESCE(:gerenciado_ou_nulo, "
            "                                  bot_flows.gerenciado_por_arquivo), "
            "updated_at = now() "
            "RETURNING versao"
        ),
        {
            "tenant_id": str(tenant_id),
            "nome": nome or grafo.get("nome") or "",
            "grafo": json.dumps(grafo, ensure_ascii=False),
            "gerenciado": gerenciado,
            "gerenciado_ou_nulo": gerenciado_por_arquivo,
        },
    )
    return row.scalar_one()


async def avancar(
    db: AsyncSession, conversation_id: UUID, turno_lido: int, estado: dict
) -> bool:
    """Grava o novo estado do fluxo. True só para o turno que ganhou a corrida.

    `turno` é um contador que só sobe. Dois turnos concorrentes leem o mesmo valor; o
    primeiro a escrever incrementa e o segundo sai com rowcount 0 — e o motor devolve
    `None`, o orquestrador registra `SkipReason.FLOW_RACE` e NADA é enviado.

    O commit acontece aqui, e não no fim do turno, pelo mesmo motivo de
    `bot_orchestrator._claim_handoff`: enviar as bolhas leva segundos (o Evolution segura
    a resposta HTTP durante o "digitando") e o lock da linha ficaria preso todo esse
    tempo, com o turno concorrente parado nele.
    """
    result = await db.execute(
        text(
            "UPDATE bot_sessions SET "
            "variables = jsonb_set(COALESCE(variables, '{}'::jsonb), '{fluxo}', "
            "                      CAST(:estado AS jsonb), true), "
            "updated_at = now() "
            "WHERE conversation_id = :conversation_id "
            "AND COALESCE(variables #>> '{fluxo,turno}', '0') = :turno_lido"
        ),
        {
            "conversation_id": str(conversation_id),
            "estado": json.dumps(estado, ensure_ascii=False),
            "turno_lido": str(turno_lido),
        },
    )
    if result.rowcount == 1:
        await db.commit()
        return True
    log.info(
        "fluxo: turno perdeu a corrida na conversa %s (turno lido %s)",
        conversation_id,
        turno_lido,
    )
    return False


async def assumir_no_painel(db: AsyncSession, tenant_id: UUID) -> bool:
    """Passa a fonte de verdade do arquivo para o painel. False se não há fluxo.

    Uma via só, de propósito. Depois disto, `sincronizar_conteudo.py` recusa publicar por
    cima sem `--forcar`: o texto que alguém escreveu no painel não pode sumir porque outra
    pessoa rodou o sincronizador. Voltar para o arquivo é uma decisão explícita, com o
    mesmo peso de tê-lo assumido.
    """
    result = await db.execute(
        text(
            "UPDATE bot_flows SET gerenciado_por_arquivo = false, updated_at = now() "
            "WHERE tenant_id = :tenant_id"
        ),
        {"tenant_id": str(tenant_id)},
    )
    return result.rowcount == 1
