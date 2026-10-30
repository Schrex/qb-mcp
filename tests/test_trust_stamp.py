"""The trust stamp (#25): one `stamp` on every tool result saying what the answer is based on
(filters applied) and whether it is complete or capped."""

import pytest

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
