"""Minimal LexAPI client scaffold (P0: GET /info)."""

from __future__ import annotations

import os
from typing import Any

import httpx

from lexapi.errors import LexAPIError

DEFAULT_BASE_URL = "https://lex-api.com/api/v1"


class LexAPI:
    """Synchronous LexAPI client.

    Args:
        api_key: LexAPI key (``lex_...``). Falls back to ``LEXAPI_API_KEY``.
        base_url: API base URL (defaults to production).
        timeout: request timeout in seconds.
        transport: optional httpx transport (used for testing).
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        key = api_key or os.environ.get("LEXAPI_API_KEY")
        if not key:
            raise ValueError("api_key is required (or set LEXAPI_API_KEY)")
        from lexapi import __version__

        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers={
                "x-api-key": key,
                "user-agent": f"lexapi-python/{__version__}",
                "accept": "application/json",
            },
            transport=transport,
        )

    def get_info(self) -> dict[str, Any]:
        """GET /info — API capabilities, subscription tier, and current usage."""
        return self._request("GET", "/info")

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = self._http.request(method, path, **kwargs)
        if response.is_success:
            return response.json()
        try:
            body: Any = response.json()
        except ValueError:
            body = None
        retry_after: float | None = None
        header = response.headers.get("retry-after")
        if header is not None:
            try:
                retry_after = float(header)
            except ValueError:
                retry_after = None
        raise LexAPIError.from_response(response.status_code, body, retry_after=retry_after)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> LexAPI:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
