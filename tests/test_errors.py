"""Error-mapping tests: typed envelope, legacy shapes, unknown codes."""

import pytest

from lexapi.errors import (
    AuthenticationError,
    CreditsExhaustedError,
    InternalServerError,
    InvalidCelexError,
    InvalidParamsError,
    InvalidURLError,
    LexAPIError,
    NotFoundError,
    RateLimitedError,
    TierForbiddenError,
    TimeoutError,
    UpstreamError,
    error_from_response,
)


def envelope(code, message="boom", details=None):
    err = {"code": code, "message": message}
    if details is not None:
        err["details"] = details
    return {"success": False, "error": err}


@pytest.mark.parametrize(
    ("status", "code", "expected_cls"),
    [
        (404, "NOT_FOUND", NotFoundError),
        (400, "INVALID_CELEX", InvalidCelexError),
        (400, "INVALID_URL", InvalidURLError),
        (400, "INVALID_PARAMS", InvalidParamsError),
        (429, "RATE_LIMITED", RateLimitedError),
        (403, "TIER_FORBIDDEN", TierForbiddenError),
        (402, "CREDITS_EXHAUSTED", CreditsExhaustedError),
        (502, "UPSTREAM_ERROR", UpstreamError),
        (504, "TIMEOUT", TimeoutError),
        (500, "INTERNAL", InternalServerError),
    ],
)
def test_typed_envelope_maps_every_spec_code(status, code, expected_cls):
    err = error_from_response(status, envelope(code))
    assert type(err) is expected_cls
    assert err.code == code
    assert err.status == status
    assert err.message == "boom"


def test_401_maps_to_authentication_error():
    err = error_from_response(401, {"error": "Invalid API key"})
    assert isinstance(err, AuthenticationError)
    assert err.code == "UNAUTHORIZED"
    assert err.message == "Invalid API key"


def test_unknown_code_preserved_on_base_error():
    err = error_from_response(418, envelope("TEAPOT_MODE", "I'm a teapot"))
    assert type(err) is LexAPIError
    assert err.code == "TEAPOT_MODE"
    assert err.message == "I'm a teapot"


def test_details_carried_through():
    details = {"operation": "search", "weight": 3}
    err = error_from_response(402, envelope("CREDITS_EXHAUSTED", details=details))
    assert err.details == details


def test_legacy_bare_error_derives_code_from_status():
    err = error_from_response(404, {"error": "No such document"})
    assert isinstance(err, NotFoundError)
    assert err.code == "NOT_FOUND"
    assert err.message == "No such document"


def test_legacy_error_string_is_text_not_a_code():
    # PLAN §1.3: a string `error` is human text; the code comes from the status.
    err = error_from_response(429, {"error": "RATE_LIMITED", "message": "Slow down"})
    assert isinstance(err, RateLimitedError)
    assert err.code == "RATE_LIMITED"
    assert err.message == "RATE_LIMITED: Slow down"
    # a 400 whose text happens to equal a slug is NOT misread as that code
    err = error_from_response(400, {"error": "NOT_FOUND"})
    assert isinstance(err, InvalidParamsError)
    assert err.code == "INVALID_PARAMS"


def test_legacy_prose_error_with_extra_message():
    err = error_from_response(400, {"error": "Bad input", "message": "field x missing"})
    assert isinstance(err, InvalidParamsError)
    assert "Bad input" in err.message
    assert "field x missing" in err.message


def test_non_json_body_falls_back_to_status():
    err = error_from_response(503, None)
    assert isinstance(err, UpstreamError)  # ServiceUnavailableError subclasses it
    assert err.code == "SERVICE_UNAVAILABLE"
    assert err.message == "HTTP 503"
    err = error_from_response(502, None)
    assert err.code == "UPSTREAM_ERROR"


def test_credits_exhausted_resets_at_from_details():
    details = {"resetsAt": "2026-08-01T00:00:00.000Z", "remaining": 0}
    err = error_from_response(402, envelope("CREDITS_EXHAUSTED", details=details))
    assert isinstance(err, CreditsExhaustedError)
    assert err.resets_at == "2026-08-01T00:00:00.000Z"


def test_credits_exhausted_resets_at_from_body_usage():
    body = {
        "success": False,
        "error": {"code": "CREDITS_EXHAUSTED", "message": "empty"},
        "usage": {"remaining": 0, "resetsAt": "2026-08-01T00:00:00.000Z"},
    }
    err = error_from_response(402, body)
    assert err.resets_at == "2026-08-01T00:00:00.000Z"


def test_credits_exhausted_resets_at_absent():
    err = error_from_response(402, envelope("CREDITS_EXHAUSTED"))
    assert err.resets_at is None


def test_error_str_includes_code_and_status():
    err = error_from_response(404, envelope("NOT_FOUND", "gone"))
    assert str(err) == "[NOT_FOUND] gone (HTTP 404)"


def test_body_preserved_on_error():
    body = envelope("NOT_FOUND")
    err = error_from_response(404, body)
    assert err.body == body
