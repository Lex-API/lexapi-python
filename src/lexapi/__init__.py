"""Official Python SDK for LexAPI (https://lex-api.com)."""

from lexapi._meta import __version__
from lexapi._response import APIResponse, CreditsInfo, UsageInfo
from lexapi._retry import RetryConfig
from lexapi.async_client import AsyncLexAPI
from lexapi.client import LexAPI
from lexapi.errors import (
    AuthenticationError,
    CreditsExhaustedError,
    InternalServerError,
    InvalidCelexError,
    InvalidParamsError,
    InvalidURLError,
    LexAPIConnectionError,
    LexAPIError,
    NotFoundError,
    RateLimitedError,
    RequestTimeoutError,
    TierForbiddenError,
    TimeoutError,
    UpstreamError,
)

__all__ = [
    "APIResponse",
    "AsyncLexAPI",
    "AuthenticationError",
    "CreditsExhaustedError",
    "CreditsInfo",
    "InternalServerError",
    "InvalidCelexError",
    "InvalidParamsError",
    "InvalidURLError",
    "LexAPI",
    "LexAPIConnectionError",
    "LexAPIError",
    "NotFoundError",
    "RateLimitedError",
    "RequestTimeoutError",
    "RetryConfig",
    "TierForbiddenError",
    "TimeoutError",
    "UpstreamError",
    "UsageInfo",
    "__version__",
]
