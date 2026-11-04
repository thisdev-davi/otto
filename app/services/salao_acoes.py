"""As duas ações que o agente pode disparar: mostrar o espaço e segurar uma data.

**Como o agente pede uma ação sem tocar em `app/ai/llm.py`.** `decision["captured"]` é,
por construção, um dicionário arbitrário que o modelo preenche a partir da persona
(`PLATAFORMA.md` §3). Então a persona pede duas chaves:

    "_acao_fotos": "sim"
    "_acao_pre_reserva": "2026-08-15"

e elas chegam aqui. O prefixo `_` não é enfeite: `bot_orchestrator._build_intake_note` já
ignora chaves começadas em `_`, então a ação não polui a nota de handoff. Mas
`contact_memory.remember` **não** filtra — por isso `extrair()` REMOVE as chaves de
`captured` antes que o orquestrador as grave no perfil permanente do contato.

**Persona não é contrato.** O modelo às vezes vai pedir foto sem o cliente ter pedido, ou
mandar uma data que não existe. Toda validação está em `executar()`, e a atomicidade da
reserva continua sendo do índice parcial em `salao_agenda` — nada aqui autoriza escrita.
"""

import logging
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.salao import ReservaStatus
from app.services import salao_agenda, salao_contexto, salao_fotos

log = logging.getLogger("uvicorn.error")

# Aliases porque o modelo erra o nome da chave de vez em quando. Todas são removidas de
# `captured`, mesmo as que não viram ação — chave de controle não é dado do cliente.
_CHAVES_FOTOS = ("_acao_fotos", "acao_fotos", "_acao_foto", "acao_foto")
_CHAVES_RESERVA = (
    "_acao_pre_reserva",
    "acao_pre_reserva",
    "_acao_prereserva",
    "acao_prereserva",
    "_acao_reserva",
    "acao_reserva",
)

_AFIRMATIVOS = {"sim", "true", "1", "yes", "ok", "s"}

# Quantas datas o AGENTE pode segurar por tenant num dia. O guard por conversa
# (`pre_reserva_id`) não é teto de verdade: `services/ingestion.py` abre conversa nova
# para cada número de remetente, e outra assim que a anterior é resolvida — quem manda a
# mensagem controla quantas conversas existem. Sem este limite, um texto que consiga fazer
# o modelo emitir `_acao_pre_reserva`, repetido a partir de vários números, congela por 48
# horas todas as datas vendáveis da agenda.
#
# O número é folgado de propósito: um salão que receba mais de 10 pré-reservas do BOT no
# mesmo dia tem um evento anormal acontecendo, e a resposta certa é um humano olhar — não
# o bot seguir segurando datas.
MAX_PRE_RESERVAS_BOT_DIA = 10


def extrair(decision: dict, hoje: date | None = None) -> dict:
    """Tira as chaves de ação de `decision["captured"]` e devolve o que fazer.

    Muta `decision` de propósito: o orquestrador usa `decision["captured"]` logo em
    seguida para alimentar `bot_sessions.variables` e o perfil do contato, e a limpeza
    precisa acontecer antes disso.
    """
    captured = decision.get("captured")
    if not isinstance(captured, dict):
        return {"fotos": False, "pre_reserva": None}

    fotos = False
    for chave in _CHAVES_FOTOS:
        valor = captured.pop(chave, None)
        if valor is not None and str(valor).strip().lower() in _AFIRMATIVOS:
            fotos = True

    pre_reserva: date | None = None
    for chave in _CHAVES_RESERVA:
        valor = captured.pop(chave, None)
        if valor is not None and pre_reserva is None:
            pre_reserva = _parse_data(str(valor), hoje)

    return {"fotos": fotos, "pre_reserva": pre_reserva}


def _parse_data(bruto: str, hoje: date | None = None) -> date | None:
    """A persona pede AAAA-MM-DD; o modelo às vezes manda 15/08/2026 ou "15 de agosto"."""
    texto = bruto.strip()
    if not texto:
        return None
    try:
        return datetime.strptime(texto[:10], "%Y-%m-%d").date()
    except ValueError:
        pass
    datas = salao_contexto.extrair_datas(texto, hoje)
    return datas[0] if datas else None


async def executar(
    db: AsyncSession,
    tenant_id: UUID,
    conversation_id: UUID,
    conv,
    session: dict,
    acoes: dict,
) -> dict:
    """Executa as ações pedidas pelo agente. Best-effort, como o resto do orquestrador.

    Uma foto que não subiu ou uma reserva que não deu não podem derrubar o turno — a
    resposta de texto já foi para o cliente e o erro tem de morrer no log, não na conversa.

    Devolve o que de fato aconteceu (`{"fotos": bool|None, "pre_reserva": bool|None}`).
    O caminho do LLM ignora o retorno: lá a mensagem já saiu antes da ação, e não há o que
    decidir com o resultado. Quem o usa é o roteiro, que precisa escolher entre "segurei
    sua data" e "essa data acabou de ser fechada" — ver `app/services/salao_fluxo.py`.
    """
    feito: dict = {"fotos": None, "pre_reserva": None}
    try:
        if acoes.get("fotos"):
            feito["fotos"] = await enviar_fotos(
                db, tenant_id, conversation_id, conv, session
            )
        if acoes.get("pre_reserva"):
            feito["pre_reserva"] = await pre_reservar(
                db, tenant_id, conversation_id, conv, session, acoes["pre_reserva"]
            )
    except Exception:
        log.exception(
            "salao_acoes: falha ao executar ações na conversa %s", conversation_id
        )
    return feito


async def enviar_fotos(
    db: AsyncSession, tenant_id: UUID, conversation_id: UUID, conv, session: dict
) -> bool:
    """Manda a galeria — no máximo uma vez por conversa. `False` = nada foi enviado.

    O teto por conversa segue o mesmo raciocínio do `media_notice_sent` já existente:
    quem pergunta de novo pelas fotos não pode receber as mesmas quatro imagens outra vez.
    A partir da segunda vez quem manda é o atendente, que sabe qual foto falta.
    """
    if session["variables"].get("fotos_enviadas"):
        return False
    if not await _sem_dono(db, tenant_id, conversation_id):
        return (
            False  # humano assumiu durante a chamada ao LLM — o bot não fala por cima
        )

    enviadas = await salao_fotos.enviar_galeria(
        db, tenant_id, conversation_id, conv, limite=salao_fotos.MAX_ENVIO
    )
    if enviadas:
        session["variables"]["fotos_enviadas"] = True
    return bool(enviadas)


async def pre_reservar(
    db: AsyncSession,
    tenant_id: UUID,
    conversation_id: UUID,
    conv,
    session: dict,
    data: date,
) -> bool:
    """Segura a data por `PRE_RESERVA_HORAS` e avisa a equipe. `False` = não segurou.

    Sempre encaminha.

    Encaminha **nos dois desfechos**. Deu certo: alguém precisa confirmar antes de expirar.
    Não deu: quem explica a um cliente que a data caiu é gente, não o bot.
    """
    if session["variables"].get("pre_reserva_id"):
        return False  # esta conversa já segurou uma data; a segunda é de humano

    if await _bot_estourou_o_dia(db, tenant_id):
        await _nota(
            db,
            tenant_id,
            conversation_id,
            f"O agente tentou segurar {data.strftime('%d/%m/%Y')} e não conseguiu: o "
            f"limite de {MAX_PRE_RESERVAS_BOT_DIA} pré-reservas automáticas por dia foi "
            "atingido. Se o volume for legítimo, confirme ou cancele as pendentes em "
            "Agenda; se não for, alguém pode estar forçando reservas pelo WhatsApp.",
        )
        session["handoff"] = True
        return False

    reserva, motivo = await salao_agenda.criar_reserva(
        db,
        tenant_id,
        data,
        status=ReservaStatus.PRE_RESERVADA,
        contact_id=conv.contact_id,
        conversation_id=conversation_id,
        criada_por="bot",
        nome_cliente=conv.contact_name,
        # `contact_phone`, NUNCA `contact_external_id`. O endereço do canal pode ser um
        # identificador opaco (`@lid`), e gravá-lo aqui produziu uma reserva confirmada
        # com "telefone" que ninguém consegue discar. Sem telefone conhecido o campo fica
        # NULL — honesto — e `salao_resumo` mostra o do contato assim que ele aparecer.
        telefone=conv.contact_phone,
    )

    if reserva is None:
        await _nota(db, tenant_id, conversation_id, _nota_falha(data, motivo))
        session["handoff"] = True
        return False

    session["variables"]["pre_reserva_id"] = reserva["id"]
    session["handoff"] = True
    situacao = (await salao_agenda.consultar(db, tenant_id, [data])).get(data) or {}
    preco = salao_agenda.formatar_preco(situacao.get("preco_centavos"))
    validade = (
        reserva["expira_em"].strftime("%d/%m/%Y às %Hh")
        if reserva["expira_em"]
        else "sem prazo"
    )
    await _nota(
        db,
        tenant_id,
        conversation_id,
        "Pré-reserva criada pelo agente\n"
        f"- data: {data.strftime('%d/%m/%Y')}\n"
        f"- valor da data: {preco or 'não cadastrado'}\n"
        f"- expira em: {validade}\n"
        "Confirme ou cancele em Agenda antes do prazo — passado ele, a data volta a ser "
        "vendida automaticamente.",
    )
    return True


def _nota_falha(data: date, motivo: str) -> str:
    explicacao = {
        salao_agenda.MOTIVO_OCUPADA: "a data já estava reservada",
        salao_agenda.MOTIVO_BLOQUEADA: "a data está bloqueada na agenda",
        salao_agenda.MOTIVO_SEM_CADASTRO: "a data não está cadastrada na agenda",
        salao_agenda.MOTIVO_SEM_PRECO: "a data está sem valor cadastrado",
        salao_agenda.MOTIVO_PASSADO: "a data já passou",
    }.get(motivo, motivo)
    return (
        f"O agente tentou segurar {data.strftime('%d/%m/%Y')} e não conseguiu: "
        f"{explicacao}. O cliente pode estar esperando essa data — confira na Agenda."
    )


async def _nota(
    db: AsyncSession, tenant_id: UUID, conversation_id: UUID, texto: str
) -> None:
    # Import tardio: `bot_orchestrator` importa este módulo, e no topo seria ciclo. Mesmo
    # padrão (e mesmo motivo) de `bot_orchestrator._schedule_retry`.
    from app.services.bot_orchestrator import _insert_internal_note

    await _insert_internal_note(db, tenant_id, conversation_id, texto)


async def _bot_estourou_o_dia(db: AsyncSession, tenant_id: UUID) -> bool:
    """Quantas datas o bot já segurou hoje neste tenant.

    Conta no banco, e não no Redis, porque o que interessa é o efeito real na agenda —
    reserva criada é linha em `salao_reservas`. Canceladas contam: o estrago (segurar a
    data, ainda que por minutos) já aconteceu, e não contá-las deixaria o teto ser zerado
    por quem cancelasse em seguida.
    """
    row = await db.execute(
        text(
            "SELECT count(*) FROM salao_reservas "
            "WHERE tenant_id = :tenant_id AND criada_por = 'bot' "
            "AND created_at >= date_trunc('day', now())"
        ),
        {"tenant_id": str(tenant_id)},
    )
    return (row.scalar_one() or 0) >= MAX_PRE_RESERVAS_BOT_DIA


async def _sem_dono(db: AsyncSession, tenant_id: UUID, conversation_id: UUID) -> bool:
    from app.services.bot_orchestrator import _still_unassigned

    return await _still_unassigned(db, tenant_id, conversation_id)
