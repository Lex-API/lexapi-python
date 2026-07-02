"""Request body / query-param builders shared by the sync and async clients.

``None`` values are always omitted so the server's own defaults apply and
older deployments never see parameters they don't know.
"""

from __future__ import annotations

from typing import Any, Union

# Filters that accept a single value or a list (the API accepts both).
OneOrMany = Union[str, "list[str]"]
IntOrMany = Union[int, "list[int]"]


def drop_none(mapping: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in mapping.items() if value is not None}


def search_body(
    query: str | None = None,
    *,
    text_scope: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    year: IntOrMany | None = None,
    month: IntOrMany | None = None,
    document_type: OneOrMany | None = None,
    author: OneOrMany | None = None,
    procedure: OneOrMany | None = None,
    domain: str | None = None,
    subdomain: OneOrMany | None = None,
    language: str | None = None,
    max_pages: int | None = None,
) -> dict[str, Any]:
    return drop_none(
        {
            "query": query,
            "textScope": text_scope,
            "dateFrom": date_from,
            "dateTo": date_to,
            "year": year,
            "month": month,
            "documentType": document_type,
            "author": author,
            "procedure": procedure,
            "domain": domain,
            "subdomain": subdomain,
            "language": language,
            "maxPages": max_pages,
        }
    )


def document_content_body(
    celex: str,
    *,
    bypass_corpus: bool | None = None,
    language: str | None = None,
    include: OneOrMany | None = None,
    fields: OneOrMany | None = None,
    article_id: str | None = None,
) -> dict[str, Any]:
    return drop_none(
        {
            "celexNumber": celex,
            "bypassCorpus": bypass_corpus,
            "language": language,
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
    include: OneOrMany | None = None,
    fields: OneOrMany | None = None,
    article_id: str | None = None,
) -> dict[str, Any]:
    return drop_none(
        {
            "celexNumbers": list(celexes),
            "bypassCorpus": bypass_corpus,
            "language": language,
            "include": include,
            "fields": fields,
            "articleId": article_id,
        }
    )


def document_by_url_body(
    url: str,
    *,
    include: OneOrMany | None = None,
    fields: OneOrMany | None = None,
    article_id: str | None = None,
) -> dict[str, Any]:
    return drop_none(
        {"url": url, "include": include, "fields": fields, "articleId": article_id}
    )


def semantic_search_body(
    query: str,
    *,
    hyde: bool = False,
    min_score: float | None = None,
    limit: int | None = None,
    language: str | None = None,
    include_text: bool = False,
    filters: dict | None = None,
) -> dict[str, Any]:
    """Body for the semantic endpoints.

    ``hyde`` is only sent when True (additive param — older deployments
    never see it); ``include_text=True`` maps to ``include: ["text"]``.
    """
    body = drop_none(
        {
            "query": query,
            "min_score": min_score,
            "limit": limit,
            "language": language,
            "filters": filters,
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
    document_type: str | None = None,
    author: str | None = None,
    domain: str | None = None,
    subdomain: str | None = None,
    language: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
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
