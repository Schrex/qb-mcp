"""Stripping stays narrow (#30): Vendor and Customer contacts, addresses, phone and email,
memos and custom fields (DataExt) survive it."""

import pytest

pytestmark = pytest.mark.anyio


async def test_list_vendors_keeps_contact_phone_email_and_address(call, replay):
    replay.respond_with("VendorQueryRq", "VendorQueryRq.sensitive.xml")

    vendors = (await call("list_vendors", status="all")).structured_content["results"]

    patton = next(v for v in vendors if v["name"] == "Patton Hardware Supplies")
    assert patton["contact"] == "Tim Patton"
    assert patton["phone"] == "650-555-1230"
    assert patton["email"] == "sales@pattonhardware.example"
    assert patton["address"] == {
        "Addr1": "Patton Hardware Supplies",
        "Addr2": "4872 County Rd",
        "City": "Bayshore",
        "State": "CA",
        "PostalCode": "94326",
    }


async def test_list_customer_jobs_keeps_contact_phone_email_and_address(call):
    jobs = (await call("list_customer_jobs", status="all")).structured_content["results"]

    kristy = next(c for c in jobs if c["name"] == "Abercrombie, Kristy")
    assert kristy["contact"] == "Kristy Abercrombie"
    assert kristy["phone"] == "415-555-6579"
    assert kristy["email"] == "kristy@samplename.com"
    assert kristy["bill_address"] == {
        "Addr1": "Kristy Abercrombie",
        "Addr2": "5647 Cypress Hill Rd",
        "City": "Bayshore",
        "State": "CA",
        "PostalCode": "94326",
    }


async def test_run_query_keeps_memos_and_custom_fields(call, replay):
    replay.respond_with("CheckQueryRq", "CheckQueryRq.sensitive.xml")

    result = await call(
        "run_query", query="<CheckQueryRq><MaxReturned>101</MaxReturned><OwnerID>0</OwnerID></CheckQueryRq>"
    )

    check = next(c for c in result.structured_content["results"] if c["RefNumber"] == "517")
    assert check["Memo"] == "Ticket 88123 culvert parts"
    assert check["ExpenseLineRet"][0]["Memo"] == "PO 4471 lien waiver on file"
    assert check["DataExtRet"] == [{"OwnerID": "0", "DataExtName": "Ticket No", "DataExtType": "STR255TYPE", "DataExtValue": "88123"}]
