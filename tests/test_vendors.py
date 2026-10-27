import xml.etree.ElementTree as ET

import pytest

pytestmark = pytest.mark.anyio


async def test_list_vendors_returns_name_active_balance_and_contact_basics(call):
    result = await call("list_vendors")

    assert not result.is_error
    out = result.structured_content
    assert out["results"][0] == {
        "name": "Patton Hardware Supplies",
        "is_active": True,
        "balance": "3260.00",
        "company_name": "Patton Hardware Supplies",
        "contact": "Tim Patton",
        "phone": "650-555-1230",
        "email": "sales@pattonhardware.example",
        "address": {
            "Addr1": "Patton Hardware Supplies",
            "Addr2": "4872 County Rd",
            "City": "Bayshore",
            "State": "CA",
            "PostalCode": "94326",
        },
        "account_number": "RC-5543",
    }
    assert [v["name"] for v in out["results"]] == ["Patton Hardware Supplies", "Timberloft Lumber"]
    assert out["more_available"] is False


def _sent_vendor_query(replay) -> ET.Element:
    return ET.fromstring(replay.sent[-1].encode()).find("QBXMLMsgsRq/VendorQueryRq")


async def test_list_vendors_sends_name_filter_status_and_limit(call, replay):
    await call("list_vendors", name_contains="Stone & Co", status="all", limit=5)

    rq = _sent_vendor_query(replay)
    assert [c.tag for c in rq] == ["MaxReturned", "ActiveStatus", "NameFilter"]
    assert rq.findtext("MaxReturned") == "6"  # one extra, to detect the cap
    assert rq.findtext("ActiveStatus") == "All"
    assert rq.findtext("NameFilter/MatchCriterion") == "Contains"
    assert rq.findtext("NameFilter/Name") == "Stone & Co"


async def test_list_vendors_defaults_to_active_vendors_and_100(call, replay):
    await call("list_vendors")

    rq = _sent_vendor_query(replay)
    assert rq.findtext("ActiveStatus") == "ActiveOnly"
    assert rq.findtext("MaxReturned") == "101"
    assert rq.find("NameFilter") is None


async def test_list_vendors_inactive_status(call, replay):
    await call("list_vendors", status="inactive")

    assert _sent_vendor_query(replay).findtext("ActiveStatus") == "InactiveOnly"


async def test_list_vendors_reports_when_more_vendors_exist_than_the_limit(call):
    out = (await call("list_vendors", limit=1)).structured_content

    assert [v["name"] for v in out["results"]] == ["Patton Hardware Supplies"]
    assert out["more_available"] is True
    assert "more exist" in out["note"]


async def test_list_vendors_no_match_is_an_empty_list(call, replay):
    replay.respond_with("VendorQueryRq", "VendorQueryRq.nomatch.xml")

    result = await call("list_vendors", name_contains="Nonexistent")

    assert not result.is_error
    assert result.structured_content["results"] == []
    assert result.structured_content["more_available"] is False
