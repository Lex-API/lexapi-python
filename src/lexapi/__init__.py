"""Official Python SDK for LexAPI (https://lex-api.com)."""

from lexapi.client import LexAPI
from lexapi.errors import LexAPIError

__version__ = "0.1.0"

__all__ = ["LexAPI", "LexAPIError", "__version__"]
