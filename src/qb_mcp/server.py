"""MCP surface. `build_server(transport)` registers every tool over one Transport."""

import os
import sys
from collections.abc import Awaitable, Callable
from functools import wraps
from pathlib import Path
from datetime import date
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from qb_mcp.errors import QBError
from qb_mcp.logs import configure_logging
from qb_mcp.output import DEFAULT_LIMIT, capped, fetch_limit
from qb_mcp.qbxml import el, query
from qb_mcp.quickbooks import QuickBooks
from qb_mcp.reports import parse_report
from qb_mcp.transport import Transport

JobReport = Literal[
    "job_profitability_summary",
    "job_profitability_detail",
    "job_estimates_vs_actuals_summary",
    "job_estimates_vs_actuals_detail",
]
JOB_REPORT_TYPES: dict[str, str] = {
    "job_profitability_summary": "JobProfitabilitySummary",
    "job_profitability_detail": "JobProfitabilityDetail",
    "job_estimates_vs_actuals_summary": "JobEstimatesVsActualsSummary",
    "job_estimates_vs_actuals_detail": "JobEstimatesVsActualsDetail",
}

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

    @mcp.tool()
    @user_facing_errors
    async def list_vendors(
        name_contains: str | None = None,
        status: Literal["active", "inactive", "all"] = "active",
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """List Vendors with name, active flag, balance and contact basics.
        Optional: `name_contains` (case-insensitive substring), `status` (default active), `limit` (default 100);
        if more Vendors match than `limit`, `more_available` is true."""
        filters = [el("MaxReturned", fetch_limit(limit)), el("ActiveStatus", ACTIVE_STATUS[status])]
        if name_contains:
            filters.append(el("NameFilter", None, el("MatchCriterion", "Contains"), el("Name", name_contains)))
        (vendor_rs,) = await qb.lookup(query("VendorQueryRq", *filters))
        return capped([_vendor(v) for v in vendor_rs.records], limit)

    @mcp.tool()
    @user_facing_errors
    async def run_job_report(
        report: JobReport,
        from_date: date,
        to_date: date,
        customer_job: str | None = None,
    ) -> dict[str, Any]:
        """Run a QuickBooks job report (profitability, or estimates vs. actuals; summary or detail)
        for a date range, optionally limited to one Customer:Job (e.g. "Acme:Main St Paving") or a
        customer and all its jobs (e.g. "Acme"). Returns the report's column titles and its rows:
        each row has a `kind` (header, data, subtotal, total), a `label`, the full `name` when the
        row is for a customer/job/item, and `values` keyed by column title. Empty report -> no rows."""
        children = [
            el("JobReportType", JOB_REPORT_TYPES[report]),
            el("ReportPeriod", None, el("FromReportDate", from_date.isoformat()), el("ToReportDate", to_date.isoformat())),
        ]
        if customer_job:
            children.append(el("ReportEntityFilter", None, el("FullNameWithChildren", customer_job)))
        (rs,) = await qb.lookup(query("JobReportQueryRq", *children))
        return parse_report(rs.records[0] if rs.records else None)

    return mcp


ACTIVE_STATUS = {"active": "ActiveOnly", "inactive": "InactiveOnly", "all": "All"}


def _vendor(v: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": v.get("Name"),
        "is_active": v.get("IsActive") == "true",
        "balance": v.get("Balance"),
        "company_name": v.get("CompanyName"),
        "contact": v.get("Contact"),
        "phone": v.get("Phone"),
        "email": v.get("Email"),
        "address": v.get("VendorAddress", {}),
        "account_number": v.get("AccountNumber"),
    }


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
