"""Orquestrador do agente de IA: recebe a mensagem ingerida, decide e responde.

Invariantes (vindas de bugs reais mapeados no guia de referência):
- O bot só fala quando NENHUM humano capturou a conversa (assigned_user_id IS NULL),
  com re-checagem tardia antes de enviar (a chamada ao LLM leva segundos).
- Estado coletado vive em bot_sessions (a IA é stateless).
- Falha da IA nunca trava o atendimento: conta falhas consecutivas e, na 2ª,
  encaminha para a fila humana com nota interna (ver AI_MAX_FAILURES abaixo — o
  número mudou de 3 para 2 quando a re-tentativa automática entrou).
- Handoff = nota interna com o resumo da triagem; a conversa já está na fila
  de não-capturadas do Otto, então nenhum roteamento extra é necessário.
- Escalar é transição DISPUTADA: quem autoriza é o UPDATE condicional de
  `_claim_handoff`, nunca um `if` sobre o `handoff` lido no começo do turno.
  Dois turnos podem rodar juntos na mesma conversa (re-tentativa agendada +
  mensagem nova do cliente) e o sintoma era duas mensagens de encaminhamento
  seguidas, com duas notas de triagem.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import llm
from app.channels.factory import get_provider
from app.channels.types import OutboundMessage, SendResult
from app.core import ratelimit
from app.core.config import settings
from app.models.enums import Channel, ContentType, MessageDirection, MessageStatus
from app.flow import engine as flow_engine
from app.services import audit, contact_memory, events, midia as midia_txt, salao_acoes
from app.services import salao_fluxo  # noqa: F401  registra os nós `agenda` e `acao`
from app.services.channels import JOIN_CONEXAO
from app.services.ai_context import build_knowledge, load_history
from app.services.bot_settings import BotConfig, load_bot_config

log = logging.getLogger("uvicorn.error")

# Após N mensagens do cliente, força o encaminhamento. Rede de segurança, não meta: o
# agente é consultivo e o momento do handoff é da persona. Era 8 e cortava a conversa no
# meio da coleta. Contado no banco (`_mensagens_do_cliente`), não no `history`, que é
# cortado em HISTORY_LIMIT e nunca chegaria a 25.
AI_MAX_USER_TURNS = 25
# 2, não 3: com a re-tentativa agendada (tasks.bot.retry_bot_turn), a 1ª falha avisa e
# tenta de novo sozinha, e a 2ª encaminha. O valor 3 criava um estado do meio em que o
# bot não enviava nada e ainda esperava o cliente insistir para avançar o contador.
AI_MAX_FAILURES = 2
# Quanto tempo a RE-TENTATIVA espera depois de estourar `AI_MAX_FAILURES`, antes de o bot
# tentar de novo sozinho. Mensagem nova do cliente não espera: reativa na hora (ver
# `fresh` em `_load_or_create_session`).
#
# Antes não havia prazo: `bot_disabled` era definitivo, e um provedor que voltou ao normal
# em dois minutos deixava a conversa sem agente para sempre. Foi o que aconteceu com um
# cliente que perguntou se a reserva dele tinha sido confirmada — o bot morreu por uma
# resposta truncada e nunca mais falou.
#
# 3 minutos: curto demais e o bot martela um provedor fora do ar (e resposta truncada é
# COBRADA, ao contrário de um 503); longo demais e quem escreve de novo na mesma sessão de
# conversa — o cliente voltou em 49 segundos — continua no vácuo.
AI_COOLDOWN_SECONDS = 180
# Teto de respostas pós-handoff enquanto nenhum humano assume. Era 6, e subiu quando o
# agente passou a CONVIDAR o cliente a continuar tirando dúvidas na mensagem de
# encaminhamento (ver _POST_HANDOFF_NOTE em app/ai/llm.py): convidar e emudecer no sexto
# turno é pior que não convidar. Continua sendo teto de gasto, não meta — e quem o
# atinge recebe POST_HANDOFF_LIMIT_MESSAGE, em vez de falar sozinho.
POST_HANDOFF_MAX_TURNS = 10

WAIT_MESSAGE = "Só um segundinho, já te respondo. 😉"
# Provedor fora do ar numa conversa JÁ encaminhada. Repetir a mensagem de transferência
# seria a mesma frase duas vezes; calar deixava sem resposta a pergunta que o próprio
# encaminhamento convidou a fazer.
INSTABILIDADE_MESSAGE = (
    "Estou com instabilidade para responder agora. "
    "Sua conversa já está com a nossa equipe."
)
POST_HANDOFF_LIMIT_MESSAGE = (
    "A partir daqui, quem continua com você é a nossa equipe, por aqui mesmo."
)

# Motivo do encaminhamento quando ele NÃO vem da triagem — quem escala ali é o sistema, e
# não há `summary` de decisão nenhuma para anunciar. Frase curta de propósito: isto vai
# para o cartão de notificação do navegador, ao lado do nome do cliente.
MOTIVO_SEM_RESUMO = "O cliente está esperando um atendente."
MOTIVO_FALHA_DE_ENVIO = "O envio falhou e o cliente ficou sem parte da resposta."
MOTIVO_IA_INDISPONIVEL = "A triagem por IA falhou e o agente parou de responder."

# O que o modo AGENTE acrescenta ao texto de `services/midia.py` — no fluxo esse papel
# é do próprio nó, que é repetido logo abaixo do aviso.
MEDIA_CONVITE = " Me conta em uma linha o que você precisa? 😊"

# Mídias seguidas antes de chamar gente. Uma frase por foto irrita; calar depois da
# primeira abandona justamente quem mandou foto por não saber escrever o que queria.
# Zera quando o cliente escreve texto — ver `_handle`.
MAX_MIDIAS_SEGUIDAS = 2


class SkipReason:
    """Vocabulário fechado do motivo pelo qual o bot não falou num turno.

    Existe porque as saídas antecipadas de `_handle` eram `return` nu: o sintoma
    era sempre o mesmo ("liguei o toggle e o bot não respondeu") e não havia como
    distinguir qual guard disparou. Fechado (e não string livre) para que a UI possa
    traduzir cada caso sem adivinhar.
    """

    NOT_TEXT = "nao_texto"  # MVP: o bot só conversa por texto
    AI_OFF = "ia_desligada"  # toggle do painel desligado
    STALE = "mensagem_antiga"  # backlog do WhatsApp ao (re)conectar
    RESOLVED = "conversa_resolvida"
    CLAIMED = "conversa_capturada"  # humano no comando — comportamento correto
    SESSION_OFF = "sessao_desativada"  # bot_disabled SEM prazo: teto pós-handoff
    LLM_DOWN = "llm_indisponivel"  # o provedor não devolveu decisão utilizável
    # bot_disabled COM prazo: falhou, e a re-tentativa espera o prazo vencer (mensagem nova
    # do cliente reativa antes). Separado de
    # SESSION_OFF porque a ação do atendente é diferente — aqui não há nada a fazer, lá é
    # preciso liberar a conversa para reativar.
    LLM_COOLDOWN = "llm_em_espera"
    RETRY_OBSOLETE = (
        "retry_obsoleto"  # turno posterior já respondeu antes da re-tentativa
    )
    DUPLICATE_HANDOFF = "handoff_duplicado"  # outro turno ganhou a corrida da escalação
    BUDGET = "teto_diario"  # teto de turnos de LLM do tenant no dia
    # modo=fluxo e o tenant não publicou roteiro nenhum. Visível: é exatamente o
    # "liguei e o bot não responde" que este vocabulário existe para explicar.
    FLOW_MISSING = flow_engine.SKIP_FLUXO_AUSENTE
    # Dois turnos na mesma conversa; este perdeu o UPDATE condicional do avanço de nó.
    # Fora de VISIBLE pelo mesmo motivo de RETRY_OBSOLETE: o cliente FOI atendido, pelo
    # turno vencedor — anunciar aqui seria anunciar um silêncio que não existiu.
    FLOW_RACE = flow_engine.SKIP_FLUXO_CORRIDA
    # Uma bolha não saiu: o cliente ficou com meia resposta. Visível de propósito — é o
    # único motivo desta lista em que o bot FALOU e ainda assim há algo a consertar.
    SEND_FAILED = "envio_falhou"
    OK = "ok"  # respondeu

    # Motivos que o atendente precisa ver na thread. `CLAIMED` fica de fora de propósito:
    # é o funcionamento esperado depois de capturar, e avisar viraria ruído constante.
    # `RETRY_OBSOLETE` e `DUPLICATE_HANDOFF` também: nos dois o cliente FOI atendido, por
    # outro turno — mostrá-los na thread anunciaria um silêncio que não existiu.
    VISIBLE = (
        AI_OFF,
        STALE,
        SESSION_OFF,
        LLM_DOWN,
        LLM_COOLDOWN,
        FLOW_MISSING,
        SEND_FAILED,
    )


# Cadência de digitação. As bolhas saíam em rajada, uma imediatamente após a outra —
# o padrão que mais denuncia robô numa conexão não-oficial (baileys) e que arrisca ban
# do número. O Evolution mostra "digitando" durante a pausa, então ela é visível.
TYPING_MS_PER_CHAR = 35
TYPING_MIN_MS = 1200
TYPING_MAX_MS = 4000
TYPING_FIRST_MS = 800  # teto da 1ª bolha: o cliente acabou de escrever e está olhando

# Teto do motivo de falha guardado na mensagem. Corpo de erro de provider é ilimitado —
# o Evolution devolve o stack inteiro em alguns casos — e a linha da mensagem não é lugar
# de log.
MAX_ERRO_ENVIO = 500

# Quanto de cada bolha não entregue entra na nota — a equipe precisa reconhecer o texto,
# não relê-lo inteiro.
MAX_PREVIA_NOTA = 200


def _typing_delay(text: str, index: int = 0) -> int:
    """Pausa antes de enviar uma bolha, proporcional ao tamanho.

    A 1ª bolha responde mais rápido de propósito: 4 s de espera para devolver um "Oi" é
    pior que responder rápido demais. Da segunda em diante vale o tempo cheio, que é
    quando a rajada aparecia.
    """
    ms = min(max(len(text) * TYPING_MS_PER_CHAR, TYPING_MIN_MS), TYPING_MAX_MS)
    return min(ms, TYPING_FIRST_MS) if index == 0 else ms


def _is_stale(message_ts: datetime | None) -> bool:
    """True se a mensagem é velha demais para auto-resposta (anti-flood ao conectar).

    O WhatsApp entrega o backlog de mensagens não lidas como novos eventos ao
    (re)conectar; sem esta guarda o bot responderia todas elas de uma vez.
    Usa o timestamp ORIGINAL da mensagem, então pega até entregas atrasadas.
    """
    limit = settings.BOT_REPLY_MAX_AGE_SECONDS
    if limit <= 0 or message_ts is None:
        return False
    if message_ts.tzinfo is None:
        message_ts = message_ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - message_ts).total_seconds() > limit


async def handle_inbound(
    db: AsyncSession,
    redis_client,
    tenant_id: UUID,
    conversation_id: UUID,
    content_type: str,
    message_ts: datetime | None = None,
    from_retry: bool = False,
    audio_ilegivel: bool = False,
) -> None:
    """Processa UMA mensagem inbound já persistida. Best-effort: erros são logados, nunca propagados.

    `audio_ilegivel` é como `app/tasks/media.py` diz "esta nota de voz não virou texto".
    Sem ele, áudio sem transcrição era silêncio absoluto: o download ou a transcrição
    falhavam e o cliente ficava esperando resposta que nunca vinha.
    """
    with audit.actor_context(tenant_id, actor_type="bot"):
        try:
            await _handle(
                db,
                redis_client,
                tenant_id,
                conversation_id,
                content_type,
                message_ts,
                from_retry,
                audio_ilegivel,
            )
        except Exception:
            log.exception(
                "bot_orchestrator: falha ao processar conversa %s", conversation_id
            )


async def _handle(
    db: AsyncSession,
    redis_client,
    tenant_id: UUID,
    conversation_id: UUID,
    content_type: str,
    message_ts: datetime | None = None,
    from_retry: bool = False,
    audio_ilegivel: bool = False,
) -> None:
    # A guarda anti-flood vem ANTES de qualquer envio, sem exceção. Ela ficava depois do
    # ramo de mídia, e o resultado foi um incidente real: ao reconectar, o WhatsApp
    # entregou o backlog de figurinhas e imagens antigas, cada uma disparou um "Recebi
    # seu arquivo!" e o bot escreveu do nada para vários contatos de uma vez. Qualquer
    # caminho novo que envie mensagem tem que passar por aqui primeiro.
    if _is_stale(message_ts):
        # mensagem antiga (backlog do WhatsApp ao conectar) — ingerida, mas sem auto-resposta
        await _skip(db, tenant_id, conversation_id, SkipReason.STALE, persist=False)
        return

    # Mídia deixou de ser saída antecipada. Ela era tratada aqui em cima, fora dos dois
    # cérebros: uma frase fixa uma vez por conversa e, da SEGUNDA em diante, silêncio
    # total — sem nota interna e sem encaminhar. Quem mandava duas fotos era abandonado.
    # Agora a mídia desce e vira turno de verdade; o que sobra aqui é só o áudio, que
    # não se responde antes de saber se virou texto.
    e_midia = content_type != ContentType.TEXT.name
    if content_type == ContentType.AUDIO.name and not audio_ilegivel:
        # `app/tasks/media.py` transcreve e reentra como TEXT (ou como áudio ilegível, se
        # falhar). Responder aqui mandava "me conta por texto" ANTES da resposta de
        # verdade — e queimava o aviso de mídia, emudecendo o bot para a foto seguinte.
        await _skip(db, tenant_id, conversation_id, SkipReason.NOT_TEXT, persist=False)
        return

    bot = await load_bot_config(db, tenant_id)
    if not bot.ai_enabled:
        await _skip(db, tenant_id, conversation_id, SkipReason.AI_OFF, persist=False)
        return

    # Teto de gasto do tenant no dia. Os limites abaixo (AI_MAX_USER_TURNS,
    # POST_HANDOFF_MAX_TURNS) são por CONVERSA — e `ingestion` abre conversa nova para
    # cada remetente e outra assim que a anterior é resolvida. Sem um teto por tenant,
    # quem manda mensagem controla quanto o salão gasta em LLM.
    # Só o agente de IA consome cota: o teto existe para o gasto com LLM, e o roteiro não
    # faz nenhuma chamada. Cobrá-lo em modo fluxo emudeceria o bot mais barato do produto.
    if not bot.em_fluxo and not e_midia and not await _dentro_do_teto_diario(tenant_id):
        await _skip(db, tenant_id, conversation_id, SkipReason.BUDGET, persist=False)
        return

    conv = await _load_conversation(db, tenant_id, conversation_id)
    if conv is None or conv.assigned_user_id is not None or conv.status == "RESOLVED":
        # humano no comando — o bot não fala por cima
        await _skip(db, tenant_id, conversation_id, SkipReason.CLAIMED)
        return

    session = await _load_or_create_session(
        db, tenant_id, conversation_id, fresh=not from_retry
    )
    if session["bot_disabled"]:
        # Chegou aqui ainda desativado: ou não tem prazo (teto pós-handoff, permanente),
        # ou é a re-tentativa dentro do prazo — mensagem nova do cliente já teria sido
        # reativada pelo UPDATE condicional do load.
        # São ações diferentes para o atendente, então são motivos diferentes.
        motivo = (
            SkipReason.LLM_COOLDOWN
            if session.get("bot_disabled_until")
            else SkipReason.SESSION_OFF
        )
        await _skip(db, tenant_id, conversation_id, motivo)
        return

    # Re-tentativa que perdeu a razão de existir. `ai_failures` é zerado por toda decisão
    # bem-sucedida, então 0 aqui significa: outro turno respondeu nos 8 s de countdown
    # (o caso comum é o cliente ter mandado outra mensagem enquanto esperava). Sem esta
    # guarda os dois turnos rodam em paralelo na mesma conversa e o cliente recebe duas
    # respostas — foi assim que a escalação saiu duplicada em teste real.
    if from_retry and session["ai_failures"] == 0:
        await _skip(db, tenant_id, conversation_id, SkipReason.RETRY_OBSOLETE)
        return

    history = await load_history(db, tenant_id, conversation_id)

    # Escrever texto zera a contagem de mídias seguidas: o cliente voltou a conversar, e
    # duas fotos separadas por uma conversa inteira não podem somar e mandá-lo para a
    # fila. (`media_notice_sent` é o formato antigo, de quando o aviso era único por
    # conversa — some na primeira mídia da sessão.)
    if e_midia:
        session["variables"].pop("media_notice_sent", None)
    else:
        session["variables"].pop("midias_seguidas", None)

    # ── qual cérebro atende ──
    # `llm.agent_turn` e `flow_engine.decidir` devolvem o MESMO dicionário de decisão, e é
    # só por isso que tudo daqui para baixo é um código só. Ver `app/flow/__init__.py`.
    #
    # Mídia tem uma terceira porta, e não um caminho paralelo: nenhum dos dois cérebros lê
    # imagem, mas os dois sabem encaminhar. No fluxo ela entra como resposta que não
    # encaixa (mesmo contador de inválidas, mesmo handoff); no agente, `_decisao_de_midia`
    # monta a MESMA decisão sem gastar uma chamada de modelo para dizer "me conta em
    # texto". Dos dois lados, o que vem depois é o código de sempre.
    if e_midia and not bot.em_fluxo:
        decision = _decisao_de_midia(session, content_type)
    elif bot.em_fluxo:
        decision = await flow_engine.decidir(
            db,
            tenant_id,
            conversation_id,
            conv,
            session,
            bot,
            _ultima_do_cliente(history),
            midia=content_type if e_midia else None,
        )
        if decision is None:
            # O motor já registrou o motivo (roteiro ausente, corrida perdida). Sai ANTES
            # de `_save_session`: em corrida perdida, salvar sobrescreveria com estado
            # velho o que o turno vencedor acabou de gravar.
            await _skip(
                db,
                tenant_id,
                conversation_id,
                session.get("skip_reason") or SkipReason.FLOW_MISSING,
            )
            return
    else:
        user_turns = await _mensagens_do_cliente(db, tenant_id, conversation_id)

        # Memória do cliente: o que ele já contou em atendimentos ANTERIORES. A sessão vale
        # só para esta conversa, então sem semear a partir do perfil o cliente que volta
        # depois de uma conversa resolvida seria interrogado de novo sobre tudo.
        perfil = dict(conv.contact_profile or {})
        state = session["variables"].get("intake") or dict(perfil.get("intake") or {})

        # conhecimento do turno: inline (Fase 1) + chunks por RAG (Fase 2) + a reserva
        # DESTE cliente, que permite responder "meu lugar foi confirmado?" sem inventar.
        knowledge = await build_knowledge(
            db, tenant_id, bot, history, conv.contact_id, conv.contact_phone
        )

        decision = await llm.agent_turn(
            persona=bot.persona,
            state=state,
            history=history,
            knowledge=knowledge,
            max_turns_reached=user_turns >= AI_MAX_USER_TURNS,
            provider=bot.provider,
            model=bot.model,
            api_key=bot.api_key,
            contact_name=conv.contact_name or "",
            post_handoff=session["handoff"],
            # `history` é só desta conversa; o perfil é o que atravessa conversas.
            contact_profile=perfil,
            # Só é "reencontro" se for uma conversa nova: no meio de um papo em andamento
            # cumprimentar de novo com "que bom te ver!" soaria quebrado.
            returning=contact_memory.is_returning(perfil) and not history[:-1],
        )

        if decision is None:
            await _handle_ai_failure(
                db, redis_client, tenant_id, conversation_id, conv, session, bot
            )
            return

    # Decisão atrasada não pode atualizar memória, reservar ou enviar após encerramento.
    if not await _still_unassigned(db, tenant_id, conversation_id):
        await _skip(db, tenant_id, conversation_id, SkipReason.RESOLVED)
        return
    await audit.record(
        db,
        tenant_id,
        "bot.decision",
        "conversation",
        conversation_id,
        actor_type="bot",
        reason=decision["status"],
        metadata={"mode": "fluxo" if bot.em_fluxo else "agente"},
    )
    # ── aplica a decisão ──
    session["ai_failures"] = 0
    # Derivado salão: retira de `captured` as chaves de controle (_acao_fotos,
    # _acao_pre_reserva) ANTES de elas virarem dado do cliente. Tem de vir aqui, acima da
    # linha que alimenta a sessão e o perfil do contato — `contact_memory.remember` não
    # filtra chaves com `_` e gravaria a ação como se fosse informação da festa.
    acoes = salao_acoes.extrair(decision)
    if decision["captured"]:
        session["variables"]["intake"] = decision["captured"]
        # E também no contato: é o que sobrevive quando esta conversa for resolvida.
        await contact_memory.remember(
            db, tenant_id, conv.contact_id, decision["captured"]
        )

    reply = decision["reply"]
    quer_escalar = decision["status"] in ("ready_to_handoff", "needs_human")
    # `session["handoff"]` foi lido no início do turno e NÃO autoriza a escalação — quem
    # autoriza é o UPDATE condicional de `_claim_handoff`. O teste curto vem primeiro só
    # para não gastar uma ida ao banco na conversa que já escalou há muito tempo.
    escalou_agora = (
        quer_escalar
        and not session["handoff"]
        and await _claim_handoff(db, tenant_id, conversation_id)
    )
    # Motivo do silêncio decidido por quem zera a `reply`, não inferido depois de olhar
    # uma string vazia e adivinhar quem a esvaziou.
    motivo_do_silencio: str | None = None

    # O que a equipe precisa ouvir quando o aviso chegar. `None` = nada a anunciar.
    resumo_encaminhamento: str | None = None

    if escalou_agora:
        session["handoff"] = True
        # `or`: resumo vazio (nó de handoff sem `resumo`, modelo que devolveu string
        # em branco) não pode virar "não avisa ninguém" — o aviso é sobre a conversa
        # existir na fila, e o texto é o acessório.
        resumo_encaminhamento = decision["summary"] or MOTIVO_SEM_RESUMO
        note = _build_intake_note(
            decision, session["variables"].get("intake", {}), por_fluxo=bot.em_fluxo
        )
        await _insert_internal_note(db, tenant_id, conversation_id, note)
        # O resumo da triagem deixa de morrer na nota desta conversa: guardado no contato,
        # ele é o contexto do próximo atendimento deste mesmo cliente.
        await contact_memory.remember(
            db, tenant_id, conv.contact_id, decision["captured"], decision["summary"]
        )
        # prioridade e tags da triagem entram em colunas da conversa (filtro/ordenação).
        await _apply_triage(db, tenant_id, conversation_id, decision)
        if not reply:
            reply = bot.transfer_message
    elif quer_escalar and not session["handoff"]:
        # Perdeu a corrida: outro turno escalou esta conversa entre a leitura da sessão e
        # agora. Esta `reply` anuncia o encaminhamento DE NOVO — foi gerada sem o bloco
        # "você já encaminhou este cliente" no system prompt, porque na hora da chamada ao
        # LLM o handoff ainda não existia. Descartá-la é o conserto: o cliente já foi
        # avisado, uma vez, pelo turno vencedor. As ações (fotos, pré-reserva) seguem
        # valendo — perder uma data segurada em silêncio seria pior que uma foto sem legenda.
        session["handoff"] = True
        session["post_handoff_turns"] += 1
        reply = ""
        motivo_do_silencio = SkipReason.DUPLICATE_HANDOFF
        log.info("bot: escalação duplicada descartada na conversa %s", conversation_id)
    elif session["handoff"]:
        session["post_handoff_turns"] += 1
        if session["post_handoff_turns"] > POST_HANDOFF_MAX_TURNS:
            # Teto pós-handoff: para de gastar até um humano assumir, mas avisa uma vez.
            # Calar direto deixava o cliente perguntando para ninguém.
            session["bot_disabled"] = True
            reply = POST_HANDOFF_LIMIT_MESSAGE

    # ── re-checagem tardia: um humano pode ter assumido durante a chamada ao LLM ──
    if not reply:
        # Sem texto a enviar: escalação duplicada ou o modelo devolveu reply vazia. Nos
        # dois casos o silêncio tem dono registrado.
        session["skip_reason"] = motivo_do_silencio or (
            SkipReason.SESSION_OFF if session["bot_disabled"] else SkipReason.OK
        )
    elif await _still_unassigned(db, tenant_id, conversation_id):
        bolhas = llm.split_bubbles(reply)
        for i, bubble in enumerate(bolhas):
            if not await _send_and_persist(
                db,
                tenant_id,
                conversation_id,
                conv,
                bubble,
                delay_ms=_typing_delay(bubble, i),
            ):
                # Insistir nas próximas é gastar tentativa contra o mesmo canal que acabou
                # de recusar — e, se uma delas passasse, o cliente receberia a resposta com
                # um buraco no meio.
                if await _desviar_por_falha_de_envio(
                    db, tenant_id, conversation_id, session, bolhas[i:]
                ):
                    resumo_encaminhamento = MOTIVO_FALHA_DE_ENVIO
                break
        else:
            session["skip_reason"] = SkipReason.OK
    else:
        # Capturada no meio da chamada ao LLM — a resposta é descartada de propósito.
        session["skip_reason"] = SkipReason.CLAIMED

    # Depois do texto, nunca antes: as fotos chegam atrás do "olha só 😊" que as anuncia.
    if await _still_unassigned(db, tenant_id, conversation_id):
        await salao_acoes.executar(db, tenant_id, conversation_id, conv, session, acoes)

    await _save_session(db, conversation_id, session)
    await db.commit()
    await _publish(redis_client, tenant_id, conversation_id, conv.channel)
    if resumo_encaminhamento:
        await _publish_handoff(
            redis_client, tenant_id, conversation_id, conv, resumo_encaminhamento
        )


def _ultima_do_cliente(history: list[dict]) -> str:
    """A fala mais recente do cliente — é ela que o roteiro interpreta no nó atual.

    Sai do `history` que já foi carregado, e não de uma query nova: `load_history` é quem
    sabe filtrar nota interna e incluir áudio transcrito, e uma segunda implementação
    disso divergiria na primeira vez que a regra mudasse.
    """
    for h in reversed(history):
        if h.get("role") == "user":
            return str(h.get("content") or "")
    return ""


# ─────────────────────────── falha da IA / fallback ───────────────────────────


async def _handle_ai_failure(
    db: AsyncSession,
    redis_client,
    tenant_id: UUID,
    conversation_id: UUID,
    conv,
    session: dict,
    bot: BotConfig,
) -> None:
    """Um blip não joga o cliente pra fila na 1ª falha: avisa, AGENDA a re-tentativa e,
    se ela também falhar, encaminha na hora.

    A versão anterior avisava "já te respondo" e dependia do cliente mandar mais duas
    mensagens para o contador chegar ao handoff — a 2ª falha não enviava nada. Resultado
    observado em teste real: o bot prometeu voltar, ficou mudo e a conversa morreu. A
    promessa agora tem quem a cumpra (tasks.bot.retry_bot_turn) e não existe mais estado
    intermediário silencioso.
    """
    session["ai_failures"] += 1
    session["skip_reason"] = SkipReason.LLM_DOWN
    escalou_agora = False
    log.warning(
        "bot: LLM indisponível na conversa %s (falha %s de %s)",
        conversation_id,
        session["ai_failures"],
        AI_MAX_FAILURES,
    )
    if session["ai_failures"] >= AI_MAX_FAILURES:
        # Se a conversa JÁ foi encaminhada, o cliente já ouviu que a equipe vai assumir:
        # repetir a frase de transferência agora é a mesma mensagem duas vezes, que é
        # justamente o que o cliente reclamou no teste real. Ele recebe o aviso de
        # instabilidade, e a nota interna sai só na escalação — numa conversa encaminhada
        # cada pergunta durante a queda gravaria outra nota igual.
        ja_encaminhado = session["handoff"]
        escalou_agora = not ja_encaminhado
        session["handoff"] = True
        session["bot_disabled"] = True
        # COM prazo: esta causa é acidente, não decisão. É o que faz o bot voltar sozinho
        # quando o provedor se recupera, em vez de a conversa ficar sem agente para sempre.
        session["bot_disabled_until"] = datetime.now(timezone.utc) + timedelta(
            seconds=AI_COOLDOWN_SECONDS
        )
        if escalou_agora:
            await _insert_internal_note(
                db,
                tenant_id,
                conversation_id,
                "Triagem por IA indisponível (falhas consecutivas). Atendimento manual "
                "necessário. O agente volta na próxima mensagem do cliente ou sozinho em "
                f"{AI_COOLDOWN_SECONDS // 60} min.",
            )
        aviso = INSTABILIDADE_MESSAGE if ja_encaminhado else bot.transfer_message
        if await _still_unassigned(db, tenant_id, conversation_id):
            await _send_and_persist(
                db,
                tenant_id,
                conversation_id,
                conv,
                aviso,
                delay_ms=_typing_delay(aviso),
            )
    else:
        # 1ª falha: avisa (uma vez só) e agenda a re-tentativa. O agendamento acontece
        # mesmo se o aviso já tiver sido enviado antes — é ele que cumpre a promessa.
        if not session["variables"].get("wait_notice_sent"):
            session["variables"]["wait_notice_sent"] = True
            if await _still_unassigned(db, tenant_id, conversation_id):
                await _send_and_persist(
                    db,
                    tenant_id,
                    conversation_id,
                    conv,
                    WAIT_MESSAGE,
                    delay_ms=_typing_delay(WAIT_MESSAGE),
                )
        _schedule_retry(tenant_id, conversation_id)

    await _save_session(db, conversation_id, session)
    await db.commit()
    await _publish(redis_client, tenant_id, conversation_id, conv.channel)
    if escalou_agora:
        await _publish_handoff(
            redis_client, tenant_id, conversation_id, conv, MOTIVO_IA_INDISPONIVEL
        )


def _decisao_de_midia(session: dict, content_type: str) -> dict:
    """A decisão do modo agente para mídia que o modelo não lê. Sem chamar o modelo.

    Monta o mesmo dicionário que `llm.agent_turn` devolveria, e é só por isso que o resto
    do turno — bolhas com cadência, handoff atômico, nota de triagem, persistência — é o
    código de sempre. Gastar uma chamada de IA para dizer "me conta em texto" seria
    desperdício, e inventar um caminho de envio paralelo seria pior.

    A segunda mídia seguida encaminha. O limite existe porque a alternativa testada em
    produção era pior dos dois lados: responder a mesma frase a cada foto irrita, e calar
    depois da primeira abandona quem mandou a foto justamente por não saber escrever o
    que queria. Quem escreve texto zera o contador (ver `_handle`).
    """
    vistas = int(session["variables"].get("midias_seguidas") or 0) + 1
    session["variables"]["midias_seguidas"] = vistas

    if vistas >= MAX_MIDIAS_SEGUIDAS:
        return {
            "reply": "",  # vira `bot.transfer_message` no handoff
            "captured": {},
            "status": "ready_to_handoff",
            "summary": (
                f"O cliente mandou {midia_txt.rotulo(content_type)} e o bot "
                "não lê esse tipo de mensagem. Vale abrir o que ele enviou."
            ),
            "priority": "normal",
            "sentiment": "neutral",
        }
    return {
        "reply": midia_txt.texto(content_type) + MEDIA_CONVITE,
        "captured": {},
        "status": "gathering",
        "summary": "",
        "priority": "normal",
        "sentiment": "neutral",
    }


def _schedule_retry(tenant_id: UUID, conversation_id: UUID) -> None:
    """Agenda a re-tentativa do turno. Best-effort: se o Celery estiver fora, não pode
    derrubar o fluxo que já enviou o aviso ao cliente."""
    # Import tardio: app.tasks.bot importa o orquestrador no retry, e no topo seria ciclo.
    try:
        from app.tasks.bot import RETRY_DELAY_SECONDS, retry_bot_turn

        retry_bot_turn.apply_async(
            args=[str(tenant_id), str(conversation_id)],
            countdown=RETRY_DELAY_SECONDS,
        )
    except Exception as exc:
        log.warning(
            "bot: falha ao agendar re-tentativa da conversa %s: %s",
            conversation_id,
            exc,
        )


async def _apply_triage(
    db: AsyncSession, tenant_id: UUID, conversation_id: UUID, decision: dict
) -> None:
    """No handoff, grava prioridade e tags derivadas da triagem do bot na conversa."""
    tags: list[str] = []
    if decision["sentiment"] == "negative":
        tags.append("sentimento_negativo")
    if decision["priority"] == "high":
        tags.append("prioridade_alta")
    await db.execute(
        text(
            "UPDATE conversations SET priority = :priority, "
            "tags = (SELECT COALESCE(jsonb_agg(DISTINCT t), '[]'::jsonb) "
            "        FROM jsonb_array_elements_text(tags || CAST(:new_tags AS jsonb)) t) "
            "WHERE id = :conversation_id AND tenant_id = :tenant_id"
        ),
        {
            "priority": decision["priority"],
            "new_tags": json.dumps(tags),
            "conversation_id": str(conversation_id),
            "tenant_id": str(tenant_id),
        },
    )


def _build_intake_note(decision: dict, state: dict, por_fluxo: bool = False) -> str:
    """Nota interna de handoff: resumo + dados coletados + metadados, para o humano.

    O título diz qual cérebro triou. Um roteiro determinístico assinando "Triagem por IA"
    faria o atendente descontar a confiança errada: o que está aqui foi o cliente que
    digitou num menu, não um modelo que interpretou.
    """
    lines = ["Triagem pelo roteiro" if por_fluxo else "Triagem por IA"]
    if decision["summary"]:
        lines.append(f"— resumo:\n{decision['summary']}")
    dados = [
        f"- {k}: {v}"
        for k, v in state.items()
        if not k.startswith("_") and str(v).strip()
    ]
    if dados:
        lines.append("\nDados coletados:\n" + "\n".join(dados))
    meta = []
    if decision["priority"] == "high":
        meta.append("prioridade: alta")
    if decision["sentiment"] != "neutral":
        meta.append(f"sentimento: {decision['sentiment']}")
    if meta:
        lines.append(f"\n({' · '.join(meta)})")
    return "\n".join(lines)


# ─────────────────────────── acesso a dados ───────────────────────────


async def _load_conversation(db: AsyncSession, tenant_id: UUID, conversation_id: UUID):
    row = await db.execute(
        text(
            "SELECT c.assigned_user_id, c.status, ct.id AS contact_id, ct.name AS contact_name, "
            "ct.profile AS contact_profile, "
            "ct.external_id AS contact_external_id, ct.phone AS contact_phone, "
            "COALESCE(cc.channel, fb.channel) AS channel, "
            "COALESCE(cc.id, fb.id) AS channel_connection_id, "
            "COALESCE(cc.credentials, fb.credentials)->>'instance' AS instance_key "
            "FROM conversations c "
            "JOIN contacts ct ON ct.id = c.contact_id "
            + JOIN_CONEXAO
            + "WHERE c.id = :conversation_id AND c.tenant_id = :tenant_id"
        ),
        {"conversation_id": str(conversation_id), "tenant_id": str(tenant_id)},
    )
    return row.fetchone()


async def _still_unassigned(
    db: AsyncSession, tenant_id: UUID, conversation_id: UUID
) -> bool:
    row = await db.execute(
        text(
            "SELECT assigned_user_id, status FROM conversations "
            "WHERE id = :conversation_id AND tenant_id = :tenant_id"
        ),
        {"conversation_id": str(conversation_id), "tenant_id": str(tenant_id)},
    )
    r = row.fetchone()
    return r is not None and r.assigned_user_id is None and r.status != "RESOLVED"


async def _mensagens_do_cliente(
    db: AsyncSession, tenant_id: UUID, conversation_id: UUID
) -> int:
    """Quantas mensagens o cliente mandou nesta conversa, contadas no banco."""
    row = await db.execute(
        text(
            "SELECT count(*) FROM messages "
            "WHERE conversation_id = :conversation_id AND tenant_id = :tenant_id "
            "AND direction = :direction"
        ),
        {
            "conversation_id": str(conversation_id),
            "tenant_id": str(tenant_id),
            "direction": MessageDirection.INBOUND.name,
        },
    )
    return row.scalar_one()


async def _claim_handoff(
    db: AsyncSession, tenant_id: UUID, conversation_id: UUID
) -> bool:
    """Marca a conversa como encaminhada e devolve True só para quem ganhou a corrida.

    UPDATE condicional, mesma forma da captura de conversa (services/assignment.py) e da
    reserva de data: a condição vai no WHERE, nunca em Python. Ler `handoff` da sessão,
    concluir "ainda não escalou" e depois escrever é read-modify-write — e foi exatamente
    o bug do teste real: uma re-tentativa agendada e uma mensagem nova do cliente rodaram
    em paralelo, as duas leram `handoff = false` e o cliente recebeu duas mensagens de
    encaminhamento, com duas notas internas de triagem.

    Os turnos rodam em transações separadas (WorkerSessionLocal), então o UPDATE trava a
    linha: o segundo espera o commit do primeiro e sai com rowcount 0.

    O commit acontece AQUI, e não no fim do turno, de propósito: enviar as bolhas leva
    segundos (o Evolution segura a resposta HTTP durante o delay de "digitando", ver
    channels/whatsapp.py) e o lock da linha ficaria preso todo esse tempo, com o turno
    concorrente parado nele. Publicando a escalação de imediato, o outro turno descobre a
    verdade na hora e nem chega a bloquear.
    """
    result = await db.execute(
        text(
            "UPDATE bot_sessions SET handoff = true, updated_at = now() "
            "WHERE conversation_id = :conversation_id AND tenant_id = :tenant_id "
            "AND handoff = false"
        ),
        {"conversation_id": str(conversation_id), "tenant_id": str(tenant_id)},
    )
    if result.rowcount == 1:
        await db.commit()
        return True
    return False


async def _load_or_create_session(
    db: AsyncSession, tenant_id: UUID, conversation_id: UUID, *, fresh: bool
) -> dict:
    await db.execute(
        text(
            "INSERT INTO bot_sessions (conversation_id, tenant_id, variables) "
            "VALUES (:conversation_id, :tenant_id, '{}'::jsonb) "
            "ON CONFLICT (conversation_id) DO NOTHING"
        ),
        {"conversation_id": str(conversation_id), "tenant_id": str(tenant_id)},
    )
    # Prazo vencido reativa o bot AQUI, antes da leitura — e em UPDATE condicional, não
    # lendo o prazo em Python para decidir depois (regra 2: dois turnos podem rodar juntos
    # na mesma conversa, e o read-modify-write faria os dois zerarem o contador em cima um
    # do outro). `bot_disabled_until IS NOT NULL` é o que impede isto de reativar o que foi
    # desligado por DECISÃO: teto pós-handoff não tem prazo e não volta sozinho.
    #
    # Zerar `ai_failures` na MESMA sentença é obrigatório: sem isso o contador voltaria em 2
    # e a primeira falha isolada depois do prazo re-desativaria o bot na hora.
    #
    # `fresh` (mensagem nova do cliente, não re-tentativa) reativa mesmo dentro do prazo.
    # O prazo existe para a re-tentativa não martelar um provedor fora do ar; aplicado a
    # quem acabou de perguntar, deixava sem resposta justamente a dúvida que a mensagem
    # de encaminhamento convidou a fazer.
    await db.execute(
        text(
            "UPDATE bot_sessions SET bot_disabled = false, bot_disabled_until = NULL, "
            "ai_failures = 0, skip_reason = NULL, updated_at = now() "
            "WHERE conversation_id = :conversation_id AND tenant_id = :tenant_id "
            "AND bot_disabled = true AND bot_disabled_until IS NOT NULL "
            "AND (bot_disabled_until <= now() OR :fresh)"
        ),
        {
            "conversation_id": str(conversation_id),
            "tenant_id": str(tenant_id),
            "fresh": fresh,
        },
    )
    row = await db.execute(
        text(
            "SELECT variables, ai_failures, post_handoff_turns, handoff, bot_disabled, "
            "bot_disabled_until, skip_reason "
            "FROM bot_sessions WHERE conversation_id = :conversation_id"
        ),
        {"conversation_id": str(conversation_id)},
    )
    r = row.fetchone()
    return {
        "variables": dict(r.variables or {}),
        "ai_failures": r.ai_failures,
        "post_handoff_turns": r.post_handoff_turns,
        "handoff": r.handoff,
        "bot_disabled": r.bot_disabled,
        "bot_disabled_until": r.bot_disabled_until,
        "skip_reason": r.skip_reason,
    }


async def _save_session(db: AsyncSession, conversation_id: UUID, session: dict) -> None:
    await db.execute(
        text(
            "UPDATE bot_sessions SET variables = CAST(:variables AS jsonb), "
            "ai_failures = :ai_failures, post_handoff_turns = :post_handoff_turns, "
            "handoff = :handoff, bot_disabled = :bot_disabled, "
            "bot_disabled_until = :bot_disabled_until, "
            "skip_reason = :skip_reason, updated_at = now() "
            "WHERE conversation_id = :conversation_id"
        ),
        {
            "conversation_id": str(conversation_id),
            "variables": json.dumps(session["variables"], ensure_ascii=False),
            "ai_failures": session["ai_failures"],
            "post_handoff_turns": session["post_handoff_turns"],
            "handoff": session["handoff"],
            "bot_disabled": session["bot_disabled"],
            "bot_disabled_until": session.get("bot_disabled_until"),
            "skip_reason": session["skip_reason"],
        },
    )


async def _dentro_do_teto_diario(tenant_id: UUID) -> bool:
    """Consome um turno do orçamento diário do tenant. `False` quando estourou.

    Janela por dia do calendário (UTC) em vez de deslizante: o dono precisa entender o
    limite pelo extrato do provedor, e "500 por dia" é explicável — "500 nas últimas 24h
    corridas" não é. A contagem é no Redis, e não no banco, porque um turno bloqueado não
    pode custar uma escrita.
    """
    if settings.AI_MAX_TURNS_DIA <= 0:
        return True
    dia = datetime.now(timezone.utc).strftime("%Y%m%d")
    return await ratelimit.consumir(
        f"ai:turnos:{tenant_id}:{dia}", settings.AI_MAX_TURNS_DIA, 60 * 60 * 26
    )


async def _skip(
    db: AsyncSession,
    tenant_id: UUID,
    conversation_id: UUID,
    reason: str,
    persist: bool = True,
) -> None:
    """Registra que o bot não falou e por quê. Best-effort: nunca atrapalha a ingestão.

    `persist=False` nos guards que rodam antes de existir sessão garantida (não-texto,
    IA desligada, mensagem antiga): ali só o log faz sentido, porque criar a linha em
    bot_sessions marcaria conversas em que o bot nunca chegou a ter vez.
    """
    log.info("bot: sem resposta na conversa %s (motivo=%s)", conversation_id, reason)
    await audit.record(
        db,
        tenant_id,
        "bot.skipped",
        "conversation",
        conversation_id,
        actor_type="bot",
        result="skipped",
        reason=reason,
    )

    if not persist:
        await db.commit()
        return
    await db.execute(
        text(
            "UPDATE bot_sessions SET skip_reason = :reason, updated_at = now() "
            "WHERE conversation_id = :conversation_id AND tenant_id = :tenant_id"
        ),
        {
            "reason": reason,
            "conversation_id": str(conversation_id),
            "tenant_id": str(tenant_id),
        },
    )
    await db.commit()


# ─────────────────────────── saída (canal + persistência) ───────────────────────────


async def _send_and_persist(
    db: AsyncSession,
    tenant_id: UUID,
    conversation_id: UUID,
    conv,
    content: str,
    delay_ms: int = 0,
) -> bool:
    """Envia e grava. Devolve se o cliente REALMENTE recebeu.

    O retorno não é decoração: quem chama para de mandar o resto das bolhas quando uma
    falha. Antes esta função engolia a falha e o laço seguia, então o turno terminava
    marcado `ok` com o cliente tendo lido metade da resposta.
    """
    if not await _still_unassigned(db, tenant_id, conversation_id):
        return False
    channel = Channel(conv.channel.lower())
    provider = get_provider(channel)
    outbound = OutboundMessage(
        channel=channel,
        channel_connection_id=conv.channel_connection_id,
        to_external_id=conv.contact_external_id,
        to_phone=conv.contact_phone,
        content_type=ContentType.TEXT,
        content=content,
        channel_metadata={"instance_key": conv.instance_key},
        delay_ms=delay_ms,
    )
    try:
        result = await provider.send(outbound)
    except Exception as exc:
        result = SendResult(success=False, error=f"{type(exc).__name__}: {exc}")

    success = result.success
    meta: dict = {"bot": True}
    if not success:
        # O provider JÁ devolvia o motivo em `SendResult.error`, e ele era descartado: a
        # mensagem ficava FAILED sem registro do porquê em lugar nenhum. Resposta não-2xx
        # não levanta exceção, então nem o log pegava — um "Connection Closed" do Evolution
        # (sessão do WhatsApp derrubada) aparecia na inbox como um "falhou" mudo, e
        # descobrir a causa exigia reproduzir o envio na mão. Guardar aqui é o lugar certo:
        # quem abre a conversa e quem lê o banco veem o mesmo motivo.
        meta["erro"] = (result.error or "sem detalhe do provider")[:MAX_ERRO_ENVIO]
        log.warning("bot: envio falhou (%s): %s", channel, meta["erro"])
    # `clock_timestamp()`, nunca `now()`. Em PostgreSQL `now()` é
    # `transaction_timestamp()` — CONGELADO no início da transação — e o turno inteiro é
    # uma transação só. As bolhas saem separadas por segundos de verdade (o `delay` do
    # Evolution segura o HTTP), mas as três gravavam o mesmo instante ao microssegundo, e
    # aí `ORDER BY created_at` não tem como desempatar: o cliente via o menu ANTES da
    # saudação. `clock_timestamp()` anda dentro da transação e grava a cadência real.
    # Vale para todo INSERT de mensagem outbound — inclusive a nota interna abaixo e as
    # fotos da galeria, que carregavam o timestamp do texto que as anuncia.
    await db.execute(
        text(
            "INSERT INTO messages "
            "(id, conversation_id, tenant_id, direction, content_type, content, "
            "external_id, channel, status, channel_metadata, created_at) "
            "VALUES (gen_random_uuid(), :conversation_id, :tenant_id, :direction, 'TEXT', "
            ":content, :external_id, :channel, :status, CAST(:meta AS jsonb), "
            "clock_timestamp())"
        ),
        {
            "conversation_id": str(conversation_id),
            "tenant_id": str(tenant_id),
            "direction": MessageDirection.OUTBOUND.name,
            "content": content,
            "external_id": result.external_id if success else None,
            "channel": conv.channel,
            "status": MessageStatus.SENT.name if success else MessageStatus.FAILED.name,
            "meta": json.dumps(meta),
        },
    )
    await db.execute(
        text(
            "UPDATE conversations SET last_message_at = now(), last_message_preview = :preview "
            "WHERE id = :conversation_id"
        ),
        {"preview": content[:120], "conversation_id": str(conversation_id)},
    )
    return success


async def _desviar_por_falha_de_envio(
    db: AsyncSession,
    tenant_id: UUID,
    conversation_id: UUID,
    session: dict,
    nao_entregues: list[str],
) -> bool:
    """Bolha que não saiu vira tarefa da equipe, nunca meia conversa em silêncio.

    Devolve `True` quando FOI ESTE turno que escalou a conversa — quem avisa a equipe
    precisa dessa resposta, e re-inferi-la em `_handle` seria ler duas vezes a corrida
    que `_claim_handoff` já arbitrou.

    Mesma doutrina do roteiro quebrado (`CLAUDE.md` §7): o que o sistema não consegue
    cumprir termina com gente avisada. Sem isto, o sintoma era o pior possível — a sessão
    do WhatsApp caía, o cliente recebia só a primeira bolha, e o turno era gravado como
    `ok`. Ninguém era chamado e o estado do fluxo tinha avançado assim mesmo, então o bot
    passava a esperar resposta de um menu que o cliente nunca viu.

    O estado do fluxo NÃO é revertido de propósito: `flow_store.avancar` é o UPDATE
    condicional que arbitra dois turnos simultâneos, e desfazê-lo aqui reabriria essa
    corrida para consertar um sintoma que a fila já resolve.
    """
    session["skip_reason"] = SkipReason.SEND_FAILED
    faltou = "\n".join(f"- {b[:MAX_PREVIA_NOTA]}" for b in nao_entregues if b.strip())
    await _insert_internal_note(
        db,
        tenant_id,
        conversation_id,
        "O envio falhou e o cliente NÃO recebeu o resto da resposta.\n"
        f"Ficou sem entregar:\n{faltou}\n"
        "Confira a conexão em Configurações › WhatsApp e retome daqui — o cliente parou "
        "de receber neste ponto.",
    )
    if not session["handoff"] and await _claim_handoff(db, tenant_id, conversation_id):
        session["handoff"] = True
        return True
    return False


async def _insert_internal_note(
    db: AsyncSession, tenant_id: UUID, conversation_id: UUID, note: str
) -> None:
    """Nota interna: mensagem OUTBOUND marcada como internal — nunca vai ao cliente."""
    await db.execute(
        text(
            "INSERT INTO messages "
            "(id, conversation_id, tenant_id, direction, content_type, content, "
            "channel, channel_metadata, created_at) "
            "SELECT gen_random_uuid(), c.id, c.tenant_id, :direction, 'TEXT', :content, "
            "COALESCE(cc.channel, fb.channel), CAST(:meta AS jsonb), clock_timestamp() "
            "FROM conversations c "
            "JOIN contacts ct ON ct.id = c.contact_id "
            + JOIN_CONEXAO
            + "WHERE c.id = :conversation_id AND c.tenant_id = :tenant_id"
        ),
        {
            "conversation_id": str(conversation_id),
            "tenant_id": str(tenant_id),
            "direction": MessageDirection.OUTBOUND.name,
            "content": note,
            "meta": json.dumps({"internal": True, "bot": True}),
        },
    )


async def _publish(
    redis_client, tenant_id: UUID, conversation_id: UUID, channel: str
) -> None:
    """Notifica o front (WebSocket) que o bot respondeu na conversa."""
    await events.publish(
        redis_client,
        tenant_id,
        events.MESSAGE_OUTBOUND,
        {"conversation_id": str(conversation_id), "channel": channel, "bot": True},
    )


async def _publish_handoff(
    redis_client, tenant_id: UUID, conversation_id: UUID, conv, resumo: str
) -> None:
    """Avisa o front que esta conversa passou a precisar de gente.

    Publicado DEPOIS do commit e só por quem ganhou `_claim_handoff` — o mesmo UPDATE
    condicional que impede a nota de triagem duplicada impede aqui o aviso duplicado, de
    graça. Um evento separado, e não um campo em `message.outbound`, porque escalação
    também acontece em turno que não envia mensagem nenhuma (teto de gasto, falha de
    envio, IA fora do ar) — que é justamente quando o silêncio custa mais caro.
    """
    await events.publish(
        redis_client,
        tenant_id,
        events.CONVERSATION_HANDOFF,
        {
            "conversation_id": str(conversation_id),
            "channel": conv.channel,
            "contact_name": conv.contact_name or conv.contact_phone or "",
            "resumo": resumo,
        },
    )
