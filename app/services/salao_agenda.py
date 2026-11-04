"""Agenda do salão: preço por data, disponibilidade e reserva atômica.

**A regra inviolável deste módulo** (mesma classe da captura de conversa, `CLAUDE.md` §2):
a exclusividade da data é do **índice parcial** `uq_salao_reservas_data_ativa`, não de
uma checagem em Python. Ler as reservas, concluir que a data está livre e depois inserir
reabre o overbooking — dois clientes fecham o mesmo sábado no intervalo entre o SELECT e
o INSERT, e isso só aparece em produção, com dois atendimentos simultâneos.

O `INSERT ... ON CONFLICT DO NOTHING` é quem decide. A consulta que vem antes existe só
para dizer *por que* não deu (data sem cadastro, sem preço, bloqueada) — nunca para
autorizar a escrita.

**Disponibilidade tem uma implementação só**: `resolver`. Nenhuma outra função deste
módulo — e nenhuma camada acima — decide isso por conta própria. O front lê o `motivo` que
vem pronto; recalcular no cliente cria uma segunda regra que diverge, e a divergência
aparece como preço errado dito ao cliente.

**Ninguém aqui dá commit.** No caminho do bot estas funções rodam dentro da transação do
`bot_orchestrator`, que commita no fim do turno; commitar aqui publicaria pela metade o
estado da conversa. Quem chama pela API commita na rota.
"""

import logging
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.salao import ReservaStatus

log = logging.getLogger("uvicorn.error")

# Validade da pré-reserva criada pelo agente. Longo o bastante para o cliente conversar
# com a família e a equipe retornar num dia útil; curto o bastante para não congelar um
# sábado por causa de quem só estava pesquisando.
PRE_RESERVA_HORAS = 48

# Motivos de recusa. Fechado (e não string livre) porque a UI e o agente traduzem cada
# caso — e "não deu" sem motivo foi o que tornou insuportável depurar a captura no Otto.
MOTIVO_OK = "ok"
MOTIVO_SEM_CADASTRO = "sem_cadastro"  # não há linha em salao_datas para o dia
MOTIVO_SEM_PRECO = "sem_preco"  # dia cadastrado, valor não definido
MOTIVO_BLOQUEADA = "bloqueada"  # manutenção/uso próprio
MOTIVO_OCUPADA = "ocupada"  # já existe reserva ativa
MOTIVO_PASSADO = "passado"  # data anterior a hoje

# Motivos que o atendente humano pode passar por cima (ele sabe o que combinou ao
# telefone); bloqueada, ocupada e passado são duros para todo mundo.
_MOTIVOS_CONTORNAVEIS = (MOTIVO_SEM_CADASTRO, MOTIVO_SEM_PRECO)

_SELECT_RESERVA = (
    "SELECT id, data, status, contact_id, conversation_id, criada_por, "
    "criada_por_user_id, expira_em, nome_cliente, telefone, observacao, created_at "
    "FROM salao_reservas "
)

# Projeção da resolução: a data cadastrada (se houver) e a reserva ativa (se houver).
# `{fonte}` é o gerador de dias — `generate_series` para intervalo, `unnest` para datas
# avulsas —, e precisa entregar uma coluna `dia`.
#
# Dirige do gerador e não de `salao_datas` porque o calendário precisa de uma linha por
# DIA do intervalo, cadastrado ou não: é o que permite ao front desenhar o mês sem
# sintetizar os buracos, e sintetizar buraco é onde a segunda implementação da regra nasce.
_SQL_RESOLUCAO = """
SELECT dia::date AS data,
       d.preco_centavos,
       (d.id IS NOT NULL) AS cadastrada,
       COALESCE(d.bloqueada, false) AS bloqueada,
       d.observacao,
       r.id AS reserva_id, r.status AS reserva_status, r.nome_cliente,
       r.telefone, r.expira_em, r.conversation_id, r.criada_por
FROM {fonte} dia
LEFT JOIN salao_datas d
       ON d.tenant_id = :tenant_id AND d.data = dia::date
LEFT JOIN salao_reservas r
       ON r.tenant_id = :tenant_id AND r.data = dia::date
      AND r.status IN ('pre_reservada', 'confirmada')
"""

_FONTE_INTERVALO = "generate_series(CAST(:de AS date), CAST(:ate AS date), '1 day')"
_FONTE_DATAS = "unnest(CAST(:datas AS date[]))"


def _motivo_de(r, hoje: date) -> str:
    """Traduz uma linha da resolução no motivo pelo qual a data está (ou não) à venda.

    A ordem importa: `passado`, `bloqueada` e `ocupada` primeiro, porque são fatos sobre a
    data e é isso que o usuário precisa ouvir; só depois a ausência de cadastro ou de
    valor, que são estados de preenchimento.
    """
    if r.data < hoje:
        return MOTIVO_PASSADO
    if r.bloqueada:
        return MOTIVO_BLOQUEADA
    if r.reserva_status is not None:
        return MOTIVO_OCUPADA
    if not r.cadastrada:
        return MOTIVO_SEM_CADASTRO
    if r.preco_centavos is None:
        return MOTIVO_SEM_PRECO
    return MOTIVO_OK


def _reserva_dict(r) -> dict:
    return {
        "id": str(r.id),
        "data": r.data,
        "status": r.status,
        "contact_id": str(r.contact_id) if r.contact_id else None,
        "conversation_id": str(r.conversation_id) if r.conversation_id else None,
        "criada_por": r.criada_por,
        "expira_em": r.expira_em,
        "nome_cliente": r.nome_cliente,
        "telefone": r.telefone,
        "observacao": r.observacao,
    }


async def expirar_vencidas(
    db: AsyncSession, tenant_id: UUID, data: date | None = None
) -> int:
    """Marca como EXPIRADA toda pré-reserva vencida (do dia informado, ou de todas).

    Expiração **preguiçosa**, cobrada em toda leitura e em toda disputa de data. É o que
    dispensa um `celery beat`: não existe processo novo para manter no ar, e um hold
    vencido nunca chega a segurar uma data de verdade — na primeira vez que alguém olha,
    ele cai.
    """
    sql = (
        "UPDATE salao_reservas SET status = :expirada, updated_at = now() "
        "WHERE tenant_id = :tenant_id AND status = :pre AND expira_em IS NOT NULL "
        "AND expira_em < now()"
    )
    params: dict = {
        "tenant_id": str(tenant_id),
        "expirada": ReservaStatus.EXPIRADA,
        "pre": ReservaStatus.PRE_RESERVADA,
    }
    if data is not None:
        sql += " AND data = :data"
        params["data"] = data
    result = await db.execute(text(sql), params)
    return result.rowcount or 0


# ─────────────────────────── leitura ───────────────────────────


async def resolver(
    db: AsyncSession,
    tenant_id: UUID,
    datas: list[date],
    hoje: date | None = None,
) -> dict[date, dict]:
    """Situação de cada data pedida: cadastro, bloqueio, reserva e valor.

    **É a única fonte de verdade de disponibilidade do módulo.** Diferente de `consultar`,
    devolve TODAS as datas pedidas, inclusive as sem cadastro — quem precisa distinguir
    "não existe" de "existe e não pode" é `criar_reserva`, para dizer o motivo certo.
    """
    if not datas:
        return {}
    await expirar_vencidas(db, tenant_id)
    hoje = hoje or date.today()
    rows = await db.execute(
        text(_SQL_RESOLUCAO.format(fonte=_FONTE_DATAS)),
        {"tenant_id": str(tenant_id), "datas": list(datas)},
    )
    return {r.data: _linha_resolvida(r, hoje) for r in rows.fetchall()}


def _linha_resolvida(r, hoje: date) -> dict:
    motivo = _motivo_de(r, hoje)
    return {
        "data": r.data,
        "preco_centavos": r.preco_centavos,
        "cadastrada": r.cadastrada,
        "bloqueada": r.bloqueada,
        "observacao": r.observacao,
        "reserva_status": r.reserva_status,
        "motivo": motivo,
        "livre": motivo == MOTIVO_OK,
    }


def _reserva_da_linha(r) -> dict | None:
    if not r.reserva_id:
        return None
    return {
        "id": str(r.reserva_id),
        "status": r.reserva_status,
        "nome_cliente": r.nome_cliente,
        "telefone": r.telefone,
        "expira_em": r.expira_em,
        "conversation_id": str(r.conversation_id) if r.conversation_id else None,
        "criada_por": r.criada_por,
    }


async def listar(
    db: AsyncSession, tenant_id: UUID, de: date, ate: date, hoje: date | None = None
) -> list[dict]:
    """Todo dia do intervalo, resolvido, com a reserva ativa de cada um (se houver).

    Devolve o intervalo INTEIRO, inclusive os dias sem cadastro. O calendário precisa
    desenhar o mês completo, e deixar que ele invente as lacunas seria recriar a regra de
    disponibilidade no cliente — que é justamente o que `resolver` existe para impedir.
    """
    await expirar_vencidas(db, tenant_id)
    hoje = hoje or date.today()
    rows = await db.execute(
        text(_SQL_RESOLUCAO.format(fonte=_FONTE_INTERVALO) + " ORDER BY dia"),
        {"tenant_id": str(tenant_id), "de": de, "ate": ate},
    )
    return [
        {**_linha_resolvida(r, hoje), "reserva": _reserva_da_linha(r)}
        for r in rows.fetchall()
    ]


async def consultar(
    db: AsyncSession, tenant_id: UUID, datas: list[date], hoje: date | None = None
) -> dict[date, dict]:
    """Situação de datas específicas — é o que o agente pergunta antes de falar valor.

    Data ausente do retorno significa **sem cadastro**, e isso é informação: o agente
    tem de dizer que confirma com um atendente em vez de estimar. Os demais motivos vêm
    na chave `motivo`, porque "não pode" tem respostas diferentes ao cliente — data
    ocupada se oferece outra, data bloqueada se lamenta.
    """
    resolvidas = await resolver(db, tenant_id, datas, hoje)
    return {
        d: linha
        for d, linha in resolvidas.items()
        if linha["motivo"] != MOTIVO_SEM_CADASTRO
    }


async def proximas_livres(
    db: AsyncSession, tenant_id: UUID, limite: int = 6, a_partir_de: date | None = None
) -> list[dict]:
    """Próximas datas vendáveis: cadastradas, com preço, sem bloqueio e sem reserva ativa.

    Consulta `salao_datas` direto, e não o intervalo de dias: só data cadastrada pode
    estar livre, então varrer o calendário dia a dia leria milhares de linhas vazias para
    devolver seis.
    """
    await expirar_vencidas(db, tenant_id)
    rows = await db.execute(
        text(
            "SELECT d.data, d.preco_centavos FROM salao_datas d "
            "LEFT JOIN salao_reservas r "
            "  ON r.tenant_id = d.tenant_id AND r.data = d.data "
            " AND r.status IN ('pre_reservada', 'confirmada') "
            "WHERE d.tenant_id = :tenant_id AND d.data >= :hoje "
            "  AND NOT d.bloqueada AND d.preco_centavos IS NOT NULL AND r.id IS NULL "
            "ORDER BY d.data LIMIT :limite"
        ),
        {
            "tenant_id": str(tenant_id),
            "hoje": a_partir_de or date.today(),
            "limite": limite,
        },
    )
    return [
        {"data": r.data, "preco_centavos": r.preco_centavos} for r in rows.fetchall()
    ]


async def reservas_ativas(db: AsyncSession, tenant_id: UUID) -> list[dict]:
    """Reservas que ocupam data, da mais próxima para a mais distante."""
    await expirar_vencidas(db, tenant_id)
    rows = await db.execute(
        text(
            _SELECT_RESERVA + "WHERE tenant_id = :tenant_id "
            "AND status IN ('pre_reservada', 'confirmada') ORDER BY data"
        ),
        {"tenant_id": str(tenant_id)},
    )
    return [_reserva_dict(r) for r in rows.fetchall()]


async def reservas_do_contato(
    db: AsyncSession,
    tenant_id: UUID,
    contact_id: UUID | None,
    telefone: str | None = None,
    limite: int = 2,
) -> list[dict]:
    """As reservas DESTE cliente, para o agente saber responder "já foi confirmado?".

    Mora aqui, e não em `salao_contexto`, por causa do `expirar_vencidas` da primeira
    linha: sem ele um hold vencido seria descrito ao cliente como "segurada", que é
    exatamente a promessa que o sistema não pode mais cumprir.

    **Casa por contato OU por telefone.** O contato é a chave normal; o telefone é o que
    faz o contexto acompanhar a PESSOA se ela um dia aparecer sob outro registro — o
    endereço do WhatsApp mudou de forma uma vez (`@lid`) e pode mudar de novo. Sem isso,
    quem volta a escrever recomeça do zero.

    Ordem: primeiro as que ocupam data (o que o cliente quer saber), da mais próxima
    para a mais distante. Se não houver nenhuma, a última encerrada dos últimos 7 dias —
    é o ramo que responde "minha data caiu e ninguém me avisou".
    """
    if contact_id is None and not telefone:
        return []
    await expirar_vencidas(db, tenant_id)

    # CAST explícito nos dois: os parâmetros podem chegar NULL, e sem o tipo o Postgres
    # recusa a query inteira com AmbiguousParameterError em vez de simplesmente não casar.
    filtro = (
        "(contact_id = CAST(:contact_id AS uuid) "
        " OR (CAST(:telefone AS text) IS NOT NULL "
        "     AND telefone = CAST(:telefone AS text)))"
    )
    params: dict = {
        "tenant_id": str(tenant_id),
        "contact_id": str(contact_id) if contact_id else None,
        "telefone": telefone or None,
        "limite": limite,
    }

    rows = await db.execute(
        text(
            _SELECT_RESERVA + f"WHERE tenant_id = :tenant_id AND {filtro} "
            "AND status IN ('pre_reservada', 'confirmada') "
            "ORDER BY data LIMIT :limite"
        ),
        params,
    )
    ativas = [_reserva_dict(r) for r in rows.fetchall()]
    if ativas:
        return ativas

    rows = await db.execute(
        text(
            _SELECT_RESERVA + f"WHERE tenant_id = :tenant_id AND {filtro} "
            "AND status IN ('expirada', 'cancelada') "
            "AND updated_at > now() - interval '7 days' "
            "ORDER BY updated_at DESC LIMIT 1"
        ),
        params,
    )
    return [_reserva_dict(r) for r in rows.fetchall()]


# ─────────────────────────── cadastro de datas ───────────────────────────


async def definir(
    db: AsyncSession,
    tenant_id: UUID,
    data: date,
    preco_centavos: int | None,
    bloqueada: bool = False,
    observacao: str | None = None,
) -> dict:
    """Cria ou atualiza uma data do calendário."""
    await db.execute(
        text(
            "INSERT INTO salao_datas "
            "(id, tenant_id, data, preco_centavos, bloqueada, observacao) "
            "VALUES (gen_random_uuid(), :tenant_id, :data, :preco, :bloqueada, :obs) "
            "ON CONFLICT (tenant_id, data) DO UPDATE SET "
            "preco_centavos = EXCLUDED.preco_centavos, "
            "bloqueada = EXCLUDED.bloqueada, "
            "observacao = EXCLUDED.observacao, updated_at = now()"
        ),
        {
            "tenant_id": str(tenant_id),
            "data": data,
            "preco": preco_centavos,
            "bloqueada": bloqueada,
            "obs": observacao,
        },
    )
    return {
        "data": data,
        "preco_centavos": preco_centavos,
        "bloqueada": bloqueada,
        "observacao": observacao,
    }


async def definir_em_lote(
    db: AsyncSession,
    tenant_id: UUID,
    de: date,
    ate: date,
    dias_da_semana: list[int],
    preco_centavos: int | None,
    bloqueada: bool | None = None,
) -> int:
    """Aplica um valor a todos os dias do intervalo que caem nos dias da semana escolhidos.

    Com a regra semanal, isto deixou de ser o jeito de encher a agenda e virou o jeito de
    abrir **exceção em faixa**: "dezembro inteiro mais caro", "a semana da reforma
    bloqueada". Continua útil justamente por isso.

    `dias_da_semana` em ISO (1=segunda … 7=domingo); vazio = todos os dias do intervalo.

    `bloqueada=None` **não mexe no bloqueio existente**. Antes o valor entrava sempre pelo
    EXCLUDED, então todo lote de preço desbloqueava em silêncio as datas bloqueadas do
    intervalo — a manutenção do dia 18 voltava a ser vendida sem ninguém pedir.

    NÃO mexe em datas com reserva ativa: mudar o valor de um sábado já vendido alteraria o
    que foi combinado com o cliente. Essas datas são puladas e não entram na contagem.
    """
    if ate < de:
        return 0
    await expirar_vencidas(db, tenant_id)
    result = await db.execute(
        text(
            "INSERT INTO salao_datas "
            "(id, tenant_id, data, preco_centavos, bloqueada) "
            "SELECT gen_random_uuid(), :tenant_id, dia::date, :preco, "
            "       COALESCE(CAST(:bloqueada AS boolean), false) "
            "FROM generate_series(CAST(:de AS date), CAST(:ate AS date), '1 day') dia "
            "WHERE (:sem_filtro OR CAST(EXTRACT(ISODOW FROM dia) AS int) = ANY(:dias)) "
            "  AND NOT EXISTS ("
            "    SELECT 1 FROM salao_reservas r WHERE r.tenant_id = :tenant_id "
            "    AND r.data = dia::date AND r.status IN ('pre_reservada', 'confirmada')) "
            "ON CONFLICT (tenant_id, data) DO UPDATE SET "
            "preco_centavos = EXCLUDED.preco_centavos, "
            "bloqueada = COALESCE(CAST(:bloqueada AS boolean), salao_datas.bloqueada), "
            "updated_at = now()"
        ),
        {
            "tenant_id": str(tenant_id),
            "de": de,
            "ate": ate,
            "preco": preco_centavos,
            "bloqueada": bloqueada,
            "sem_filtro": not dias_da_semana,
            "dias": [int(d) for d in dias_da_semana] or [0],
        },
    )
    return result.rowcount or 0


# ─────────────────────────── reserva (o ponto crítico) ───────────────────────────


async def criar_reserva(
    db: AsyncSession,
    tenant_id: UUID,
    data: date,
    *,
    status: str = ReservaStatus.PRE_RESERVADA,
    contact_id: UUID | None = None,
    conversation_id: UUID | None = None,
    criada_por: str = "humano",
    criada_por_user_id: UUID | None = None,
    nome_cliente: str | None = None,
    telefone: str | None = None,
    observacao: str | None = None,
    exigir_preco: bool = True,
    criar_data_se_faltar: bool = False,
    hoje: date | None = None,
) -> tuple[dict | None, str]:
    """Reserva uma data. Devolve `(reserva, motivo)`; `reserva` é None se não deu.

    `exigir_preco=True` (o padrão, usado pelo bot): não se segura uma data cujo valor
    ninguém cadastrou — seria compromisso sobre um número que não existe. O atendente
    humano pode reservar sem preço (`exigir_preco=False`, `criar_data_se_faltar=True`),
    porque quem fecha por telefone sabe o que combinou.

    A ordem importa e não é negociável:
      1. expira os holds vencidos DESTA data (senão um hold morto bloqueia uma venda);
      2. lê a situação — só para poder dizer o motivo;
      3. INSERT condicional. **É o passo 3 que decide.** Zero linhas com a situação lida
         como livre = outra transação ganhou a corrida entre a leitura e a escrita.
    """
    hoje = hoje or date.today()
    if data < hoje:
        return None, MOTIVO_PASSADO

    await expirar_vencidas(db, tenant_id, data)

    if criar_data_se_faltar:
        await db.execute(
            text(
                "INSERT INTO salao_datas (id, tenant_id, data) "
                "VALUES (gen_random_uuid(), :tenant_id, :data) "
                "ON CONFLICT (tenant_id, data) DO NOTHING"
            ),
            {"tenant_id": str(tenant_id), "data": data},
        )

    situacao = (await resolver(db, tenant_id, [data], hoje)).get(data)
    if situacao is None:  # a data pedida não voltou da resolução: não deveria acontecer
        return None, MOTIVO_SEM_CADASTRO
    motivo = situacao["motivo"]
    if motivo != MOTIVO_OK:
        # O atendente humano passa por cima de "sem cadastro" e "sem valor" porque ele
        # sabe o que combinou ao telefone; bloqueada, ocupada e passado continuam duras
        # para todo mundo. O bot nunca contorna nada — ele não tem como saber.
        pode_ignorar = (
            criar_data_se_faltar
            and not exigir_preco
            and motivo in _MOTIVOS_CONTORNAVEIS
        )
        if not pode_ignorar:
            return None, motivo

    expira_em = (
        datetime.now(timezone.utc) + timedelta(hours=PRE_RESERVA_HORAS)
        if status == ReservaStatus.PRE_RESERVADA
        else None
    )
    # O ON CONFLICT casa com o índice PARCIAL: só conflita contra reserva ativa. Reserva
    # cancelada ou expirada na mesma data não impede a nova.
    row = await db.execute(
        text(
            "INSERT INTO salao_reservas "
            "(id, tenant_id, data, status, contact_id, conversation_id, criada_por, "
            " criada_por_user_id, expira_em, nome_cliente, telefone, observacao) "
            "VALUES (gen_random_uuid(), :tenant_id, :data, :status, :contact_id, "
            " :conversation_id, :criada_por, :user_id, :expira_em, :nome, :telefone, :obs) "
            "ON CONFLICT (tenant_id, data) "
            "WHERE status IN ('pre_reservada', 'confirmada') DO NOTHING "
            "RETURNING id, data, status, contact_id, conversation_id, criada_por, "
            "criada_por_user_id, expira_em, nome_cliente, telefone, observacao"
        ),
        {
            "tenant_id": str(tenant_id),
            "data": data,
            "status": status,
            "contact_id": str(contact_id) if contact_id else None,
            "conversation_id": str(conversation_id) if conversation_id else None,
            "criada_por": criada_por,
            "user_id": str(criada_por_user_id) if criada_por_user_id else None,
            "expira_em": expira_em,
            "nome": nome_cliente,
            "telefone": telefone,
            "obs": observacao,
        },
    )
    r = row.fetchone()
    if r is None:
        # A leitura dizia livre e o banco recusou: outra transação reservou a mesma data
        # neste intervalo. É exatamente a corrida que o índice existe para arbitrar.
        log.info(
            "salao_agenda: corrida perdida na data %s do tenant %s", data, tenant_id
        )
        return None, MOTIVO_OCUPADA

    return _reserva_dict(r), MOTIVO_OK


async def confirmar(db: AsyncSession, tenant_id: UUID, reserva_id: UUID) -> dict | None:
    """Pré-reserva vira reserva confirmada. Só um humano chega aqui.

    UPDATE condicional (`status = 'pre_reservada'` no WHERE): confirmar duas vezes, ou
    confirmar o que outro atendente acabou de cancelar, devolve None em vez de reescrever
    um estado que já mudou.
    """
    row = await db.execute(
        text(
            "UPDATE salao_reservas SET status = :confirmada, expira_em = NULL, "
            "updated_at = now() "
            "WHERE id = :id AND tenant_id = :tenant_id AND status = :pre "
            "RETURNING id, data, status, contact_id, conversation_id, criada_por, "
            "criada_por_user_id, expira_em, nome_cliente, telefone, observacao"
        ),
        {
            "id": str(reserva_id),
            "tenant_id": str(tenant_id),
            "confirmada": ReservaStatus.CONFIRMADA,
            "pre": ReservaStatus.PRE_RESERVADA,
        },
    )
    r = row.fetchone()
    return _reserva_dict(r) if r else None


async def cancelar(db: AsyncSession, tenant_id: UUID, reserva_id: UUID) -> dict | None:
    """Libera a data. A linha continua no banco como histórico — o índice é parcial."""
    row = await db.execute(
        text(
            "UPDATE salao_reservas SET status = :cancelada, expira_em = NULL, "
            "updated_at = now() "
            "WHERE id = :id AND tenant_id = :tenant_id "
            "AND status IN ('pre_reservada', 'confirmada') "
            "RETURNING id, data, status, contact_id, conversation_id, criada_por, "
            "criada_por_user_id, expira_em, nome_cliente, telefone, observacao"
        ),
        {
            "id": str(reserva_id),
            "tenant_id": str(tenant_id),
            "cancelada": ReservaStatus.CANCELADA,
        },
    )
    r = row.fetchone()
    return _reserva_dict(r) if r else None


# ─────────────────────────── formatação ───────────────────────────


def formatar_preco(centavos: int | None) -> str:
    """`420000` → `R$ 4.200,00`. Vazio quando não há valor cadastrado."""
    if centavos is None:
        return ""
    inteiro, resto = divmod(int(centavos), 100)
    return f"R$ {inteiro:,.0f}".replace(",", ".") + f",{resto:02d}"


def formatar_livres(livres: list[dict]) -> str:
    """Uma data por linha, com o dia em negrito do WhatsApp (`*texto*`).

    Seis datas numa linha só viram um parágrafo que o cliente não lê no celular — e a
    lista existe para ser comparada, não lida. O `*` é a marcação do próprio WhatsApp;
    num canal que não a entenda, o pior caso é o asterisco aparecer.

    Mora aqui, e não num dos cérebros, porque os dois mostram a mesma lista: o roteiro a
    envia pronta e o agente a lê como modelo do que escrever. Formato em dois lugares
    divergiu uma vez — o roteiro mudou e o agente seguiu mandando tudo numa frase.
    """
    return "\n".join(
        f"*{d['data'].strftime('%d/%m')}* — {formatar_preco(d['preco_centavos'])}"
        for d in livres
    )
