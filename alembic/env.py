from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

from app.models.core import Base

# Import só pelo efeito colateral de registrar as tabelas do derivado no Base.metadata.
# Sem ele, `alembic revision --autogenerate` não enxerga salao_datas/reservas/fotos e
# gera um DROP TABLE para cada uma — o autogenerate apaga o que não está no metadata.
from app.models import salao  # noqa: F401
from app.models import contatos  # noqa: F401
from app.models import fluxo  # noqa: F401
from app.models import audit  # noqa: F401
from app.core.config import settings
from app.core.database_urls import sync_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Use sync URL for Alembic (replace asyncpg driver with psycopg2)
_sync_url = sync_url(settings.DATABASE_DIRECT_URL or settings.DATABASE_URL)
# ConfigParser treats '%' in percent-encoded credentials as interpolation.
config.set_main_option("sqlalchemy.url", _sync_url.replace("%", "%%"))


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
