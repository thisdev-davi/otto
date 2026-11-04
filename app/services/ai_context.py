"""Contexto de conversa compartilhado entre o bot automático e o copilot do atendente."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import retrieval
from app.models.enums import MessageDirection
from app.services import salao_contexto
from app.services.bot_settings import BotConfig, EmbeddingsTarget, resolve_embeddings

HISTORY_LIMIT = 24

# Teto de caracteres por mensagem no histórico. O WhatsApp aceita ~65 mil por mensagem, e
# o histórico ia inteiro para o prompt: uma mensagem só, repetida, enchia a janela de
# contexto — empurrando para fora o CONHECIMENTO e a persona, que é onde moram as regras.
# Custo por turno também é ditado por isto. 2000 cabe qualquer mensagem real de cliente.
MAX_CHARS_POR_MENSAGEM = 2000

# Teto da query da SEGUNDA tentativa de busca (ver `_falas_recentes`). Curto de propósito:
# query longa borra o vetor pelo mesmo motivo que chunk longo borra (docstring de
# `app/ai/chunking.py`) — o embedding é a média do que está dentro, e juntar cinco falas
# do cliente para achar uma resposta produz um vetor que não fica perto de nenhuma delas.
MAX_CHARS_QUERY = 300


async def load_history(
    db: AsyncSession, tenant_id: UUID, conversation_id: UUID, limit: int = HISTORY_LIMIT
) -> list[dict]:
    """Últimas mensagens da conversa como [{role, content}]. Ignora notas internas.

    Inclui AUDIO com content — áudio transcrito entra no contexto como texto.
    """
    rows = await db.execute(
        text(
            # O desempate por `id` importa mais aqui do que na tela: o histórico fora
            # de ordem é o que o PRÓPRIO bot lê no turno seguinte, e ver a própria
            # resposta antes da pergunta muda a decisão dele.
            "SELECT direction, content FROM ("
            "  SELECT id, direction, content, created_at FROM messages "
            "  WHERE conversation_id = :conversation_id AND tenant_id = :tenant_id "
            "    AND content IS NOT NULL AND content_type IN ('TEXT', 'AUDIO') "
            "    AND COALESCE(channel_metadata->>'internal', 'false') != 'true' "
            "  ORDER BY created_at DESC, id DESC LIMIT :limit"
            ") sub ORDER BY created_at ASC, id ASC"
        ),
        {
            "conversation_id": str(conversation_id),
            "tenant_id": str(tenant_id),
            "limit": limit,
        },
    )
    return [
        {
            "role": "user"
            if r.direction == MessageDirection.INBOUND.name
            else "assistant",
            "content": _trunca(r.content),
        }
        for r in rows.fetchall()
    ]


def _trunca(conteudo: str) -> str:
    if len(conteudo) <= MAX_CHARS_POR_MENSAGEM:
        return conteudo
    return conteudo[:MAX_CHARS_POR_MENSAGEM] + " […mensagem truncada]"


def _falas_recentes(history: list[dict], budget: int = MAX_CHARS_QUERY) -> str:
    """Últimas falas do CLIENTE em ordem cronológica, até caber no orçamento.

    Existe para a segunda tentativa de busca, quando a última mensagem sozinha não
    encontrou nada. Ninguém escreve no WhatsApp como quem preenche um campo de busca: a
    pergunta chega partida ("queria saber sobre o dia 12" / "de agosto") e cada pedaço
    dispara seu próprio turno (`bot_orchestrator._handle` não agrupa mensagens). O
    pedaço final vira uma query que não quer dizer nada sozinha.

    A fala mais recente entra SEMPRE, mesmo estourando o orçamento — assim esta função
    nunca devolve menos contexto que a query normal, e a segunda busca nunca é pior que
    a primeira por falta de texto.
    """
    falas: list[str] = []
    total = 0
    for h in reversed(history):
        if h["role"] != "user":
            continue
        texto = (h["content"] or "").strip()
        if not texto:
            continue
        if falas and total + len(texto) > budget:
            break
        falas.append(texto)
        total += len(texto)
    return " ".join(reversed(falas))


async def _buscar(
    db: AsyncSession, tenant_id: UUID, pergunta: str, target: EmbeddingsTarget | None
) -> list[dict]:
    """Busca RAG do turno, com o alvo de embeddings já resolvido.

    Sem target, chama mesmo assim com provedor/chave vazios: o embed falha, e é ESSA
    falha que aciona a busca textual em retrieval._keyword_search. Curto-circuitar aqui
    deixaria o RAG mudo em vez de degradado.
    """
    return await retrieval.search(
        db,
        tenant_id,
        pergunta,
        target.api_key if target else "",
        target.provider if target else "",
        top_k=5,
    )


async def build_knowledge(
    db: AsyncSession,
    tenant_id: UUID,
    bot: BotConfig,
    history: list[dict],
    contact_id: UUID | None = None,
    contact_phone: str | None = None,
) -> str:
    """Conhecimento do turno: inline (Fase 1) + chunks recuperados por RAG (Fase 2)."""
    knowledge = bot.knowledge.strip()
    if bot.use_rag and history:
        last_user_msg = next(
            (h["content"] for h in reversed(history) if h["role"] == "user"), ""
        )
        target = resolve_embeddings(bot)
        if last_user_msg:
            hits = await _buscar(db, tenant_id, last_user_msg, target)
            if not hits:
                # Nada encontrado: a pergunta pode ter chegado partida em mensagens, e o
                # pedaço final não se sustentar sozinho. Segunda tentativa com as falas
                # recentes juntas — SÓ aqui, porque juntar nem sempre ajuda ("oi" antes de
                # "cabe quanta gente?" é ruído) e no turno que já achou não há o que
                # ganhar. O corte de relevância (`embeddings.max_distance`) vale igual na
                # segunda busca: isto reformula a pergunta, não afrouxa o filtro.
                contexto = _falas_recentes(history)
                if contexto != last_user_msg:  # senão seria a mesma busca de novo
                    hits = await _buscar(db, tenant_id, contexto, target)
            if hits:
                dynamic = "\n\n".join(
                    f"{h['title'] or 'trecho'}: {h['content']}" for h in hits
                )
                knowledge = (
                    (knowledge + "\n\n" + dynamic).strip() if knowledge else dynamic
                )
    # Agenda do salão (derivado): datas e valores são fato transacional, não texto
    # estático — o RAG acima não sabe que o sábado 15/08 foi vendido ontem. Entra por
    # aqui, e não no orquestrador, porque este é o ponto comum do bot e do copilot: o
    # atendente humano recebe sugestão com a mesma agenda que o agente enxerga.
    # Devolve `knowledge` intocado se o tenant não usa agenda.
    #
    # `contact_id`/`contact_phone` trazem junto a reserva DESTE cliente. Pelo mesmo motivo
    # da agenda: é fato transacional, muda depois que o atendimento anterior terminou, e o
    # resumo estático guardado no contato envelhece — dizia "aguardando confirmação"
    # enquanto a reserva já estava confirmada.
    return await salao_contexto.aumentar(
        db, tenant_id, knowledge, history, contact_id, contact_phone
    )
