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
