"""Job status filter (#31) on list_customer_jobs and run_job_report.

QuickBooks' CustomerQueryRq has no JobStatus filter (qbXML 16.0 CustomerListFilter:
ListFilter, TotalBalanceFilter, CurrencyFilter, ClassFilter), so it is applied here.
Fixture CustomerQueryRq.jobstatus.xml is hand-written: two customers, jobs in every status."""

import xml.etree.ElementTree as ET

import pytest
from conftest import tool_requests

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def mixed_statuses(replay):
    replay.respond_with("CustomerQueryRq", "CustomerQueryRq.jobstatus.xml")


def _sent(replay, rq_type: str) -> ET.Element:
    return ET.fromstring(tool_requests(replay)[-1].encode()).find(f"QBXMLMsgsRq/{rq_type}")


def _names(result) -> list[str]:
    assert not result.is_error, result.content
    return [r["name"] for r in result.structured_content["results"]]


async def test_list_customer_jobs_returns_only_jobs_in_the_given_status(call):
    result = await call("list_customer_jobs", job_status=["closed"], status="all")

    assert _names(result) == ["Acme Paving:Pine Ct", "Brook Homes:Lot 7"]


async def test_list_customer_jobs_job_status_combines_with_active_status(call, replay):
    # Replay ignores ActiveStatus, so QuickBooks' half of the combination is checked on the request.
    result = await call("list_customer_jobs", job_status=["in_progress", "closed"], status="inactive")

    assert _sent(replay, "CustomerQueryRq").findtext("ActiveStatus") == "InactiveOnly"
    assert _names(result) == [
        "Acme Paving:Oak Rd", "Acme Paving:Pine Ct", "Brook Homes:Lot 7", "Brook Homes:Lot 8", "Brook Homes:Lot 9",
    ]
    assert result.structured_content["filters"] == {"job_status": ["closed", "in_progress"]}


async def test_list_customer_jobs_cap_counts_matching_jobs_not_records_scanned(call, replay):
    # 10 records, 3 in progress: limit 2 must say more exist, limit 3 that the list is complete.
    capped_out = (await call("list_customer_jobs", job_status=["in_progress"], status="all", limit=2)).structured_content
    whole = (await call("list_customer_jobs", job_status=["in_progress"], status="all", limit=3)).structured_content

    assert _sent(replay, "CustomerQueryRq").find("MaxReturned") is None
    assert (capped_out["count"], capped_out["more_available"]) == (2, True)
    assert (whole["count"], whole["more_available"]) == (3, False)


async def test_list_customer_jobs_rejects_an_unknown_job_status(call):
    result = await call("list_customer_jobs", job_status=["finished"])

    assert result.is_error
    message = result.content[0].text
    assert "finished" in message and "'in_progress'" in message and "'not_awarded'" in message


async def test_run_job_report_limits_the_report_to_jobs_in_the_given_status(call, replay):
    result = await call(
        "run_job_report", report="job_profitability_summary", from_date="2024-01-01", to_date="2024-12-31",
        job_status=["awarded", "in_progress"],
    )

    assert not result.is_error, result.content
    customers = ET.fromstring(replay.sent[0].encode()).find("QBXMLMsgsRq/CustomerQueryRq")
    assert customers.findtext("ActiveStatus") == "All"  # a report covers inactive jobs too
    entities = _sent(replay, "JobReportQueryRq").find("ReportEntityFilter")
    assert [e.text for e in entities] == [
        "Acme Paving:Elm Ave", "Acme Paving:Oak Rd", "Brook Homes:Lot 8", "Brook Homes:Lot 9",
    ]
    assert result.structured_content["filters"] == {"job_status": ["awarded", "in_progress"]}


async def test_run_job_report_job_status_within_one_customer(call, replay):
    await call(
        "run_job_report", report="job_profitability_detail", from_date="2024-01-01", to_date="2024-12-31",
        customer_job="Brook Homes", job_status=["in_progress"],
    )

    entities = _sent(replay, "JobReportQueryRq").find("ReportEntityFilter")
    assert [e.text for e in entities] == ["Brook Homes:Lot 8", "Brook Homes:Lot 9"]


async def test_run_job_report_with_no_job_in_the_status_runs_no_report(call, replay):
    result = await call(
        "run_job_report", report="job_profitability_summary", from_date="2024-01-01", to_date="2024-12-31",
        customer_job="Brook Homes", job_status=["pending"],
    )

    assert not result.is_error, result.content
    assert all("JobReportQueryRq" not in rq for rq in replay.sent)
    assert result.structured_content["rows"] == []
    assert "No Customer:Job" in result.structured_content["note"]


async def test_run_job_report_job_status_combines_with_active_status(call, replay):
    # Replay ignores ActiveStatus, so QuickBooks' half of the combination is checked on the request.
    result = await call(
        "run_job_report", report="job_profitability_summary", from_date="2024-01-01", to_date="2024-12-31",
        job_status=["in_progress"], status="inactive",
    )

    assert not result.is_error, result.content
    customers = ET.fromstring(replay.sent[0].encode()).find("QBXMLMsgsRq/CustomerQueryRq")
    assert customers.findtext("ActiveStatus") == "InactiveOnly"
    filters = result.structured_content["stamp"]["filters"]
    assert (filters["status"], filters["job_status"]) == ("inactive", ["in_progress"])


async def test_run_job_report_active_status_alone_names_the_customer_jobs_to_report(call, replay):
    result = await call(
        "run_job_report", report="job_profitability_detail", from_date="2024-01-01", to_date="2024-12-31",
        customer_job="Brook Homes", status="active",
    )

    assert not result.is_error, result.content
    customers = ET.fromstring(replay.sent[0].encode()).find("QBXMLMsgsRq/CustomerQueryRq")
    assert customers.findtext("ActiveStatus") == "ActiveOnly"
    entities = _sent(replay, "JobReportQueryRq").find("ReportEntityFilter")
    assert [e.text for e in entities] == ["Brook Homes", "Brook Homes:Lot 7", "Brook Homes:Lot 8", "Brook Homes:Lot 9"]
    assert result.structured_content["stamp"]["filters"]["status"] == "active"


async def test_run_job_report_covers_active_and_inactive_jobs_by_default(call, replay):
    result = await call(
        "run_job_report", report="job_profitability_detail", from_date="2024-01-01", to_date="2024-12-31",
        customer_job="Brook Homes",
    )

    entities = _sent(replay, "JobReportQueryRq").find("ReportEntityFilter")
    assert [e.tag for e in entities] == ["FullNameWithChildren"]
    assert "status" not in result.structured_content["stamp"]["filters"]


async def test_run_job_report_rejects_an_unknown_job_status(call):
    result = await call(
        "run_job_report", report="job_profitability_summary", from_date="2024-01-01", to_date="2024-12-31",
        job_status=["done"],
    )

    assert result.is_error
    assert "done" in result.content[0].text
