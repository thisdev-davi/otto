"""O contrato de um nó e o registro dos tipos que o derivado acrescenta.

O motor conhece **sete** tipos genéricos (`schema.ESPEC`). Os do nicho — `agenda` e
`acao`, no salão — entram por aqui, uma chamada de `registrar()` cada, sem patch no motor.
Mesmo padrão e mesmo motivo de `channels/factory.py::_registry`: o que é da plataforma fica
na plataforma, o que é do nicho fica num arquivo que a base não tem.

Quem registra é `app/services/salao_fluxo.py`, e quem o importa são os três lugares que
precisam conhecer os nós do salão: o orquestrador, o sincronizador de conteúdo e os testes.
Esquecer o import não é falha silenciosa — o validador reprova o grafo com "tipo
desconhecido: agenda".
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Awaitable, Callable, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class Contexto:
    """O que um executor de nó recebe. Escreve em `captured` e lê o resto."""

    db: AsyncSession
    tenant_id: UUID
    conversation_id: UUID
    conv: object  # linha de bot_orchestrator._load_conversation
    session: dict  # a sessão do bot, para os guards de uma-vez-por-conversa
    captured: dict  # o que já foi coletado nesta conversa; o executor pode acrescentar
    variaveis: (
        dict  # tudo o que as mensagens podem interpolar com {{...}}, só de leitura
    )
    # Onde o executor ESCREVE o que produziu (o nó `agenda` põe data, preco e as próximas
    # livres). Separado de `captured` porque `captured` vira `contacts.profile`, memória
    # permanente do cliente — um preço de outubro guardado ali seria mentira em novembro.
    # Sobrevive entre turnos em `bot_sessions.variables.fluxo.vars`: a mensagem que diz
    # "segurei o dia {{data}}" é enviada TRÊS turnos depois de a agenda ter resolvido.
    derivadas: dict
    bot: object  # BotConfig do tenant
    hoje: date | None = None
    notas: list[str] = field(default_factory=list)
    # True quando o turno roda no simulador do painel. UM único efeito, e ele é
    # obrigatório: nenhum executor pode ESCREVER. Ler a agenda continua valendo — ver o
    # preço real da data é o motivo de o simulador existir. Executor que ignora isto
    # segura data de verdade quando alguém está só testando o roteiro.
    simulacao: bool = False  # notas internas a gravar no fim


class Executor(Protocol):
    """Executa um nó e devolve o NOME da saída a seguir (ex.: 'livre', 'ok')."""

    def __call__(self, ctx: Contexto, no: dict) -> Awaitable[str]: ...


@dataclass(frozen=True)
class Espec:
    """O que o validador precisa saber sobre um tipo de nó."""

    saidas: tuple[str, ...]  # nomes das saídas nomeadas, em `no["saidas"]`
    campos: tuple[str, ...] = ()  # chaves obrigatórias no nó
    espera_resposta: bool = False
    terminal: bool = False
    tem_texto: bool = False


_TIPOS: dict[str, Espec] = {}
_EXECUTORES: dict[str, Callable[..., Awaitable[str]]] = {}


def registrar(tipo: str, espec: Espec, executor: Executor) -> None:
    """Idempotente de propósito: o módulo do nicho é importado de vários pontos."""
    _TIPOS[tipo] = espec
    _EXECUTORES[tipo] = executor


def espec_de(tipo: str) -> Espec | None:
    return _TIPOS.get(tipo)


def executor_de(tipo: str) -> Executor | None:
    return _EXECUTORES.get(tipo)


def registrados() -> tuple[str, ...]:
    return tuple(sorted(_TIPOS))
