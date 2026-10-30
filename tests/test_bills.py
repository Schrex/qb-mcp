import xml.etree.ElementTree as ET

import pytest

pytestmark = pytest.mark.anyio


# The default fixtures are recorded from Rock Castle Construction (Intuit's sample company).
ROCK_CASTLE_BILLS = ["3847498", "8374892", "930357", "37498", "908472", "380482", "014", "Q1/02", "382940", "015", None]


async def test_query_bills_returns_header_with_item_and_expense_lines(call):
    result = await call("query_bills")

    assert not result.is_error
    out = result.structured_content
    assert out["results"][0] == {
        "vendor": "Patton Hardware Supplies",
        "ap_account": "Accounts Payable",
        "ref_number": "3847498",
        "date": "2024-12-02",
        "due_date": "2026-01-01",
        "amount": "13695.00",
        "open_balance": "0.00",  # paid; QuickBooks' OpenAmount (4479.20) is a Vendor-wide figure (#14)
        "memo": None,
        "is_paid": True,
        "expense_lines": [],
        "item_lines": [
            {
                "item": "Cabinets:Light Pine",
                "description": "Light pine kitchen cabinet wall unit \n#CLD- 1235-p",
                "quantity": "5",
                "cost": "1500.00",
                "amount": "7500.00",
                "customer_job": "Burch, Jason:Room Addition",
                "class": "New Construction",
                "billable_status": "NotBillable",
            },
            {
                "item": "Hardware:Doorknobs Std",
                "description": "Doorknobs Part # DK 3704",
                "quantity": "100",
                "cost": "27.00",
                "amount": "2700.00",
                "customer_job": "Burch, Jason:Room Addition",
                "class": "New Construction",
                "billable_status": "NotBillable",
            },
            {
                "item": "Hardware:Lk Doorknobs",
                "description": "Locking interior doorknobs  Part # DK415",
                "quantity": "100",
                "cost": "34.95",
                "amount": "3495.00",
                "customer_job": "Burch, Jason:Room Addition",
                "class": "New Construction",
                "billable_status": "NotBillable",
            },
        ],
    }
    # A Bill with expense lines only (truck payment split across a loan and its interest).
    assert out["results"][6]["ref_number"] == "014"
    assert out["results"][6]["memo"] == "Monthly Truck Payment"
    assert out["results"][6]["item_lines"] == []
    assert out["results"][6]["expense_lines"] == [
        {
            "account": "Loan - Vehicles (Van)",
            "amount": "436.07",
            "memo": None,
            "customer_job": "Overhead",
            "class": "Overhead",
            "billable_status": None,
        },
        {
            "account": "Interest Expense:Loan Interest",
            "amount": "96.90",
            "memo": None,
            "customer_job": "Overhead",
            "class": "Overhead",
            "billable_status": "NotBillable",
        },
    ]
    assert [b["ref_number"] for b in out["results"]] == ROCK_CASTLE_BILLS
    assert [b["is_paid"] for b in out["results"]] == [True] * 11
    assert out["more_available"] is False


async def test_query_bills_flattens_item_group_lines(call, replay):
    # Rock Castle's Bills hold no item groups; this hand-written Bill has one.
    replay.respond_with("BillQueryRq", "BillQueryRq.jobmix.xml")

    out = (await call("query_bills")).structured_content

    assert [line["item"] for line in out["results"][0]["item_lines"]] == ["Lumber:Trim", "Doors:Interior"]
    assert out["results"][0]["item_lines"][1] == {  # from inside the item group
        "item": "Doors:Interior",
        "description": "Interior door",
        "quantity": "1",
        "cost": "600.00",
        "amount": "600.00",
        "customer_job": "Cook, Brian:Kitchen",
        "class": None,
        "billable_status": "HasBeenBilled",
    }


def _sent_bill_query(replay) -> ET.Element:
    return ET.fromstring(replay.sent[-1].encode()).find("QBXMLMsgsRq/BillQueryRq")


async def test_query_bills_sends_date_vendor_paid_filters_in_schema_order(call, replay):
    await call(
        "query_bills",
        from_date="2024-01-01",
        to_date="2024-03-31",
        vendor="Stone & Co",
        paid_status="unpaid",
        limit=5,
    )

    rq = _sent_bill_query(replay)
    assert [c.tag for c in rq] == [
        "MaxReturned",
        "TxnDateRangeFilter",
        "EntityFilter",
        "PaidStatus",
        "IncludeLineItems",
        "IncludeLinkedTxns",
    ]
    assert rq.findtext("MaxReturned") == "6"
    assert rq.findtext("TxnDateRangeFilter/FromTxnDate") == "2024-01-01"
    assert rq.findtext("TxnDateRangeFilter/ToTxnDate") == "2024-03-31"
    assert rq.findtext("EntityFilter/FullName") == "Stone & Co"
    assert rq.findtext("PaidStatus") == "NotPaidOnly"
    assert rq.findtext("IncludeLineItems") == "true"


async def test_query_bills_defaults_to_all_paid_statuses_with_lines_and_100(call, replay):
    await call("query_bills")

    rq = _sent_bill_query(replay)
    assert [c.tag for c in rq] == ["MaxReturned", "PaidStatus", "IncludeLineItems", "IncludeLinkedTxns"]
    assert rq.findtext("MaxReturned") == "101"
    assert rq.findtext("PaidStatus") == "All"
    assert rq.findtext("IncludeLineItems") == "true"


async def test_query_bills_paid_only_and_open_ended_date_range(call, replay):
    await call("query_bills", from_date="2024-06-01", paid_status="paid")

    rq = _sent_bill_query(replay)
    assert [c.tag for c in rq.find("TxnDateRangeFilter")] == ["FromTxnDate"]
    assert rq.findtext("PaidStatus") == "PaidOnly"


async def test_query_bills_ref_number_is_an_exact_lookup_sent_alone(call, replay):
    replay.respond_with("BillQueryRq", "BillQueryRq.sameref.xml")

    out = (await call("query_bills", ref_number="1001")).structured_content

    rq = _sent_bill_query(replay)
    # qbXML: an exact RefNumber cannot be combined with MaxReturned or other filters.
    assert [c.tag for c in rq] == ["RefNumber", "IncludeLineItems", "IncludeLinkedTxns"]
    assert rq.findtext("RefNumber") == "1001"
    assert [(b["vendor"], b["ref_number"]) for b in out["results"]] == [
        ("Patton Hardware Supplies", "1001"),
        ("Timberloft Lumber", "1001"),
    ]


async def test_query_bills_ref_number_with_other_filters_applies_them_to_the_matches(call, replay):
    replay.respond_with("BillQueryRq", "BillQueryRq.sameref.xml")

    by_vendor = (await call("query_bills", ref_number="1001", vendor="timberloft lumber")).structured_content
    by_paid = (await call("query_bills", ref_number="1001", paid_status="paid")).structured_content
    by_date = (await call("query_bills", ref_number="1001", from_date="2024-03-15", to_date="2024-12-31")).structured_content
    capped_out = (await call("query_bills", ref_number="1001", limit=1)).structured_content

    assert [c.tag for c in _sent_bill_query(replay)] == ["RefNumber", "IncludeLineItems", "IncludeLinkedTxns"]
    assert [b["vendor"] for b in by_vendor["results"]] == ["Timberloft Lumber"]
    assert [b["vendor"] for b in by_paid["results"]] == ["Patton Hardware Supplies"]
    assert [b["vendor"] for b in by_date["results"]] == ["Timberloft Lumber"]
    assert capped_out["count"] == 1 and capped_out["more_available"] is True


async def test_query_bills_unknown_ref_number_is_an_empty_result(call, replay):
    replay.respond_with("BillQueryRq", "BillQueryRq.nomatch.xml")

    result = await call("query_bills", ref_number="NOPE-1")

    assert not result.is_error
    assert result.structured_content["results"] == []
    assert result.structured_content["more_available"] is False


async def test_query_bills_reports_when_more_bills_exist_than_the_limit(call):
    out = (await call("query_bills", limit=1)).structured_content

    assert [b["ref_number"] for b in out["results"]] == ["3847498"]
    assert out["more_available"] is True
    assert "more exist" in out["note"]


async def test_query_bills_customer_job_keeps_only_bills_and_lines_charged_to_it(call, replay):
    # No Rock Castle Bill splits its lines across Customer:Jobs, so these hand-written
    # Bills (one line charged to Cook, Brian:Kitchen among lines for other jobs) stand in.
    replay.respond_with("BillQueryRq", "BillQueryRq.jobmix.xml")

    out = (await call("query_bills", customer_job="cook, brian:kitchen", vendor="Patton Hardware Supplies")).structured_content

    # BillQueryRq cannot filter by Customer:Job, so nothing job-related is sent.
    rq = _sent_bill_query(replay)
    assert [c.tag for c in rq] == ["MaxReturned", "EntityFilter", "PaidStatus", "IncludeLineItems", "IncludeLinkedTxns"]
    assert rq.findtext("IncludeLineItems") == "true"
    assert [b["ref_number"] for b in out["results"]] == ["INV-88213", "TL-5521"]
    patton, timberloft = out["results"]
    assert patton["expense_lines"] == []
    assert [line["item"] for line in patton["item_lines"]] == ["Doors:Interior"]
    assert [line["account"] for line in timberloft["expense_lines"]] == ["Job Expenses:Job Materials"]


async def test_query_bills_customer_includes_its_jobs(call):
    # Every line of these two Bills is charged to the job Vasquez, Anabel:Basement Remodel.
    out = (await call("query_bills", customer_job="Vasquez, Anabel")).structured_content

    assert [b["ref_number"] for b in out["results"]] == ["380482", "382940"]
    kitchens, vu = out["results"]
    assert kitchens["expense_lines"] == [] and vu["expense_lines"] == []
    assert [line["item"] for line in kitchens["item_lines"]] == ["Door Frame", "Wood Door:Exterior"]
    assert [line["item"] for line in vu["item_lines"]] == [
        "Subs:Carpet",
        "Subs:Drywall",
        "Subs:Duct Work",
        "Subs:Electrical",
        "Subs:Insulating",
        "Subs:Painting",
    ]
    assert {line["customer_job"] for b in out["results"] for line in b["item_lines"]} == {
        "Vasquez, Anabel:Basement Remodel"
    }


async def test_query_bills_customer_job_cap_counts_bills_scanned_not_matched(call):
    # Vasquez's Bills are the 6th and 9th of 11: scanning 6 finds one, scanning 1 finds none.
    six = (await call("query_bills", customer_job="Vasquez, Anabel:Basement Remodel", limit=6)).structured_content
    one = (await call("query_bills", customer_job="Vasquez, Anabel:Basement Remodel", limit=1)).structured_content

    assert [b["ref_number"] for b in six["results"]] == ["380482"]
    assert six["count"] == 1
    assert six["more_available"] is True
    assert "more exist" in six["note"]
    assert one["results"] == [] and one["count"] == 0
    assert one["more_available"] is True


async def test_query_bills_customer_job_without_lines_is_rejected(call):
    result = await call("query_bills", customer_job="Cook, Brian:Kitchen", include_lines=False)

    assert result.is_error


async def test_query_bills_without_lines(call, replay):
    out = (await call("query_bills", include_lines=False)).structured_content

    assert _sent_bill_query(replay).findtext("IncludeLineItems") == "false"
    assert "item_lines" not in out["results"][0]
    assert "expense_lines" not in out["results"][0]


async def test_query_bills_open_balance_is_each_bills_own_balance_not_the_vendor_total(call, replay):
    # On the test PC every Perry Windows & Doors Bill reported the same OpenAmount (#14);
    # this hand-written response reproduces that with payments and credits linked to the Bills.
    replay.respond_with("BillQueryRq", "BillQueryRq.vendortotal.xml")

    out = (await call("query_bills", vendor="Perry Windows & Doors")).structured_content

    assert [(b["ref_number"], b["amount"], b["open_balance"], b["is_paid"]) for b in out["results"]] == [
        ("PW-101", "1800.00", "1300.00", False),  # partly paid by check: 1,800 - 500
        ("PW-102", "2325.00", "0.00", True),  # paid in full by check
        ("PW-103", "2400.00", "0.00", True),  # 400 Vendor credit + 2,000 check
        ("PW-104", "130.00", "100.00", False),  # 30 credit; the linked item receipt is not a payment
        ("PW-105", "50.00", "50.00", False),  # nothing applied
    ]
    assert "1450.00" not in str(out)  # QuickBooks' Vendor-wide OpenAmount is not passed off as a Bill's


async def test_query_bills_asks_quickbooks_for_linked_transactions(call, replay):
    await call("query_bills")
    await call("query_bills", ref_number="1001")

    assert _sent_bill_query(replay).findtext("IncludeLinkedTxns") == "true"
    assert ET.fromstring(replay.sent[-2].encode()).findtext("QBXMLMsgsRq/BillQueryRq/IncludeLinkedTxns") == "true"


async def test_query_bills_paid_status_follows_the_computed_open_balance(call, replay):
    replay.respond_with("BillQueryRq", "BillQueryRq.vendortotal.xml")

    unpaid = (await call("query_bills", paid_status="unpaid")).structured_content
    paid = (await call("query_bills", paid_status="paid")).structured_content
    unpaid_ref = (await call("query_bills", ref_number="PW-101", paid_status="unpaid")).structured_content

    assert [b["ref_number"] for b in unpaid["results"]] == ["PW-101", "PW-104", "PW-105"]
    assert unpaid["count"] == 3
    assert [b["ref_number"] for b in paid["results"]] == ["PW-102", "PW-103"]
    assert paid["count"] == 2
    assert [b["ref_number"] for b in unpaid_ref["results"]] == ["PW-101", "PW-104", "PW-105"]


async def test_query_bills_names_each_bills_ap_account(call, replay):
    # Retainage withheld from a Vendor kept in its own A/P account (#32).
    replay.respond_with("BillQueryRq", "BillQueryRq.apaccounts.xml")

    out = (await call("query_bills", vendor="Timberloft Lumber", include_lines=False)).structured_content

    assert [(b["ref_number"], b["ap_account"], b["amount"]) for b in out["results"]] == [
        ("TL-501", "Accounts Payable", "9000.00"),
        ("TL-501R", "Retainage Payable", "1000.00"),
    ]
