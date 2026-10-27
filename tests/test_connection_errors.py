"""Connection failures as Jeri sees them: clear tool errors, last error in
qb_status, and a log file. Driven through the tools with Replay failure simulation."""

from datetime import UTC, datetime, timedelta

import pytest

from qb_mcp.errors import TransportError, TransportFailure

pytestmark = pytest.mark.anyio


async def test_other_com_error_carries_quickbooks_message(call, replay):
    replay.fail_with(TransportError(TransportFailure.OTHER, "The QuickBooks database is locked; code 0x8004041B"))

    result = await call("get_company_info")

    assert result.is_error
    text = result.content[0].text
    assert "Couldn't talk to QuickBooks" in text and "database is locked" in text and "0x8004041B" in text
    assert "Traceback" not in text


async def test_qb_status_has_no_last_error_when_all_is_well(call):
    assert (await call("qb_status")).structured_content["last_error"] is None


async def test_qb_status_reports_last_error_and_when_it_happened(call, replay):
    replay.fail_with(TransportError(TransportFailure.NOT_AUTHORIZED))
    await call("get_company_info")
    replay.fail_with(None)  # QuickBooks recovers

    status = (await call("qb_status")).structured_content

    assert status["reachable"] is True
    assert "hasn't authorized this integration" in status["last_error"]["message"]
    at = datetime.fromisoformat(status["last_error"]["at"])
    assert timedelta(0) <= datetime.now(UTC) - at < timedelta(minutes=1)


async def test_quickbooks_status_errors_also_count_as_last_error(call, replay):
    replay.respond_with("CompanyQueryRq", "CompanyQueryRq.error3260.xml")
    await call("get_company_info")
    replay.respond_with("CompanyQueryRq", "CompanyQueryRq.xml")

    status = (await call("qb_status")).structured_content

    assert "3260" in status["last_error"]["message"]
