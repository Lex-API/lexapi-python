"""Shared test helpers — all HTTP is mocked via httpx.MockTransport."""

from lexapi._retry import RetryConfig

#: Retry config with near-zero backoff so retry tests run instantly.
FAST_RETRY = RetryConfig(max_retries=3, backoff_base=0.0001, backoff_cap=0.001)

INFO_BODY = {
    "service": "LexAPI",
    "version": "2.0.0",
    "subscription": {"tier": "FREE", "pricingMode": "credits"},
    "usage": {
        "current": 0,
        "limit": 100,
        "remaining": 100,
        "unit": "credits",
        "resetsAt": "2026-08-01T00:00:00.000Z",
    },
    "credits": {
        "operation_weight": 0,
        "current": 0,
        "limit": 100,
        "remaining": 100,
        "included": 100,
        "topup": 0,
        "resetsAt": "2026-08-01T00:00:00.000Z",
    },
}
