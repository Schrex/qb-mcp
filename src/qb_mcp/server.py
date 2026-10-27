"""MCP surface. `build_server(transport)` registers every tool over one Transport."""

import os
import sys
from collections.abc import Awaitable, Callable
from functools import wraps
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from qb_mcp.errors import QBError
from qb_mcp.logs import configure_logging
from qb_mcp.qbxml import query
from qb_mcp.quickbooks import QuickBooks
from qb_mcp.transport import Transport

INSTRUCTIONS = (
    "Read-only Lookups against the QuickBooks Desktop Company File that is open on this PC. "
    "Nothing here can change QuickBooks data."
)


def user_facing_errors(tool: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Turn `QBError`s into tool errors the model (and Jeri) can read."""

    @wraps(tool)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await tool(*args, **kwargs)
        except QBError as e:
            raise ToolError(str(e)) from e

    return wrapper


def build_server(transport: Transport) -> MCPServer:
    qb = QuickBooks(transport)
    mcp = MCPServer("qb-mcp", instructions=INSTRUCTIONS)

    @mcp.tool()
    async def qb_status() -> dict[str, Any]:
        """Check the QuickBooks connection: whether QuickBooks is reachable, its product and version,
        the qbXML versions it supports, which Company File is open, and the last error (and when)."""
        try:
            host_rs, company_rs = await qb.lookup(query("HostQueryRq"), query("CompanyQueryRq"))
            company_file = await qb.company_file()
        except QBError as e:
            return {"reachable": False, "error": str(e), "last_error": qb.last_error.as_dict()}
        host, company = host_rs.records[0], company_rs.records[0]
        return {
            "reachable": True,
            "product": host["ProductName"],
            "version": f"{host['MajorVersion']}.{host['MinorVersion']}",
            "supported_qbxml_versions": host["SupportedQBXMLVersion"],
            "company_name": company["CompanyName"],
            "company_file": company_file,
            "last_error": qb.last_error and qb.last_error.as_dict(),
        }

    @mcp.tool()
    @user_facing_errors
    async def get_company_info() -> dict[str, Any]:
        """Company name, address and fiscal year start month from the open Company File."""
        (company_rs,) = await qb.lookup(query("CompanyQueryRq"))
        company = company_rs.records[0]
        return {
            "company_name": company.get("CompanyName"),
            "legal_name": company.get("LegalCompanyName"),
            "address": company.get("Address", {}),
            "fiscal_year_start_month": company.get("FirstMonthFiscalYear"),
        }

    return mcp


def default_transport() -> Transport:
    """COM on Windows; Replay elsewhere (or when QB_MCP_TRANSPORT=replay)."""
    kind = os.environ.get("QB_MCP_TRANSPORT", "com" if sys.platform == "win32" else "replay")
    if kind == "replay":
        from qb_mcp.transports.replay import ReplayTransport

        fixtures = os.environ.get(
            "QB_MCP_REPLAY_DIR", Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "replay"
        )
        return ReplayTransport(Path(fixtures), company_file="(replay)")
    from qb_mcp.transports.com import ComTransport

    return ComTransport()


def main() -> None:
    try:
        configure_logging()
    except OSError as e:  # never let logging stop the server
        print(f"qb-mcp: log file disabled ({e})", file=sys.stderr)
    build_server(default_transport()).run()
