"""Shared test seam: MCP tools called in-process through an MCP client,
with the Replay transport standing in for QuickBooks."""

from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest
from mcp import Client

from qb_mcp.server import build_server
from qb_mcp.transports.replay import ReplayTransport

FIXTURES = Path(__file__).parent / "fixtures" / "replay"


def tool_requests(replay: ReplayTransport) -> list[str]:
    """The requests the tools sent, without the trust stamp's once-per-session closing-date read."""
    return [rq for rq in replay.sent if "<PreferencesQueryRq" not in rq]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def replay() -> ReplayTransport:
    """Default Replay transport over the canned fixtures. Tests may mutate it
    (e.g. `replay.respond_with(...)`, `replay.fail_with(...)`) before calling tools."""
    return ReplayTransport(FIXTURES, company_file=r"C:\QB\Rock Castle Construction.QBW")


@pytest.fixture
async def client(replay: ReplayTransport) -> AsyncIterator[Client]:
    async with Client(build_server(replay), raise_exceptions=True) as c:
        yield c


@pytest.fixture
def call(client: Client) -> Callable:
    """`await call(tool, **args)` -> CallToolResult."""

    async def _call(tool: str, **args):
        return await client.call_tool(tool, args)

    return _call
