"""Nesting guard for the JSON parser.

On every Python version a JSON text nested deeper than :data:`MAX_DEPTH`
levels is refused before it is parsed: the C parser recurses once per level,
and in a thread with a small stack a deep text can exhaust the C stack before
the parser's own limit is reached. Before Python 3.12 the parser's recursion
also counts against the recursion limit, and once a ``RecursionError`` was
raised near the limit, a second deep parse from the same depth aborts the
interpreter; there a text is also refused when it nests deeper than the
recursion limit leaves room for at the caller's depth. A ``RecursionError``
the parser still raises is treated like ``ValueError`` by every caller.

The depth is measured in one linear pass of C-level byte operations, and
only when the text holds more brackets than the room available. In a thread
whose stack is far smaller than the default, the interpreter itself can run
out of stack before any check runs; that cannot be detected from here.
"""

from __future__ import annotations

import sys
from itertools import accumulate
from types import FrameType
from typing import Union

#: The deepest nesting handed to the parser, on every Python version.
MAX_DEPTH = 512

#: Whether the recursion-room check applies (Python before 3.12).
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
    than :data:`MAX_DEPTH` (every Python version), or — where
    :data:`GUARDED` — deeper than the recursion limit leaves room for here,
    less :data:`RESERVED`. A text without brackets is never refused."""
    data = text.encode("utf-8", "surrogatepass") if isinstance(text, str) else bytes(text)
    brackets = data.count(b"[") + data.count(b"{")
    if brackets == 0:
        return False
    room = MAX_DEPTH
    if GUARDED:
        room = min(room, sys.getrecursionlimit() - _stack_depth() - RESERVED)
    if brackets <= room:
        return False
    return nesting_depth(data) > room
