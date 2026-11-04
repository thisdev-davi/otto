"""Records external effects before dispatch without retaining their payloads."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar
from uuid import UUID, uuid4

from sqlalchemy import text

from app.channels.base import ChannelProvider
from app.channels.types import (
    ChannelStatus,
    NormalizedInbound,
    OutboundMessage,
    SendResult,
)
from app.core.database import WorkerSessionLocal
from app.services import audit

log = logging.getLogger(__name__)
T = TypeVar("T")


class AuditedProvider(ChannelProvider):
    def __init__(self, provider: ChannelProvider) -> None:
        self.provider = provider

    def __getattr__(self, name: str) -> Any:
        return getattr(self.provider, name)

    def verify_webhook(self, headers: dict, body: bytes) -> bool:
        return self.provider.verify_webhook(headers, body)

    def parse_webhook(self, headers: dict, body: bytes) -> list[NormalizedInbound]:
        return self.provider.parse_webhook(headers, body)

    async def _operation(
        self,
        action: str,
        call: Callable[[], Awaitable[T]],
        connection_id: UUID | None = None,
    ) -> T:
        context = audit.get_context()
        tenant_id = context.pop("tenant_id")
        async with WorkerSessionLocal() as session:
            if tenant_id is None and connection_id is not None:
                tenant_id = await session.scalar(
                    text("SELECT tenant_id FROM channel_connections WHERE id=:id"),
                    {"id": connection_id},
                )
            if tenant_id is None:
                raise ValueError("External operation requires a verified tenant")
            operation_id = uuid4()
            context["correlation_id"] = context.get("correlation_id") or operation_id
            await audit.record(
                session,
                tenant_id,
                action,
                "channel",
                connection_id,
                result="pending",
                metadata={"operation_id": str(operation_id)},
                **context,
            )
            # A failed intent cancels dispatch. This is a separate committed transaction
            # because a network operation cannot participate in the caller's DB commit.
            await session.commit()

        async def outcome(result: str, reason: str | None = None) -> None:
            try:
                async with WorkerSessionLocal() as session:
                    await audit.record(
                        session,
                        tenant_id,
                        action,
                        "channel",
                        connection_id,
                        result=result,
                        reason=reason,
                        metadata={"operation_id": str(operation_id)},
                        **context,
                    )
                    await session.commit()
            except Exception as exc:
                # The effect may already exist remotely. Preserve the known result (or
                # original provider exception), never convert accepted sends into retry
                # candidates. The pending intent remains available for reconciliation.
                log.error("External outcome audit failed (%s)", type(exc).__name__)

        try:
            result = await call()
        except Exception:
            await outcome("unknown", "provider_exception")
            raise
        await outcome("accepted" if getattr(result, "success", True) else "failed")
        return result

    async def send(self, message: OutboundMessage) -> SendResult:
        return await self._operation(
            "channel.send",
            lambda: self.provider.send(message),
            message.channel_connection_id,
        )

    async def provision(self, credentials: dict) -> dict:
        return await self._operation(
            "channel.provision", lambda: self.provider.provision(credentials)
        )

    async def deprovision(self, credentials: dict) -> None:
        return await self._operation(
            "channel.deprovision", lambda: self.provider.deprovision(credentials)
        )

    async def get_status(self, credentials: dict) -> ChannelStatus:
        return await self.provider.get_status(credentials)

    async def mark_read(
        self, instance_key: str, to_external_id: str, message_ids: list[str]
    ) -> bool:
        return await self.provider.mark_read(instance_key, to_external_id, message_ids)

    async def fetch_avatar(
        self, credentials: dict, external_id: str
    ) -> tuple[bytes, str] | None:
        return await self.provider.fetch_avatar(credentials, external_id)

    async def send_presence(
        self, instance_key: str, to_external_id: str, presence: str = "composing"
    ) -> bool:
        return await self.provider.send_presence(instance_key, to_external_id, presence)

    async def fetch_media(
        self, credentials: dict, message_metadata: dict
    ) -> tuple[bytes, str] | None:
        return await self.provider.fetch_media(credentials, message_metadata)
