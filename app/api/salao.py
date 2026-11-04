"""Endpoints do derivado Salão de Festas: agenda, reservas e galeria do espaço.

Leitura para todo atendente, escrita de CADASTRO só para ADMIN — mesma assimetria (e mesmo
motivo) de `app/api/quick_replies.py`: preço e fotos são copy do negócio, mesma classe de
risco da persona da IA.

A exceção proposital é **reserva**: criar, confirmar e cancelar ficam abertos a qualquer
atendente. Quem atende o telefone e fecha um sábado não é necessariamente admin, e obrigar
a chamar o dono para registrar é o caminho mais curto para a agenda ficar desatualizada —
que é exatamente o que faz o agente informar valor de uma data já vendida.
"""

from datetime import date, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import TokenData, decode_token, get_current_user, require_admin
from app.core.database import get_session
from app.models.salao import ReservaStatus
from app.schemas import (
    AgendaDataInSchema,
    AgendaDataSchema,
    AgendaListSchema,
    AgendaLoteInSchema,
    AgendaLoteResultSchema,
    ConfirmarReservaRequest,
    ConfirmarReservaResponse,
    FotoEspacoListSchema,
    FotoEspacoSchema,
    FotoEspacoUpdateSchema,
    ReservaDetalheSchema,
    ReservaInSchema,
    ReservaListSchema,
    ReservaSchema,
)
from app.models.enums import ContentType
from app.services import (
    media_links,
    salao_agenda,
    salao_aviso,
    salao_fotos,
    salao_resumo,
)
from app.services.attachments import mimetype_para_servir
from app.services import audit, media_response

router = APIRouter(tags=["salao"])

# Janela padrão quando a tela não manda intervalo: o mês corrente mais folga nas pontas.
_JANELA_PADRAO_DIAS = 45


# ─────────────────────────── agenda ───────────────────────────


@router.get("/agenda")
async def listar_agenda(
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    de: Annotated[date | None, Query()] = None,
    ate: Annotated[date | None, Query()] = None,
) -> AgendaListSchema:
    inicio = de or date.today()
    fim = ate or (inicio + timedelta(days=_JANELA_PADRAO_DIAS))
    if fim < inicio:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Intervalo inválido: 'ate' é anterior a 'de'.",
        )
    linhas = await salao_agenda.listar(session, current_user.tenant_id, inicio, fim)
    # `listar` expira pré-reservas vencidas; sem commit, a próxima leitura refaria o
    # trabalho e a tela mostraria como ativa uma reserva que já caiu.
    await session.commit()
    return AgendaListSchema(datas=[AgendaDataSchema(**linha) for linha in linhas])


@router.put("/agenda/{data}")
async def definir_data(
    data: date,
    body: AgendaDataInSchema,
    current_user: Annotated[TokenData, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgendaDataSchema:
    if body.preco_centavos is not None and body.preco_centavos < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Valor não pode ser negativo.",
        )
    await salao_agenda.definir(
        session,
        current_user.tenant_id,
        data,
        body.preco_centavos,
        body.bloqueada,
        body.observacao,
    )
    await session.commit()
    linhas = await salao_agenda.listar(session, current_user.tenant_id, data, data)
    await session.commit()
    return AgendaDataSchema(**linhas[0])


@router.post("/agenda/lote")
async def definir_lote(
    body: AgendaLoteInSchema,
    current_user: Annotated[TokenData, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AgendaLoteResultSchema:
    if body.ate < body.de:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Intervalo inválido: 'ate' é anterior a 'de'.",
        )
    if (body.ate - body.de).days > 730:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Intervalo máximo de 2 anos por lote.",
        )
    if any(d < 1 or d > 7 for d in body.dias_da_semana):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Dias da semana vão de 1 (segunda) a 7 (domingo).",
        )
    afetadas = await salao_agenda.definir_em_lote(
        session,
        current_user.tenant_id,
        body.de,
        body.ate,
        body.dias_da_semana,
        body.preco_centavos,
        body.bloqueada,
    )
    await session.commit()
    return AgendaLoteResultSchema(datas_afetadas=afetadas)


# ─────────────────────────── reservas ───────────────────────────


@router.get("/agenda/reservas")
async def listar_reservas(
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReservaListSchema:
    reservas = await salao_agenda.reservas_ativas(session, current_user.tenant_id)
    await session.commit()
    return ReservaListSchema(reservas=[ReservaSchema(**r) for r in reservas])


@router.post("/agenda/reservas", status_code=status.HTTP_201_CREATED)
async def criar_reserva(
    body: ReservaInSchema,
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReservaSchema:
    """Reserva feita por um humano (telefone, visita, indicação).

    `exigir_preco=False` e `criar_data_se_faltar=True`: quem fechou por telefone sabe o que
    combinou, e a data pode nem estar cadastrada ainda. O agente é que não pode segurar
    data sem valor — ele não tem como saber o que prometeu.
    """
    reserva, motivo = await salao_agenda.criar_reserva(
        session,
        current_user.tenant_id,
        body.data,
        status=ReservaStatus.CONFIRMADA
        if body.confirmar
        else ReservaStatus.PRE_RESERVADA,
        criada_por="humano",
        criada_por_user_id=current_user.user_id,
        nome_cliente=body.nome_cliente,
        telefone=body.telefone,
        observacao=body.observacao,
        exigir_preco=False,
        criar_data_se_faltar=True,
    )
    if reserva is None:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                salao_agenda.MOTIVO_OCUPADA: "Esta data já tem uma reserva ativa.",
                salao_agenda.MOTIVO_BLOQUEADA: "Esta data está bloqueada na agenda.",
                salao_agenda.MOTIVO_PASSADO: "Não é possível reservar uma data que já passou.",
            }.get(motivo, "Não foi possível reservar esta data."),
        )
    await session.commit()
    return ReservaSchema(**reserva)


@router.get("/agenda/reservas/{reserva_id}/resumo")
async def resumo_reserva(
    reserva_id: UUID,
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReservaDetalheSchema:
    """Contexto da reserva para quem vai decidir. Aberto a qualquer atendente, como
    confirmar e cancelar — quem decide precisa ver por que decidiria."""
    detalhe = await salao_resumo.de_reserva(session, current_user.tenant_id, reserva_id)
    if detalhe is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Reserva não encontrada."
        )
    # O aviso é renderizado aqui, junto do resumo, para o diálogo já abrir com o texto
    # pronto — e para que exista UMA implementação da frase, no servidor.
    detalhe["mensagem_confirmacao"] = await salao_aviso.montar_mensagem(
        session,
        current_user.tenant_id,
        {
            "data": detalhe["data"],
            "contact_id": detalhe.get("contact_id"),
            "nome_cliente": detalhe.get("nome_cliente"),
        },
    )
    detalhe["pode_avisar"] = detalhe["mensagem_confirmacao"] is not None
    # `resolver` expira pré-reservas vencidas; sem commit a próxima leitura refaria o
    # trabalho, e o resumo mostraria prazo em reserva que já caiu.
    await session.commit()
    return ReservaDetalheSchema(**detalhe)


@router.post("/agenda/reservas/{reserva_id}/confirmar")
async def confirmar_reserva(
    reserva_id: UUID,
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
    body: ConfirmarReservaRequest | None = None,
) -> ConfirmarReservaResponse:
    """Confirma e, se o corpo trouxer `mensagem`, avisa o cliente no WhatsApp.

    A ordem é obrigatória: confirmar e COMMITAR primeiro, enviar depois. Dois motivos.

    O `UPDATE` condicional de `salao_agenda.confirmar` só devolve a reserva para quem de
    fato virou o status — é ele, e nada mais, que impede o cliente de receber dois avisos
    quando alguém clica duas vezes.

    E o envio fica FORA da transação: segurar uma conexão de banco durante uma chamada de
    rede é ruim, e uma falha de envio não pode desfazer a confirmação. A data está mesmo
    segurada; o que se perde é o aviso, e isso volta em `aviso_enviado=False` para o
    atendente reenviar pela Inbox.
    """
    reserva = await salao_agenda.confirmar(session, current_user.tenant_id, reserva_id)
    if reserva is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pré-reserva não encontrada ou já confirmada/cancelada.",
        )
    await session.commit()

    aviso_enviado = False
    if body is not None and (body.mensagem or "").strip():
        aviso_enviado = await salao_aviso.enviar_confirmacao(
            session, current_user.tenant_id, reserva, body.mensagem
        )
        await session.commit()

    return ConfirmarReservaResponse(
        reserva=ReservaSchema(**reserva), aviso_enviado=aviso_enviado
    )


@router.post("/agenda/reservas/{reserva_id}/cancelar")
async def cancelar_reserva(
    reserva_id: UUID,
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ReservaSchema:
    reserva = await salao_agenda.cancelar(session, current_user.tenant_id, reserva_id)
    if reserva is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Reserva não encontrada ou já encerrada.",
        )
    await session.commit()
    return ReservaSchema(**reserva)


# ─────────────────────────── galeria do espaço ───────────────────────────

# auto_error=False: sem isso o HTTPBearer aborta com 401 antes de olharmos a query.
_bearer = HTTPBearer(auto_error=False)


@router.get("/espaco/fotos")
async def listar_fotos(
    current_user: Annotated[TokenData, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FotoEspacoListSchema:
    fotos = await salao_fotos.listar(session, current_user.tenant_id)
    return FotoEspacoListSchema(fotos=[FotoEspacoSchema(**f) for f in fotos])


@router.get("/espaco/fotos/{foto_id}/arquivo")
async def baixar_foto(
    request: Request,
    foto_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer)
    ] = None,
    exp: Annotated[int | None, Query()] = None,
    sig: Annotated[str | None, Query()] = None,
) -> Response:
    """Autoriza por assinatura na URL (`<img src>`) ou por Bearer.

    Substitui o `?token=<JWT>` que existia aqui: `<img src>` não manda header, mas a
    resposta para isso não precisa ser uma credencial de sessão inteira na query string.
    A assinatura vale para ESTA foto e por poucas horas. Ver `services/media_links.py`.
    """
    dono = await salao_fotos.tenant_de(session, foto_id)
    if dono is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    if not _pode_ver_foto(dono, foto_id, credentials, exp, sig):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    encontrada = await salao_fotos.caminho_de(session, dono, foto_id)
    if encontrada is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    caminho, mimetype = encontrada
    # O mimetype daqui é confiável (veio da nossa validação no upload, só imagem passa),
    # mas passa pelo mesmo filtro da mídia de conversa para que exista UMA regra de
    # "com que Content-Type servir arquivo", e não duas que podem divergir.
    media_type, pode_inline = mimetype_para_servir(mimetype, ContentType.IMAGE.name)
    headers = {
        "Cache-Control": "private",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
    }
    if not pode_inline:
        headers["Content-Disposition"] = "attachment"
    signed_access = media_links.validar(salao_fotos.TIPO_LINK, foto_id, dono, exp, sig)
    if not signed_access and not await audit.bind_media_identity(
        session, credentials, dono, request
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await audit.record(
        session,
        dono,
        "media.read",
        "salao_fotos",
        foto_id,
        actor_type="signed_link" if signed_access else "system",
        reason="signed_resource" if signed_access else "bearer_tenant",
    )
    await session.commit()
    return await media_response.serve(caminho, media_type, headers, request)


def _pode_ver_foto(
    dono: UUID,
    foto_id: UUID,
    credentials: HTTPAuthorizationCredentials | None,
    exp: int | None,
    sig: str | None,
) -> bool:
    if media_links.validar(salao_fotos.TIPO_LINK, foto_id, dono, exp, sig):
        return True
    if credentials is None:
        return False
    token_data = decode_token(credentials.credentials)
    return token_data is not None and token_data.tenant_id == dono


@router.post("/espaco/fotos", status_code=status.HTTP_201_CREATED)
async def subir_foto(
    current_user: Annotated[TokenData, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    file: Annotated[UploadFile, File()],
    titulo: Annotated[str, Form()] = "",
) -> FotoEspacoSchema:
    foto = await salao_fotos.criar(session, current_user.tenant_id, file, titulo)
    await session.commit()
    return FotoEspacoSchema(**foto)


@router.patch("/espaco/fotos/{foto_id}")
async def atualizar_foto(
    foto_id: UUID,
    body: FotoEspacoUpdateSchema,
    current_user: Annotated[TokenData, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> FotoEspacoSchema:
    foto = await salao_fotos.atualizar(
        session, current_user.tenant_id, foto_id, body.titulo, body.ordem, body.ativa
    )
    if foto is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await session.commit()
    return FotoEspacoSchema(**foto)


@router.delete("/espaco/fotos/{foto_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remover_foto(
    foto_id: UUID,
    current_user: Annotated[TokenData, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    removida = await salao_fotos.remover(session, current_user.tenant_id, foto_id)
    if not removida:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    await session.commit()
