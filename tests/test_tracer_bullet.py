import pytest
from conftest import FIXTURES
from mcp import Client

from qb_mcp.errors import ReadOnlyViolation, TransportError, TransportFailure
from qb_mcp.quickbooks import QuickBooks
from qb_mcp.server import build_server
from qb_mcp.transports.replay import ReplayTransport, xsd_dir_from_env

pytestmark = pytest.mark.anyio


async def test_server_lists_status_and_company_info_tools(client):
    names = {t.name for t in (await client.list_tools()).tools}
    assert {"qb_status", "get_company_info"} <= names


async def test_qb_status_reports_quickbooks_and_company_file(call):
    result = await call("qb_status")

    assert not result.is_error
    assert result.structured_content == {
        "reachable": True,
        "product": "Intuit QuickBooks Enterprise Solutions: Contractor 24.0 Trial Version",
        "version": "34.0",
        "supported_qbxml_versions": [
            "1.0", "1.1", "2.0", "2.1", "3.0", "4.0", "4.1", "5.0", "6.0", "7.0", "8.0", "9.0", "10.0",
            "11.0", "12.0", "13.0", "14.0", "15.0", "16.0", "17.0",
        ],
        "company_name": "Rock Castle Construction",
        "company_file": r"C:\QB\Rock Castle Construction.QBW",
        "server_version": "0.1.0",
        "last_error": None,
        "stamp": {"filters": {}, "complete": True, "cap_notice": None, "closing_date": "2023-12-31", "period": "may still change"},
    }


async def test_get_company_info_returns_name_address_and_fiscal_year_start(call):
    result = await call("get_company_info")

    assert not result.is_error
    assert result.structured_content == {
        "company_name": "Rock Castle Construction",
        "legal_name": "Rock Castle Construction",
        "address": {
            "Addr1": "1735 County Road",
            "City": "Bayshore",
            "State": "CA",
            "PostalCode": "94326",
            "Country": "US",
        },
        "fiscal_year_start_month": "January",
        "stamp": {"filters": {}, "complete": True, "cap_notice": None, "closing_date": "2023-12-31", "period": "may still change"},
    }


async def test_company_info_never_exposes_tax_ids(call, replay):
    replay.respond_with("CompanyQueryRq", "CompanyQueryRq.sensitive.xml")

    text = (await call("get_company_info")).content[0].text
    assert "EIN" not in text and "SSN" not in text and "00-7904153" not in text


async def test_qb_status_reports_unreachable_when_quickbooks_is_closed(call, replay):
    replay.fail_with(TransportError(TransportFailure.QB_NOT_RUNNING))

    result = await call("qb_status")

    assert not result.is_error
    assert result.structured_content["reachable"] is False
    assert "QuickBooks isn't running" in result.structured_content["error"]


async def test_qb_status_reports_server_version_even_when_quickbooks_is_closed(call, replay):
    replay.fail_with(TransportError(TransportFailure.QB_NOT_RUNNING))

    assert (await call("qb_status")).structured_content["server_version"] == "0.1.0"


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (TransportFailure.QB_NOT_RUNNING, "QuickBooks isn't running"),
        (TransportFailure.NO_COMPANY_FILE, "no Company File is loaded"),
        (TransportFailure.NOT_AUTHORIZED, "hasn't authorized this integration"),
    ],
)
async def test_tools_give_clear_message_when_quickbooks_unavailable(call, replay, failure, expected):
    replay.fail_with(TransportError(failure))

    result = await call("get_company_info")

    assert result.is_error
    assert expected in result.content[0].text


async def test_quickbooks_error_status_is_reported_with_code_and_message(call, replay):
    replay.respond_with("CompanyQueryRq", "CompanyQueryRq.error3260.xml")

    result = await call("get_company_info")

    assert result.is_error
    text = result.content[0].text
    assert "3260" in text and "Insufficient permission level" in text


# --- Read-only guard -------------------------------------------------------
# No tool sends caller-supplied qbXML yet (run_query arrives later), so this
# exercises the single path every tool's request takes.


async def test_non_query_request_is_refused_before_reaching_quickbooks(replay):
    vendor_add = (
        '<?xml version="1.0"?><?qbxml version="16.0"?><QBXML><QBXMLMsgsRq onError="stopOnError">'
        "<VendorAddRq><VendorAdd><Name>Evil Co</Name></VendorAdd></VendorAddRq>"
        "</QBXMLMsgsRq></QBXML>"
    )

    with pytest.raises(ReadOnlyViolation, match="VendorAddRq"):
        await QuickBooks(replay).send(vendor_add)
    assert replay.sent == []


async def test_query_mixed_with_a_write_is_refused(replay):
    mixed = (
        '<?xml version="1.0"?><QBXML><QBXMLMsgsRq onError="stopOnError">'
        '<CompanyQueryRq requestID="1"/><BillModRq requestID="2"/>'
        "</QBXMLMsgsRq></QBXML>"
    )

    with pytest.raises(ReadOnlyViolation, match="BillModRq"):
        await QuickBooks(replay).send(mixed)
    assert replay.sent == []


async def test_write_nested_inside_a_query_is_refused(replay):
    nested = (
        '<?xml version="1.0"?><QBXML><QBXMLMsgsRq onError="stopOnError">'
        '<InvoiceQueryRq requestID="1"><InvoiceAddRq><InvoiceAdd/></InvoiceAddRq></InvoiceQueryRq>'
        "</QBXMLMsgsRq></QBXML>"
    )

    with pytest.raises(ReadOnlyViolation, match="InvoiceAddRq"):
        await QuickBooks(replay).send(nested)
    assert replay.sent == []


# --- Replay schema validation ----------------------------------------------


async def test_replay_warns_and_skips_validation_when_xsd_absent(tmp_path):
    with pytest.warns(UserWarning, match="XSD not found"):
        replay = ReplayTransport(FIXTURES, xsd_dir=tmp_path)
    async with Client(build_server(replay)) as c:
        assert not (await c.call_tool("get_company_info", {})).is_error


@pytest.mark.skipif(not list(xsd_dir_from_env().glob("qbxmlops*.xsd")), reason="qbXML XSD not available locally")
async def test_replay_rejects_requests_that_violate_the_schema():
    replay = ReplayTransport(FIXTURES)
    out_of_order = (
        '<?xml version="1.0"?><QBXML><QBXMLMsgsRq onError="stopOnError"><VendorQueryRq requestID="1">'
        "<ActiveStatus>All</ActiveStatus><MaxReturned>5</MaxReturned>"
        "</VendorQueryRq></QBXMLMsgsRq></QBXML>"
    )

    with pytest.raises(TransportError, match="schema validation"):
        await QuickBooks(replay).send(out_of_order)
