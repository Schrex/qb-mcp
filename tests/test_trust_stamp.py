"""The trust stamp (#25): one `stamp` on every tool result saying what the answer is based on
(filters applied) and whether it is complete or capped."""

import pytest

from qb_mcp.errors import TransportError, TransportFailure

pytestmark = pytest.mark.anyio

INVOICES = "<InvoiceQueryRq><MaxReturned>50</MaxReturned></InvoiceQueryRq>"

# The fewest arguments each tool needs; a tool missing here fails the coverage test below.
MINIMAL_ARGS = {
    "qb_status": {},
    "get_company_info": {},
    "list_vendors": {},
    "query_bills": {},
    "list_customer_jobs": {},
    "list_items": {},
    "list_accounts": {},
    "run_job_report": {"report": "job_profitability_summary", "from_date": "2024-01-01", "to_date": "2024-12-31"},
    "run_query": {"query": INVOICES},
}


async def test_every_tool_is_covered_by_the_stamp_tests(client):
    assert {t.name for t in (await client.list_tools()).tools} == set(MINIMAL_ARGS)


@pytest.mark.parametrize("tool", sorted(MINIMAL_ARGS))
async def test_every_tool_result_carries_a_trust_stamp(call, tool):
    result = await call(tool, **MINIMAL_ARGS[tool])

    assert not result.is_error
    stamp = result.structured_content["stamp"]
    assert isinstance(stamp["filters"], dict)
    assert isinstance(stamp["complete"], bool)


async def test_stamp_lists_the_filters_applied_and_not_output_options(call):
    out = (
        await call(
            "query_bills", from_date="2024-01-01", vendor="Timberloft Lumber", paid_status="unpaid",
            customer_job="Cook, Brian", include_lines=True, limit=50,
        )
    ).structured_content

    assert out["stamp"]["filters"] == {
        "from_date": "2024-01-01",
        "vendor": "Timberloft Lumber",
        "paid_status": "unpaid",
        "customer_job": "Cook, Brian",
    }


async def test_stamp_lists_default_filters_that_still_apply(call):
    jobs = (await call("list_customer_jobs")).structured_content
    accounts = (await call("list_accounts", account_type="Expense", status="all")).structured_content

    assert jobs["stamp"]["filters"] == {"status": "active"}
    assert accounts["stamp"]["filters"] == {"account_type": "Expense"}


async def test_run_query_stamp_lists_the_filters_in_the_query(call):
    query = (
        "<InvoiceQueryRq><MaxReturned>50</MaxReturned>"
        "<TxnDateRangeFilter><FromTxnDate>2024-01-01</FromTxnDate></TxnDateRangeFilter>"
        "<EntityFilter><FullName>Cook, Brian</FullName></EntityFilter>"
        "<IncludeLineItems>true</IncludeLineItems></InvoiceQueryRq>"
    )

    out = (await call("run_query", query=query)).structured_content

    assert out["stamp"]["filters"] == {
        "TxnDateRangeFilter": {"FromTxnDate": "2024-01-01"},
        "EntityFilter": {"FullName": "Cook, Brian"},
    }


async def test_capped_result_says_so_in_plain_words_and_how_to_narrow(call, replay):
    replay.respond_with("BillQueryRq", "BillQueryRq.capped.xml")

    out = (await call("query_bills", vendor="Perry Windows & Doors", limit=2)).structured_content

    assert [b["ref_number"] for b in out["results"]] == ["CAP-1", "CAP-2"]
    assert out["stamp"]["complete"] is False
    assert out["stamp"]["cap_notice"] == (
        "Showing the first 2; more exist; totals incomplete. Narrow by date or Vendor."
    )


async def test_capped_result_carries_no_total(call, replay):
    replay.respond_with("BillQueryRq", "BillQueryRq.capped.xml")

    out = (await call("query_bills", vendor="Perry Windows & Doors", limit=2)).structured_content

    assert not [key for key in out if "total" in key.casefold() or "sum" in key.casefold()]


async def test_uncapped_result_says_it_is_complete(call, replay):
    replay.respond_with("BillQueryRq", "BillQueryRq.capped.xml")

    out = (await call("query_bills", vendor="Perry Windows & Doors", limit=3)).structured_content

    assert out["count"] == 3
    assert out["stamp"]["complete"] is True
    assert out["stamp"]["cap_notice"] is None


async def test_each_capped_tool_names_its_own_way_to_narrow(call):
    vendors = (await call("list_vendors", limit=1)).structured_content
    invoices = (await call("run_query", query=INVOICES, limit=1)).structured_content

    assert vendors["stamp"]["cap_notice"] == (
        "Showing the first 1; more exist; totals incomplete. Narrow by name_contains or status."
    )
    assert invoices["stamp"]["cap_notice"] == (
        "Showing the first 1; more exist; totals incomplete. Narrow by date, Vendor or Customer:Job."
    )


# --- closing date (#26) -------------------------------------------------------------------------
# Hand-written Preferences fixtures: closing date 2024-06-30, and none set.
CLOSING_DATE = "PreferencesQueryRq.closingdate.xml"
NO_CLOSING_DATE = "PreferencesQueryRq.noclosingdate.xml"


@pytest.mark.parametrize("tool", sorted(MINIMAL_ARGS))
async def test_every_tool_result_carries_the_closing_date(call, replay, tool):
    replay.respond_with("PreferencesQueryRq", CLOSING_DATE)

    stamp = (await call(tool, **MINIMAL_ARGS[tool])).structured_content["stamp"]

    assert stamp["closing_date"] == "2024-06-30"


@pytest.mark.parametrize(
    "from_date,to_date,period",
    [
        ("2024-01-01", "2024-03-31", "closed"),  # before the closing date
        ("2024-01-01", "2024-06-30", "closed"),  # ends on it
        ("2024-01-01", "2024-12-31", "may still change"),  # spans it
        ("2024-07-01", "2024-12-31", "may still change"),  # after it
    ],
)
async def test_stamp_says_whether_the_date_range_is_closed(call, replay, from_date, to_date, period):
    replay.respond_with("PreferencesQueryRq", CLOSING_DATE)

    bills = (await call("query_bills", from_date=from_date, to_date=to_date)).structured_content
    report = (
        await call("run_job_report", report="job_profitability_summary", from_date=from_date, to_date=to_date)
    ).structured_content

    assert bills["stamp"]["period"] == period
    assert report["stamp"]["period"] == period


@pytest.mark.parametrize("to_date,period", [("2024-05-31", "closed"), ("2024-07-31", "may still change")])
async def test_run_query_stamp_says_whether_the_query_date_range_is_closed(call, replay, to_date, period):
    replay.respond_with("PreferencesQueryRq", CLOSING_DATE)
    query = (
        "<InvoiceQueryRq><MaxReturned>50</MaxReturned><TxnDateRangeFilter>"
        f"<FromTxnDate>2024-01-01</FromTxnDate><ToTxnDate>{to_date}</ToTxnDate></TxnDateRangeFilter></InvoiceQueryRq>"
    )

    out = (await call("run_query", query=query)).structured_content

    assert out["stamp"]["period"] == period


async def test_results_up_to_today_may_still_change(call, replay):
    replay.respond_with("PreferencesQueryRq", CLOSING_DATE)

    bills = (await call("query_bills", from_date="2024-01-01")).structured_content
    vendors = (await call("list_vendors")).structured_content

    assert bills["stamp"]["period"] == "may still change"
    assert vendors["stamp"]["period"] == "may still change"


async def test_stamp_says_closing_date_unknown_when_quickbooks_has_none(call, replay):
    replay.respond_with("PreferencesQueryRq", NO_CLOSING_DATE)

    stamp = (await call("query_bills", from_date="2024-01-01", to_date="2024-03-31")).structured_content["stamp"]

    assert stamp["closing_date"] is None
    assert stamp["period"] == "closing date unknown"


async def test_closing_date_is_read_once_per_session(call, replay):
    replay.respond_with("PreferencesQueryRq", CLOSING_DATE)

    await call("list_vendors")
    await call("query_bills", to_date="2024-03-31")
    last = (await call("get_company_info")).structured_content["stamp"]

    # A *QueryRq, so the read-only guard lets it through; the Replay transport schema-validates it
    # (an invalid request would leave the closing date unknown).
    assert sum("<PreferencesQueryRq" in rq for rq in replay.sent) == 1
    assert last["closing_date"] == "2024-06-30"


async def test_run_query_still_refuses_preferences_queries(call, replay):
    result = await call("run_query", query="<PreferencesQueryRq></PreferencesQueryRq>")

    assert result.is_error
    assert replay.sent == []


async def test_closing_date_is_read_again_after_quickbooks_was_unreachable(call, replay):
    replay.respond_with("PreferencesQueryRq", CLOSING_DATE)
    replay.fail_with(TransportError(TransportFailure.QB_NOT_RUNNING))
    down = (await call("qb_status")).structured_content["stamp"]
    replay.fail_with(None)

    up = (await call("list_vendors")).structured_content["stamp"]

    assert down["period"] == "closing date unknown"
    assert up["closing_date"] == "2024-06-30"
