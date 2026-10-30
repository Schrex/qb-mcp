"""Voided and non-posting transactions (#28): voids stay listable, marked void, but are left out of
totals and counted in the trust stamp; Estimates and Purchase Orders are labelled non-posting and
never summed with posting transactions."""

import pytest

pytestmark = pytest.mark.anyio

CHECKS = "<CheckQueryRq><MaxReturned>101</MaxReturned></CheckQueryRq>"


async def test_voided_bill_is_still_listed_and_marked_void(call, replay):
    replay.respond_with("BillQueryRq", "BillQueryRq.voided.xml")

    out = (await call("query_bills", vendor="Perry Windows & Doors")).structured_content

    assert {b["ref_number"]: b["is_void"] for b in out["results"]} == {
        "PW-201": False, "PW-202": False, "PW-203": True,
    }


async def test_bill_totals_leave_out_voided_bills_and_the_stamp_counts_them(call, replay):
    replay.respond_with("BillQueryRq", "BillQueryRq.voided.xml")

    out = (await call("query_bills", vendor="Perry Windows & Doors")).structured_content

    # PW-201 (1000.00, all open) + PW-202 (250.00, 150.00 open); PW-203 is voided.
    assert out["totals"] == {"amount": "1250.00", "open_balance": "1150.00"}
    assert out["stamp"]["voided_excluded"] == 1
    assert out["stamp"]["voided_listed"] == 1


async def test_voided_bills_without_a_total_are_counted_as_listed_not_excluded(call, replay):
    replay.respond_with("BillQueryRq", "BillQueryRq.voided.xml")

    capped = (await call("query_bills", vendor="Perry Windows & Doors", limit=2)).structured_content
    replay.respond_with("BillQueryRq", "BillQueryRq.voided.xml")
    whole = (await call("query_bills", vendor="Perry Windows & Doors")).structured_content

    assert "totals" not in capped
    assert capped["stamp"]["voided_excluded"] is None  # no total, so nothing was excluded from one
    assert whole["stamp"]["voided_listed"] == 1


async def test_bills_cut_to_a_customer_job_carry_no_total(call):
    # A Bill's amount includes its lines for other Customer:Jobs, so it can't be summed for one job.
    out = (await call("query_bills", customer_job="Vasquez, Anabel")).structured_content

    assert out["count"] > 0
    assert "totals" not in out


async def test_voided_check_is_still_listed_marked_void_and_counted(call, replay):
    replay.respond_with("CheckQueryRq", "CheckQueryRq.voided.xml")

    out = (await call("run_query", query=CHECKS)).structured_content

    assert {c["RefNumber"]: c["is_void"] for c in out["results"]} == {"6001": False, "6002": True}
    assert out["stamp"]["voided_listed"] == 1
    assert out["stamp"]["voided_excluded"] is None  # run_query computes no total


async def test_nothing_voided_means_none_excluded(call):
    out = (await call("query_bills")).structured_content

    assert out["stamp"]["voided_excluded"] == 0
    assert out["stamp"]["voided_listed"] == 0
    assert out["stamp"]["non_posting_listed"] == 0


@pytest.mark.parametrize(
    "rq,fixture",
    [
        ("EstimateQueryRq", "EstimateQueryRq.nonposting.xml"),
        ("PurchaseOrderQueryRq", "PurchaseOrderQueryRq.nonposting.xml"),
    ],
)
async def test_estimates_and_purchase_orders_are_listed_labelled_non_posting_and_never_totalled(
    call, replay, rq, fixture
):
    replay.respond_with(rq, fixture)

    out = (await call("run_query", query=f"<{rq}><MaxReturned>101</MaxReturned></{rq}>")).structured_content

    assert out["count"] == 1
    assert out["results"][0]["posting"] is False
    assert out["stamp"]["non_posting_listed"] == 1
    assert not [key for key in out if "total" in key.casefold() or "sum" in key.casefold()]


async def test_checks_and_bills_are_labelled_posting(call, replay):
    replay.respond_with("CheckQueryRq", "CheckQueryRq.voided.xml")

    checks = (await call("run_query", query=CHECKS)).structured_content
    bills = (await call("query_bills")).structured_content

    assert {c["posting"] for c in checks["results"]} == {True}
    assert {b["posting"] for b in bills["results"]} == {True}
