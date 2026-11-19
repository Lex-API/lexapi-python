"""Nesting guard for the JSON parser on interpreters older than 3.12.

Before Python 3.12 the C JSON parser recurses on the C stack once per level
of nesting; a body nested deeply enough can abort the interpreter instead
of raising ``RecursionError``. On those interpreters a text nested deeper
than :data:`MAX_DEPTH` is refused before it is parsed. From 3.12 the parser
raises ``RecursionError`` on its own, which every caller treats like
``ValueError``.
"""

from __future__ import annotations

import re
import sys
from itertools import accumulate
from typing import Union

#: The deepest nesting parsed on interpreters older than 3.12.
MAX_DEPTH = 512

GUARDED = sys.version_info < (3, 12)

# A JSON string, escapes included.
_STRING = re.compile(rb'"[^"\\]*(?:\\.[^"\\]*)*"', re.S)
# Every byte but the four brackets.
_NOT_BRACKETS = bytes(c for c in range(256) if c not in b"[]{}")
_STEP = {ord("["): 1, ord("{"): 1, ord("]"): -1, ord("}"): -1}

Text = Union[str, bytes, bytearray, memoryview]


def too_deep(text: Text) -> bool:
    """True when ``text`` nests arrays and objects deeper than
    :data:`MAX_DEPTH` (only checked where :data:`GUARDED`). Brackets inside
    strings do not count."""
    if not GUARDED:
        return False
    data = text.encode("utf-8", "surrogatepass") if isinstance(text, str) else bytes(text)
    if data.count(b"[") + data.count(b"{") <= MAX_DEPTH:
        return False
    brackets = _STRING.sub(b"", data).translate(None, _NOT_BRACKETS)
    return max(accumulate(map(_STEP.__getitem__, brackets)), default=0) > MAX_DEPTH
