"""Job status filter (#31) on list_customer_jobs and run_job_report.

QuickBooks' CustomerQueryRq has no JobStatus filter (qbXML 16.0 CustomerListFilter:
ListFilter, TotalBalanceFilter, CurrencyFilter, ClassFilter), so it is applied here.
Fixture CustomerQueryRq.jobstatus.xml is hand-written: two customers, jobs in every status."""

import xml.etree.ElementTree as ET

import pytest

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def mixed_statuses(replay):
    replay.respond_with("CustomerQueryRq", "CustomerQueryRq.jobstatus.xml")


def _sent(replay, rq_type: str) -> ET.Element:
    return ET.fromstring(replay.sent[-1].encode()).find(f"QBXMLMsgsRq/{rq_type}")


def _names(result) -> list[str]:
    assert not result.is_error, result.content
    return [r["name"] for r in result.structured_content["results"]]


async def test_list_customer_jobs_returns_only_jobs_in_the_given_status(call):
    result = await call("list_customer_jobs", job_status=["closed"], status="all")

    assert _names(result) == ["Acme Paving:Pine Ct", "Brook Homes:Lot 7"]
