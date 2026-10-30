"""list_customer_jobs, list_items, list_accounts: the list_vendors pattern
(name filter, active status, limit + cap, sensitive fields stripped)."""

import json
import xml.etree.ElementTree as ET

import pytest
from conftest import tool_requests

pytestmark = pytest.mark.anyio


def _sent(replay, rq_type: str) -> ET.Element:
    return ET.fromstring(tool_requests(replay)[-1].encode()).find(f"QBXMLMsgsRq/{rq_type}")


# --- list_customer_jobs ---------------------------------------------------


# The default fixtures are recorded from Rock Castle Construction (Intuit's sample company).
ROCK_CASTLE_CUSTOMER_JOBS = [
    "Abercrombie, Kristy",
    "Abercrombie, Kristy:Family Room",
    "Abercrombie, Kristy:Kitchen",
    "Abercrombie, Kristy:Remodel Bathroom",
    "Allard, Robert",
    "Allard, Robert:Remodel",
    "Babcock's Music Shop",
    "Babcock's Music Shop:Remodel",
    "Baker, Chris",
    "Baker, Chris:Family Room",
    "Baker, Chris:Garage Repair",
    "Balak, Mike",
    "Balak, Mike:Utility Shed",
    "Barley, Renee",
    "Barley, Renee:Repairs",
    "Bauman, Mark",
    "Bauman, Mark:Home Remodel",
    "Bolinski, Rafal",
    "Bolinski, Rafal:2nd story addition",
    "Bristol, Sonya",
    "Bristol, Sonya:Repairs",
    "Bristol, Sonya:Utility Shed",
    "Burch, Jason",
    "Burch, Jason:Room Addition",
    "Campbell, Heather",
    "Campbell, Heather:Remodel",
]


async def test_list_customer_jobs_shows_full_names_and_tells_customers_from_jobs(call):
    result = await call("list_customer_jobs")

    assert not result.is_error
    out = result.structured_content
    assert out["results"][:6] == [
        {
            "name": "Abercrombie, Kristy",
            "kind": "customer",
            "parent": None,
            "is_active": True,
            "job_status": "None",
            "balance": "0.00",
            "total_balance": "0.00",
            "job_description": None,
        },
        {
            "name": "Abercrombie, Kristy:Family Room",
            "kind": "job",
            "parent": "Abercrombie, Kristy",
            "is_active": True,
            "job_status": "Closed",
            "balance": "0.00",
            "total_balance": "0.00",
            "job_description": "Remodel family room: Drywall, re-carpet, add fireplace.",
        },
        {
            "name": "Abercrombie, Kristy:Kitchen",
            "kind": "job",
            "parent": "Abercrombie, Kristy",
            "is_active": True,
            "job_status": "Pending",
            "balance": "0.00",
            "total_balance": "0.00",
            "job_description": "Fire restoration of kitchen: Replace counter, dishwasher, oven and paint.",
        },
        {
            "name": "Abercrombie, Kristy:Remodel Bathroom",
            "kind": "job",
            "parent": "Abercrombie, Kristy",
            "is_active": True,
            "job_status": "InProgress",
            "balance": "0.00",
            "total_balance": "0.00",
            "job_description": "Remodel master bathroom: Build new counter, Replace fixtures, Add whirlpool.",
        },
        {  # the customer's total_balance rolls up its job's balance
            "name": "Allard, Robert",
            "kind": "customer",
            "parent": None,
            "is_active": True,
            "job_status": "None",
            "balance": "0.00",
            "total_balance": "14510.00",
            "job_description": None,
        },
        {
            "name": "Allard, Robert:Remodel",
            "kind": "job",
            "parent": "Allard, Robert",
            "is_active": True,
            "job_status": "None",
            "balance": "14510.00",
            "total_balance": "14510.00",
            "job_description": None,
        },
    ]
    assert [c["name"] for c in out["results"]] == ROCK_CASTLE_CUSTOMER_JOBS
    assert [c["kind"] for c in out["results"]] == [
        "job" if ":" in name else "customer" for name in ROCK_CASTLE_CUSTOMER_JOBS
    ]
    assert out["count"] == 26
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

    assert [c["name"] for c in out["results"]] == ["Abercrombie, Kristy", "Abercrombie, Kristy:Family Room"]
    assert out["more_available"] is True


async def test_list_customer_jobs_never_returns_tax_ids_or_card_data(call, replay):
    replay.respond_with("CustomerQueryRq", "CustomerQueryRq.sensitive.xml")

    result = await call("list_customer_jobs", status="all")

    text = result.content[0].text + json.dumps(result.structured_content)
    for secret in ["SR-998877", "4111111111111111", "CreditCard", "ResaleNumber"]:
        assert secret not in text


# --- list_items -----------------------------------------------------------


async def test_list_items_returns_services_and_inventory_with_cost_and_account(call):
    out = (await call("list_items")).structured_content

    services = [
        "Blueprint changes",
        "Blueprints",
        "Concrete Slab",
        "Floor Plans",
        "Framing",
        "Installation",
        "Labor",
        "Mileage",
        "Removal",
        "Repairs",
        "Subs",
        "Subs:Carpet",
        "Subs:Drywall",
        "Subs:Duct Work",
        "Subs:Electrical",
        "Subs:Insulating",
        "Subs:Metal Wrk",
        "Subs:Painting",
        "Subs:Plumbing",
        "Subs:Roofing",
        "Subs:Tile &Counter",
    ]
    inventory = ["Cabinets", "Cabinets:Cabinet Pulls", "Cabinets:Light Pine", "Door Frame", "Hardware"]
    assert [(i["name"], i["kind"]) for i in out["results"]] == [(n, "service") for n in services] + [
        (n, "inventory") for n in inventory
    ]
    assert out["count"] == 26 and out["more_available"] is False
    by_name = {i["name"]: i for i in out["results"]}
    assert by_name["Framing"] == {  # one-way service: SalesOrPurchase (sold at a price, to an income account)
        "name": "Framing",
        "kind": "service",
        "is_active": True,
        "description": "Framing labor",
        "cost": "55.00",
        "expense_account": "Construction Income:Labor Income",
    }
    assert by_name["Mileage"] == {
        "name": "Mileage",
        "kind": "service",
        "is_active": False,
        "description": None,
        "cost": "0.365",
        "expense_account": "Reimbursement Income:Mileage Income",
    }
    assert by_name["Subs:Carpet"] == {  # two-way service: SalesAndPurchase, purchase side reported
        "name": "Subs:Carpet",
        "kind": "service",
        "is_active": True,
        "description": "Install carpeting",
        "cost": "25.00",
        "expense_account": "Job Expenses:Subcontractors",
    }
    assert by_name["Cabinets:Light Pine"] == {  # inventory: top-level cost and COGS account
        "name": "Cabinets:Light Pine",
        "kind": "inventory",
        "is_active": True,
        "description": "Light pine kitchen cabinet wall unit \n#CLD- 1235-p",
        "cost": "1500.00",
        "expense_account": "Cost of Goods Sold",
    }


async def test_list_items_tags_each_mixed_item_with_its_kind(call, replay):
    # Rock Castle's recorded Items are services and inventory only; this hand-written
    # list has one Item of each remaining kind.
    replay.respond_with("ItemQueryRq", "ItemQueryRq.mixedkinds.xml")

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
