"""Normalize provider PostgreSQL URLs for each SQLAlchemy driver."""

from sqlalchemy.engine import make_url


def async_url(value: str) -> str:
    url = make_url(value).set(drivername="postgresql+asyncpg")
    query = dict(url.query)
    if "sslmode" in query:
        query["ssl"] = query.pop("sslmode")
    # libpq-specific option not understood by asyncpg. TLS remains mandatory when
    # requested by sslmode; asyncpg negotiates SCRAM independently.
    query.pop("channel_binding", None)
    if "-pooler." in (url.host or ""):
        query["prepared_statement_cache_size"] = "0"
    return url.set(query=query).render_as_string(hide_password=False)


def sync_url(value: str) -> str:
    url = make_url(value).set(drivername="postgresql+psycopg2")
    query = dict(url.query)
    if "ssl" in query:
        query["sslmode"] = query.pop("ssl")
    query.pop("prepared_statement_cache_size", None)
    query.pop("statement_cache_size", None)
    return url.set(query=query).render_as_string(hide_password=False)
