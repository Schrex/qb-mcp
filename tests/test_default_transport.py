"""Which transport the installed server uses: COM on Windows; off Windows only
Replay when a developer explicitly selects it, otherwise a clear refusal."""

import pytest
from conftest import FIXTURES
from mcp import Client

from qb_mcp.server import build_server, default_transport

pytestmark = pytest.mark.anyio

NOT_WINDOWS = "only works on Windows"


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.delenv("QB_MCP_TRANSPORT", raising=False)
    monkeypatch.delenv("QB_MCP_REPLAY_DIR", raising=False)
    return monkeypatch


async def _call(tool: str) -> object:
    async with Client(build_server(default_transport()), raise_exceptions=True) as client:
        return await client.call_tool(tool, {})


async def test_off_windows_qb_status_says_the_server_only_works_on_windows(mac):
    status = (await _call("qb_status")).structured_content

    assert status["reachable"] is False
    assert NOT_WINDOWS in status["error"] and "QuickBooks" in status["error"]


async def test_off_windows_tools_say_the_server_only_works_on_windows(mac):
    result = await _call("get_company_info")

    assert result.is_error and NOT_WINDOWS in result.content[0].text


async def test_replay_is_used_only_when_explicitly_selected(mac):
    mac.setenv("QB_MCP_TRANSPORT", "replay")
    mac.setenv("QB_MCP_REPLAY_DIR", str(FIXTURES))

    status = (await _call("qb_status")).structured_content

    assert status["reachable"] is True


async def test_replay_without_a_fixtures_dir_says_what_to_set(mac):
    mac.setenv("QB_MCP_TRANSPORT", "replay")

    status = (await _call("qb_status")).structured_content

    assert status["reachable"] is False and "QB_MCP_REPLAY_DIR" in status["error"]
