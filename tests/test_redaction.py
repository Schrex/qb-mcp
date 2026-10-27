"""Sensitive-field stripping: applied once, on the path every Lookup takes,
so every tool's output (lists, reports, run_query) is covered."""

import json

import pytest

from qb_mcp.qbxml import query
from qb_mcp.quickbooks import QuickBooks

pytestmark = pytest.mark.anyio

SECRETS = ["94-1234567", "4111111111111111", "SR-998877", "000123456789", "987-65-4321", "00-7904153", "123-45-6789"]


async def test_list_vendors_never_returns_the_vendor_tax_id(call):
    result = await call("list_vendors", status="all")

    text = result.content[0].text + json.dumps(result.structured_content)
    assert "94-1234567" not in text and "VendorTaxIdent" not in text


async def test_sensitive_elements_are_stripped_at_any_depth_from_every_lookup(replay):
    replay.respond_with("CustomerQueryRq", "CustomerQueryRq.sensitive.xml")

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
async def test_list_vendors_never_returns_the_vendor_account_number(call):
    text = json.dumps((await call("list_vendors", status="all")).structured_content)

    assert "RC-5543" not in text and "account_number" not in text


async def test_run_query_never_returns_the_vendor_account_number(call):
    text = (await call("run_query", query="<VendorQueryRq/>")).content[0].text

    assert "RC-5543" not in text and "AccountNumber" not in text


async def test_chart_of_accounts_numbers_are_kept(call):
    accounts = (await call("list_accounts", status="all")).structured_content["results"]

    assert any(a["account_number"] for a in accounts)


# Report cells carry their values in attributes, so sensitive columns are found by title.
REPORT_SECRETS = ["94-1234567", "371449635398431", "4111111111111111", "000123456789"]


async def test_run_job_report_drops_tax_id_account_card_and_bank_columns(call, replay):
    replay.respond_with("JobReportQueryRq", "JobReportQueryRq.sensitive.xml")

    result = await call(
        "run_job_report", report="job_profitability_detail", from_date="2024-01-01", to_date="2024-12-31"
    )

    report = result.structured_content
    assert report["columns"] == ["Name", "Act. Cost"]
    assert report["rows"][0]["values"] == {"Name": "Patton Hardware Supplies", "Act. Cost": "2520.00"}
    text = result.content[0].text
    assert not any(secret in text for secret in REPORT_SECRETS)


async def test_run_query_report_drops_sensitive_columns(call, replay):
    replay.respond_with("JobReportQueryRq", "JobReportQueryRq.sensitive.xml")

    result = await call(
        "run_query", query="<JobReportQueryRq><JobReportType>JobProfitabilityDetail</JobReportType></JobReportQueryRq>"
    )

    assert not result.is_error
    text = result.content[0].text
    assert "2520.00" in text
    assert not any(secret in text for secret in REPORT_SECRETS)
    assert "Tax ID" not in text and "Card" not in text
