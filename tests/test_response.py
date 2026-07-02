"""Credit-visibility helpers on the response wrapper (SDK contract §1.5)."""

import httpx

from lexapi import APIResponse


def test_credits_block_parsed():
    resp = APIResponse(
        {
            "credits": {
                "operation_weight": 5,
                "current": 42,
                "limit": 1000,
                "remaining": 958,
                "included": 1000,
                "topup": 0,
                "resetsAt": "2026-08-01T00:00:00.000Z",
            }
        }
    )
    credits = resp.credits
    assert credits is not None
    assert credits.operation_weight == 5
    assert credits.remaining == 958
    assert credits.included == 1000
    assert credits.topup == 0
    assert credits.resets_at == "2026-08-01T00:00:00.000Z"
    assert resp.units_charged == 5
    assert resp.credits_remaining == 958
    assert resp.resets_at == "2026-08-01T00:00:00.000Z"


def test_credits_raw_preserved():
    raw = {"operation_weight": 1, "future_field": "x"}
    resp = APIResponse({"credits": raw})
    assert resp.credits.raw == raw


def test_legacy_account_falls_back_to_usage():
    resp = APIResponse(
        {
            "usage": {
                "current": 3,
                "limit": 100,
                "remaining": 97,
                "unit": "calls",
                "resetsAt": "2026-07-03T00:00:00.000Z",
            }
        }
    )
    assert resp.credits is None
    assert resp.usage is not None
    assert resp.usage.unit == "calls"
    assert resp.units_charged == 0
    assert resp.credits_remaining == 97
    assert resp.resets_at == "2026-07-03T00:00:00.000Z"


def test_both_blocks_absent():
    resp = APIResponse({"success": True})
    assert resp.credits is None
    assert resp.usage is None
    assert resp.units_charged == 0
    assert resp.credits_remaining is None
    assert resp.resets_at is None


def test_free_operation_zero_weight():
    resp = APIResponse({"credits": {"operation_weight": 0, "remaining": 100}})
    assert resp.units_charged == 0
    assert resp.credits_remaining == 100


def test_warning_header_exposed():
    resp = APIResponse({}, httpx.Headers({"X-Warning": "maxPages clamped to 1"}))
    assert resp.warning == "maxPages clamped to 1"
    assert APIResponse({}).warning is None


def test_subscription_block():
    resp = APIResponse({"subscription": {"tier": "STARTER"}})
    assert resp.subscription == {"tier": "STARTER"}


def test_malformed_blocks_tolerated():
    resp = APIResponse({"credits": "nope", "usage": 3})
    assert resp.credits is None
    assert resp.usage is None
    assert resp.units_charged == 0
