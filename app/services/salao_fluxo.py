"""Os dois nós do salão no roteiro: `agenda` e `acao`.

O motor (`app/flow/`) conhece sete tipos genéricos e mais nenhum. Estes dois entram por
`registro.registrar()` — uma chamada cada, sem patch no motor. Mesmo padrão e mesmo motivo
de `channels/factory.py::_registry`: o que é do nicho mora num arquivo que a base não tem
(`docs/produto/PLATAFORMA.md` §1).

**Nada aqui reimplementa regra de agenda.** O nó `agenda` chama `salao_agenda.resolver`, a
única fonte de verdade de disponibilidade (`CLAUDE.md` regra 4), e só ramifica no motivo
que ela devolve. O nó `acao` chama `salao_acoes`, o mesmo código que executa as ações do
agente de IA. Se a regra mudar, muda num lugar e os dois cérebros mudam juntos.
"""

import logging
from datetime import date, datetime

from app.flow.registro import Contexto, Espec, registrar
from app.models.salao import ReservaStatus  # noqa: F401  (documenta o vocabulário)
from app.services import salao_acoes, salao_agenda, salao_fotos

log = logging.getLogger("uvicorn.error")

# As sete saídas do nó `agenda` são os `MOTIVO_*` do service, com um apelido e um
# acréscimo: `MOTIVO_OK` vira `livre`, porque "ok" ao lado do `ok` do nó de ação seria
# ambíguo em quem lê o roteiro. Todas são OBRIGATÓRIAS no grafo — é isso que impede um
# caso de cair num ramo que fala valor sem ter valor.
SAIDA_LIVRE = "livre"

# `agenda_vazia` não é um motivo do service, e é de propósito: para `resolver`, uma data
# sem cadastro é sempre a mesma coisa. Quem conhece a diferença é o roteiro — dizer "essa
# data não está aberta, veja as próximas" só é honesto quando EXISTE agenda; num tenant
# que ainda não cadastrou nada (o estado de todo cliente novo na primeira semana), a
# mesma frase é o bot respondendo com confiança sobre um calendário que ninguém preencheu,
# e sem chamar ninguém. Aí a resposta certa é gente.
SAIDA_AGENDA_VAZIA = "agenda_vazia"
SAIDAS_AGENDA = (
    SAIDA_LIVRE,
    salao_agenda.MOTIVO_OCUPADA,
    salao_agenda.MOTIVO_BLOQUEADA,
    salao_agenda.MOTIVO_SEM_CADASTRO,
    salao_agenda.MOTIVO_SEM_PRECO,
    salao_agenda.MOTIVO_PASSADO,
    SAIDA_AGENDA_VAZIA,
)

SAIDAS_ACAO = ("ok", "falhou")
ACOES = ("fotos", "pre_reserva")

# Fragmento usado quando a agenda não tem NENHUMA data aberta à frente. Precisa existir:
# `{{proximas_livres}}` vazio faria o motor tratar a mensagem como quebrada e encaminhar,
# e agenda sem data futura é situação normal de um salão que vendeu bem. O texto é
# sobrescrevível pelo nó (`sem_livres`), porque copy pertence ao roteiro, não ao código.
SEM_LIVRES_PADRAO = "nenhuma no momento"


def _formatar_livres(livres: list[dict], vazio: str) -> str:
    """A lista de `salao_agenda.formatar_livres`, a mesma que o agente de IA recebe.

    A quebra de linha fica no código e não no roteiro porque quem sabe quantos itens
    existem é o código.
    """
    return salao_agenda.formatar_livres(livres) if livres else vazio


async def _executar_agenda(ctx: Contexto, no: dict) -> str:
    """Resolve a data coletada e ramifica pelo motivo. Preenche data, preco e livres."""
    bruto = str(ctx.variaveis.get(no.get("campo"), "") or "").strip()
    data = _para_data(bruto)
    if data is None:
        # Sai sem preencher as derivadas de propósito. O `pergunta` com `validacao: data`
        # já normalizou o campo, então chegar aqui é defeito de roteiro (`campo` apontando
        # para outra variável) — e roteiro com defeito degrada para gente, com nota
        # dizendo o que faltou (`CLAUDE.md` §7), não para uma resposta amigável inventada.
        log.warning(
            "fluxo/agenda: campo %r não tem data utilizável (%r)",
            no.get("campo"),
            bruto,
        )
        return salao_agenda.MOTIVO_SEM_CADASTRO

    situacao = (
        await salao_agenda.resolver(ctx.db, ctx.tenant_id, [data], ctx.hoje)
    ).get(data)
    motivo = (situacao or {}).get("motivo") or salao_agenda.MOTIVO_SEM_CADASTRO

    # Em `derivadas`, e NÃO em `captured`: preço e lista de livres são verdade do instante,
    # e `captured` vira `contacts.profile`, a memória permanente do cliente — um preço de
    # outubro guardado ali seria mentira em novembro. E não em `variaveis` direto, porque
    # `variaveis` morre no fim do turno: a mensagem "segurei o dia {{data}}" é enviada
    # três turnos depois desta consulta.
    ctx.derivadas["data"] = data.strftime("%d/%m/%Y")
    ctx.derivadas["preco"] = salao_agenda.formatar_preco(
        (situacao or {}).get("preco_centavos")
    )
    livres = await salao_agenda.proximas_livres(ctx.db, ctx.tenant_id)
    ctx.derivadas["proximas_livres"] = _formatar_livres(
        livres, str(no.get("sem_livres") or SEM_LIVRES_PADRAO)
    )
    if motivo == salao_agenda.MOTIVO_OK:
        return SAIDA_LIVRE
    # Nada cadastrado para o dia pedido E nenhuma data à venda à frente: não é "esse dia
    # não", é "a agenda não está em uso". Só `sem_cadastro` vira isto — `ocupada` e
    # `bloqueada` são fatos sobre uma agenda que existe, e continuam sendo respondidos
    # pelo roteiro mesmo quando não sobra nenhuma alternativa a oferecer.
    if motivo == salao_agenda.MOTIVO_SEM_CADASTRO and not livres:
        return SAIDA_AGENDA_VAZIA
    return motivo


async def _executar_acao(ctx: Contexto, no: dict) -> str:
    acao = no.get("acao")
    if acao == "fotos":
        return await _acao_fotos(ctx)
    if acao == "pre_reserva":
        return await _acao_pre_reserva(ctx, no)
    log.error("fluxo/acao: ação desconhecida %r", acao)
    return "falhou"


async def _acao_fotos(ctx: Contexto) -> str:
    """Não envia nada aqui: emite a MESMA chave que a persona do agente emite.

    O envio acontece em `bot_orchestrator`, depois de todo o texto do turno — que é como
    as fotos chegam atrás do "Olha só ✨" que as anuncia. O que este nó decide é se vale a
    pena anunciar: galeria vazia ou já enviada nesta conversa viram `falhou`, e o roteiro
    manda para a equipe em vez de prometer foto que não vem.
    """
    if ctx.simulacao:
        # Nada é enviado; o painel só precisa saber por qual ramo a conversa seguiria.
        return (
            "ok"
            if await salao_fotos.listar(ctx.db, ctx.tenant_id, apenas_ativas=True)
            else "falhou"
        )
    if ctx.session["variables"].get("fotos_enviadas"):
        return "falhou"
    if not await salao_fotos.listar(ctx.db, ctx.tenant_id, apenas_ativas=True):
        return "falhou"
    ctx.captured["_acao_fotos"] = "sim"
    return "ok"


async def _acao_pre_reserva(ctx: Contexto, no: dict) -> str:
    """Segura a data AGORA, no meio da caminhada — e é de propósito.

    A escrita precisa acontecer antes do texto ser composto: quem arbitra a corrida é o
    índice parcial único de `salao_reservas` (`CLAUDE.md` regra 3), e sem o resultado em
    mãos o roteiro diria "segurei o dia 15" sobre uma reserva que outra conversa levou.
    """
    data = _para_data(str(ctx.variaveis.get(no.get("campo"), "") or ""))
    if data is None:
        log.error("fluxo/acao: pré-reserva sem data no campo %r", no.get("campo"))
        return "falhou"
    if ctx.simulacao:
        # Sem escrita: quem está testando o roteiro no painel não pode segurar a data de
        # um cliente real. O ramo sai da agenda, que é leitura — a resposta é honesta.
        situacao = (
            await salao_agenda.resolver(ctx.db, ctx.tenant_id, [data], ctx.hoje)
        ).get(data)
        return "ok" if (situacao or {}).get("livre") else "falhou"
    ok = await salao_acoes.pre_reservar(
        ctx.db, ctx.tenant_id, ctx.conversation_id, ctx.conv, ctx.session, data
    )
    return "ok" if ok else "falhou"


def _para_data(bruto: str) -> date | None:
    try:
        return datetime.strptime(bruto[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


registrar(
    "agenda",
    Espec(saidas=SAIDAS_AGENDA, campos=("campo",)),
    _executar_agenda,
)
registrar(
    "acao",
    Espec(saidas=SAIDAS_ACAO, campos=("acao",)),
    _executar_acao,
)
