"""Fixtures compartilhadas da suíte."""

import pytest_asyncio

from app.core import ratelimit


@pytest_asyncio.fixture(autouse=True)
async def _zera_rate_limit():
    """Limpa os contadores de rate limit antes de cada teste.

    Sem isto a suíte inteira quebra, e por um motivo que é o limitador funcionando: o
    limite de login é de 5 por minuto POR IP, todos os testes chegam pelo mesmo cliente
    ASGI, e a partir do sexto login qualquer teste que autentique recebe 429.

    Zerar aqui mantém os outros testes falando do que eles testam. Quem testa o limite em
    si (`tests/test_ratelimit.py`) conta a partir do zero, que é justamente o que esta
    fixture garante.
    """
    try:
        redis_client = ratelimit._cliente()
        chaves = [chave async for chave in redis_client.scan_iter("otto:rl:*")]
        if chaves:
            await redis_client.delete(*chaves)
    except Exception:
        # Sem Redis o limitador falha aberto; a suíte não deve depender dele para rodar.
        pass
    yield


def pytest_sessionstart(session):
    """Refuse integration tests against a development/customer database."""
    from app.core.config import settings
    from sqlalchemy.engine import make_url
    import pytest

    url = make_url(settings.DATABASE_URL)
    if settings.ENV != "test" or "test" not in (url.database or "").lower():
        raise pytest.UsageError(
            "Use ENV=test and a dedicated DATABASE_URL with test in its database name; see docs/VALIDACAO.md."
        )


@pytest_asyncio.fixture(autouse=True)
async def _no_external_http(monkeypatch):
    import httpx

    original = httpx.AsyncHTTPTransport.handle_async_request

    async def local_only(transport, request):
        if request.url.host not in {
            "localhost",
            "127.0.0.1",
            "::1",
            "test",
            "testserver",
        }:
            raise RuntimeError("External HTTP disabled in the validation suite")
        return await original(transport, request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", local_only)
