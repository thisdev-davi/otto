"""Ponto único de entrada síncrono → async das tasks Celery.

Toda task async passa por `run_task`. Isso concentra num lugar só a regra de que o
event loop de uma task não sobrevive a ela, e garante que o engine do worker seja
descartado antes do loop fechar.

Regra ao escrever uma task nova: UM `run_task` por task, nunca um por item de um laço.
Um `run_task` por evento significa um event loop por evento, que é exatamente o que
gerava conexão órfã. Se a task processa uma lista, o laço vai DENTRO da corrotina.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.core import ratelimit
from app.core.database import worker_engine

T = TypeVar("T")


def run_task(factory: Callable[[], Awaitable[T]]) -> T:
    """Roda a corrotina num loop próprio e não deixa conexão vazando para a próxima task.

    Recebe uma FACTORY, não uma corrotina pronta: criar a corrotina antes do
    `asyncio.run` a deixaria órfã se algo falhasse na montagem.

    O `dispose` é redundante com o NullPool do worker_engine e é de propósito: se um dia
    alguém trocar o poolclass, esta linha é o que impede o bug de voltar em silêncio.

    O cliente do rate limit segue a mesma regra, e por um motivo mais caro: guardado num
    global ele sobrevivia ao loop, toda chamada seguinte levantava, e como o rate limit
    falha aberta o teto diário de turnos do agente parava de existir sem ninguém notar.
    """

    async def _main() -> T:
        try:
            return await factory()
        finally:
            await ratelimit.fechar_cliente()
            await worker_engine.dispose()

    return asyncio.run(_main())
