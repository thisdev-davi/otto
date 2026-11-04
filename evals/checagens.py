"""Checagens determinísticas sobre a decisão do agente (`llm.agent_turn`).

O modelo é probabilístico; estas funções não. Cada uma recebe a decisão e o contexto do
caso e devolve `(passou, detalhe)`. Elas medem só o que dá para afirmar sem outro LLM:
valor dito, ação pedida e status. Tom e qualidade da conversa ficam fora de propósito.
"""

import re
from datetime import date

from app.services import salao_agenda, salao_acoes

_PRECO = re.compile(r"R\$\s?[\d.]+(?:,\d{2})?")
# "está confirmada", "foi reservada", "reserva confirmada", "confirmei"... sem "não" antes.
_CONFIRMA = re.compile(
    r"(?<!não )(?<!nao )\b(?:est[aá]|foi|fica|ficou)\s+(?:confirmad|reservad)"
    r"|\breserva\s+confirmada\b|\bconfirmei\b",
    re.IGNORECASE,
)


def _normaliza_preco(bruto: str) -> str:
    valor = bruto.replace("R$", "").strip()
    return "R$ " + (valor if "," in valor else valor + ",00")


def precos_validos(agenda: dict[str, dict]) -> set[str]:
    """Os únicos valores que o agente pode dizer: os das datas LIVRES da agenda."""
    return {
        salao_agenda.formatar_preco(d["preco_centavos"])
        for d in agenda.values()
        if d["motivo"] == salao_agenda.MOTIVO_OK
    }


def precos_da_agenda(decisao: dict, ctx: dict) -> tuple[bool, str]:
    ditos = {_normaliza_preco(p) for p in _PRECO.findall(decisao.get("reply") or "")}
    fora = ditos - precos_validos(ctx["agenda"])
    return (not fora, f"valor fora da agenda: {sorted(fora)}" if fora else "")


def cita_preco(decisao: dict, ctx: dict, esperado: str) -> tuple[bool, str]:
    ok = esperado in (decisao.get("reply") or "")
    return (ok, "" if ok else f"não citou {esperado}")


def sem_valor_para(decisao: dict, ctx: dict, dia_mes: str) -> tuple[bool, str]:
    """Nenhuma linha que fala de `dia_mes` traz valor."""
    for linha in (decisao.get("reply") or "").replace("|||", "\n").splitlines():
        if dia_mes in linha and _PRECO.search(linha):
            return (False, f"deu valor para {dia_mes}: {linha.strip()!r}")
    return (True, "")


def nao_confirma_reserva(decisao: dict, ctx: dict) -> tuple[bool, str]:
    achado = _CONFIRMA.search(decisao.get("reply") or "")
    return (
        achado is None,
        f"afirmou confirmação: {achado.group(0)!r}" if achado else "",
    )


def encaminha(decisao: dict, ctx: dict) -> tuple[bool, str]:
    ok = decisao.get("status") in ("ready_to_handoff", "needs_human")
    return (ok, "" if ok else f"status {decisao.get('status')!r}")


def _acoes(decisao: dict, ctx: dict) -> dict:
    # Cópia: `extrair` muta o `captured`, e outra checagem pode ler a mesma decisão.
    copia = {**decisao, "captured": dict(decisao.get("captured") or {})}
    return salao_acoes.extrair(copia, date.fromisoformat(ctx["hoje"]))


def sem_pre_reserva(decisao: dict, ctx: dict) -> tuple[bool, str]:
    data = _acoes(decisao, ctx)["pre_reserva"]
    return (data is None, f"pediu pré-reserva de {data}" if data else "")


def pre_reserva_em(decisao: dict, ctx: dict, esperada: str) -> tuple[bool, str]:
    data = _acoes(decisao, ctx)["pre_reserva"]
    ok = data == date.fromisoformat(esperada)
    return (ok, "" if ok else f"pré-reserva {data}, esperada {esperada}")


CHECAGENS = {
    f.__name__: f
    for f in (
        precos_da_agenda,
        cita_preco,
        sem_valor_para,
        nao_confirma_reserva,
        encaminha,
        sem_pre_reserva,
        pre_reserva_em,
    )
}


def avaliar(decisao: dict | None, ctx: dict, checagens: list[list]) -> list[str]:
    """Falhas do caso; lista vazia = passou. Decisão `None` (modelo falhou) reprova."""
    if decisao is None:
        return ["o modelo não devolveu decisão"]
    falhas = []
    for nome, *args in checagens:
        ok, detalhe = CHECAGENS[nome](decisao, ctx, *args)
        if not ok:
            falhas.append(f"{nome}: {detalhe}")
    return falhas
