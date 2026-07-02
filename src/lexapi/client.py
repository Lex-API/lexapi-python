"""Synchronous LexAPI client."""

from __future__ import annotations

import time
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


class LexAPI:
    """Synchronous LexAPI client.

    Args:
        api_key: LexAPI key (``lex_...``). Falls back to ``LEXAPI_API_KEY``.
            Keys are server-side secrets — never ship them in client-side code.
        base_url: API base URL (defaults to production).
        timeout: read/overall timeout in seconds, or a full ``httpx.Timeout``.
        connect_timeout: connect timeout in seconds (ignored when ``timeout``
            is an ``httpx.Timeout``).
        max_retries: retry budget for 429/502/503/504 and connect errors
            (default 3). ``0`` disables retries.
        retry_config: full :class:`~lexapi._retry.RetryConfig` override;
            takes precedence over ``max_retries``.
        user_agent_suffix: optional suffix appended to the
            ``lexapi-python/<version>`` User-Agent.
        transport: optional httpx transport (used for testing).
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
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._retry = make_retry_config(max_retries, retry_config)
        self._http = httpx.Client(
            transport=transport,
            **build_httpx_kwargs(
                api_key, base_url, timeout, connect_timeout, user_agent_suffix
            ),
        )

    # ── Meta ──────────────────────────────────────────────────────────
    def get_info(self) -> APIResponse:
        """GET /info — API capabilities, subscription tier, and current usage.

        Free operation (0 credits).
        """
        return self._request("GET", "/info")

    # ── Request core ──────────────────────────────────────────────────
    def _request(
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
                response = self._http.request(method, path, **request_kwargs)
            except httpx.HTTPError as exc:
                wait = exception_retry_wait(exc, attempt, self._retry)
                if wait is None:
                    raise transport_error(exc) from exc
                time.sleep(wait)
                attempt += 1
                continue
            wait = status_retry_wait(method, response, attempt, self._retry)
            if wait is None:
                return process_response(response, cast)
            time.sleep(wait)
            attempt += 1

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> LexAPI:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
