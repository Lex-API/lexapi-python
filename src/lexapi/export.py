"""Streaming NDJSON corpus export (``GET /export``, BUSINESS tier).

The wire format is: a leading ``_meta`` envelope line, one JSON document
per line, then a trailing ``_done`` line. :class:`ExportStream` /
:class:`AsyncExportStream` iterate the *document* rows and capture the
``_meta`` / ``_done`` envelopes as attributes:

- ``stream.meta`` — the ``_meta`` envelope. Populated as soon as the first
  line is read; call ``read_meta()`` / ``await read_meta()`` to fetch it
  eagerly before iterating (no document rows are lost).
- ``stream.done`` — the ``_done`` trailer, populated once the iterator is
  exhausted; carries ``written``, ``truncated: true`` when matching rows
  remain that the export did not reach (``truncatedBy``: ``limit``,
  ``max_rows``, ``credits`` or ``recovered``), and ``skippedStubs`` (metadata-only rows
  left out because ``include_stubs`` was not set) — also exposed as ``stream.written`` /
  ``stream.skipped_stubs``.
- ``stream.total`` / ``stream.streaming`` — from the ``X-Export-Total`` /
  ``X-Export-Streaming`` response headers, available immediately.
- ``stream.truncated`` — ``_done.truncated`` once the trailer has been read
  (authoritative); before that, the ``X-Export-Truncated`` header, a hint
  from the up-front count.

Iterating a stream whose ``_done`` trailer was never read raises
:class:`~lexapi.IncompleteExportError` — whether the body ended early, or the
caller ended it with ``close()`` / ``break`` / leaving the ``with`` block
before the trailer arrived. One stream yields its rows once; to read again,
call ``export()`` again.

Failure handling: a transport failure while reading rows raises the SDK's
:class:`~lexapi.LexAPIConnectionError` / :class:`~lexapi.RequestTimeoutError`
(never a raw ``httpx`` exception), and a body that ends without its ``_done``
trailer raises :class:`~lexapi.IncompleteExportError` after the rows that did
arrive — a cut-short export never looks complete. So does a ``_done`` line
that says ``"complete": false``, and a line whose only key is ``_error``: such
a line is a server failure signal, never yielded as a row (its ``code`` /
``message`` are on the error's ``details``). Unknown ``_done`` fields
(``truncatedBy``, ``credits``, ...) are kept on ``stream.done`` and otherwise
ignored. Lines that are not JSON objects are counted on
``stream.invalid_lines`` rather than dropped silently.

Both classes are context managers; the underlying HTTP response is closed
when iteration finishes, on ``close()``/``aclose()``, or on context exit.
"""

from __future__ import annotations

import datetime as _dt
import json
from collections.abc import AsyncIterator, Iterable, Iterator
from typing import Any

import httpx

from lexapi._json import too_deep
from lexapi._payloads import DateLike, drop_none, iso_date
from lexapi._response import header, parse_header_int
from lexapi.errors import IncompleteExportError, LexAPIConnectionError, RequestTimeoutError


def _transport_error(exc: httpx.HTTPError) -> LexAPIConnectionError:
    if isinstance(exc, httpx.TimeoutException):
        return RequestTimeoutError(f"Export stream timed out: {exc}")
    return LexAPIConnectionError(f"Export stream failed: {exc}")


def _incomplete(
    rows: int, error: dict[str, Any] | None = None, done_incomplete: bool = False
) -> IncompleteExportError:
    if error is not None:
        reason = (
            "the server reported an error mid-stream "
            f"({error.get('code', 'UNKNOWN')}: {error.get('message', '')})"
        )
    elif done_incomplete:
        reason = "its _done trailer says complete: false"
    else:
        reason = "it ended without its _done trailer"
    return IncompleteExportError(
        f"Export stream is incomplete after {rows} row(s): {reason}; "
        "retry, or resume with fetched_since / a date window.",
        details=error,
    )


def _bool(value: bool | None) -> str | None:
    if value is None:
        return None
    return "true" if value else "false"


def _iso_datetime(name: str, value: str | _dt.datetime | None) -> str | None:
    """A ``datetime`` must be timezone-aware: the API rejects a date-time
    without a timezone (400), so a naive one is refused here, before any
    request. Strings pass through; the server validates them."""
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                f"{name} must be timezone-aware (the API rejects a date-time without a "
                "timezone); e.g. datetime(..., tzinfo=datetime.timezone.utc)"
            )
        return value.isoformat()
    return value


def _limit(value: int | None) -> int | None:
    """The API accepts a positive integer only (anything else is a 400)."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"limit must be a positive integer, got {value!r}")
    return value


def export_params(
    *,
    document_type: str | list[str] | None = None,
    document_type_code: str | list[str] | None = None,
    author: str | list[str] | None = None,
    subdomain: str | list[str] | None = None,
    language: str | None = None,
    date_from: DateLike | None = None,
    date_to: DateLike | None = None,
    fetched_since: str | _dt.datetime | None = None,
    changed_since: str | _dt.datetime | None = None,
    include_html: bool | None = None,
    include_content: bool | None = None,
    include_stubs: bool | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Query params for /export. List values are joined comma-separated;
    booleans are sent as ``true`` / ``false``; dates may be ``datetime.date``;
    ``fetched_since`` / ``changed_since`` may be timezone-aware
    ``datetime.datetime`` objects (a naive one raises ``ValueError``), and
    ``limit`` must be a positive integer (else ``ValueError``) — both mirror
    the API's 400s."""

    def _join(value: str | list[str] | None) -> str | None:
        if isinstance(value, list):
            return ",".join(value)
        return value

    return drop_none(
        {
            "documentType": _join(document_type),
            "documentTypeCode": _join(document_type_code),
            "author": _join(author),
            "subdomain": _join(subdomain),
            "language": language,
            "dateFrom": iso_date(date_from),
            "dateTo": iso_date(date_to),
            "fetchedSince": _iso_datetime("fetched_since", fetched_since),
            "changedSince": _iso_datetime("changed_since", changed_since),
            "includeHtml": _bool(include_html),
            "includeContent": _bool(include_content),
            "includeStubs": _bool(include_stubs),
            "limit": _limit(limit),
        }
    )


def _envelope(row: dict[str, Any], key: str) -> dict[str, Any]:
    """Extract the ``_meta`` / ``_done`` envelope from its line.

    Tolerates both ``{"_meta": {...}}`` (nested) and ``{"_meta": true, ...}``
    (flat) line shapes.
    """
    value = row.get(key)
    return value if isinstance(value, dict) else row


class _ExportStreamBase:
    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self._finished = False
        self._pending: list[dict[str, Any]] = []
        #: The leading ``_meta`` envelope (populated once the stream is read).
        self.meta: dict[str, Any] | None = None
        #: The trailing ``_done`` envelope (populated when iteration completes).
        self.done: dict[str, Any] | None = None
        #: Body lines that were not JSON objects (e.g. a half-written last line).
        self.invalid_lines = 0
        self._first_line = True
        #: Document rows yielded so far.
        self.rows_read = 0
        #: A server ``_error`` line, if one arrived (never yielded as a row).
        self.error: dict[str, Any] | None = None
        self.headers = response.headers

    @property
    def total(self) -> int | None:
        """Total rows matching the filters in the database (X-Export-Total)."""
        return self._header_int("x-export-total")

    @property
    def streaming(self) -> int | None:
        """Rows actually streamed — min(total, limit) (X-Export-Streaming)."""
        return self._header_int("x-export-streaming")

    @property
    def truncated(self) -> bool:
        """True when matching rows remain that this export did not reach.

        Once the ``_done`` trailer has been read, its ``truncated`` flag is
        authoritative (API 2.3.0): the server sets it when it wrote up to
        ``limit`` / the server's row ceiling (50,000) and found another matching row past
        the last one it read, or when the credit pool could not pay for rows
        added since its count (``done["truncatedBy"]`` says which). It never
        derives it from the row count, and skipped stubs do not count.

        Before the trailer, it falls back to ``X-Export-Truncated``, then
        ``_meta.truncated``. Both come from the up-front count, which may be
        cached for up to a minute and never caps the stream: a hint only.
        """
        if isinstance(self.done, dict) and isinstance(self.done.get("truncated"), bool):
            return bool(self.done["truncated"])
        value = header(self.headers, "x-export-truncated")
        if value is not None and value.strip() in ("0", "1"):
            return value.strip() == "1"
        if isinstance(self.meta, dict) and isinstance(self.meta.get("truncated"), bool):
            return bool(self.meta["truncated"])
        return False

    @property
    def complete(self) -> bool:
        """True once a ``_done`` trailer has been read that does not say
        ``"complete": false``, and no ``_error`` line arrived."""
        return (
            self.done is not None and self.done.get("complete") is not False and self.error is None
        )

    def _raise_if_incomplete(self) -> None:
        if not self.complete:
            raise _incomplete(
                self.rows_read,
                self.error,
                done_incomplete=self.done is not None and self.done.get("complete") is False,
            )

    @property
    def written(self) -> int | None:
        """Rows the server wrote (``_done.written``; after iteration)."""
        return self._done_int("written")

    @property
    def skipped_stubs(self) -> int | None:
        """Metadata-only rows skipped because ``include_stubs`` was off
        (``_done.skippedStubs``; after iteration)."""
        return self._done_int("skippedStubs")

    def _done_int(self, key: str) -> int | None:
        if not isinstance(self.done, dict):
            return None
        value = self.done.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value if abs(value) <= 9_007_199_254_740_991 else None

    def _header_int(self, name: str) -> int | None:
        return parse_header_int(header(self.headers, name))

    def _classify(self, raw: bytes) -> tuple[str, dict[str, Any]] | None:
        """Sort a raw NDJSON line into ("meta"|"done"|"row", payload).

        A line is read as strict UTF-8 whatever charset the response names
        (one byte order mark at the start of the stream is ignored); a line
        that is not UTF-8 is an invalid line."""
        try:
            line = raw.decode("utf-8-sig" if self._first_line else "utf-8").strip()
        except UnicodeDecodeError:
            line = None
        self._first_line = False
        if line is None:
            self.invalid_lines += 1
            return None
        if not line:
            return None
        try:
            if too_deep(line):
                raise ValueError("nested too deeply")
            row = json.loads(line)
        except (ValueError, RecursionError):  # RecursionError: nested too deeply
            self.invalid_lines += 1
            return None
        if not isinstance(row, dict):
            self.invalid_lines += 1
            return None
        if self.meta is None and self.done is None and "_meta" in row:
            self.meta = _envelope(row, "_meta")
            return ("meta", self.meta)
        if "_done" in row:
            self.done = _envelope(row, "_done")
            return ("done", self.done)
        if set(row) == {"_error"}:
            # A failure signal, never a document row.
            value = row["_error"]
            self.error = value if isinstance(value, dict) else {"message": str(value)}
            return ("error", self.error)
        return ("row", row)


def _split_lines(chunks: Iterable[bytes]) -> Iterator[bytes]:
    """The lines of a byte stream (``\\n``-separated; a trailing ``\\r`` is
    removed with the rest of the whitespace when the line is read)."""
    pending: list[bytes] = []
    for chunk in chunks:
        pieces = chunk.split(b"\n")
        if len(pieces) == 1:
            pending.append(chunk)
            continue
        pending.append(pieces[0])
        yield b"".join(pending)
        yield from pieces[1:-1]
        pending = [pieces[-1]] if pieces[-1] else []
    if pending:
        yield b"".join(pending)


async def _asplit_lines(chunks: AsyncIterator[bytes]) -> AsyncIterator[bytes]:
    """Async twin of :func:`_split_lines`."""
    pending: list[bytes] = []
    async for chunk in chunks:
        pieces = chunk.split(b"\n")
        if len(pieces) == 1:
            pending.append(chunk)
            continue
        pending.append(pieces[0])
        yield b"".join(pending)
        for piece in pieces[1:-1]:
            yield piece
        pending = [pieces[-1]] if pieces[-1] else []
    if pending:
        yield b"".join(pending)


class ExportStream(_ExportStreamBase):
    """Iterator over export document rows (one dict per corpus row)."""

    def __init__(self, response: httpx.Response) -> None:
        super().__init__(response)
        self._lines: Iterator[bytes] | None = None

    def _line_iter(self) -> Iterator[bytes]:
        if self._lines is None:
            # Bytes, not httpx's text lines: the charset a response names is
            # not used to read the stream (see _classify).
            self._lines = _split_lines(self._response.iter_bytes())
        return self._lines

    def _lines_checked(self) -> Iterator[bytes]:
        # Pull with next() rather than `yield from`: read_meta() abandons this
        # generator after one line, and `yield from` would close the shared
        # underlying line iterator along with it.
        lines = self._line_iter()
        while True:
            try:
                line = next(lines)
            except StopIteration:
                return
            except httpx.HTTPError as exc:
                self.close()
                raise _transport_error(exc) from exc
            yield line

    def read_meta(self) -> dict[str, Any] | None:
        """Eagerly read the leading ``_meta`` envelope before iterating.

        Any document row encountered instead (no ``_meta`` line) is buffered
        and yielded first by iteration — nothing is lost. Returns ``None``
        when the stream carries no ``_meta`` envelope.
        """
        if self.meta is not None or self._finished:
            return self.meta
        for line in self._lines_checked():
            kind = self._classify(line)
            if kind is None:
                continue
            if kind[0] == "meta":
                return self.meta
            if kind[0] == "error":
                continue
            if kind[0] == "done":
                self.close()
                return self.meta
            self._pending.append(kind[1])
            return self.meta
        return self.meta

    def __iter__(self) -> Iterator[dict[str, Any]]:
        try:
            while self._pending:
                self.rows_read += 1
                yield self._pending.pop(0)
            if self._finished:
                self._raise_if_incomplete()
                return
            for line in self._lines_checked():
                kind = self._classify(line)
                if kind is None or kind[0] in ("meta", "error"):
                    continue
                if kind[0] == "done":
                    break
                self.rows_read += 1
                yield kind[1]
            self._raise_if_incomplete()
        finally:
            self.close()

    def close(self) -> None:
        if not self._finished:
            self._finished = True
            self._response.close()

    def __enter__(self) -> ExportStream:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class AsyncExportStream(_ExportStreamBase):
    """Async iterator over export document rows."""

    def __init__(self, response: httpx.Response) -> None:
        super().__init__(response)
        self._lines: AsyncIterator[bytes] | None = None

    def _line_iter(self) -> AsyncIterator[bytes]:
        if self._lines is None:
            self._lines = _asplit_lines(self._response.aiter_bytes())
        return self._lines

    async def _lines_checked(self) -> AsyncIterator[bytes]:
        lines = self._line_iter()  # pulled manually — see ExportStream._lines_checked
        while True:
            try:
                line = await lines.__anext__()
            except StopAsyncIteration:
                return
            except httpx.HTTPError as exc:
                await self.aclose()
                raise _transport_error(exc) from exc
            yield line

    async def read_meta(self) -> dict[str, Any] | None:
        """Async twin of :meth:`ExportStream.read_meta`."""
        if self.meta is not None or self._finished:
            return self.meta
        async for line in self._lines_checked():
            kind = self._classify(line)
            if kind is None:
                continue
            if kind[0] == "meta":
                return self.meta
            if kind[0] == "error":
                continue
            if kind[0] == "done":
                await self.aclose()
                return self.meta
            self._pending.append(kind[1])
            return self.meta
        return self.meta

    async def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        try:
            while self._pending:
                self.rows_read += 1
                yield self._pending.pop(0)
            if self._finished:
                self._raise_if_incomplete()
                return
            async for line in self._lines_checked():
                kind = self._classify(line)
                if kind is None or kind[0] in ("meta", "error"):
                    continue
                if kind[0] == "done":
                    break
                self.rows_read += 1
                yield kind[1]
            self._raise_if_incomplete()
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        if not self._finished:
            self._finished = True
            await self._response.aclose()

    async def __aenter__(self) -> AsyncExportStream:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
