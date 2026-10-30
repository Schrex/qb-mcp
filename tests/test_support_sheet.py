"""Jeri's support sheet (#34): every starter prompt on docs/jeri-support-sheet.md is answered
by a tool call against the sample-file fixtures, with no error and a trust stamp."""

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.anyio

SHEET = Path(__file__).parent.parent / "docs" / "jeri-support-sheet.md"
JOB = "Abercrombie, Kristy:Remodel Bathroom"
YEAR = {"from_date": "2024-01-01", "to_date": "2025-12-31"}

# Starter prompt (exactly as printed on the sheet) -> (tool, args) Claude would use to answer it,
# plus the fixture variant to answer with when the base fixture isn't the right sample.
STARTER_PROMPTS = {
    "Which Bills from Patton Hardware Supplies are still unpaid?": (
        "query_bills", {"vendor": "Patton Hardware Supplies", "paid_status": "unpaid"}, None,
    ),
    "Is Bill 930357 from Patton Hardware Supplies already entered?": (
        "query_bills", {"ref_number": "930357", "vendor": "Patton Hardware Supplies"}, None,
    ),
    "What do we owe Vendors right now, oldest Bills first?": (
        "query_bills", {"paid_status": "unpaid"}, None,
    ),
    f"Which Bills were charged to {JOB} this year?": (
        "query_bills", {"customer_job": JOB, **YEAR}, None,
    ),
    f"What are the costs and income on {JOB}?": (
        "run_job_report", {"report": "job_profitability_detail", "customer_job": JOB, **YEAR}, None,
    ),
    f"How does {JOB} compare to its estimate?": (
        "run_job_report", {"report": "job_estimates_vs_actuals_detail", "customer_job": JOB, **YEAR}, None,
    ),
    "Which Customer:Jobs are in progress?": (
        "list_customer_jobs", {"job_status": ["in_progress"]}, None,
    ),
    "How profitable are the Customer:Jobs in progress this year?": (
        "run_job_report", {"report": "job_profitability_summary", "job_status": ["in_progress"], **YEAR}, None,
    ),
    "What card charges went on Burch, Jason:Room Addition this year?": (
        "run_query",
        {
            "query": "<CreditCardChargeQueryRq><TxnDateRangeFilter><FromTxnDate>2025-01-01</FromTxnDate>"
            "<ToTxnDate>2025-12-31</ToTxnDate></TxnDateRangeFilter><IncludeLineItems>true</IncludeLineItems>"
            "</CreditCardChargeQueryRq>"
        },
        None,
    ),
    # No time entries were recorded from the sample file yet: answered from the hand-written fixture.
    "How many hours went on Acme:Main St Paving in March?": (
        "run_query",
        {
            "query": "<TimeTrackingQueryRq><TxnDateRangeFilter><FromTxnDate>2024-03-01</FromTxnDate>"
            "<ToTxnDate>2024-03-31</ToTxnDate></TxnDateRangeFilter></TimeTrackingQueryRq>"
        },
        ("TimeTrackingQueryRq", "TimeTrackingQueryRq.sensitive.xml"),
    ),
}


def printed_prompts() -> list[str]:
    """The starter prompts on the sheet: numbered list items in quotes."""
    return re.findall(r'^\d+\. "(.+)"$', SHEET.read_text(), flags=re.MULTILINE)


def test_sheet_prints_8_to_10_starter_prompts_each_one_verified_here():
    prompts = printed_prompts()

    assert 8 <= len(prompts) <= 10
    assert set(prompts) == set(STARTER_PROMPTS)


@pytest.mark.parametrize("prompt", sorted(STARTER_PROMPTS))
async def test_starter_prompt_gets_a_stamped_answer(call, replay, prompt):
    tool, args, fixture = STARTER_PROMPTS[prompt]
    if fixture:
        replay.respond_with(*fixture)

    result = await call(tool, **args)

    assert not result.is_error, result.content
    stamp = result.structured_content["stamp"]
    assert stamp["check_in_quickbooks"]
    assert stamp["period"] in {"closed", "may still change", "closing date unknown"}
