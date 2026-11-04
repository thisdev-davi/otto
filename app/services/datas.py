"""Datas e horas em português, do jeito que o cliente escreve no WhatsApp.

Módulo NEUTRO de propósito. O parser nasceu dentro do contexto de agenda do derivado de
salão, e o próprio motor de fluxo (`app/flow/engine.py`) já o importava de lá com o
comentário "não tem nada de salão" — um import tardio só para escapar do ciclo com o
módulo do nicho. Numa migração que renomeia o arquivo do nicho de qualquer jeito, mover
para cá custou a mesma linha e tirou o motor genérico da dependência de um derivado.

Duas funções, uma regra comum: **conservador**. Número solto não vira data nem hora,
porque cada falso positivo aqui vira uma linha errada dentro da fonte de verdade do agente
ou um horário que o cliente não pediu.
"""

import re
import unicodedata
from datetime import date, time, timedelta

# Teto de datas citadas que viram linha no bloco. Quem escreve seis datas numa mensagem
# está pesquisando, não fechando — e o prompt não pode crescer sem limite.
MAX_DATAS = 6

_MESES = {
    "janeiro": 1,
    "fevereiro": 2,
    "marco": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}

# ISO: 1=segunda … 7=domingo
_DIAS_SEMANA = {
    "segunda": 1,
    "terca": 2,
    "quarta": 3,
    "quinta": 4,
    "sexta": 5,
    "sabado": 6,
    "domingo": 7,
}

_NOME_DIA = {
    1: "segunda-feira",
    2: "terça-feira",
    3: "quarta-feira",
    4: "quinta-feira",
    5: "sexta-feira",
    6: "sábado",
    7: "domingo",
}


def _sem_acento(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _no_futuro(dia: int, mes: int, ano: int | None, hoje: date) -> date | None:
    """Resolve uma data parcial para a próxima ocorrência que ainda não passou.

    Sem isto, "15/08" perguntado em dezembro devolveria uma data no passado — e o cliente
    que pergunta por uma data está sempre perguntando pela próxima vez que ela acontece.
    """
    for candidato in (ano,) if ano else (hoje.year, hoje.year + 1):
        try:
            resolvida = date(candidato, mes, dia)
        except ValueError:
            continue  # 31/02 e afins; 29/02 pode existir no ano seguinte
        if ano or resolvida >= hoje:
            return resolvida
    return None


def extrair_datas(texto: str, hoje: date | None = None) -> list[date]:
    """Datas citadas numa mensagem de WhatsApp, do jeito que o cliente escreve.

    Cobre `15/08`, `15/08/2026`, `15 de agosto`, `dia 15`, `sábado que vem`, `amanhã` e
    `fim de semana`. Ano ausente resolve para o futuro (ver `_no_futuro`).

    Deliberadamente conservador: número solto NÃO vira data. "150 pessoas" e "R$ 4200"
    apareceriam como dia 150 e dia 4200 num parser guloso, e cada falso positivo aqui
    vira uma linha errada dentro da fonte de verdade do agente.
    """
    hoje = hoje or date.today()
    plano = _sem_acento(texto)
    achadas: list[date] = []

    def adicionar(d: date | None) -> None:
        if d is not None and d not in achadas:
            achadas.append(d)

    # 15/08, 15/08/2026, 15-8. O ponto NÃO é separador aceito de propósito: com ele,
    # "R$ 4.200" e "1.500 reais" entrariam como datas dentro da fonte de verdade.
    for m in re.finditer(r"\b(\d{1,2})[/\-](\d{1,2})(?:[/\-](\d{2,4}))?\b", plano):
        dia, mes = int(m.group(1)), int(m.group(2))
        ano = m.group(3)
        if not (1 <= dia <= 31 and 1 <= mes <= 12):
            continue
        if ano:
            ano_int = int(ano)
            ano_int += 2000 if ano_int < 100 else 0
        else:
            ano_int = None
        adicionar(_no_futuro(dia, mes, ano_int, hoje))

    # 15 de agosto [de 2026]
    for m in re.finditer(
        r"\b(\d{1,2})\s+de\s+([a-z]+)(?:\s+de\s+(\d{4}))?\b",
        plano,
    ):
        mes = _MESES.get(m.group(2))
        if mes:
            adicionar(
                _no_futuro(
                    int(m.group(1)), mes, int(m.group(3)) if m.group(3) else None, hoje
                )
            )

    # "dia 15" — sem mês: o dia 15 que ainda vem
    for m in re.finditer(r"\bdia\s+(\d{1,2})\b(?!\s*[/\-.]\s*\d)", plano):
        dia = int(m.group(1))
        if not 1 <= dia <= 31:
            continue
        alvo = _no_futuro(dia, hoje.month, hoje.year, hoje)
        if alvo is None or alvo < hoje:
            mes_seguinte = hoje.month % 12 + 1
            ano_seguinte = hoje.year + (1 if mes_seguinte == 1 else 0)
            alvo = _no_futuro(dia, mes_seguinte, ano_seguinte, hoje)
        adicionar(alvo)

    if re.search(r"\bhoje\b", plano):
        adicionar(hoje)
    if re.search(r"\bamanha\b", plano):
        adicionar(hoje + timedelta(days=1))

    # "sábado", "próximo sábado", "sábado que vem" → o próximo dia dessa semana
    for nome, iso in _DIAS_SEMANA.items():
        if re.search(rf"\b{nome}\b", plano):
            adicionar(_proximo_dia_da_semana(hoje, iso))

    # "fim de semana" / "fds" → o sábado e o domingo que vêm
    if re.search(r"\bfim de semana\b|\bfds\b|\bfinal de semana\b", plano):
        sabado = _proximo_dia_da_semana(hoje, 6)
        adicionar(sabado)
        adicionar(sabado + timedelta(days=1))

    return sorted(achadas)[:MAX_DATAS]


def _proximo_dia_da_semana(hoje: date, iso: int) -> date:
    """Próxima ocorrência do dia da semana. Hoje conta como 'próxima'."""
    delta = (iso - hoje.isoweekday()) % 7
    return hoje + timedelta(days=delta)


def extrair_meses(texto: str, hoje: date | None = None) -> list[tuple[int, int]]:
    """Meses citados sem dia ("tem alguma data em agosto?") → [(ano, mês)]."""
    hoje = hoje or date.today()
    plano = _sem_acento(texto)
    meses: list[tuple[int, int]] = []
    for nome, numero in _MESES.items():
        # Ignora o mês que já veio grudado num dia ("15 de agosto") — aquele já virou data.
        if re.search(rf"\b{nome}\b", plano) and not re.search(
            rf"\d{{1,2}}\s+de\s+{nome}\b", plano
        ):
            ano = hoje.year if numero >= hoje.month else hoje.year + 1
            if (ano, numero) not in meses:
                meses.append((ano, numero))
    return meses


def formatar_data(d: date) -> str:
    return f"{d.strftime('%d/%m/%Y')} ({_NOME_DIA[d.isoweekday()]})"


# "às 10", "10h", "10h30", "10:30", "10 horas", "meio-dia". O número sozinho ("10") só
# vale quando quem chama diz que a pergunta ERA sobre hora — ver `extrair_hora`.
_HORA_COM_MARCA = re.compile(r"\b(?:as\s+)?(\d{1,2})\s*(?:[:h]\s*(\d{2})?|\s+horas?\b)")
_HORA_APOS_AS = re.compile(r"\bas\s+(\d{1,2})(?:[:h](\d{2}))?\b")


def extrair_hora(texto: str, *, aceitar_numero_solto: bool = False) -> time | None:
    """A hora citada numa mensagem, ou None.

    `aceitar_numero_solto=True` só quando a mensagem é RESPOSTA a "qual horário?" — aí
    "17" quer dizer 17h. Fora desse contexto, "tenho 17 anos" viraria um agendamento.
    Horas de 1 a 7 sem marca de manhã são lidas como tarde (13h–19h): ninguém marca corte
    às 3 da madrugada, e é assim que o cliente fala ("pode ser às 3?").
    """
    plano = _sem_acento(texto or "")
    if re.search(r"\bmeio[\s-]?dia\b", plano):
        return time(12, 0)

    achado = _HORA_COM_MARCA.search(plano) or _HORA_APOS_AS.search(plano)
    if achado is None and aceitar_numero_solto:
        achado = re.fullmatch(r"\s*(\d{1,2})(?:[:h](\d{2}))?\s*", plano)
    if achado is None:
        return None

    hora = int(achado.group(1))
    minuto = int(achado.group(2)) if achado.group(2) else 0
    if not (0 <= hora <= 23 and 0 <= minuto <= 59):
        return None
    if 1 <= hora <= 7 and not re.search(r"\bmanha\b|\bmadrugada\b", plano):
        hora += 12
    return time(hora, minuto)
