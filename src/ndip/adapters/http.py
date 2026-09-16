"""Shared outbound HTTP policy. Every external call in this project goes through here."""

from __future__ import annotations

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)
RETRYABLE = (httpx.TransportError, httpx.HTTPStatusError)


def build_client(
    *, timeout: httpx.Timeout | None = None, headers: dict[str, str] | None = None
) -> httpx.Client:
    """A client with an explicit timeout on every phase. There is no unbounded call."""
    return httpx.Client(
        timeout=timeout or DEFAULT_TIMEOUT,
        headers={
            "User-Agent": "ndip/0.1 (Nepal Disaster Intelligence Platform)",
            **(headers or {}),
        },
        follow_redirects=True,
    )


@retry(
    retry=retry_if_exception_type(RETRYABLE),
    stop=stop_after_attempt(4),
    wait=wait_exponential_jitter(initial=1.0, max=30.0),
    reraise=True,
)
def get_json(client: httpx.Client, url: str, params: dict[str, object] | None = None) -> dict:
    """GET returning JSON, with exponential backoff and jitter on transient failure."""
    response = client.get(url, params=params)
    response.raise_for_status()
    return response.json()
