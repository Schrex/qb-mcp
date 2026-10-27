import pytest

pytestmark = pytest.mark.anyio


async def test_server_lists_status_and_company_info_tools(client):
    names = {t.name for t in (await client.list_tools()).tools}
    assert {"qb_status", "get_company_info"} <= names


async def test_qb_status_reports_quickbooks_and_company_file(call):
    result = await call("qb_status")

    assert not result.is_error
    assert result.structured_content == {
        "reachable": True,
        "product": "QuickBooks Enterprise Solutions: Contractor 24.0",
        "version": "34.0",
        "supported_qbxml_versions": ["1.0", "1.1", "2.0", "13.0", "14.0", "15.0", "16.0"],
        "company_name": "Rock Castle Construction",
        "company_file": r"C:\QB\Rock Castle Construction.QBW",
    }
