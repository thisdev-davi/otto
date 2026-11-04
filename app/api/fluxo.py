"""Endpoints do atendimento por fluxo: ler, validar, simular e editar TEXTOS do roteiro.

Arquivo novo (`docs/produto/PLATAFORMA.md` §1). O guard é no router inteiro, como em `api/bot.py`:
mexer no roteiro muda o atendimento do negócio inteiro, e rota nova aqui nasce
protegida em vez de depender de alguém lembrar da guarda.

**Não há rota que receba o grafo inteiro** (regra 7 do CLAUDE.md): a estrutura vem só de
`conteudo/fluxo.json`, pelo sincronizador. O painel edita texto em `PATCH /fluxo/textos`.

**Nada entra sem passar pelo validador.** É o mesmo `flow.schema.validar` que o
sincronizador de conteúdo roda — um só lugar decide o que é um roteiro publicável, e é
isso que sustenta "o fluxo que você desenha é o fluxo que roda".
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import TokenData, require_admin
from app.core.database import get_session
from app.flow import schema as flow_schema
from app.schemas import (
    FluxoSchema,
    FluxoSimulacaoInSchema,
    FluxoSimulacaoSchema,
    FluxoTextosSchema,
    FluxoUpdateSchema,
    FluxoValidacaoSchema,
)
from app.flow import engine as flow_engine
from app.services import flow_store
from app.services.bot_settings import load_bot_config
from app.services import salao_fluxo  # noqa: F401  registra os nós `agenda` e `acao`

router = APIRouter(
    prefix="/fluxo", tags=["fluxo"], dependencies=[Depends(require_admin)]
)


@router.get("", response_model=FluxoSchema)
async def obter_fluxo(
    user: Annotated[TokenData, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> FluxoSchema:
    publicado = await flow_store.carregar(db, user.tenant_id)
    if publicado is None:
        return FluxoSchema()
    return FluxoSchema(**publicado)


@router.post("/validar", response_model=FluxoValidacaoSchema)
async def validar_fluxo(
    body: FluxoUpdateSchema,
    user: Annotated[TokenData, Depends(require_admin)],
) -> FluxoValidacaoSchema:
    """Confere sem publicar. É o que um editor de fluxo chama a cada alteração."""
    erros = flow_schema.validar(body.grafo)
    return FluxoValidacaoSchema(ok=not erros, erros=erros)


@router.patch("/textos", response_model=FluxoSchema)
async def editar_textos(
    body: FluxoTextosSchema,
    user: Annotated[TokenData, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> FluxoSchema:
    """Edita o TEXTO das caixas. A estrutura do roteiro não passa por aqui.

    O corpo traz só o que mudou, e a alteração é aplicada sobre o grafo publicado **no
    servidor**. É isso que torna a edição pelo cliente segura: não existe requisição capaz
    de criar uma saída solta, apagar um ramo de motivo ou mudar o tipo de um nó.
    """
    atual = await flow_store.carregar(db, user.tenant_id)
    if not atual or not (atual["grafo"] or {}).get("nos"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Esta conta não tem roteiro publicado para editar.",
        )
    if atual["gerenciado_por_arquivo"]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Este roteiro vem de conteudo/fluxo.json. Para editar pelo painel, "
                "assuma a edição aqui — depois disso o sincronizador não sobrescreve mais "
                "sem --forcar."
            ),
        )

    novo, erros = flow_schema.aplicar_textos(atual["grafo"], body.model_dump())
    if erros:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=" · ".join(erros[:5]),
        )

    versao = await flow_store.salvar(db, user.tenant_id, novo, atual["nome"])
    await db.commit()
    return FluxoSchema(
        nome=atual["nome"], grafo=novo, versao=versao, gerenciado_por_arquivo=False
    )


@router.post("/assumir", response_model=FluxoSchema)
async def assumir_edicao(
    user: Annotated[TokenData, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> FluxoSchema:
    """Passa a fonte de verdade de `conteudo/fluxo.json` para o painel.

    Explícito de propósito: o sincronizador deixa de publicar neste tenant, e voltar atrás
    exige `--forcar`. Trocar isso num clique escondido faria alguém perder o texto do
    outro sem entender por quê.
    """
    if not await flow_store.assumir_no_painel(db, user.tenant_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Esta conta não tem roteiro publicado.",
        )
    await db.commit()
    atual = await flow_store.carregar(db, user.tenant_id)
    return FluxoSchema(**atual)


@router.post("/simular", response_model=FluxoSimulacaoSchema)
async def simular_fluxo(
    body: FluxoSimulacaoInSchema,
    user: Annotated[TokenData, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_session)],
) -> FluxoSimulacaoSchema:
    """Roda o roteiro contra mensagens de teste. Não envia nada e não escreve nada.

    A agenda é consultada de verdade — ver o preço real da data é o motivo de isto
    existir. Já a pré-reserva e o envio de fotos são desviados por `Contexto.simulacao`:
    quem está testando o roteiro não pode segurar a data de um cliente real.
    """
    if len(body.mensagens) > 40:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Simulação de no máximo 40 mensagens.",
        )
    bot = await load_bot_config(db, user.tenant_id)
    passos = await flow_engine.simular(
        db, user.tenant_id, bot, body.mensagens, grafo=body.grafo
    )
    return FluxoSimulacaoSchema(passos=passos)
