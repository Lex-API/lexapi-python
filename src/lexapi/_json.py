"""Nesting guard for the JSON parser on interpreters older than 3.12.

Before Python 3.12 the C JSON parser recurses once per level of nesting,
counted against the recursion limit. A body nested deeply enough can abort
the interpreter instead of raising ``RecursionError`` — and once a
``RecursionError`` was raised near the limit, a second deep parse from the
same depth aborts it too. On those interpreters a text is therefore refused
before it is parsed when it nests deeper than :data:`MAX_DEPTH` levels, or
deeper than the recursion limit leaves room for at the caller's depth. From
3.12 the parser has its own limit and raises ``RecursionError``, which every
caller treats like ``ValueError``.

The depth is measured in one linear pass of C-level byte operations, and
only when the text holds more brackets than the room available.
"""

from __future__ import annotations

import sys
from itertools import accumulate
from types import FrameType
from typing import Union

#: The deepest nesting parsed on interpreters older than 3.12.
MAX_DEPTH = 512

GUARDED = sys.version_info < (3, 12)

#: Recursion levels kept free below the limit for the parser's own calls.
RESERVED = 5

#: Bytes of bracket-and-quote text handled per step of the depth scan.
_CHUNK = 1 << 16

_NOT_KEPT = bytes(c for c in range(256) if c not in b'[]{}"')
_STEPS = bytearray(256)
_STEPS[ord("[")] = _STEPS[ord("{")] = 1
_STEPS[ord("]")] = _STEPS[ord("}")] = 255  # -1 as a signed byte
_STEP_TABLE = bytes(_STEPS)

Text = Union[str, bytes, bytearray, memoryview]


def nesting_depth(data: bytes) -> int:
    """The deepest nesting of arrays and objects in a JSON text, ignoring
    brackets inside strings (an unterminated string runs to the end).

    Linear in time, and in memory a small multiple of the text: escaped
    backslashes, then escaped quotes, are removed, so every quote left opens
    or closes a string; only quotes and brackets are kept; the rest is read
    in fixed-size chunks, where the text outside strings is every other
    piece between quotes.
    """
    kept = data.replace(b"\\\\", b"").replace(b'\\"', b"").translate(None, _NOT_KEPT)
    depth = peak = 0
    inside = False
    for start in range(0, len(kept), _CHUNK):
        pieces = kept[start : start + _CHUNK].split(b'"')
        outside = b"".join(pieces[1::2] if inside else pieces[0::2])
        if len(pieces) % 2 == 0:  # an odd number of quotes in this chunk
            inside = not inside
        del pieces
        steps = memoryview(outside.translate(_STEP_TABLE)).cast("b")
        if len(steps):
            peak = max(peak, max(accumulate(steps, initial=depth)))
            depth += sum(steps)
    return peak


def _stack_depth() -> int:
    depth = 0
    frame: FrameType | None = sys._getframe()
    while frame is not None:
        depth += 1
        frame = frame.f_back
    return depth


def too_deep(text: Text) -> bool:
    """True when ``text`` must not be handed to the parser: it nests deeper
    than :data:`MAX_DEPTH`, or deeper than the recursion limit leaves room
    for here, less :data:`RESERVED` (only checked where :data:`GUARDED`).
    A text without brackets is never refused."""
    if not GUARDED:
        return False
    data = text.encode("utf-8", "surrogatepass") if isinstance(text, str) else bytes(text)
    brackets = data.count(b"[") + data.count(b"{")
    if brackets == 0:
        return False
    room = min(MAX_DEPTH, sys.getrecursionlimit() - _stack_depth() - RESERVED)
    if brackets <= room:
        return False
    return nesting_depth(data) > room
