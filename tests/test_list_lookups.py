"""list_customer_jobs, list_items, list_accounts: the list_vendors pattern
(name filter, active status, limit + cap, sensitive fields stripped)."""

import json
import xml.etree.ElementTree as ET

import pytest

pytestmark = pytest.mark.anyio


def _sent(replay, rq_type: str) -> ET.Element:
    return ET.fromstring(replay.sent[-1].encode()).find(f"QBXMLMsgsRq/{rq_type}")


# --- list_customer_jobs ---------------------------------------------------


async def test_list_customer_jobs_shows_full_names_and_tells_customers_from_jobs(call):
    result = await call("list_customer_jobs")

    assert not result.is_error
    out = result.structured_content
    assert out["results"] == [
        {
            "name": "Abercrombie, Kristy",
            "kind": "customer",
            "parent": None,
            "is_active": True,
            "job_status": "None",
            "balance": "0.00",
            "total_balance": "7633.28",
            "job_description": None,
        },
        {
            "name": "Abercrombie, Kristy:Kitchen",
            "kind": "job",
            "parent": "Abercrombie, Kristy",
            "is_active": True,
            "job_status": "Closed",
            "balance": "0.00",
            "total_balance": "0.00",
            "job_description": "Kitchen remodel",
        },
        {
            "name": "Abercrombie, Kristy:Remodel Bathroom",
            "kind": "job",
            "parent": "Abercrombie, Kristy",
            "is_active": True,
            "job_status": "InProgress",
            "balance": "7633.28",
            "total_balance": "7633.28",
            "job_description": None,
        },
    ]
    assert out["more_available"] is False


async def test_list_customer_jobs_sends_name_filter_status_and_limit(call, replay):
    await call("list_customer_jobs", name_contains="Abercrombie", status="all", limit=5)

    rq = _sent(replay, "CustomerQueryRq")
    assert [c.tag for c in rq] == ["MaxReturned", "ActiveStatus", "NameFilter"]
    assert rq.findtext("MaxReturned") == "6"
    assert rq.findtext("ActiveStatus") == "All"
    assert rq.findtext("NameFilter/Name") == "Abercrombie"


async def test_list_customer_jobs_reports_the_cap(call):
    out = (await call("list_customer_jobs", limit=2)).structured_content

    assert [c["name"] for c in out["results"]] == ["Abercrombie, Kristy", "Abercrombie, Kristy:Kitchen"]
    assert out["more_available"] is True


async def test_list_customer_jobs_never_returns_tax_ids_or_card_data(call, replay):
    replay.respond_with("CustomerQueryRq", "CustomerQueryRq.sensitive.xml")

    result = await call("list_customer_jobs", status="all")

    text = result.content[0].text + json.dumps(result.structured_content)
    for secret in ["SR-998877", "4111111111111111", "CreditCard", "ResaleNumber"]:
        assert secret not in text


# --- list_items -----------------------------------------------------------


async def test_list_items_tags_each_mixed_item_with_its_kind(call):
    out = (await call("list_items")).structured_content

    assert [(i["name"], i["kind"]) for i in out["results"]] == [
        ("Framing", "service"),
        ("Wood Door:Exterior", "inventory"),
        ("Lumber:Trim", "non_inventory"),
        ("Permit", "other_charge"),
        ("Subtotal", "subtotal"),
    ]
    assert out["results"][0] == {
        "name": "Framing",
        "kind": "service",
        "is_active": True,
        "description": "Framing labor",
        "cost": "35.00",
        "expense_account": "Job Expenses:Subcontractors",
    }
    assert out["results"][1] == {
        "name": "Wood Door:Exterior",
        "kind": "inventory",
        "is_active": True,
        "description": "Exterior wood door",
        "cost": "59.00",
        "expense_account": "Cost of Goods Sold",
    }
    assert out["results"][2]["description"] == "Trim lumber"
    assert out["results"][2]["cost"] == "0.00"
    assert out["results"][2]["expense_account"] == "Job Expenses:Job Materials"
    assert out["results"][4] == {
        "name": "Subtotal",
        "kind": "subtotal",
        "is_active": True,
        "description": "Subtotal",
        "cost": None,
        "expense_account": None,
    }


async def test_list_items_sends_name_filter_status_and_limit_and_reports_the_cap(call, replay):
    out = (await call("list_items", name_contains="Door", status="inactive", limit=1)).structured_content

    rq = _sent(replay, "ItemQueryRq")
    assert [c.tag for c in rq] == ["MaxReturned", "ActiveStatus", "NameFilter"]
    assert rq.findtext("MaxReturned") == "2"
    assert rq.findtext("ActiveStatus") == "InactiveOnly"
    assert rq.findtext("NameFilter/Name") == "Door"
    assert out["count"] == 1 and out["more_available"] is True


# --- list_accounts --------------------------------------------------------


async def test_list_accounts_returns_type_number_and_balances(call):
    out = (await call("list_accounts")).structured_content

    assert out["results"] == [
        {
            "name": "Checking",
            "account_type": "Bank",
            "account_number": "10100",
            "is_active": True,
            "description": "Operating account",
            "balance": "46969.10",
            "total_balance": "46969.10",
        },
        {
            "name": "Job Expenses:Subcontractors",
            "account_type": "Expense",
            "account_number": "54300",
            "is_active": True,
            "description": None,
            "balance": "0.00",
            "total_balance": "0.00",
        },
        {
            "name": "Cost of Goods Sold",
            "account_type": "CostOfGoodsSold",
            "account_number": "50100",
            "is_active": True,
            "description": None,
            "balance": "0.00",
            "total_balance": "0.00",
        },
    ]


async def test_list_accounts_filters_by_account_type(call, replay):
    await call("list_accounts", account_type="Expense", name_contains="Job", status="all", limit=10)

    rq = _sent(replay, "AccountQueryRq")
    assert [c.tag for c in rq] == ["MaxReturned", "ActiveStatus", "NameFilter", "AccountType"]
    assert rq.findtext("MaxReturned") == "11"
    assert rq.findtext("AccountType") == "Expense"
    assert rq.get("iterator") is None


async def test_list_accounts_without_type_filter_sends_none_and_reports_the_cap(call, replay):
    out = (await call("list_accounts", limit=2)).structured_content

    assert _sent(replay, "AccountQueryRq").find("AccountType") is None
    assert out["more_available"] is True


async def test_list_accounts_rejects_an_unknown_account_type(call):
    result = await call("list_accounts", account_type="Savings")

    assert result.is_error


async def test_list_accounts_never_returns_bank_account_numbers(call, replay):
    replay.respond_with("AccountQueryRq", "AccountQueryRq.sensitive.xml")

    result = await call("list_accounts", status="all")

    text = result.content[0].text + json.dumps(result.structured_content)
    assert "000123456789" not in text and "BankNumber" not in text
