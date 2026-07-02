"""Asynchronous LexAPI client (mirrors :class:`lexapi.LexAPI`)."""

from __future__ import annotations

import asyncio
from typing import Any, TypeVar

import httpx

from lexapi._base import (
    DEFAULT_BASE_URL,
    build_httpx_kwargs,
    exception_retry_wait,
    make_retry_config,
    process_response,
    status_retry_wait,
    transport_error,
)
from lexapi._response import APIResponse
from lexapi._retry import RetryConfig

R = TypeVar("R", bound=APIResponse)


class AsyncLexAPI:
    """Asynchronous LexAPI client.

    Same configuration surface as :class:`lexapi.LexAPI`; every operation
    method is a coroutine. Shares all request-building, retry, and
    response-parsing logic with the sync client.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float | httpx.Timeout = 60.0,
        connect_timeout: float = 10.0,
        max_retries: int | None = None,
        retry_config: RetryConfig | None = None,
        user_agent_suffix: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._retry = make_retry_config(max_retries, retry_config)
        self._http = httpx.AsyncClient(
            transport=transport,
            **build_httpx_kwargs(
                api_key, base_url, timeout, connect_timeout, user_agent_suffix
            ),
        )

    # ── Meta ──────────────────────────────────────────────────────────
    async def get_info(self) -> APIResponse:
        """GET /info — API capabilities, subscription tier, and current usage.

        Free operation (0 credits).
        """
        return await self._request("GET", "/info")

    # ── Request core ──────────────────────────────────────────────────
    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        cast: type[R] = APIResponse,  # type: ignore[assignment]
        timeout: float | httpx.Timeout | None = None,
    ) -> R:
        request_kwargs: dict[str, Any] = {"params": params, "json": json}
        if timeout is not None:
            request_kwargs["timeout"] = timeout
        attempt = 0
        while True:
            try:
                response = await self._http.request(method, path, **request_kwargs)
            except httpx.HTTPError as exc:
                wait = exception_retry_wait(exc, attempt, self._retry)
                if wait is None:
                    raise transport_error(exc) from exc
                await asyncio.sleep(wait)
                attempt += 1
                continue
            wait = status_retry_wait(method, response, attempt, self._retry)
            if wait is None:
                return process_response(response, cast)
            await asyncio.sleep(wait)
            attempt += 1

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> AsyncLexAPI:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()
