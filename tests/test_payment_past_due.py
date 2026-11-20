"""``PAYMENT_PAST_DUE`` (403) and the unknown-endpoint 404 (API 2.27.0)."""

import asyncio

import httpx
import pytest

import lexapi
from conftest import FAST_RETRY
from lexapi import (
    AsyncLexAPI,
    LexAPI,
    LexAPIError,
    NotFoundError,
    PaymentPastDueError,
    TierForbiddenError,
)
from lexapi.errors import CODE_TO_ERROR, error_from_response

ENDED_AT = "2026-11-25T09:14:03.000Z"
BILLING_URL = "https://lex-api.com/dashboard#billing"

PAST_DUE_BODY = {
    "success": False,
    "code": "PAYMENT_PAST_DUE",
    "error": "Payment past due",
    "message": "Your subscription payment failed, so API requests are refused.",
    "accessEndedAt": ENDED_AT,
    "billingUrl": BILLING_URL,
}

PAST_DUE_TYPED_BODY = {
    "success": False,
    "error": {
        "code": "PAYMENT_PAST_DUE",
        "message": "Payment past due",
        "details": {"accessEndedAt": ENDED_AT, "billingUrl": BILLING_URL},
    },
}

UNKNOWN_ENDPOINT_BODY = {
    "success": False,
    "error": {
        "code": "NOT_FOUND",
        "message": "Unknown API endpoint: GET /api/v1/serach. See the endpoint reference.",
        "details": {"reason": "unknown_endpoint"},
    },
}


def without(body, *keys):
    return {k: v for k, v in body.items() if k not in keys}


def raised_sync(status, body, call=lambda c: c.get_info()):
    """The error a sync call raises, and how many requests were sent."""
    calls = {"count": 0}

    def handler(request):
        calls["count"] += 1
        return httpx.Response(status, json=body, headers={"RateLimit-Limit": "60"})

    client = LexAPI(
        api_key="lex_test_key", transport=httpx.MockTransport(handler), retry_config=FAST_RETRY
    )
    with client, pytest.raises(LexAPIError) as info:
        call(client)
    return info.value, calls["count"]


def raised_async(status, body, call=lambda c: c.get_info()):
    calls = {"count": 0}

    def handler(request):
        calls["count"] += 1
        return httpx.Response(status, json=body, headers={"RateLimit-Limit": "60"})

    async def run():
        async with AsyncLexAPI(
            api_key="lex_test_key",
            transport=httpx.MockTransport(handler),
            retry_config=FAST_RETRY,
        ) as client:
            with pytest.raises(LexAPIError) as info:
                await call(client)
            return info.value

    return asyncio.run(run()), calls["count"]


RAISED = [raised_sync, raised_async]


# --- PAYMENT_PAST_DUE -------------------------------------------------------


def test_payment_past_due_is_exported_and_registered():
    assert lexapi.PaymentPastDueError is PaymentPastDueError
    assert "PaymentPastDueError" in lexapi.__all__
    assert CODE_TO_ERROR["PAYMENT_PAST_DUE"] is PaymentPastDueError
    assert PaymentPastDueError.default_code == "PAYMENT_PAST_DUE"
    assert issubclass(PaymentPastDueError, LexAPIError)
    # A subclass, so `except TierForbiddenError` keeps catching a failed payment.
    assert issubclass(PaymentPastDueError, TierForbiddenError)


@pytest.mark.parametrize("raised", RAISED)
def test_legacy_shaped_past_due_body(raised):
    err, calls = raised(403, PAST_DUE_BODY)
    assert type(err) is PaymentPastDueError
    assert isinstance(err, LexAPIError)
    assert isinstance(err, TierForbiddenError)
    assert err.code == "PAYMENT_PAST_DUE"
    assert err.status == 403
    assert err.message.startswith("Payment past due: Your subscription payment failed")
    assert err.access_ended_at == ENDED_AT
    assert err.billing_url == BILLING_URL
    assert err.retry_after is None
    assert err.body == PAST_DUE_BODY
    assert calls == 1


@pytest.mark.parametrize("raised", RAISED)
def test_past_due_without_access_ended_at(raised):
    err, calls = raised(403, without(PAST_DUE_BODY, "accessEndedAt"))
    assert type(err) is PaymentPastDueError
    assert err.access_ended_at is None
    assert err.billing_url == BILLING_URL
    assert calls == 1


@pytest.mark.parametrize("raised", RAISED)
@pytest.mark.parametrize("value", [None, 1764061200000, True, ["x"], {"at": ENDED_AT}])
def test_past_due_non_string_fields_are_none(raised, value):
    err, _ = raised(403, {**PAST_DUE_BODY, "accessEndedAt": value, "billingUrl": value})
    assert type(err) is PaymentPastDueError
    assert err.access_ended_at is None
    assert err.billing_url is None


@pytest.mark.parametrize("raised", RAISED)
def test_past_due_typed_envelope(raised):
    err, calls = raised(403, PAST_DUE_TYPED_BODY)
    assert type(err) is PaymentPastDueError
    assert err.code == "PAYMENT_PAST_DUE"
    assert err.access_ended_at == ENDED_AT
    assert err.billing_url == BILLING_URL
    assert calls == 1


@pytest.mark.parametrize("raised", RAISED)
def test_past_due_details_take_precedence_over_the_top_level(raised):
    details = {"accessEndedAt": "2026-12-01T00:00:00.000Z", "billingUrl": "https://x.test/d"}
    err, _ = raised(403, {**PAST_DUE_BODY, "details": details})
    assert type(err) is PaymentPastDueError
    assert err.access_ended_at == "2026-12-01T00:00:00.000Z"
    assert err.billing_url == "https://x.test/d"
    # Each field on its own: the other still comes from the top level.
    err, _ = raised(403, {**PAST_DUE_BODY, "details": {"billingUrl": "https://x.test/d"}})
    assert err.billing_url == "https://x.test/d"
    assert err.access_ended_at == ENDED_AT
    err, _ = raised(403, {**PAST_DUE_BODY, "details": {"accessEndedAt": "2026-12-01"}})
    assert err.access_ended_at == "2026-12-01"
    assert err.billing_url == BILLING_URL
    # A value present in details is used even when it is not a string.
    err, _ = raised(403, {**PAST_DUE_BODY, "details": {"accessEndedAt": 5, "billingUrl": None}})
    assert err.access_ended_at is None
    assert err.billing_url is None


def test_past_due_typed_envelope_partial_and_non_string_details():
    body = {"error": {"code": "PAYMENT_PAST_DUE", "message": "m", "details": {"billingUrl": 7}}}
    err = error_from_response(403, body)
    assert type(err) is PaymentPastDueError
    assert err.access_ended_at is None
    assert err.billing_url is None
    no_details = error_from_response(403, {"error": {"code": "PAYMENT_PAST_DUE", "message": "m"}})
    assert type(no_details) is PaymentPastDueError
    assert no_details.access_ended_at is None
    assert no_details.billing_url is None


@pytest.mark.parametrize("raised", RAISED)
def test_past_due_is_not_retried_on_any_kind_of_call(raised):
    # A free GET, a billed GET and a POST: one request each.
    for call in (
        lambda c: c.get_info(),
        lambda c: c.citations.stats(),
        lambda c: c.search("data protection"),
    ):
        err, calls = raised(403, PAST_DUE_BODY, call)
        assert type(err) is PaymentPastDueError
        assert calls == 1


@pytest.mark.parametrize("raised", RAISED)
def test_past_due_with_retry_after_header_is_still_not_retried(raised):
    calls = {"count": 0}

    def handler(request):
        calls["count"] += 1
        return httpx.Response(
            403, json=PAST_DUE_BODY, headers={"Retry-After": "0", "RateLimit-Limit": "60"}
        )

    if raised is raised_sync:
        with LexAPI(
            api_key="k", transport=httpx.MockTransport(handler), retry_config=FAST_RETRY
        ) as client:
            with pytest.raises(PaymentPastDueError):
                client.get_info()
    else:

        async def run():
            async with AsyncLexAPI(
                api_key="k", transport=httpx.MockTransport(handler), retry_config=FAST_RETRY
            ) as client:
                with pytest.raises(PaymentPastDueError):
                    await client.get_info()

        asyncio.run(run())
    assert calls["count"] == 1


# --- other 403s are unchanged -----------------------------------------------


@pytest.mark.parametrize("raised", RAISED)
def test_typed_tier_forbidden_is_unchanged(raised):
    body = {"success": False, "error": {"code": "TIER_FORBIDDEN", "message": "No plan"}}
    err, calls = raised(403, body)
    assert type(err) is TierForbiddenError
    assert not isinstance(err, PaymentPastDueError)
    assert err.code == "TIER_FORBIDDEN"
    assert calls == 1


@pytest.mark.parametrize("raised", RAISED)
@pytest.mark.parametrize(
    "body",
    [
        {
            "error": "Subscription inactive",
            "message": "Please activate your subscription or downgrade to the FREE tier",
        },
        {"error": "No subscription found"},
        # The fields alone, without the code, do not make it a past-due error.
        without(PAST_DUE_BODY, "code"),
    ],
)
def test_code_less_403_is_still_tier_forbidden(raised, body):
    err, calls = raised(403, body)
    assert type(err) is TierForbiddenError
    assert err.code == "TIER_FORBIDDEN"
    assert not hasattr(err, "billing_url")
    assert calls == 1


def test_past_due_code_on_the_base_class_only_through_the_registry():
    # An unknown code still falls back to the base class.
    err = error_from_response(403, {**PAST_DUE_BODY, "code": "PAYMENT_SOMETHING_NEW"})
    assert type(err) is LexAPIError
    assert err.code == "PAYMENT_SOMETHING_NEW"


# --- unknown endpoint -------------------------------------------------------


@pytest.mark.parametrize("raised", RAISED)
def test_unknown_endpoint_reason(raised):
    err, calls = raised(404, UNKNOWN_ENDPOINT_BODY)
    assert type(err) is NotFoundError
    assert err.code == "NOT_FOUND"
    assert err.unknown_endpoint is True
    assert calls == 1


@pytest.mark.parametrize("raised", RAISED)
def test_document_404_is_not_an_unknown_endpoint(raised):
    body = {
        "success": False,
        "error": {"code": "NOT_FOUND", "message": "Document 39999X9999 does not exist (EN)"},
    }
    err, _ = raised(404, body, lambda c: c.get_document("39999X9999"))
    assert type(err) is NotFoundError
    assert err.unknown_endpoint is False


def test_unknown_endpoint_is_never_inferred_from_the_message():
    # An API before 2.27.0: same message, no reason.
    body = {
        "success": False,
        "error": {"code": "NOT_FOUND", "message": UNKNOWN_ENDPOINT_BODY["error"]["message"]},
    }
    err = error_from_response(404, body)
    assert isinstance(err, NotFoundError)
    assert err.message.startswith("Unknown API endpoint")
    assert err.unknown_endpoint is False


@pytest.mark.parametrize(
    "details",
    [
        None,
        {},
        {"reason": "not_live"},
        {"reason": "UNKNOWN_ENDPOINT"},
        {"reason": "unknown_endpoint "},
        {"reason": None},
        {"reason": True},
        {"reason": 1},
        {"reason": ["unknown_endpoint"]},
        {"reason": {"unknown_endpoint": True}},
        {"other": "unknown_endpoint"},
        ["unknown_endpoint"],
        "unknown_endpoint",
    ],
)
def test_other_details_are_not_an_unknown_endpoint(details):
    error = {"code": "NOT_FOUND", "message": "Not found"}
    if details is not None:
        error["details"] = details
    err = error_from_response(404, {"success": False, "error": error})
    assert isinstance(err, NotFoundError)
    assert err.unknown_endpoint is False


def test_unknown_endpoint_false_on_legacy_and_empty_404s():
    for body in (
        {"success": False, "code": "NOT_FOUND", "error": "Document not found: AT-NOR-1"},
        {"error": "Webhook not found"},
        None,
        "<html>404</html>",
    ):
        err = error_from_response(404, body)
        assert isinstance(err, NotFoundError)
        assert err.unknown_endpoint is False


def test_unknown_endpoint_true_only_from_details():
    # A top-level `reason` beside a legacy-shape body is not the documented field.
    body = {"success": False, "code": "NOT_FOUND", "error": "nope", "reason": "unknown_endpoint"}
    top_level = error_from_response(404, body)
    assert isinstance(top_level, NotFoundError)
    assert top_level.unknown_endpoint is False
    body = {"code": "NOT_FOUND", "error": "nope", "details": {"reason": "unknown_endpoint"}}
    in_details = error_from_response(404, body)
    assert isinstance(in_details, NotFoundError)
    assert in_details.unknown_endpoint is True


@pytest.mark.parametrize("raised", RAISED)
def test_except_tier_forbidden_still_catches_a_failed_payment(raised):
    err, _ = raised(403, PAST_DUE_BODY)
    try:
        raise err
    except PaymentPastDueError as caught:  # the specific clause first
        assert caught.billing_url == PAST_DUE_BODY["billingUrl"]
    try:
        raise err
    except TierForbiddenError as caught:  # a 0.4.0 handler
        assert caught.code == "PAYMENT_PAST_DUE"
        assert caught.status == 403


def test_the_factory_picks_the_specific_class_for_either_envelope():
    from lexapi.errors import error_from_response

    typed = {"success": False, "error": {"code": "PAYMENT_PAST_DUE", "message": "x"}}
    for body in (PAST_DUE_BODY, typed):
        err = error_from_response(403, body)
        assert type(err) is PaymentPastDueError
        assert err.code == "PAYMENT_PAST_DUE"
    assert type(error_from_response(403, {"error": "x", "code": "TIER_FORBIDDEN"})) is (
        TierForbiddenError
    )


def test_pickle_and_repr_keep_the_class_and_fields():
    import pickle

    from lexapi.errors import error_from_response

    err = error_from_response(403, PAST_DUE_BODY)
    again = pickle.loads(pickle.dumps(err))
    assert type(again) is PaymentPastDueError and isinstance(again, TierForbiddenError)
    assert again.billing_url == err.billing_url and again.access_ended_at == err.access_ended_at
    assert str(again) == str(err)
    assert repr(err).startswith("PaymentPastDueError(")
