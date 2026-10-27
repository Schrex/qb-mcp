import xml.etree.ElementTree as ET

import pytest

pytestmark = pytest.mark.anyio


async def test_query_bills_returns_header_with_item_and_expense_lines(call):
    result = await call("query_bills")

    assert not result.is_error
    out = result.structured_content
    assert out["results"][0] == {
        "vendor": "Patton Hardware Supplies",
        "ref_number": "INV-88213",
        "date": "2024-01-10",
        "due_date": "2024-02-09",
        "amount": "1325.40",
        "open_amount": "1325.40",
        "memo": "Bathroom materials",
        "is_paid": False,
        "expense_lines": [
            {
                "account": "Job Expenses:Permits and Licenses",
                "amount": "125.40",
                "memo": "Delivery fee",
                "customer_job": "Abercrombie, Kristy:Remodel Bathroom",
                "class": "Remodel",
                "billable_status": "NotBillable",
            }
        ],
        "item_lines": [
            {
                "item": "Lumber:Trim",
                "description": "Trim lumber",
                "quantity": "40",
                "cost": "15.00",
                "amount": "600.00",
                "customer_job": "Abercrombie, Kristy:Remodel Bathroom",
                "class": "Remodel",
                "billable_status": "Billable",
            },
            {  # from inside an item group
                "item": "Doors:Interior",
                "description": "Interior door",
                "quantity": "1",
                "cost": "600.00",
                "amount": "600.00",
                "customer_job": "Cook, Brian:Kitchen",
                "class": None,
                "billable_status": "HasBeenBilled",
            },
        ],
    }
    assert [b["ref_number"] for b in out["results"]] == ["INV-88213", "TL-5521"]
    assert out["results"][1]["is_paid"] is True
    assert out["more_available"] is False


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
    assert [c.tag for c in rq] == ["MaxReturned", "PaidStatus", "IncludeLineItems"]
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
    assert [c.tag for c in rq] == ["RefNumber", "IncludeLineItems"]
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

    assert [c.tag for c in _sent_bill_query(replay)] == ["RefNumber", "IncludeLineItems"]
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

    assert [b["ref_number"] for b in out["results"]] == ["INV-88213"]
    assert out["more_available"] is True
    assert "more exist" in out["note"]


async def test_query_bills_customer_job_keeps_only_bills_and_lines_charged_to_it(call, replay):
    out = (await call("query_bills", customer_job="cook, brian:kitchen", vendor="Patton Hardware Supplies")).structured_content

    # BillQueryRq cannot filter by Customer:Job, so nothing job-related is sent.
    rq = _sent_bill_query(replay)
    assert [c.tag for c in rq] == ["MaxReturned", "EntityFilter", "PaidStatus", "IncludeLineItems"]
    assert rq.findtext("IncludeLineItems") == "true"
    assert [b["ref_number"] for b in out["results"]] == ["INV-88213", "TL-5521"]
    patton, timberloft = out["results"]
    assert patton["expense_lines"] == []
    assert [line["item"] for line in patton["item_lines"]] == ["Doors:Interior"]
    assert [line["account"] for line in timberloft["expense_lines"]] == ["Job Expenses:Job Materials"]


async def test_query_bills_customer_includes_its_jobs(call):
    out = (await call("query_bills", customer_job="Abercrombie, Kristy")).structured_content

    assert [b["ref_number"] for b in out["results"]] == ["INV-88213"]
    bill = out["results"][0]
    assert [line["account"] for line in bill["expense_lines"]] == ["Job Expenses:Permits and Licenses"]
    assert [line["item"] for line in bill["item_lines"]] == ["Lumber:Trim"]


async def test_query_bills_customer_job_cap_counts_bills_scanned_not_matched(call):
    out = (await call("query_bills", customer_job="Cook, Brian:Kitchen", limit=1)).structured_content

    assert [b["ref_number"] for b in out["results"]] == ["INV-88213"]
    assert out["more_available"] is True
    assert "more exist" in out["note"]


async def test_query_bills_customer_job_without_lines_is_rejected(call):
    result = await call("query_bills", customer_job="Cook, Brian:Kitchen", include_lines=False)

    assert result.is_error


async def test_query_bills_without_lines(call, replay):
    out = (await call("query_bills", include_lines=False)).structured_content

    assert _sent_bill_query(replay).findtext("IncludeLineItems") == "false"
    assert "item_lines" not in out["results"][0]
    assert "expense_lines" not in out["results"][0]
