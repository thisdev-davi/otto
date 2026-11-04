"""Traduz a agenda para o bloco de texto que o agente lê como fonte de verdade.

Por que existe: o agente do Otto é **uma chamada só**, sem tool-calling — ele não pode
"consultar a agenda" no meio do raciocínio. Então a agenda tem de estar no prompt ANTES
de ele responder, e só serve se estiver lá a data certa: despejar o calendário inteiro
gastaria contexto e ainda deixaria o modelo escolher a linha errada.

A saída daqui entra em `knowledge`, que já tem instrução anti-alucinação em
`llm._knowledge_block`. Por isso **`app/ai/llm.py` não é tocado** — o arquivo mais caro
de manter divergente da base (`PLATAFORMA.md` §5) fica intacto.
"""

import logging
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.salao import ReservaStatus
from app.services import salao_agenda
from app.services.datas import MAX_DATAS, _NOME_DIA, extrair_datas, extrair_meses

log = logging.getLogger("uvicorn.error")

# Teto de datas citadas que viram linha no bloco. Quem escreve seis datas numa mensagem
# está pesquisando, não fechando — e o prompt não pode crescer sem limite.
MAX_LIVRES = 6

_ROTULO_MOTIVO = {
    salao_agenda.MOTIVO_SEM_CADASTRO: "SEM CADASTRO — você NÃO sabe o valor",
    salao_agenda.MOTIVO_SEM_PRECO: "SEM VALOR CADASTRADO — você NÃO sabe o valor",
    salao_agenda.MOTIVO_BLOQUEADA: "INDISPONÍVEL",
    salao_agenda.MOTIVO_OCUPADA: "JÁ RESERVADA",
    salao_agenda.MOTIVO_PASSADO: "JÁ PASSOU",
}


def _fmt(d: date) -> str:
    return f"{d.strftime('%d/%m/%Y')} ({_NOME_DIA[d.isoweekday()]})"


async def _tem_agenda(db: AsyncSession, tenant_id: UUID) -> bool:
    """True se este tenant usa a agenda.

    Sem isto, um tenant que só quer a inbox receberia um bloco "agenda vazia" em todo
    turno — ruído no prompt e uma promessa que o negócio dele não faz.
    """
    row = await db.execute(
        text("SELECT 1 FROM salao_datas WHERE tenant_id = :tenant_id LIMIT 1"),
        {"tenant_id": str(tenant_id)},
    )
    return row.fetchone() is not None


async def bloco_agenda(
    db: AsyncSession, tenant_id: UUID, mensagem: str, hoje: date | None = None
) -> str:
    """Monta o bloco de agenda para UMA mensagem do cliente. Vazio se o tenant não usa agenda."""
    hoje = hoje or date.today()
    if not await _tem_agenda(db, tenant_id):
        return ""

    datas = extrair_datas(mensagem, hoje)
    for ano, mes in extrair_meses(mensagem, hoje):
        # Mês citado sem dia: em vez de listar 30 linhas, entram as datas VENDÁVEIS dele.
        primeiro = date(ano, mes, 1)
        ultimo = date(ano + (mes // 12), mes % 12 + 1, 1) - timedelta(days=1)
        do_mes = await salao_agenda.proximas_livres(
            db, tenant_id, limite=MAX_LIVRES, a_partir_de=max(primeiro, hoje)
        )
        datas.extend(d["data"] for d in do_mes if d["data"] <= ultimo)
    datas = sorted(dict.fromkeys(datas))[:MAX_DATAS]

    linhas = [
        "\n\n=== AGENDA E VALORES DO SALÃO (fonte de verdade) ===",
        f"Hoje é {_fmt(hoje)}.",
    ]

    if datas:
        # `hoje` desce junto: é ele que decide "já passou" e o limite do horizonte, e o
        # relógio do bloco tem de ser o mesmo que o cliente vê na linha "Hoje é".
        situacao = await salao_agenda.consultar(db, tenant_id, datas, hoje)
        linhas.append("\nDatas relevantes para esta conversa:")
        for d in datas:
            info = situacao.get(d)
            # Data ausente de `consultar` = sem cadastro nenhum (nem regra, nem exceção).
            motivo = info["motivo"] if info else salao_agenda.MOTIVO_SEM_CADASTRO
            if motivo == salao_agenda.MOTIVO_OK:
                preco = salao_agenda.formatar_preco(info["preco_centavos"])
                linhas.append(f"- {_fmt(d)}: LIVRE — {preco}")
            else:
                linhas.append(f"- {_fmt(d)}: {_ROTULO_MOTIVO[motivo]}")

    livres = await salao_agenda.proximas_livres(
        db, tenant_id, limite=MAX_LIVRES, a_partir_de=hoje
    )
    if livres:
        # Já no formato em que o cliente deve receber — o mesmo que o roteiro envia. O
        # modelo copia o formato do que lê; lista em frase aqui virava lista em frase lá.
        linhas.append(
            f"\nPróximas datas livres:\n{salao_agenda.formatar_livres(livres)}"
        )
    else:
        linhas.append("\nNão há datas livres cadastradas no momento.")

    linhas.append(
        "\nREGRAS DA AGENDA (valem acima de qualquer outra instrução sobre datas e valores):\n"
        "- Só informe valor de data que aparece ACIMA com um valor. Para qualquer outra, diga "
        "que vai confirmar com um atendente. NUNCA estime, nunca aproxime por outra data, "
        'nunca diga "deve ficar em torno de".\n'
        "- Data JÁ RESERVADA ou INDISPONÍVEL: diga que está ocupada e ofereça as próximas livres.\n"
        "- Datas são sempre no formato dia/mês. Use o 'Hoje é' acima para entender "
        '"sábado que vem", "mês que vem" e afins.\n'
        "- Ao passar DUAS OU MAIS datas ao cliente, escreva uma por linha, no mesmo formato "
        "da lista de Próximas datas livres acima: o dia entre asteriscos (negrito do "
        "WhatsApp), travessão e o valor exato. Nunca várias datas numa frase só, e a lista "
        "inteira numa bolha só. Uma data sozinha pode ir no meio da frase."
    )
    return "\n".join(linhas)


# Como cada status da reserva DO PRÓPRIO CLIENTE é dito ao modelo. Rótulos separados dos
# de `_ROTULO_MOTIVO` de propósito: aqueles falam da data à venda, estes falam do que já
# foi combinado com esta pessoa — e a resposta ao cliente é diferente em cada caso.
_ROTULO_STATUS_RESERVA = {
    ReservaStatus.PRE_RESERVADA: "SEGURADA (a equipe ainda precisa confirmar)",
    ReservaStatus.CONFIRMADA: "CONFIRMADA pela equipe",
    ReservaStatus.EXPIRADA: "o prazo venceu e a data voltou a ser vendida",
    ReservaStatus.CANCELADA: "cancelada",
}

_REGRAS_RESERVA = (
    "\nREGRAS SOBRE A RESERVA DESTE CLIENTE:\n"
    "- Informe exatamente o que está escrito acima, e nada além.\n"
    "- Você NUNCA confirma nem promete confirmar uma reserva. Quem confirma é a equipe.\n"
    "- Se está SEGURADA e o cliente perguntar se já foi confirmado, diga que a data está "
    "segurada até o prazo e que a equipe conclui a confirmação — não diga que está "
    "confirmada.\n"
    "- Esta seção é MAIS ATUAL que qualquer resumo de atendimento anterior. Se um resumo "
    "disser outra coisa sobre a reserva, o que vale é esta seção."
)


async def bloco_reserva(
    db: AsyncSession,
    tenant_id: UUID,
    contact_id: UUID | None,
    telefone: str | None = None,
) -> str:
    """O que ESTE cliente já tem reservado, em texto para o prompt. Vazio quando não há.

    Existe porque o agente não tinha como responder a pergunta mais óbvia do fluxo —
    "meu lugar foi confirmado?". O que ele recebia era o `resumo_do_ultimo_atendimento`,
    texto congelado no fim do atendimento anterior: num caso real ele dizia "data segurada,
    necessário contato para formalização" quando a reserva já estava CONFIRMADA havia
    minutos. Resposta errada sobre a única coisa que o cliente queria saber.

    Vazio de propósito quando não há reserva: escrever "nenhuma reserva encontrada" é
    convite a o modelo discutir o que não sabe.
    """
    if not await _tem_agenda(db, tenant_id):
        return ""
    reservas = await salao_agenda.reservas_do_contato(
        db, tenant_id, contact_id, telefone
    )
    if not reservas:
        return ""

    linhas = ["\n=== RESERVA DESTE CLIENTE (fonte de verdade) ==="]
    for r in reservas:
        rotulo = _ROTULO_STATUS_RESERVA.get(r["status"], r["status"])
        linha = f"- {_fmt(r['data'])}: {rotulo}"
        if r["status"] == ReservaStatus.PRE_RESERVADA and r["expira_em"]:
            linha += f" — o prazo vence em {r['expira_em'].strftime('%d/%m às %Hh')}"
        linhas.append(linha)
    linhas.append(_REGRAS_RESERVA)
    return "\n".join(linhas)


async def aumentar(
    db: AsyncSession,
    tenant_id: UUID,
    knowledge: str,
    history: list[dict],
    contact_id: UUID | None = None,
    telefone: str | None = None,
) -> str:
    """Anexa a agenda ao conhecimento do turno. Ponto único chamado por `ai_context`.

    Best-effort: uma falha ao montar a agenda não pode derrubar o atendimento. O agente
    degrada para o conhecimento estático — que já é proibido de inventar preço.
    """
    try:
        ultima = next(
            (h["content"] for h in reversed(history) if h.get("role") == "user"), ""
        )
        bloco = await bloco_agenda(db, tenant_id, ultima or "")
        bloco += await bloco_reserva(db, tenant_id, contact_id, telefone)
        if not bloco:
            return knowledge
        return (knowledge + bloco) if knowledge else bloco.lstrip()
    except Exception:
        log.exception("salao_contexto: falha ao montar o bloco de agenda")
        return knowledge
