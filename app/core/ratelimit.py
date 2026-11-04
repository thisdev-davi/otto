"""Rate limiting em janela fixa, com o Redis que o projeto já tem.

Não havia nenhum limite em lugar nenhum. Os dois casos que doíam:

1. `POST /auth/token` era um oráculo de senha ilimitado — e, como cada tentativa queima
   um bcrypt de custo 12 no event loop, também era exaustão de CPU barata: algumas
   centenas de requisições por segundo derrubam a API sem nenhuma credencial válida.
2. `/webhooks/*` dispara o agente, e cada evento vira chamada de LLM paga. Os tetos que
   existiam (`AI_MAX_USER_TURNS`) são POR CONVERSA, e conversa nova é de graça — bastava
   variar o número do remetente para comprar outro orçamento inteiro.

**Janela fixa, e não sliding window.** É `INCR` + `EXPIRE`: duas operações, sem script
Lua, sem estrutura de dados por chave. A imprecisão conhecida da janela fixa (até 2x o
limite na virada) não importa aqui — a diferença entre 5 e 10 tentativas de senha por
minuto não muda nada, e entre 300 e 600 webhooks também não.

**Falha ABERTA.** Redis fora do ar não pode impedir o dono de entrar no painel: o risco
de negar acesso a todo mundo é maior que o de um intervalo sem limite. Fica registrado no
log para não passar despercebido.
"""

import asyncio
import logging
import weakref

import redis.asyncio as aioredis
from fastapi import HTTPException, Request, status

from app.core.config import settings

log = logging.getLogger("uvicorn.error")

# Um cliente POR EVENT LOOP, não um global.
#
# O cliente do redis.asyncio amarra suas conexões ao loop em que foi criado. Na API isso
# nunca aparece — um processo, um loop, para sempre. No worker aparece em toda task:
# `tasks/runner.py` roda um `asyncio.run` por task, e um cliente guardado num global
# sobrevive ao loop que o criou. A chamada seguinte cai em
# `RuntimeError: got Future attached to a different loop` / `Event loop is closed`.
#
# Isso NÃO era um erro barulhento e inofensivo: `consumir` falha ABERTA (é a decisão certa,
# ver o docstring do módulo), então o teto diário de turnos do agente
# (`bot_orchestrator._dentro_do_teto_diario`) devolvia `True` sempre. `AI_MAX_TURNS_DIA`
# estava desligado de fato justamente no único caminho que gasta LLM pago.
#
# WeakKeyDictionary: loop coletado leva a entrada junto, então nem o worker (um loop por
# task) acumula entradas se alguém esquecer de chamar `fechar_cliente`.
_clientes: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, aioredis.Redis]" = (
    weakref.WeakKeyDictionary()
)


def _cliente() -> aioredis.Redis:
    loop = asyncio.get_running_loop()
    cliente = _clientes.get(loop)
    if cliente is None:
        cliente = aioredis.from_url(settings.REDIS_URL)
        _clientes[loop] = cliente
    return cliente


async def fechar_cliente() -> None:
    """Fecha o cliente deste event loop, se houver. Chamado por `tasks/runner.run_task`.

    Mesmo motivo do `worker_engine.dispose()` que já vive lá: a conexão tem de morrer
    ANTES do loop, senão sobra socket órfão a cada task.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    cliente = _clientes.pop(loop, None)
    if cliente is not None:
        await cliente.aclose()


async def consumir(chave: str, limite: int, janela_seg: int) -> bool:
    """Conta mais um acesso. `False` quando estourou o limite na janela."""
    try:
        redis_client = _cliente()
        atual = await redis_client.incr(f"otto:rl:{chave}")
        if atual == 1:
            await redis_client.expire(f"otto:rl:{chave}", janela_seg)
        return atual <= limite
    except Exception:
        log.warning(
            "Rate limit indisponível (Redis); liberando %s", chave, exc_info=True
        )
        return True


def ip_do_cliente(request: Request) -> str:
    """IP de origem, respeitando `X-Forwarded-For` só quando há proxy confiável.

    Sem `TRUSTED_PROXY`, o header é ignorado: qualquer um pode mandar
    `X-Forwarded-For: 1.2.3.4` e trocar de identidade a cada tentativa, o que
    transformaria o limite por IP em decoração. Com nginx na frente, `request.client.host`
    é sempre 127.0.0.1 e o header é a única fonte real — daí a chave estar em config, e
    não num palpite do código.
    """
    if settings.TRUSTED_PROXY:
        encaminhado = request.headers.get("x-forwarded-for", "")
        if encaminhado:
            return encaminhado.split(",")[0].strip()
    return request.client.host if request.client else "desconhecido"


async def exigir(chave: str, limite: int, janela_seg: int) -> None:
    """Igual a `consumir`, mas levanta 429 em vez de devolver `False`."""
    if not await consumir(chave, limite, janela_seg):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitas tentativas. Tente novamente em instantes.",
            headers={"Retry-After": str(janela_seg)},
        )
