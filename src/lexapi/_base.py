"""Request core shared by the sync and async clients.

Everything here is transport-agnostic: header construction, response
parsing, error mapping, and retry decisions. The sync/async clients only
differ in how they send the request and how they sleep.
"""

from __future__ import annotations

import os
from typing import Any, TypeVar

import httpx

from lexapi._meta import user_agent
from lexapi._response import APIResponse
from lexapi._retry import (
    DEFAULT_RETRY_CONFIG,
    RetryConfig,
    compute_backoff,
    is_retryable_exception,
    retry_wait,
    server_wait_hint,
)
from lexapi.errors import (
    LexAPIConnectionError,
    RequestTimeoutError,
    error_from_response,
)

DEFAULT_BASE_URL = "https://lex-api.com/api/v1"

R = TypeVar("R", bound=APIResponse)


def resolve_api_key(api_key: str | None) -> str:
    key = api_key or os.environ.get("LEXAPI_API_KEY")
    if not key:
        raise ValueError("api_key is required (or set LEXAPI_API_KEY)")
    return key


def build_httpx_kwargs(
    api_key: str | None,
    base_url: str,
    timeout: float | httpx.Timeout,
    connect_timeout: float,
    user_agent_suffix: str | None,
) -> dict[str, Any]:
    """Keyword arguments shared by ``httpx.Client`` and ``httpx.AsyncClient``."""
    if isinstance(timeout, httpx.Timeout):
        resolved_timeout = timeout
    else:
        resolved_timeout = httpx.Timeout(timeout, connect=connect_timeout)
    return {
        "base_url": base_url.rstrip("/"),
        "timeout": resolved_timeout,
        "headers": {
            "x-api-key": resolve_api_key(api_key),
            "user-agent": user_agent(user_agent_suffix),
            "accept": "application/json",
        },
    }


def decode_body(response: httpx.Response) -> Any:
    """Decode the JSON body, tolerating empty (204) and non-JSON bodies."""
    if response.status_code == 204 or not response.content:
        return {}
    try:
        return response.json()
    except ValueError:
        return None


def process_response(
    response: httpx.Response, cast: type[R] = APIResponse  # type: ignore[assignment]
) -> R:
    """Wrap a successful response, or raise the typed error for a failed one."""
    body = decode_body(response)
    if response.is_success:
        return cast(body if body is not None else {}, response.headers)
    raise error_from_response(
        response.status_code,
        body,
        retry_after=server_wait_hint(response.headers, body),
    )


def status_retry_wait(
    method: str, response: httpx.Response, attempt: int, config: RetryConfig
) -> float | None:
    """Wait before retrying a non-2xx response, or ``None`` for "don't"."""
    if response.is_success:
        return None
    return retry_wait(method, response, decode_body(response), attempt, config)


def exception_retry_wait(
    exc: httpx.HTTPError, attempt: int, config: RetryConfig
) -> float | None:
    """Wait before retrying a transport failure, or ``None`` for "don't"."""
    if attempt >= config.max_retries or not is_retryable_exception(exc):
        return None
    return compute_backoff(attempt, config)


def transport_error(exc: httpx.HTTPError) -> LexAPIConnectionError:
    """Map an httpx transport failure to the SDK's typed connection errors."""
    if isinstance(exc, httpx.TimeoutException):
        return RequestTimeoutError(f"Request timed out: {exc}")
    return LexAPIConnectionError(f"Connection error: {exc}")


def make_retry_config(
    max_retries: int | None, retry_config: RetryConfig | None
) -> RetryConfig:
    if retry_config is not None:
        return retry_config
    if max_retries is None:
        return DEFAULT_RETRY_CONFIG
    return RetryConfig(max_retries=max_retries)
