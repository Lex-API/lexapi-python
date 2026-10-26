"""Request body / query-param builders shared by the sync and async clients.

``None`` values are always omitted so the server's own defaults apply and
older deployments never see parameters they don't know.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Sequence
from typing import Any, Union
from urllib.parse import quote

# Filters that accept a single value or a list (the API accepts both).
OneOrMany = Union[str, "list[str]"]
IntOrMany = Union[int, "list[int]"]
#: A calendar date: ``YYYY-MM-DD`` string or a :class:`datetime.date`.
DateLike = Union[str, _dt.date]


def drop_none(mapping: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in mapping.items() if value is not None}


def seg(value: object) -> str:
    """Percent-encode one URL path segment (a CELEX, webhook id, date, ...).

    CELEX characters (letters, digits, ``( ) . _ -``) pass through unchanged
    — ``32008R1272R(01)`` and ``02016R0679-20180524`` stay readable — while a
    stray ``/``, ``?`` or ``#`` can no longer change which endpoint is hit.
    Empty, ``.`` and ``..`` segments raise :class:`ValueError`.
    """
    text = str(value)
    if text in ("", ".", ".."):
        # httpx (like every URL library) resolves dot-segments, so `..`
        # would silently hit the parent endpoint; an empty id would too.
        raise ValueError(f"invalid path segment {text!r}")
    return quote(text, safe="()._-~")


def iso_date(value: DateLike | None) -> str | None:
    """Normalise a date argument to ``YYYY-MM-DD`` (strings pass through as-is).

    A :class:`datetime.datetime` is reduced to its date part; the server
    validates the string form and answers 400 on a malformed one.
    """
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return value.date().isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    return value


def _accept_languages(value: str | list[str] | None) -> str | list[str] | None:
    if value is None or isinstance(value, str):
        return value
    return list(value)


def search_body(
    query: str | None = None,
    *,
    text_scope: str | None = None,
    date_from: DateLike | None = None,
    date_to: DateLike | None = None,
    year: IntOrMany | None = None,
    month: IntOrMany | None = None,
    document_type: OneOrMany | None = None,
    author: OneOrMany | None = None,
    procedure: OneOrMany | None = None,
    domain: str | None = None,
    subdomain: OneOrMany | None = None,
    language: str | None = None,
    max_pages: int | None = None,
    fresh: bool | None = None,
) -> dict[str, Any]:
    return drop_none(
        {
            "query": query,
            "textScope": text_scope,
            "dateFrom": iso_date(date_from),
            "dateTo": iso_date(date_to),
            "year": year,
            "month": month,
            "documentType": document_type,
            "author": author,
            "procedure": procedure,
            "domain": domain,
            "subdomain": subdomain,
            "language": language,
            "maxPages": max_pages,
            "fresh": fresh,
        }
    )


def document_content_body(
    celex: str,
    *,
    bypass_corpus: bool | None = None,
    language: str | None = None,
    accept_languages: str | list[str] | None = None,
    include: OneOrMany | None = None,
    fields: OneOrMany | None = None,
    article_id: str | None = None,
) -> dict[str, Any]:
    return drop_none(
        {
            "celexNumber": celex,
            "bypassCorpus": bypass_corpus,
            "language": language,
            "acceptLanguages": _accept_languages(accept_languages),
            "include": include,
            "fields": fields,
            "articleId": article_id,
        }
    )


def document_batch_body(
    celexes: list[str],
    *,
    bypass_corpus: bool | None = None,
    language: str | None = None,
    accept_languages: str | list[str] | None = None,
    include: OneOrMany | None = None,
    fields: OneOrMany | None = None,
    article_id: str | None = None,
) -> dict[str, Any]:
    return drop_none(
        {
            "celexNumbers": list(celexes),
            "bypassCorpus": bypass_corpus,
            "language": language,
            "acceptLanguages": _accept_languages(accept_languages),
            "include": include,
            "fields": fields,
            "articleId": article_id,
        }
    )


def document_by_url_body(
    url: str,
    *,
    bypass_corpus: bool | None = None,
    include: OneOrMany | None = None,
    fields: OneOrMany | None = None,
    article_id: str | None = None,
) -> dict[str, Any]:
    return drop_none(
        {
            "url": url,
            "bypassCorpus": bypass_corpus,
            "include": include,
            "fields": fields,
            "articleId": article_id,
        }
    )


def semantic_search_body(
    query: str,
    *,
    hyde: bool = False,
    min_score: float | None = None,
    limit: int | None = None,
    language: str | None = None,
    include_text: bool = False,
    filters: dict[str, Any] | None = None,
    as_of: DateLike | None = None,
) -> dict[str, Any]:
    """Body for the semantic endpoints.

    ``hyde`` is only sent when True (additive param — older deployments
    never see it); ``include_text=True`` maps to ``include: ["text"]``;
    ``as_of`` (legislation only) is sent as ``asOf`` in ``YYYY-MM-DD`` form.
    """
    body = drop_none(
        {
            "query": query,
            "min_score": min_score,
            "limit": limit,
            "language": language,
            "filters": filters,
            "asOf": iso_date(as_of),
        }
    )
    if hyde:
        body["hyde"] = True
    if include_text:
        body["include"] = ["text"]
    return body


def recent_documents_params(
    *,
    days: int | None = None,
    document_type: OneOrMany | None = None,
    author: OneOrMany | None = None,
    domain: str | None = None,
    subdomain: OneOrMany | None = None,
    language: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Query params for /documents/recent. Lists are sent as repeated params."""
    return drop_none(
        {
            "days": days,
            "documentType": document_type,
            "author": author,
            "domain": domain,
            "subdomain": subdomain,
            "language": language,
            "limit": limit,
        }
    )


def webhook_create_body(
    name: str,
    url: str,
    search_criteria: dict[str, Any] | None = None,
    *,
    watch_celex: Sequence[str] | None = None,
    secret: str | None = None,
) -> dict[str, Any]:
    return drop_none(
        {
            "name": name,
            "url": url,
            "searchCriteria": search_criteria,
            "watchCelex": list(watch_celex) if watch_celex is not None else None,
            "secret": secret,
        }
    )


def webhook_update_body(
    *,
    name: str | None = None,
    url: str | None = None,
    search_criteria: dict[str, Any] | None = None,
    watch_celex: Sequence[str] | None = None,
    status: str | None = None,
    remove_search_criteria: bool = False,
) -> dict[str, Any]:
    """PUT body — only the passed fields are sent.

    ``remove_search_criteria=True`` sends ``searchCriteria: null`` (the API's
    way to drop the criteria; the webhook must keep a watch list).
    ``watch_celex`` replaces the whole list (``[]`` empties it).
    """
    if remove_search_criteria and search_criteria is not None:
        raise ValueError("pass either search_criteria or remove_search_criteria=True, not both")
    body = drop_none(
        {
            "name": name,
            "url": url,
            "searchCriteria": search_criteria,
            "watchCelex": list(watch_celex) if watch_celex is not None else None,
            "status": status,
        }
    )
    if remove_search_criteria:
        body["searchCriteria"] = None
    return body
