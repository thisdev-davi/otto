"""Validação e gravação do anexo que o atendente envia.

Regra central: **o tipo é derivado do mimetype no servidor, nunca declarado pelo
cliente**. A rota JSON de mensagem aceitava `content_type` do corpo e o provider
ignorava — o resultado era gravar uma Message IMAGE e mandar o conteúdo como texto,
erro que não aparecia em log nenhum. Aqui o cliente manda bytes; quem decide o que
aquilo é somos nós.
"""

import mimetypes
import os
from uuid import UUID

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import storage
from app.models.enums import ContentType

MEGA = 1024 * 1024

# mimetype → (ContentType, teto em bytes).
#
# Os tetos de imagem/vídeo/áudio são os do próprio WhatsApp. O de DOCUMENTO é
# deliberadamente MENOR que o limite de 100 MB do WhatsApp: o envio vai em base64 no
# corpo do request para o Evolution, e 100 MB viram ~133 MB de JSON residente na
# memória de dois processos. Não "corrigir" para 100 sem trocar a estratégia de envio.
_ACEITOS: dict[str, tuple[ContentType, int]] = {
    "image/jpeg": (ContentType.IMAGE, 5 * MEGA),
    "image/png": (ContentType.IMAGE, 5 * MEGA),
    "image/webp": (ContentType.IMAGE, 5 * MEGA),
    "video/mp4": (ContentType.VIDEO, 16 * MEGA),
    "video/3gpp": (ContentType.VIDEO, 16 * MEGA),
    "audio/mpeg": (ContentType.AUDIO, 16 * MEGA),
    "audio/ogg": (ContentType.AUDIO, 16 * MEGA),
    "audio/mp4": (ContentType.AUDIO, 16 * MEGA),
    # Nota de voz gravada no navegador. É o que o Chrome produz (`audio/webm;codecs=opus`)
    # e não havia como recusar sem recusar a gravação inteira — Firefox dá ogg/opus e
    # Safari dá mp4, mas o Chrome não sabe gravar em nenhum dos dois.
    "audio/webm": (ContentType.AUDIO, 16 * MEGA),
    "audio/aac": (ContentType.AUDIO, 16 * MEGA),
    "audio/amr": (ContentType.AUDIO, 16 * MEGA),
    "application/pdf": (ContentType.DOCUMENT, 20 * MEGA),
    "application/msword": (ContentType.DOCUMENT, 20 * MEGA),
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (
        ContentType.DOCUMENT,
        20 * MEGA,
    ),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (
        ContentType.DOCUMENT,
        20 * MEGA,
    ),
    "text/plain": (ContentType.DOCUMENT, 20 * MEGA),
    "application/zip": (ContentType.DOCUMENT, 20 * MEGA),
}

_CHUNK = 64 * 1024

# Tipos que o navegador pode renderizar EM LINHA sem virar superfície de execução.
# Note que `text/plain` e `application/pdf` estão fora: ambos são aceitos como anexo,
# mas nenhum precisa ser exibido dentro da nossa origem para a inbox funcionar.
_INLINE_SEGUROS = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "video/mp4",
        "video/3gpp",
        "audio/mpeg",
        "audio/ogg",
        "audio/mp4",
        "audio/aac",
        "audio/amr",
    }
)

_OCTET_STREAM = "application/octet-stream"


def _normaliza_mimetype(bruto: str | None) -> str:
    """`audio/ogg; codecs=opus` → `audio/ogg`."""
    return (bruto or "").split(";")[0].strip().lower()


def teto_para(mimetype_bruto: str | None) -> int:
    """Teto em bytes para um mimetype. Tipo desconhecido cai no menor teto conhecido.

    Usado também na mídia que CHEGA (`channels/whatsapp.py::fetch_media`): o limite é o
    mesmo do anexo que sai, de propósito — dois conjuntos de limites acabam divergindo, e
    o lado esquecido vira o buraco.
    """
    aceito = _ACEITOS.get(_normaliza_mimetype(mimetype_bruto))
    return aceito[1] if aceito else 5 * MEGA


def mimetype_para_servir(
    mimetype_bruto: str | None, content_type: str | None
) -> tuple[str, bool]:
    """Decide com que `Content-Type` servir um arquivo. Devolve (mimetype, pode_inline).

    **O mimetype guardado não é confiável.** Na mídia que CHEGA ele vem do payload do
    canal — quem manda a mensagem escolhe o valor (`whatsapp.py::_parse_media` copia
    `media_obj["mimetype"]`). Servir isso de volta cru deixava um estranho gravar bytes
    de HTML anunciados como `text/html`: o atendente abria o anexo pela thread e a página
    do atacante executava NA ORIGEM DA API, com o token do atendente na própria URL.

    `X-Content-Type-Options: nosniff` não cobre esse caso — ele impede o navegador de
    ADIVINHAR o tipo, e aqui o tipo declarado já era o escolhido pelo atacante.

    Então: o valor só sobrevive se estiver na allow-list de `_ACEITOS` **e** for coerente
    com o `ContentType` que gravamos na Message. Qualquer outra coisa vira
    `application/octet-stream`, que nenhum navegador executa.
    """
    mimetype = _normaliza_mimetype(mimetype_bruto)
    aceito = _ACEITOS.get(mimetype)
    if aceito is None:
        return _OCTET_STREAM, False
    # Coerência: um `image/png` numa Message DOCUMENT é sinal de metadata adulterada.
    if content_type and aceito[0].name != content_type:
        return _OCTET_STREAM, False
    return mimetype, mimetype in _INLINE_SEGUROS


class Anexo:
    """Resultado da validação: bytes em memória mais o que decidimos sobre eles."""

    def __init__(
        self, data: bytes, filename: str, mimetype: str, content_type: ContentType
    ) -> None:
        self.data = data
        self.filename = filename
        self.mimetype = mimetype
        self.content_type = content_type


async def validar(file: UploadFile) -> Anexo:
    mimetype = _normaliza_mimetype(file.content_type)
    aceito = _ACEITOS.get(mimetype)
    if aceito is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Tipo de arquivo não suportado no WhatsApp: {mimetype or 'desconhecido'}",
        )
    content_type, teto = aceito

    # Lê em pedaços e corta no teto. `await file.read()` de uma vez não protege nada:
    # o Starlette já derramou o upload para disco acima de 1 MB, então sem este corte
    # o limite prático não é a memória, é o espaço livre do servidor.
    data = bytearray()
    while chunk := await file.read(_CHUNK):
        data.extend(chunk)
        if len(data) > teto:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"Arquivo acima do limite de {teto // MEGA} MB para este tipo",
            )
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Arquivo vazio"
        )

    return Anexo(
        data=bytes(data),
        filename=os.path.basename(file.filename or "arquivo"),
        mimetype=mimetype,
        content_type=content_type,
    )


async def gravar(
    anexo: Anexo,
    tenant_id: UUID,
    message_id: UUID,
    *,
    audit_session: AsyncSession | None = None,
) -> str:
    """Persist a backend-specific locator; existing local paths remain readable."""
    ext = mimetypes.guess_extension(anexo.mimetype) or ".bin"
    return await storage.put(
        f"{tenant_id}/{message_id}{ext}",
        anexo.data,
        anexo.mimetype,
        audit_session=audit_session,
    )
