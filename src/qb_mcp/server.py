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
        the qbXML versions it supports, and which Company File is open."""
        try:
            host_rs, company_rs = await qb.lookup(query("HostQueryRq"), query("CompanyQueryRq"))
            company_file = await qb.company_file()
        except QBError as e:
            return {"reachable": False, "error": str(e)}
        host, company = host_rs.records[0], company_rs.records[0]
        return {
            "reachable": True,
            "product": host["ProductName"],
            "version": f"{host['MajorVersion']}.{host['MinorVersion']}",
            "supported_qbxml_versions": host["SupportedQBXMLVersion"],
            "company_name": company["CompanyName"],
            "company_file": company_file,
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
    async def query_bills(
        from_date: date | None = None,
        to_date: date | None = None,
        vendor: str | None = None,
        ref_number: str | None = None,
        paid_status: Literal["paid", "unpaid", "all"] = "all",
        customer_job: str | None = None,
        include_lines: bool = True,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """Find Bills. Optional: bill date range (`from_date`/`to_date`), exact `vendor` name,
        exact `ref_number` (the Vendor's invoice number; answers "is this invoice already entered?",
        empty result if not), `paid_status` (default all), `customer_job`, `include_lines` (default
        true), `limit` (default 100; `more_available` is true if more exist). Each Bill has vendor, ref
        number, date, due date, amount, open amount, memo, paid flag and, with lines, `expense_lines`
        (account) and `item_lines` (item, quantity, cost), each line with amount, Customer:Job, class
        and billable status.
        `customer_job` (e.g. "Acme:Main St Paving", or "Acme" for the customer and all its jobs) keeps
        only Bills with a line charged to it, and only those lines. QuickBooks cannot filter Bills by
        Customer:Job, so this is applied after fetching up to `limit` Bills: `limit` counts Bills
        scanned, not matched. Add a date range or Vendor to scan fewer Bills. Needs include_lines."""
        if customer_job and not include_lines:
            raise ToolError("customer_job filters Bill lines, so it needs include_lines=true.")
        if ref_number:
            # qbXML allows an exact RefNumber only on its own; apply the other filters to its matches.
            (bill_rs,) = await qb.lookup(
                query("BillQueryRq", el("RefNumber", ref_number), el("IncludeLineItems", _bool(include_lines)))
            )
            bills = [
                _bill(b, include_lines)
                for b in bill_rs.records
                if _bill_matches(b, from_date, to_date, vendor, paid_status)
            ]
            if customer_job:
                bills = _charged_to(bills, customer_job)
            return capped(bills, limit)

        filters = [el("MaxReturned", fetch_limit(limit))]
        if from_date or to_date:
            date_range = el("TxnDateRangeFilter")
            if from_date:
                date_range.append(el("FromTxnDate", from_date.isoformat()))
            if to_date:
                date_range.append(el("ToTxnDate", to_date.isoformat()))
            filters.append(date_range)
        if vendor:
            filters.append(el("EntityFilter", None, el("FullName", vendor)))
        filters.append(el("PaidStatus", PAID_STATUS[paid_status]))
        filters.append(el("IncludeLineItems", _bool(include_lines)))
        (bill_rs,) = await qb.lookup(query("BillQueryRq", *filters))
        bills = [_bill(b, include_lines) for b in bill_rs.records]
        if not customer_job:
            return capped(bills, limit)
        # The cap applies to Bills scanned; report it even if the filter leaves fewer than `limit`.
        scanned = capped(bills, limit)
        matched = _charged_to(scanned["results"], customer_job)
        return scanned | {"results": matched, "count": len(matched)}

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
PAID_STATUS = {"paid": "PaidOnly", "unpaid": "NotPaidOnly", "all": "All"}


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


def _bool(value: bool) -> str:
    return "true" if value else "false"


def _bill_matches(
    b: dict[str, Any], from_date: date | None, to_date: date | None, vendor: str | None, paid_status: str
) -> bool:
    """Client-side equivalent of BillQueryRq's date/vendor/paid filters (QuickBooks names are case-insensitive)."""
    txn_date = date.fromisoformat(b["TxnDate"])
    if (from_date and txn_date < from_date) or (to_date and txn_date > to_date):
        return False
    if vendor and (_name(b.get("VendorRef")) or "").casefold() != vendor.casefold():
        return False
    is_paid = b.get("IsPaid") == "true"
    return paid_status == "all" or is_paid == (paid_status == "paid")


def _charged_to(bills: list[dict[str, Any]], customer_job: str) -> list[dict[str, Any]]:
    """Bills (as returned by `_bill`) cut down to their lines for `customer_job` or its sub-jobs;
    Bills with no such line are dropped."""
    wanted = customer_job.casefold()

    def on_job(line: dict[str, Any]) -> bool:
        job = (line["customer_job"] or "").casefold()
        return job == wanted or job.startswith(wanted + ":")

    out = []
    for b in bills:
        expense = [line for line in b["expense_lines"] if on_job(line)]
        items = [line for line in b["item_lines"] if on_job(line)]
        if expense or items:
            out.append(b | {"expense_lines": expense, "item_lines": items})
    return out


def _name(ref: Any) -> str | None:
    return ref.get("FullName") if isinstance(ref, dict) else None


def _line_job_fields(line: dict[str, Any]) -> dict[str, Any]:
    return {
        "customer_job": _name(line.get("CustomerRef")),
        "class": _name(line.get("ClassRef")),
        "billable_status": line.get("BillableStatus"),
    }


def _expense_line(line: dict[str, Any]) -> dict[str, Any]:
    return {
        "account": _name(line.get("AccountRef")),
        "amount": line.get("Amount"),
        "memo": line.get("Memo"),
        **_line_job_fields(line),
    }


def _item_line(line: dict[str, Any]) -> dict[str, Any]:
    return {
        "item": _name(line.get("ItemRef")),
        "description": line.get("Desc"),
        "quantity": line.get("Quantity"),
        "cost": line.get("Cost"),
        "amount": line.get("Amount"),
        **_line_job_fields(line),
    }


def _bill(b: dict[str, Any], include_lines: bool = True) -> dict[str, Any]:
    header = {
        "vendor": _name(b.get("VendorRef")),
        "ref_number": b.get("RefNumber"),
        "date": b.get("TxnDate"),
        "due_date": b.get("DueDate"),
        "amount": b.get("AmountDue"),
        "open_amount": b.get("OpenAmount"),
        "memo": b.get("Memo"),
        "is_paid": b.get("IsPaid") == "true",
    }
    if not include_lines:
        return header
    # Item groups are flattened: their member item lines carry the job/class/billable detail.
    item_lines = list(b.get("ItemLineRet", []))
    for group in b.get("ItemGroupLineRet", []):
        item_lines.extend(group.get("ItemLineRet", []))
    return {
        **header,
        "expense_lines": [_expense_line(line) for line in b.get("ExpenseLineRet", [])],
        "item_lines": [_item_line(line) for line in item_lines],
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
    from qb_mcp.transports.com import ComTransport  # ticket #3

    return ComTransport()


def main() -> None:
    build_server(default_transport()).run()
