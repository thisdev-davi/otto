from abc import ABC, abstractmethod
from app.channels.types import (
    ChannelStatus,
    NormalizedInbound,
    OutboundMessage,
    SendResult,
)


class ChannelProvider(ABC):
    @abstractmethod
    def verify_webhook(self, headers: dict, body: bytes) -> bool: ...

    @abstractmethod
    def parse_webhook(self, headers: dict, body: bytes) -> list[NormalizedInbound]: ...

    @abstractmethod
    async def send(self, message: OutboundMessage) -> SendResult: ...

    async def provision(self, credentials: dict) -> dict:
        """Provisiona o canal no provider externo (ex.: cria instância no Evolution).

        Retorna as credentials, possivelmente enriquecidas. No-op por padrão —
        canais cujas credenciais já bastam não sobrescrevem.
        """
        return credentials

    async def get_status(self, credentials: dict) -> ChannelStatus:
        """Estado vivo da conexão e dados de pareamento (QR).

        Padrão: 'connected' quando há credenciais. Canais com handshake externo
        (WhatsApp via Evolution) sobrescrevem para refletir o estado real.
        """
        return ChannelStatus(state="connected")

    async def deprovision(self, credentials: dict) -> None:
        """Remove o recurso correspondente no provider externo. No-op por padrão."""
        return None

    async def mark_read(
        self, instance_key: str, to_external_id: str, message_ids: list[str]
    ) -> bool:
        """Marca no CANAL as mensagens como lidas (o tique azul para o cliente).

        Diferente de zerar `unread_count`, que é estado nosso: aqui quem passa a saber
        é o cliente, no aparelho dele. No-op por padrão — canal sem confirmação de
        leitura não deve precisar declarar nada.
        """
        return False

    async def fetch_avatar(
        self, credentials: dict, external_id: str
    ) -> tuple[bytes, str] | None:
        """`(bytes, mimetype)` da foto de perfil do contato, ou None. No-op por padrão.

        Devolve o ARQUIVO, nunca a URL do provider: quem chama guarda e serve por link
        assinado (`services/avatars.py`), e o núcleo não pode receber um endereço de
        CDN de canal para repassar ao navegador — seria o provider concreto vazando
        para fora de `channels/` pela porta dos fundos.
        """
        return None

    async def send_presence(
        self, instance_key: str, to_external_id: str, presence: str = "composing"
    ) -> bool:
        """Publica presença na conversa ('composing' = digitando). No-op por padrão.

        `presence` é vocabulário do canal por enquanto porque só há um valor em uso.
        Um segundo caso ('recording', 'paused') pede uma união fechada aqui — não uma
        string livre atravessando as camadas.
        """
        return False

    async def fetch_media(
        self, credentials: dict, message_metadata: dict
    ) -> tuple[bytes, str] | None:
        """Baixa a mídia de uma mensagem já ingerida: (bytes, mimetype).

        message_metadata é o channel_metadata persistido na Message (contém o
        payload bruto do canal). None quando o canal não suporta ou a mídia
        não está mais disponível.
        """
        return None
