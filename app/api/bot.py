"""Endpoints de configuração do agente de IA e da base de conhecimento (RAG)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import embeddings, llm, retrieval
from app.api.auth import TokenData, get_current_user, require_admin
from app.core.config import settings
from app.core.database import get_session
from app.schemas import (
    BotPingSchema,
    BotSettingsSchema,
    BotSettingsUpdateSchema,
    KbSourceListSchema,
    KbSourceSchema,
    RagAnswerInSchema,
    RagAnswerSchema,
    RagSourceSchema,
)
from app.services import flow_store, kb
from app.services.bot_settings import (
    DEFAULT_CONFIRMACAO_MESSAGE,
    DEFAULT_TRANSFER_MESSAGE,
    MODO_FLUXO,
    MODOS,
    BotConfig,
    load_bot_config,
    mask_secret,
    resolve_embeddings,
    save_bot_config,
)

# Guarda no router inteiro, não rota a rota: configurar persona, chave de API e base de
# conhecimento muda o comportamento do bot para o negócio todo. Rota nova aqui nasce
# protegida — é o ponto de esquecer uma guarda individual.
router = APIRouter(prefix="/bot", tags=["bot"], dependencies=[Depends(require_admin)])

_RAG_SYSTEM = (
    "Você responde dúvidas de clientes usando SOMENTE os TRECHOS de contexto fornecidos. "
    "Se a resposta não estiver nos trechos, diga que não tem essa informação e ofereça "
    "encaminhar a um atendente. NUNCA invente. Responda em português do Brasil, de forma "
    "calorosa e direta."
)


async def _to_schema(
    cfg: BotConfig, session: AsyncSession, tenant_id: UUID
) -> BotSettingsSchema:
    target = resolve_embeddings(cfg)
    # O painel precisa saber se existe roteiro ANTES de deixar ligar o modo fluxo. Sem
    # isso, ligar sem ter publicado nada seria um bot mudo sem nenhum aviso na tela.
    publicado = await flow_store.carregar(session, tenant_id)
    return BotSettingsSchema(
        ai_enabled=cfg.ai_enabled,
        persona=cfg.persona,
        knowledge=cfg.knowledge,
        provider=cfg.provider,
        model=cfg.model,
        api_key_masked=mask_secret(cfg.api_key),
        transfer_message=cfg.transfer_message,
        confirmacao_message=cfg.confirmacao_message,
        use_rag=cfg.use_rag,
        embeddings_ready=target is not None,
        # Provedor, nunca a chave — o front usa só para explicar o bloqueio ao usuário.
        embeddings_provider=target.provider if target else "",
        conteudo_gerenciado=cfg.conteudo_gerenciado,
        modo=cfg.modo,
        horario=cfg.horario,
        fluxo_publicado=bool(publicado and (publicado["grafo"] or {}).get("nos")),
    )


@router.get("/settings")
async def get_settings(
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BotSettingsSchema:
    cfg = await load_bot_config(session, current_user.tenant_id)
    return await _to_schema(cfg, session, current_user.tenant_id)


@router.put("/settings")
async def update_settings(
    body: BotSettingsUpdateSchema,
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BotSettingsSchema:
    if body.provider not in llm.PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Provider inválido. Use um de: {', '.join(llm.PROVIDERS)}",
        )
    if body.modo not in MODOS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Modo inválido. Use um de: {', '.join(MODOS)}",
        )
    # Ligar o modo fluxo sem roteiro publicado deixa o bot mudo e sem sintoma na tela.
    # Recusar aqui é o único momento em que dá para explicar o que falta.
    if body.ai_enabled and body.modo == MODO_FLUXO:
        publicado = await flow_store.carregar(session, current_user.tenant_id)
        if not publicado or not (publicado["grafo"] or {}).get("nos"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Esta conta ainda não tem um roteiro publicado. Publique o fluxo "
                    "antes de ligar o atendimento sem IA."
                ),
            )
    current = await load_bot_config(session, current_user.tenant_id)
    # Com o conteúdo vindo de arquivo, persona e conhecimento são de leitura: aceitar a
    # edição aqui a faria sumir na próxima sincronização, sem aviso. Recusar em voz alta
    # é melhor que perder o texto que alguém escreveu.
    if current.conteudo_gerenciado and (
        body.persona.strip() != current.persona.strip()
        or body.knowledge.strip() != current.knowledge.strip()
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Persona e conhecimento desta conta vêm de conteudo/persona.md e "
                "conteudo/conhecimento.md. Edite os arquivos e rode "
                "scripts/sincronizar_conteudo.py."
            ),
        )
    cfg = BotConfig(
        ai_enabled=body.ai_enabled,
        # Preserva o que está no banco: mesmo idênticos, é o arquivo que manda.
        persona=current.persona if current.conteudo_gerenciado else body.persona,
        knowledge=current.knowledge if current.conteudo_gerenciado else body.knowledge,
        conteudo_gerenciado=current.conteudo_gerenciado,
        provider=body.provider,
        model=body.model,
        # api_key None = mantém a atual; string (mesmo vazia) = substitui
        api_key=current.api_key if body.api_key is None else body.api_key,
        transfer_message=body.transfer_message or DEFAULT_TRANSFER_MESSAGE,
        confirmacao_message=body.confirmacao_message or DEFAULT_CONFIRMACAO_MESSAGE,
        use_rag=body.use_rag,
        modo=body.modo,
        horario=current.horario if body.horario is None else body.horario,
    )
    await save_bot_config(session, current_user.tenant_id, cfg)
    return await _to_schema(cfg, session, current_user.tenant_id)


@router.post("/ping")
async def ping_provider(
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BotPingSchema:
    cfg = await load_bot_config(session, current_user.tenant_id)
    ok, detail = await llm.ping(cfg.provider, cfg.model, cfg.api_key)
    return BotPingSchema(ok=ok, detail=detail)


# ─────────────────────────── base de conhecimento (Fase 2) ───────────────────────────
#
# Só leitura. Os documentos vêm de `conteudo/base/*.md` por
# `scripts/sincronizar_conteudo.py`, e as rotas de ingestão e remoção foram retiradas:
# escrever por aqui valia até a próxima sincronização sobrescrever ou ressuscitar a fonte,
# em silêncio. O front escondia os botões quando `conteudo_gerenciado`, mas a rota seguia
# aberta a qualquer chamada direta — apagar a rota é o que fecha isso de verdade.


@router.get("/kb/documents")
async def list_kb_documents(
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KbSourceListSchema:
    cfg = await load_bot_config(session, current_user.tenant_id)
    target = resolve_embeddings(cfg)
    current_model = embeddings.model_id(target.provider) if target else ""
    sources = await kb.list_sources(session, current_user.tenant_id)
    return KbSourceListSchema(
        sources=[
            KbSourceSchema(
                # Fonte embedada com outro modelo é invisível para a busca (o WHERE
                # exige igualdade). Marcar aqui é o que dá ao usuário como consertar:
                # re-ingerir. Sem a marca, a fonte apareceria na lista, contaria chunks
                # e nunca seria encontrada — o pior dos dois mundos.
                stale=bool(current_model) and s["embedding_model"] != current_model,
                **s,
            )
            for s in sources
        ]
    )


@router.post("/rag/answer")
async def rag_answer(
    body: RagAnswerInSchema,
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RagAnswerSchema:
    """Endpoint de teste do RAG: busca chunks e responde com citações."""
    cfg = await load_bot_config(session, current_user.tenant_id)
    # Sem target vai vazio de propósito: o embed falha e a busca degrada para texto
    # (retrieval._keyword_search), que é o que permite avaliar a base sem chave.
    target = resolve_embeddings(cfg)
    hits = await retrieval.search(
        session,
        current_user.tenant_id,
        body.question,
        target.api_key if target else "",
        target.provider if target else "",
        top_k=body.top_k,
    )
    if not hits:
        return RagAnswerSchema(
            answer="Não tenho essa informação na base. Posso te encaminhar a um atendente?",
            sources=[],
        )
    sources = [
        RagSourceSchema(
            title=h["title"],
            source_url=h["source_url"],
            score=round(1 - h["distance"], 3),
        )
        for h in hits
    ]
    prov = cfg.provider if cfg.provider != "stub" else settings.LLM_PROVIDER
    if prov == "stub":
        # Sem LLM configurado o playground ainda cumpre seu papel: avaliar a QUALIDADE
        # DA BUSCA. Devolve o trecho mais relevante cru em vez de uma mensagem de erro
        # — as outras entradas de LLM (agent_turn, ping, suggest_reply) já têm stub.
        return RagAnswerSchema(answer=hits[0]["content"], sources=sources)

    context = "\n\n".join(
        f"[{i + 1}] {h['title'] or 'trecho'} ({h['source_url'] or 's/ fonte'}):\n{h['content']}"
        for i, h in enumerate(hits)
    )
    mdl = (cfg.model or llm._default_model(prov)).strip()
    key = llm._resolve_key(prov, cfg.api_key)
    user = f"Contexto:\n{context}\n\nPergunta do cliente: {body.question}"
    raw = await llm._complete(_RAG_SYSTEM, user, 800, prov, mdl, key, json_mode=False)
    return RagAnswerSchema(
        answer=raw or "Desculpe, não consegui gerar a resposta agora.",
        sources=sources,
    )
