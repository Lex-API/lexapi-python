"""Request core shared by the sync and async clients.

Everything here is transport-agnostic: header construction, response
parsing, error mapping, and retry decisions. The sync/async clients only
differ in how they send the request and how they sleep.
"""

from __future__ import annotations

import json
import os
from typing import Any, TypeVar

import httpx

from lexapi._json import too_deep
from lexapi._meta import user_agent
from lexapi._response import APIResponse
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


#: Characters (code points) of a body that is not JSON kept on
#: ``body_excerpt`` (never in a message).
EXCERPT_CHARS = 200
#: Bytes decoded for the excerpt: a code point takes at most four bytes.
EXCERPT_BYTES = EXCERPT_CHARS * 4

_MISSING: Any = object()
#: Sentinel for a body that is not strict UTF-8 JSON (JSON ``null`` is ``None``).
NOT_JSON: Any = object()

_KIND_NAMES = {"object": "an object", "string": "a string", "boolean": "a boolean"}


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not JSON")


def parse_json_bytes(content: bytes) -> Any:
    """Strict UTF-8 + RFC 8259 JSON (SDK contract §1.3), or :data:`NOT_JSON`.

    One leading UTF-8 byte order mark is ignored; any invalid UTF-8 (an
    encoded surrogate included), ``NaN`` / ``Infinity``, trailing data, or
    nesting deeper than the parser supports is not JSON. Never raises.
    """
    if too_deep(content):
        return NOT_JSON
    try:
        return json.loads(content.decode("utf-8-sig"), parse_constant=_reject_constant)
    except (ValueError, RecursionError):  # UnicodeDecodeError is a ValueError
        return NOT_JSON


def decode_body(response: httpx.Response) -> Any:
    """The parsed JSON body of a response; ``None`` when it is empty, is not
    JSON, or is JSON ``null``."""
    if not response.content:
        return None
    body = parse_json_bytes(response.content)
    return None if body is NOT_JSON else body


def _excerpt(content: bytes) -> str | None:
    """The first 200 code points of a body, decoded as UTF-8 with
    replacement whatever the charset (a byte order mark is kept);
    ``None`` for an empty body."""
    if not content:
        return None
    return content[:EXCERPT_BYTES].decode("utf-8", "replace")[:EXCERPT_CHARS]


def _media_type(response: httpx.Response) -> str:
    """The ``Content-Type`` before ``;``, trimmed of spaces and tabs, at
    most 100 code points; header bytes read as latin-1."""
    values = [v for k, v in response.headers.raw if k.lower() == b"content-type"]
    value = b", ".join(values).decode("latin-1")
    media = value.split(";", 1)[0].strip(" \t")[:100]
    return media or "no content type"


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


def _is_kind(value: Any, kind: str) -> bool:
    if kind == "object":
        return isinstance(value, dict)
    if kind == "string":
        return isinstance(value, str)
    return isinstance(value, bool)  # "boolean"


def shape_problem(body: dict[str, Any], cast: type[APIResponse]) -> str | None:
    """What is wrong with a 2xx JSON object's documented fields for
    ``cast``, or ``None``: required lists, then required fields, then
    optional objects, then lists and their items (SDK contract §1.3).
    Unknown extra fields are fine."""

    def missing(path: str) -> str:
        return f'Expected "{path}" in the response; it is missing'

    for path in cast._required_list_fields:
        value = _lookup(body, path)
        if value is _MISSING or value is None:
            return missing(path)
    for path, kind in cast._required_fields:
        value = _lookup(body, path)
        if value is _MISSING or value is None or (kind == "string" and value == ""):
            return missing(path)
        if not _is_kind(value, kind):
            return f'Expected "{path}" to be {_KIND_NAMES[kind]}, got {_json_kind(value)}'
    for path in cast._optional_object_fields:
        value = _lookup(body, path)
        if value is not _MISSING and value is not None and not isinstance(value, dict):
            return f'Expected "{path}" to be an object, got {_json_kind(value)}'
    for path in cast._list_fields:
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
        body_excerpt=_excerpt(response.content) if excerpt else None,
    )


def parse_success(response: httpx.Response, cast: type[R]) -> R:
    """Wrap a 2xx response, or raise when it is not the documented shape
    (SDK contract §1.3, checks 1-6).

    1. A 204 (whatever it carries) or a body of zero bytes is an empty
       result only where ``cast._allow_empty`` (the webhook delete).
    2. The body must be strict UTF-8 JSON (:func:`parse_json_bytes`).
    3. It must be a JSON object.
    4. ``success: false`` raises the mapped API error when the body carries
       the error envelope, else :class:`~lexapi.errors.InvalidResponseError`
       (skipped where ``cast._success_false_ok``).
    5-6. The documented fields (:func:`shape_problem`).

    No message quotes the body.
    """
    if response.status_code == 204 or not response.content:
        if cast._allow_empty:
            return cast({}, response.headers)
        raise _invalid(response, "Expected JSON, got no body")
    # NOT_JSON is returned, not raised, so no decode error is chained.
    body = parse_json_bytes(response.content)
    if body is NOT_JSON:
        raise _invalid(
            response,
            f"Expected JSON, got {_media_type(response)} that does not parse as JSON "
            f"({len(response.content)} bytes)",
            excerpt=True,
        )
    if not isinstance(body, dict):
        raise _invalid(response, f"Expected a JSON object, got {_json_kind(body)}", body=body)
    if body.get("success") is False and not cast._success_false_ok:
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
    turned into an empty "successful" result. A non-2xx body is parsed by
    the same strict parser; one that is neither JSON nor empty is kept,
    cut short, on ``body_excerpt`` only.
    """
    if response.is_success:
        return parse_success(response, cast)
    content = response.content
    parsed = parse_json_bytes(content) if content else NOT_JSON
    body = None if parsed is NOT_JSON else parsed
    retry_after = None
    if not is_quota_exhausted(response.status_code, response.headers, body):
        retry_after = server_wait_hint(response.headers, body, response.status_code)
    raise error_from_response(
        response.status_code,
        body,
        retry_after=retry_after,
        body_excerpt=_excerpt(content) if parsed is NOT_JSON else None,
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
