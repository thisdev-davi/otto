from datetime import datetime
from uuid import UUID
from pydantic import BaseModel
from app.models.enums import Channel, ContentType, MessageStatus


class NormalizedStatusUpdate(BaseModel):
    """Atualização de status de entrega/leitura de uma mensagem já enviada."""

    channel: Channel
    external_id: str
    status: MessageStatus


class NormalizedInbound(BaseModel):
    channel: Channel
    instance_key: str
    external_id: str
    contact_external_id: str
    contact_name: str | None = None
    # Telefone do contato em dígitos, QUANDO o canal souber informá-lo.
    #
    # Separado do `contact_external_id` de propósito: aquele é o ENDEREÇO por onde se
    # responde, e nem sempre é um telefone — o WhatsApp passou a entregar identificadores
    # opacos (`@lid`) como remetente. Tratar o endereço como telefone gravou
    # `21711730823330@lid` no campo telefone de uma reserva confirmada, e o dono do salão
    # ficou sem como ligar para o cliente.
    #
    # `None` quando o canal não sabe. Nunca deduzir um número do endereço.
    contact_phone: str | None = None
    content_type: ContentType
    content: str | None = None
    channel_metadata: dict = {}
    timestamp: datetime


class OutboundMedia(BaseModel):
    """Anexo de saída, em vocabulário neutro de canal.

    `data` são os bytes CRUS de propósito. Base64, URL pública, multipart ou upload
    prévio são decisões de TRANSPORTE de cada provider — se este modelo carregasse
    base64, o núcleo já estaria falando Evolution, e trocar de provider deixaria de
    ser trocar a classe registrada no factory.
    """

    data: bytes
    filename: str
    mimetype: str
    # Nota de voz (push-to-talk), não arquivo de áudio. É a diferença entre o balão com
    # onda e play que o WhatsApp trata como voz, e um anexo com nome de arquivo.
    #
    # Vocabulário neutro de propósito: "é uma nota de voz" é um fato sobre a MENSAGEM,
    # não sobre o Evolution. Como cada provider entrega isso — outro endpoint, outra
    # flag, ou nada — é decisão dele.
    ptt: bool = False


class OutboundMessage(BaseModel):
    channel: Channel
    channel_connection_id: UUID
    to_external_id: str
    # Telefone do destinatário em dígitos, QUANDO conhecido — o par de saída do
    # `NormalizedInbound.contact_phone`, e pelo mesmo motivo: `to_external_id` é o
    # ENDEREÇO que o canal entregou, e ele nem sempre é entregável de volta.
    #
    # Quem decide se usa um ou outro é o provider, nunca o núcleo: qual formato de
    # endereço roteia e qual não roteia é conhecimento de canal (regra 1 do CLAUDE.md).
    # Aqui isto é só "o telefone, se soubermos".
    to_phone: str | None = None
    content_type: ContentType
    # Com `media` preenchido, isto vira a legenda do anexo — e legenda é opcional.
    content: str = ""
    media: OutboundMedia | None = None
    channel_metadata: dict = {}
    # Pausa antes de enviar, com indicador de "digitando" quando o canal suportar.
    # Zero por padrão de propósito: mensagem escrita por atendente humano sai na hora —
    # quem precisa parecer humano é o bot, que hoje dispara as bolhas em rajada e é
    # justamente esse padrão que arrisca ban numa conexão não-oficial.
    delay_ms: int = 0


class SendResult(BaseModel):
    success: bool
    external_id: str | None = None
    error: str | None = None


class ChannelStatus(BaseModel):
    # connected | connecting | qr_pending | disconnected | error
    state: str
    qr_code: str | None = None  # data URL base64 para pareamento (WhatsApp)
    detail: str | None = None
