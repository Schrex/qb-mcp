"""Sensitive-field stripping: applied once, on the path every Lookup takes,
so every tool's output (lists, reports, run_query) is covered."""

import json

import pytest

from qb_mcp.qbxml import query
from qb_mcp.quickbooks import QuickBooks

pytestmark = pytest.mark.anyio

SECRETS = ["94-1234567", "4111111111111111", "SR-998877", "000123456789", "987-65-4321", "00-7904153", "123-45-6789"]


async def test_list_vendors_never_returns_the_vendor_tax_id(call, replay):
    replay.respond_with("VendorQueryRq", "VendorQueryRq.sensitive.xml")

    result = await call("list_vendors", status="all")

    text = result.content[0].text + json.dumps(result.structured_content)
    assert "94-1234567" not in text and "VendorTaxIdent" not in text


async def test_sensitive_elements_are_stripped_at_any_depth_from_every_lookup(replay):
    replay.respond_with("CustomerQueryRq", "CustomerQueryRq.sensitive.xml")
    replay.respond_with("VendorQueryRq", "VendorQueryRq.sensitive.xml")
    replay.respond_with("CompanyQueryRq", "CompanyQueryRq.sensitive.xml")

    results = await QuickBooks(replay).lookup(
        query("CustomerQueryRq"), query("VendorQueryRq"), query("CompanyQueryRq")
    )

    dumped = json.dumps([r.rets for r in results])
    for secret in SECRETS:
        assert secret not in dumped
    for tag in ["CreditCardInfo", "VendorTaxIdent", "EIN", "SSN", "BankNumber", "ResaleNumber"]:
        assert f'"{tag}"' not in dumped
    customer = results[0].records[0]
    assert customer["DataExtRet"][0]["Deeper"] == {"Kept": "visible"}
    assert customer["AccountNumber"] == "CUST-42"  # chart/account numbers are not sensitive


# A Vendor's AccountNumber can be a credit-card number (e.g. an Amex "vendor").
async def test_list_vendors_never_returns_the_vendor_account_number(call, replay):
    replay.respond_with("VendorQueryRq", "VendorQueryRq.sensitive.xml")

    text = json.dumps((await call("list_vendors", status="all")).structured_content)

    assert "RC-5543" not in text and "account_number" not in text


async def test_chart_of_accounts_numbers_are_kept(call):
    accounts = (await call("list_accounts", status="all")).structured_content["results"]

    assert any(a["account_number"] for a in accounts)


# Report cells carry their values in attributes, so sensitive columns are found by title.
REPORT_SECRETS = ["94-1234567", "371449635398431", "4111111111111111", "000123456789"]


async def test_run_job_report_drops_tax_id_account_card_and_bank_columns(call, replay):
    replay.respond_with("JobReportQueryRq", "JobReportQueryRq.sensitive.xml")

    result = await call(
        "run_job_report",
        report="job_profitability_detail",
        from_date="2024-01-01",
        to_date="2024-12-31",
        customer_job="Acme:Main St Paving",
    )

    report = result.structured_content
    assert report["columns"] == ["Name", "Act. Cost"]
    assert report["rows"][0]["values"] == {"Name": "Patton Hardware Supplies", "Act. Cost": "2520.00"}
    text = result.content[0].text
    assert not any(secret in text for secret in REPORT_SECRETS)


async def test_run_query_report_drops_sensitive_columns(call, replay):
    replay.respond_with("GeneralSummaryReportQueryRq", "GeneralSummaryReportQueryRq.sensitive.xml")

    result = await call(
        "run_query",
        query="<GeneralSummaryReportQueryRq><GeneralSummaryReportType>ProfitAndLossStandard</GeneralSummaryReportType></GeneralSummaryReportQueryRq>",
    )

    assert not result.is_error
    text = result.content[0].text
    assert "2520.00" in text
    assert not any(secret in text for secret in REPORT_SECRETS)
    assert "Tax ID" not in text and "Card" not in text


# Hours by Customer:Job are job costing; the pay rate and payroll item reveal pay (#30, #18).
async def test_run_query_time_tracking_drops_rate_and_payroll_item(call, replay):
    replay.respond_with("TimeTrackingQueryRq", "TimeTrackingQueryRq.sensitive.xml")

    result = await call("run_query", query="<TimeTrackingQueryRq><MaxReturned>101</MaxReturned></TimeTrackingQueryRq>")

    entries = result.structured_content["results"]
    hours = [
        (e["TxnDate"], e["EntityRef"]["FullName"], e["CustomerRef"]["FullName"], e["ItemServiceRef"]["FullName"], e["Duration"])
        for e in entries
    ]
    assert hours == [
        ("2024-03-04", "Gregg O. Schneider", "Acme:Main St Paving", "Excavation", "PT8H30M0S"),
        ("2024-03-05", "Gregg O. Schneider", "Acme:Main St Paving", "Excavation", "PT6H0M0S"),
    ]
    assert entries[0]["Notes"] == "Grading east lot"
    text = result.content[0].text + json.dumps(result.structured_content)
    assert "48.75" not in text and "Rate" not in text
    assert "PayrollItemWageRef" not in text and "Operator Hourly" not in text
