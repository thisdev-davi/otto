"""Embedding transport implemented against the providers' public REST contracts."""

import logging
import math

import httpx

log = logging.getLogger(__name__)
EMBED_DIM = 1536
PROVIDERS = ("openai", "gemini")
_TARGETS = {
    "openai": ("https://api.openai.com/v1/embeddings", "text-embedding-3-small", 0.55),
    "gemini": (
        "https://generativelanguage.googleapis.com/v1beta/openai/embeddings",
        "gemini-embedding-001",
        0.50,
    ),
}


def model_id(provider: str) -> str:
    return _TARGETS[provider][1] if provider in _TARGETS else ""


def max_distance(provider: str) -> float:
    # Application thresholds; quality must be evaluated for each knowledge base.
    return _TARGETS.get(provider, ("", "", 0.55))[2]


async def _request(
    provider: str, api_key: str, inputs: list[str], timeout: float
) -> list[list[float]] | None:
    target = _TARGETS.get(provider)
    if target is None or not api_key.strip() or not inputs:
        return None
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                target[0],
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "input": [item[:8000] for item in inputs],
                    "model": target[1],
                    "dimensions": EMBED_DIM,
                },
                timeout=timeout,
            )
        if response.status_code != 200:
            log.warning(
                "embedding request rejected provider=%s status=%d",
                provider,
                response.status_code,
            )
            return None
        indexed = {
            int(item["index"]): item["embedding"] for item in response.json()["data"]
        }
        if set(indexed) != set(range(len(inputs))):
            return None
        ordered = []
        for index in range(len(inputs)):
            vector = indexed[index]
            if not isinstance(vector, list) or len(vector) != EMBED_DIM:
                return None
            if any(
                isinstance(n, bool)
                or not isinstance(n, (float, int))
                or not math.isfinite(n)
                for n in vector
            ):
                return None
            ordered.append(vector)
        return ordered
    except (httpx.HTTPError, ValueError, KeyError, TypeError, OverflowError):
        log.warning("embedding response unavailable provider=%s", provider)
        return None


async def embed(text: str, api_key: str, provider: str) -> list[float] | None:
    batch = await _request(provider, api_key, [text], 15.0)
    return batch[0] if batch else None


async def embed_batch(
    texts: list[str], api_key: str, provider: str
) -> list[list[float]] | None:
    return await _request(provider, api_key, texts, 60.0)
