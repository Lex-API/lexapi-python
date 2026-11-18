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
from lexapi._response import APIResponse, header
from lexapi._retry import (
    DEFAULT_RETRY_CONFIG,
    RetryConfig,
    compute_backoff,
    is_incomplete_search,
    is_quota_exhausted,
    is_retryable_exception,
    retry_wait,
    server_wait_hint,
)
from lexapi.errors import (
    InvalidResponseError,
    LexAPIConnectionError,
    RequestTimeoutError,
    error_from_response,
)

DEFAULT_BASE_URL = "https://lex-api.com/api/v1"

#: Default response timeout in seconds (SDK contract §1.6): the longest the SDK
#: waits for the next bytes of a response — the response head, then each
#: body chunk — on one attempt. It is an inactivity limit, not a deadline
#: for the whole call. The 95 s default sits above the API's 90 s deadline on
#: document reads, so that timeout comes back as a typed, refunded 504; the
#: CDN ends any wait at 100 s.
DEFAULT_TIMEOUT = 95.0
#: Default connect timeout in seconds (TCP + TLS). Also bounds the wait for
#: a free connection from the pool.
DEFAULT_CONNECT_TIMEOUT = 10.0

R = TypeVar("R", bound=APIResponse)


def resolve_api_key(api_key: str | None) -> str:
    key = api_key or os.environ.get("LEXAPI_API_KEY")
    if not key:
        raise ValueError("api_key is required (or set LEXAPI_API_KEY)")
    return key


def make_timeout(
    timeout: float | httpx.Timeout, connect_timeout: float = DEFAULT_CONNECT_TIMEOUT
) -> httpx.Timeout:
    """An ``httpx.Timeout``: ``timeout`` is the per-attempt response
    (read/write) inactivity budget, ``connect_timeout`` bounds connecting and
    waiting for a pooled connection."""
    if isinstance(timeout, httpx.Timeout):
        return timeout
    return httpx.Timeout(timeout, connect=connect_timeout, pool=connect_timeout)


def build_httpx_kwargs(
    api_key: str | None,
    base_url: str,
    timeout: float | httpx.Timeout,
    connect_timeout: float,
    user_agent_suffix: str | None,
) -> dict[str, Any]:
    """Keyword arguments shared by ``httpx.Client`` and ``httpx.AsyncClient``."""
    return {
        "base_url": base_url.rstrip("/"),
        "timeout": make_timeout(timeout, connect_timeout),
        "headers": {
            "x-api-key": resolve_api_key(api_key),
            "user-agent": user_agent(user_agent_suffix),
            "accept": "application/json",
        },
    }


def decode_body(response: httpx.Response) -> Any:
    """Decode the JSON body: ``{}`` for 204 / empty bodies, ``None`` when the
    body is present but is not JSON."""
    if response.status_code == 204 or not response.content:
        return {}
    try:
        return response.json()
    except ValueError:
        return None


#: Characters of a non-JSON body kept on ``body_excerpt`` (never in a message).
EXCERPT_CHARS = 200

_MISSING: Any = object()


def _media_type(response: httpx.Response) -> str:
    value = header(response.headers, "content-type")
    media = value.split(";", 1)[0].strip()[:100] if value else ""
    return media or "no content type"


def _excerpt(response: httpx.Response) -> str | None:
    if not response.content:
        return None
    return response.text[:EXCERPT_CHARS]


def _json_kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__  # pragma: no cover - json never yields others


def _lookup(body: dict[str, Any], path: str) -> Any:
    value: Any = body
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return _MISSING
        value = value[key]
    return value


def shape_problem(body: dict[str, Any], cast: type[APIResponse]) -> str | None:
    """What is wrong with a 2xx JSON object for ``cast``, or ``None``.

    Checks only the documented list fields; unknown extra fields are fine.
    """
    for path in cast.required_list_fields:
        if _lookup(body, path) in (_MISSING, None):
            return f'Expected "{path}" in the response; it is missing'
    for path in cast.list_fields:
        value = _lookup(body, path)
        if value is _MISSING or value is None:
            continue
        if not isinstance(value, list):
            return f'Expected "{path}" to be a list, got {_json_kind(value)}'
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                return (
                    f'Expected every item of "{path}" to be an object, '
                    f"got {_json_kind(item)} at index {index}"
                )
    return None


def _has_error_envelope(body: dict[str, Any]) -> bool:
    err = body.get("error")
    return isinstance(err, dict) or (isinstance(err, str) and bool(err))


def _invalid(
    response: httpx.Response, message: str, *, body: Any = None, excerpt: bool = False
) -> InvalidResponseError:
    return InvalidResponseError(
        message,
        status=response.status_code,
        body=body,
        body_excerpt=_excerpt(response) if excerpt else None,
    )


def parse_success(response: httpx.Response, cast: type[R], *, allow_empty: bool | None = None) -> R:
    """Wrap a 2xx response, or raise when it is not the documented shape.

    A 204 or an empty body is an empty result only where ``allow_empty``
    (by default only for the untyped :class:`APIResponse`, i.e.
    ``DELETE``). Otherwise the body must be a JSON object, must not say
    ``success: false`` (unless ``cast.success_false_ok``), and its list
    fields must be lists of objects. A violation raises
    :class:`~lexapi.errors.InvalidResponseError`, or the mapped API error
    when a ``success: false`` body carries the error envelope. No message
    quotes the body.
    """
    if allow_empty is None:
        allow_empty = cast is APIResponse
    if response.status_code == 204 or not response.content:
        if allow_empty:
            return cast({}, response.headers)
        raise _invalid(response, "Expected JSON, got an empty body")
    try:
        body = response.json()
    except ValueError:
        raise _invalid(
            response,
            f"Expected JSON, got {_media_type(response)} that does not parse as JSON "
            f"({len(response.content)} bytes)",
            excerpt=True,
        ) from None
    if not isinstance(body, dict):
        raise _invalid(response, f"Expected a JSON object, got {_json_kind(body)}", body=body)
    if body.get("success") is False and not cast.success_false_ok:
        if _has_error_envelope(body):
            raise error_from_response(response.status_code, body)
        raise _invalid(response, "Response reported success: false without an error", body=body)
    problem = shape_problem(body, cast)
    if problem is not None:
        raise _invalid(response, problem, body=body)
    return cast(body, response.headers)


def process_response(response: httpx.Response, cast: type[R]) -> R:
    """Wrap a successful response, or raise the typed error for a failed one.

    A 2xx that is not the documented shape (see :func:`parse_success`)
    raises :class:`~lexapi.errors.InvalidResponseError` — it is never
    turned into an empty "successful" result.
    """
    if response.is_success:
        return parse_success(response, cast)
    body = decode_body(response)
    retry_after = None
    if not is_quota_exhausted(response.status_code, response.headers, body):
        retry_after = server_wait_hint(response.headers, body, response.status_code)
    raise error_from_response(
        response.status_code,
        body,
        retry_after=retry_after,
        body_excerpt=_excerpt(response) if body is None else None,
    )


def status_retry_wait(
    method: str,
    response: httpx.Response,
    attempt: int,
    config: RetryConfig,
    *,
    read_only: bool = False,
    free: bool = False,
    incomplete_retried: bool = False,
) -> float | None:
    """Wait before retrying a non-2xx response, or ``None`` for "don't"."""
    if response.is_success:
        return None
    return retry_wait(
        method,
        response,
        decode_body(response),
        attempt,
        config,
        read_only=read_only,
        free=free,
        incomplete_retried=incomplete_retried,
    )


def is_incomplete_search_response(response: httpx.Response) -> bool:
    """An incomplete national semantic search 503 (see
    :func:`lexapi._retry.is_incomplete_search`)."""
    return is_incomplete_search(response.status_code, decode_body(response))


def exception_retry_wait(
    exc: httpx.HTTPError,
    attempt: int,
    config: RetryConfig,
    method: str = "GET",
    *,
    free: bool = False,
) -> float | None:
    """Wait before retrying a transport failure, or ``None`` for "don't"."""
    if attempt >= config.max_retries or not is_retryable_exception(exc, method, free=free):
        return None
    return compute_backoff(attempt, config)


def transport_error(exc: httpx.HTTPError) -> LexAPIConnectionError:
    """Map an httpx transport failure to the SDK's typed connection errors."""
    if isinstance(exc, httpx.TimeoutException):
        return RequestTimeoutError(f"Request timed out: {exc}")
    return LexAPIConnectionError(f"Connection error: {exc}")


def make_retry_config(max_retries: int | None, retry_config: RetryConfig | None) -> RetryConfig:
    if retry_config is not None:
        return retry_config
    if max_retries is None:
        return DEFAULT_RETRY_CONFIG
    return RetryConfig(max_retries=max_retries)
