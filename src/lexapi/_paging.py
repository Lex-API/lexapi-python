"""Stop rules shared by the sync and async page iterators.

A walk ends the way it always did (a short or empty page, ``total``
reached, no next cursor). It never ends silently because of what a page
contains: when the server shows that it is not paging (a cursor already
sent, an echoed ``offset`` other than the one requested, or the same page
again and again), the walk raises
:class:`~lexapi.errors.InvalidResponseError`. Pages yielded before that stay
valid; the page that showed the stall is not yielded and is on the error's
``body``.
"""

from __future__ import annotations

from typing import Any

from lexapi.errors import InvalidResponseError

#: The largest integer every JSON implementation reads exactly (2**53 - 1).
MAX_SAFE_INTEGER = 9_007_199_254_740_991

#: Consecutive pages that repeat (see the iterators) before a walk raises.
REPEAT_LIMIT = 3


def usable_number(value: Any) -> int | None:
    """A JSON number with a non-negative integral value up to 2**53 - 1
    (``30.0`` counts, ``True`` does not), else ``None``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= MAX_SAFE_INTEGER else None
    if isinstance(value, float) and value.is_integer() and 0 <= value <= MAX_SAFE_INTEGER:
        return int(value)
    return None


def check_page_size(name: str, value: Any) -> int:
    """A caller's page size: a positive integer, else :class:`ValueError`."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer, got {value!r}")
    return value


def stalled(body: Any, reason: str) -> InvalidResponseError:
    return InvalidResponseError(f"Pagination stopped: {reason}", status=200, body=body)


def canonical(value: Any) -> str:
    """A key-order-independent text form of a JSON value, built without
    recursion (so a deep value cannot exhaust the stack)."""
    out: list[str] = []
    stack: list[Any] = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, _Token):
            out.append(item.text)
        elif isinstance(item, dict):
            out.append("{")
            stack.append(_Token("}"))
            for key in sorted(item, key=str, reverse=True):
                stack.append(item[key])
                stack.append(_Token(f"{key!r}:"))
        elif isinstance(item, list):
            out.append("[")
            stack.append(_Token("]"))
            stack.extend(reversed(item))
        else:
            out.append(f"{type(item).__name__}:{item!r},")
    return "".join(out)


class _Token:
    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text


class EdgeWalk:
    """State of a ``cited_by`` / ``cites`` walk (paging is in edge rows).

    Ends at the first short page (fewer rows than the echoed ``limit`` when
    that is a positive integer, else the requested one) and once the next
    offset reaches a usable ``totalEdges``. Raises when an echoed ``offset``
    differs from the one requested, and, on pages that carry neither a
    usable ``totalEdges`` nor an echoed ``offset``, on the third consecutive
    page whose groups equal those of the page before.
    """

    def __init__(self, page_size: Any) -> None:
        self.page_size = check_page_size("page_size", page_size)
        self.offset = 0
        self._previous: str | None = None
        self._repeats = 0

    def check(self, body: dict[str, Any], key: str) -> bool:
        """Called before a page is yielded. False when the page has no list
        (the walk ends); raises when the server did not advance."""
        groups = body.get(key)
        if not isinstance(groups, list):
            return False
        echoed = usable_number(body.get("offset"))
        if echoed is not None and echoed != self.offset:
            raise stalled(body, "the server did not advance")
        current = canonical(groups)
        if echoed is not None or usable_number(body.get("totalEdges")) is not None:
            self._repeats = 0
        elif self._previous is not None and current == self._previous:
            self._repeats += 1
            if self._repeats >= REPEAT_LIMIT:
                raise stalled(body, "the server repeated the same page")
        else:
            self._repeats = 0
        self._previous = current
        return True

    def advance(self, body: dict[str, Any], rows: int) -> bool:
        """Called after a page was yielded; True when the walk ends."""
        self.offset += rows
        limit = usable_number(body.get("limit"))
        page_limit = limit if limit else self.page_size
        if rows < page_limit:
            return True
        total = usable_number(body.get("totalEdges"))
        return total is not None and self.offset >= total


class DeliveryWalk:
    """State of a webhook-deliveries walk (offset paging, newest first).

    Ends on an empty page and once the offset reaches a usable ``total``.
    Each delivery is yielded at most once (deduplicated on its ``id``, as
    before). Only when ``total`` is not usable: raises on the third
    consecutive page that has at least one readable id (a JSON string) and
    no unseen one; a page without readable ids resets the count.
    """

    def __init__(self, page_size: Any) -> None:
        self.page_size = check_page_size("page_size", page_size)
        self.offset = 0
        self.seen: set[str] = set()
        self._stalls = 0

    def check(self, body: dict[str, Any], ids: list[Any]) -> None:
        if usable_number(body.get("total")) is not None:
            self._stalls = 0
            return
        readable = [i for i in ids if isinstance(i, str)]
        if not readable or any(i not in self.seen for i in readable):
            self._stalls = 0
            return
        self._stalls += 1
        if self._stalls >= REPEAT_LIMIT:
            raise stalled(body, "the server repeated deliveries already seen")

    def advance(self, body: dict[str, Any], rows: int) -> bool:
        self.offset += rows
        total = usable_number(body.get("total"))
        return total is not None and self.offset >= total


class CursorWalk:
    """State of a cursor walk: a ``nextCursor`` already sent in this walk
    (including the one the walk started from) raises, and so does one that
    is neither a string nor ``null``."""

    def __init__(self, cursor: str | None) -> None:
        self.cursor = cursor
        self._sent: set[str] = {cursor} if cursor else set()

    def next_cursor(self, body: dict[str, Any], has_results: bool) -> str | None:
        """Called before a page is yielded: the cursor to send next, or
        ``None`` when this page is the last."""
        raw = body.get("nextCursor")
        if raw is not None and not isinstance(raw, str):
            raise stalled(body, "the page cursor is not a string")
        if body.get("hasMore") is not True or not raw or not has_results:
            return None
        if raw in self._sent:
            raise stalled(body, "the server repeated a page cursor")
        return raw

    def send(self, cursor: str) -> None:
        self._sent.add(cursor)
        self.cursor = cursor
