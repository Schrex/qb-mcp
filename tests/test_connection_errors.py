"""Connection failures as Jeri sees them: clear tool errors, last error in
qb_status, and a log file. Driven through the tools with Replay failure simulation."""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from mcp import Client

from qb_mcp.server import build_server
from qb_mcp.errors import TransportError, TransportFailure
from qb_mcp.logs import configure_logging, default_log_dir

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


# --- Unexpected responses: a clear message and a last error, never a raw exception ---

UNEXPECTED = "QuickBooks sent a response this server didn't expect"


async def test_qb_status_reports_a_response_missing_expected_fields(call, replay):
    replay.respond_with("HostQueryRq", "HostQueryRq.noproduct.xml")

    status = (await call("qb_status")).structured_content

    assert status["reachable"] is False
    assert UNEXPECTED in status["error"]
    assert UNEXPECTED in status["last_error"]["message"]


@pytest.mark.parametrize("fixture", ["CompanyQueryRq.malformed.xml", "CompanyQueryRq.empty.xml"])
async def test_tools_report_malformed_responses_clearly_and_as_last_error(call, replay, fixture):
    replay.respond_with("CompanyQueryRq", fixture)

    result = await call("get_company_info")

    assert result.is_error
    assert UNEXPECTED in result.content[0].text and "Traceback" not in result.content[0].text
    replay.respond_with("CompanyQueryRq", "CompanyQueryRq.xml")
    assert UNEXPECTED in (await call("qb_status")).structured_content["last_error"]["message"]


class NotQbxmlTransport:
    async def send(self, request: str) -> str:
        return "<html>Service Unavailable"

    async def company_file(self) -> str:
        return "C:\\QB\\x.QBW"


async def test_a_response_that_is_not_qbxml_is_reported_clearly():
    async with Client(build_server(NotQbxmlTransport()), raise_exceptions=True) as client:
        result = await client.call_tool("get_company_info", {})
        status = (await client.call_tool("qb_status", {})).structured_content

    assert result.is_error and UNEXPECTED in result.content[0].text
    assert status["reachable"] is False and UNEXPECTED in status["last_error"]["message"]


# --- Log file --------------------------------------------------------------


@pytest.fixture
def log_file(tmp_path):
    """Logging configured into a temp dir; returns a reader for all log text."""
    handler = configure_logging(tmp_path, max_bytes=4000, backups=2)

    def read() -> str:
        handler.flush()
        return "".join(p.read_text() for p in sorted(tmp_path.glob("qb-mcp.log*")))

    read.dir = tmp_path
    yield read
    logging.getLogger("qb_mcp").removeHandler(handler)
    handler.close()


async def test_log_records_request_types_and_errors(call, replay, log_file):
    await call("get_company_info")
    replay.fail_with(TransportError(TransportFailure.QB_NOT_RUNNING))
    await call("get_company_info")

    text = log_file()
    assert "CompanyQueryRq" in text
    assert "QuickBooks isn't running" in text


async def test_log_never_contains_response_bodies(call, log_file):
    await call("get_company_info")
    await call("qb_status")

    text = log_file()
    assert "Rock Castle" not in text and "1735 County Road" not in text and "Bayshore" not in text


async def test_log_file_is_size_capped(call, replay, log_file):
    replay.fail_with(TransportError(TransportFailure.OTHER, "x" * 200))
    for _ in range(100):
        await call("get_company_info")

    files = list(log_file.dir.glob("qb-mcp.log*"))
    assert 1 < len(files) <= 3
    assert all(f.stat().st_size <= 4000 for f in files)


def test_default_log_dir_is_in_local_app_data_on_windows(monkeypatch, tmp_path):
    monkeypatch.delenv("QB_MCP_LOG_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("sys.platform", "win32")

    assert default_log_dir() == tmp_path / "qb-mcp" / "logs"
