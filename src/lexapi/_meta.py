"""Package version and User-Agent construction."""

from __future__ import annotations

__version__ = "0.2.0"

_BASE_USER_AGENT = f"lexapi-python/{__version__}"


def user_agent(suffix: str | None = None) -> str:
    """Build the ``User-Agent`` header value, optionally with a caller suffix."""
    if suffix:
        return f"{_BASE_USER_AGENT} {suffix}"
    return _BASE_USER_AGENT
