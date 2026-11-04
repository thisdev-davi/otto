from enum import Enum


class Channel(str, Enum):
    WHATSAPP = "whatsapp"
    INSTAGRAM = "instagram"
    EMAIL = "email"


class UserRole(str, Enum):
    """Papéis dentro de um tenant. ADMIN faz tudo; AGENT só trabalha a inbox.

    AGENT continua podendo listar os colegas (`GET /users`) — transferir conversa
    para alguém não é gerenciar equipe.
    """

    ADMIN = "admin"
    AGENT = "agent"


class ConversationStatus(str, Enum):
    OPEN = "open"
    PENDING = "pending"
    RESOLVED = "resolved"


class MessageDirection(str, Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class MessageStatus(str, Enum):
    SENT = "sent"
    DELIVERED = "delivered"
    READ = "read"
    FAILED = "failed"


class ContentType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    DOCUMENT = "document"
    AUDIO = "audio"
    STICKER = "sticker"


class ConversationEventAction(str, Enum):
    CLAIMED = "claimed"
    RELEASED = "released"
    TRANSFERRED = "transferred"
    RESOLVED = "resolved"
    REOPENED = "reopened"
