"""Sensitive-field stripping: applied once, on the path every Lookup takes,
so every tool's output (lists, reports, run_query) is covered."""

import json

import pytest

from qb_mcp.output import redact
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


def test_redact_strips_sensitive_keys_from_data_built_outside_a_lookup():
    data = {"rows": [{"Vendor": "Patton", "Extra": {"VendorTaxIdent": "94-1234567", "BankNumber": "1"}}]}

    assert redact(data) == {"rows": [{"Vendor": "Patton", "Extra": {}}]}
