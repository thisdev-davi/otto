"""Expediente do negócio — o que o nó `horario` do fluxo consulta.

**Isto não é o padrão semanal que a agenda proibiu.** A regra 4 do `CLAUDE.md` é sobre
qual DATA está à venda e por quanto, e lá o padrão semanal foi retirado porque preço de
salão é ditado por feriado e data comemorativa, que são conjuntos irregulares. Expediente
é outra pergunta: quando existe gente para responder. Sem ele o roteiro promete às 3h da
manhã que "um atendente já vai falar com você".

Formato, em `bot_settings.horario` (dia ISO, 1 = segunda … 7 = domingo; lista de
intervalos, para dar conta de almoço; lista vazia = fechado):

    {"fuso": "America/Sao_Paulo",
     "dias": {"1": [["09:00", "18:00"]], "6": [["09:00", "13:00"]], "7": []}}

**Sem configuração, está sempre aberto.** Um tenant que não preencheu não pode cair em
"fora do expediente" para sempre — o silêncio configura 24/7, não portas fechadas.
"""

import logging
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger("uvicorn.error")

FUSO_PADRAO = "America/Sao_Paulo"


def _hora(bruto: str) -> time | None:
    try:
        h, m = str(bruto).split(":")
        return time(int(h), int(m))
    except (ValueError, AttributeError):
        return None


def dentro_do_expediente(horario: dict | None, agora: datetime | None = None) -> bool:
    """True quando há gente atendendo. Configuração ausente ou inválida = aberto."""
    dias = (horario or {}).get("dias")
    if not isinstance(dias, dict) or not dias:
        return True

    nome_fuso = (horario or {}).get("fuso") or FUSO_PADRAO
    try:
        fuso = ZoneInfo(nome_fuso)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("horario: fuso desconhecido %r, usando %s", nome_fuso, FUSO_PADRAO)
        fuso = ZoneInfo(FUSO_PADRAO)

    momento = agora or datetime.now(timezone.utc)
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    local = momento.astimezone(fuso)

    intervalos = dias.get(str(local.isoweekday()))
    if not isinstance(intervalos, list) or not intervalos:
        return False

    agora_h = local.time()
    for intervalo in intervalos:
        if not isinstance(intervalo, (list, tuple)) or len(intervalo) != 2:
            continue
        abre, fecha = _hora(intervalo[0]), _hora(intervalo[1])
        if abre is None or fecha is None:
            continue
        # Intervalo que cruza a meia-noite (ex.: 18:00–02:00) é normal em salão de festas.
        if abre <= fecha:
            if abre <= agora_h <= fecha:
                return True
        elif agora_h >= abre or agora_h <= fecha:
            return True
    return False
