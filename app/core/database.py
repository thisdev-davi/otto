from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool
from app.core.config import settings
from app.core.database_urls import async_url

# Engine da API: um event loop único e longo (uvicorn), então o pool vale a pena.
engine = create_async_engine(
    async_url(settings.DATABASE_URL),
    echo=settings.SQL_ECHO,
    hide_parameters=True,
    connect_args={"statement_cache_size": 0},
    pool_pre_ping=True,
)


class AuditedAsyncSession(AsyncSession):
    """Set transaction-local verified identity for database audit triggers.

    Triggers also cover ordinary AsyncSession instances, with system attribution.
    No DML SQL parsing or payload collection is performed here.
    """

    async def _audit_context(self) -> None:
        from app.services import audit

        request = self.info.get("audit_request")
        identity = getattr(request.state, "audit_identity", None) if request else None
        actor = audit.get_context()
        if identity is not None:
            actor = {
                "tenant_id": identity.tenant_id,
                "actor_type": "human",
                "actor_user_id": identity.user_id,
                "correlation_id": request.state.audit_correlation_id,
                "ip_network": audit.mask_ip(request.client.host)
                if request.client
                else None,
            }
        if actor["tenant_id"] is None:
            return
        context = (
            actor["actor_type"],
            str(actor["actor_user_id"] or ""),
            str(actor["correlation_id"]),
            actor["ip_network"] or "",
        )
        transaction = self.sync_session.get_transaction()
        if transaction is self.info.get(
            "audit_transaction"
        ) and context == self.info.get("audit_context"):
            return
        await super().execute(
            text(
                "SELECT set_config('otto.actor_type', :actor, true), "
                "set_config('otto.actor_user_id', :uid, true), "
                "set_config('otto.correlation_id', :cid, true), "
                "set_config('otto.ip_network', :ip, true)"
            ),
            {
                "actor": context[0],
                "uid": context[1],
                "cid": context[2],
                "ip": context[3],
            },
        )
        self.info["audit_transaction"] = self.sync_session.get_transaction()
        self.info["audit_context"] = context

    async def execute(self, statement: Any, params: Any = None, **kwargs: Any) -> Any:
        await self._audit_context()
        return await super().execute(statement, params=params, **kwargs)

    async def scalar(self, statement: Any, params: Any = None, **kwargs: Any) -> Any:
        await self._audit_context()
        return await super().scalar(statement, params=params, **kwargs)

    async def scalars(self, statement: Any, params: Any = None, **kwargs: Any) -> Any:
        await self._audit_context()
        return await super().scalars(statement, params=params, **kwargs)

    async def stream(self, statement: Any, params: Any = None, **kwargs: Any) -> Any:
        await self._audit_context()
        return await super().stream(statement, params=params, **kwargs)

    async def flush(self, objects: Any = None) -> None:
        await self._audit_context()
        await super().flush(objects)

    async def commit(self) -> None:
        await self._audit_context()
        await super().commit()


AsyncSessionLocal = async_sessionmaker(
    engine, class_=AuditedAsyncSession, expire_on_commit=False
)

# Engine do worker: NullPool, e a razão NÃO é performance.
#
# Cada task Celery roda num event loop próprio, criado e FECHADO por asyncio.run
# (ver app/tasks/runner.py). Uma conexão asyncpg fica amarrada ao loop em que nasceu;
# guardá-la num pool significa entregá-la à próxima task, já com o loop morto — o que
# estoura "Event loop is closed" / "attached to a different loop" a partir do SEGUNDO
# evento processado pelo processo. pool_pre_ping não salva: o próprio ping roda no loop
# morto, e RuntimeError não é erro de desconexão de DBAPI, então o SQLAlchemy não recicla
# a conexão. Com --pool=solo o worker fica envenenado até reiniciar, e o sintoma é
# mensagem de cliente que simplesmente não chega.
#
# NullPool abre uma conexão nova a cada checkout e a fecha no fim: nada sobrevive ao loop.
worker_engine = create_async_engine(
    async_url(settings.DATABASE_URL),
    poolclass=NullPool,
    hide_parameters=True,
    connect_args={"statement_cache_size": 0},
)

WorkerSessionLocal = async_sessionmaker(
    worker_engine, class_=AuditedAsyncSession, expire_on_commit=False
)


async def get_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    from app.services import audit

    if not hasattr(request.state, "audit_correlation_id"):
        request.state.audit_correlation_id = uuid4()
    token = audit.request_context.set(request)
    async with AsyncSessionLocal() as session:
        session.info["audit_request"] = request
        result = "success"
        try:
            yield session
        except Exception:
            result = "failure"
            raise
        finally:
            # Never commit pending business writes at dependency teardown.
            await session.rollback()
            identity = getattr(request.state, "audit_identity", None)
            if identity is not None:
                try:
                    async with AsyncSessionLocal() as access_session:
                        access_session.info["audit_request"] = request
                        endpoint = request.scope.get("endpoint")
                        action = "http." + getattr(endpoint, "__name__", "request")
                        resource_id = next(
                            (
                                str(value)
                                for value in request.path_params.values()
                                if len(str(value)) == 36
                            ),
                            None,
                        )
                        await audit.record(
                            access_session,
                            identity.tenant_id,
                            action,
                            "http",
                            resource_id,
                            result=result,
                            metadata={"method": request.method},
                        )
                        await access_session.commit()
                except Exception:
                    # Mutations already have fail-closed transaction triggers. A failed
                    # access-log write cannot turn a committed action into a retry.
                    import logging

                    logging.getLogger(__name__).exception("Audit access log failed")
            audit.request_context.reset(token)
