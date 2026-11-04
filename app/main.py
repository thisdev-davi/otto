import asyncio
from contextlib import asynccontextmanager, suppress
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from app.core.config import settings
from app.core.database import engine
from app.api.audit import router as audit_router
from app.services import audit
from app.api.auth import router as auth_router
from app.api.webhooks import router as webhooks_router
from app.api.ws import router as ws_router
from app.api.inbox import router as inbox_router
from app.api.channels import router as channels_router
from app.api.bot import router as bot_router
from app.api.media import router as media_router
from app.api.reports import router as reports_router
from app.api.reports import users_router
from app.api.team import public_router as invites_router
from app.api.team import router as team_router
from app.api.quick_replies import router as quick_replies_router
from app.api.contatos import router as contatos_router
from app.api.salao import router as salao_router
from app.api.fluxo import router as fluxo_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    retention = asyncio.create_task(audit.retention_loop())
    try:
        yield
    finally:
        retention.cancel()
        with suppress(asyncio.CancelledError):
            await retention
        await engine.dispose()


app = FastAPI(
    title="Otto",
    description="Inbox de WhatsApp para pequenos negócios",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.ENV == "dev" else None,
    redoc_url="/redoc" if settings.ENV == "dev" else None,
    openapi_url="/openapi.json" if settings.ENV == "dev" else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(auth_router, prefix="/auth")
app.include_router(audit_router)
app.include_router(webhooks_router)
app.include_router(ws_router)
app.include_router(inbox_router)
app.include_router(channels_router)
app.include_router(bot_router)
app.include_router(media_router)
app.include_router(reports_router)
app.include_router(users_router)
# team_router usa prefixo /users e é admin-only; users_router (/users, /users/me) é de
# todos. Ordem importa: /users/me precisa casar antes de qualquer rota com parâmetro.
app.include_router(team_router)
app.include_router(invites_router)
app.include_router(quick_replies_router)
app.include_router(contatos_router)
# Derivado salão de festas: agenda de datas, reservas e galeria do espaço.
app.include_router(salao_router)
# Atendimento por fluxo (nível 1): o roteiro determinístico, sem IA.
app.include_router(fluxo_router)


@app.get("/", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "otto"}


@app.get("/health/ready", tags=["health"])
async def ready() -> dict[str, str]:
    from redis.asyncio import Redis
    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        await session.execute(text("SELECT 1 FROM audit_events LIMIT 1"))
    redis_client = Redis.from_url(settings.REDIS_URL)
    try:
        await redis_client.ping()
    finally:
        await redis_client.aclose()
    return {"status": "ready"}
