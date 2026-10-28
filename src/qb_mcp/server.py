"""MCP surface. `build_server(transport)` registers every tool over one Transport."""

import os
import sys
from collections.abc import Awaitable, Callable
from datetime import date
from functools import wraps
from pathlib import Path
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from qb_mcp import bills, records
from qb_mcp.errors import QBError, TransportError, TransportFailure
from qb_mcp.guard import parse_query_element
from qb_mcp.logs import configure_logging
from qb_mcp.output import DEFAULT_LIMIT, capped, fetch_limit
from qb_mcp.qbxml import el, query
from qb_mcp.quickbooks import QuickBooks
from qb_mcp.reports import parse_report
from qb_mcp.transport import Transport
from qb_mcp.transports.unavailable import UnavailableTransport

# Each is the snake_case of its QuickBooks JobReportType (see `_job_report_type`).
JobReport = Literal[
    "job_profitability_summary",
    "job_profitability_detail",
    "job_estimates_vs_actuals_summary",
    "job_estimates_vs_actuals_detail",
]

AccountType = Literal[
    "AccountsPayable",
    "AccountsReceivable",
    "Bank",
    "CostOfGoodsSold",
    "CreditCard",
    "Equity",
    "Expense",
    "FixedAsset",
    "Income",
    "LongTermLiability",
    "NonPosting",
    "OtherAsset",
    "OtherCurrentAsset",
    "OtherCurrentLiability",
    "OtherExpense",
    "OtherIncome",
]

ListStatus = Literal["active", "inactive", "all"]
ACTIVE_STATUS = {"active": "ActiveOnly", "inactive": "InactiveOnly", "all": "All"}

Tool = Callable[..., Awaitable[Any]]
RecordMapper = Callable[[str, dict[str, Any]], dict[str, Any]]

INSTRUCTIONS = (
    "Read-only Lookups against the QuickBooks Desktop Company File that is open on this PC. "
    "Nothing here can change QuickBooks data."
)


def user_facing_errors(qb: QuickBooks) -> Callable[[Tool], Tool]:
    """Turn `QBError`s, and responses that aren't the expected shape, into tool errors
    the model (and Jeri) can read; add QuickBooks' warnings, if any, as `warnings`."""

    def decorate(tool: Tool) -> Tool:
        @wraps(tool)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                with qb.answering() as warnings:
                    out = await tool(*args, **kwargs)
            except QBError as e:
                raise ToolError(str(e)) from e
            return out | {"warnings": warnings} if warnings else out

        return wrapper

    return decorate


def build_server(transport: Transport) -> MCPServer:
    qb = QuickBooks(transport)
    mcp = MCPServer("qb-mcp", instructions=INSTRUCTIONS)
    user_facing = user_facing_errors(qb)

    async def list_lookup(
        rq_type: str, to_output: RecordMapper, name_contains: str | None, status: ListStatus, limit: int,
        *extra_filters: Any,
    ) -> dict[str, Any]:
        """The list tools' shared shape: name/status/limit filters -> capped, mapped records."""
        (rs,) = await qb.lookup(query(rq_type, *_list_filters(name_contains, status, limit), *extra_filters))
        return capped([to_output(tag, record) for tag, record in rs.rets], limit)

    @mcp.tool()
    async def qb_status() -> dict[str, Any]:
        """Check the QuickBooks connection: whether QuickBooks is reachable, its product and version,
        the qbXML versions it supports, which Company File is open, and the last error (and when)."""
        try:
            with qb.answering():
                host_rs, company_rs = await qb.lookup(query("HostQueryRq"), query("CompanyQueryRq"))
                company_file = await qb.company_file()
                host, company = host_rs.records[0], company_rs.records[0]
                status = {
                    "reachable": True,
                    "product": host["ProductName"],
                    "version": f"{host['MajorVersion']}.{host['MinorVersion']}",
                    "supported_qbxml_versions": host["SupportedQBXMLVersion"],
                    "company_name": company["CompanyName"],
                    "company_file": company_file,
                }
        except QBError as e:
            return {"reachable": False, "error": str(e), "last_error": qb.last_error.as_dict()}
        return status | {"last_error": qb.last_error and qb.last_error.as_dict()}

    @mcp.tool()
    @user_facing
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
    @user_facing
    async def list_vendors(
        name_contains: str | None = None, status: ListStatus = "active", limit: int = DEFAULT_LIMIT
    ) -> dict[str, Any]:
        """List Vendors with name, active flag, balance and contact basics.
        Optional: `name_contains` (case-insensitive substring), `status` (default active), `limit` (default 100);
        if more Vendors match than `limit`, `more_available` is true."""
        return await list_lookup("VendorQueryRq", records.vendor, name_contains, status, limit)

    @mcp.tool()
    @user_facing
    async def query_bills(
        from_date: date | None = None,
        to_date: date | None = None,
        vendor: str | None = None,
        ref_number: str | None = None,
        paid_status: bills.PaidStatus = "all",
        customer_job: str | None = None,
        include_lines: bool = True,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """Find Bills (Vendors' invoices as entered in QuickBooks). Optional: bill date range
        (`from_date`/`to_date`), exact `vendor` name, exact `ref_number` (the number on the Vendor's
        invoice; answers "is this Bill already entered?", empty result if not), `paid_status` (default
        all), `customer_job`, `include_lines` (default true), `limit` (default 100; `more_available` is
        true if more exist). Each Bill has vendor, ref number, date, due date, amount, open amount, memo,
        paid flag and, with lines, `expense_lines` (account) and `item_lines` (item, quantity, cost),
        each line with amount, Customer:Job, class and billable status.
        `customer_job` (e.g. "Acme:Main St Paving", or "Acme" for the customer and all its
        Customer:Jobs) keeps only Bills with a line charged to it, and only those lines. QuickBooks
        cannot filter Bills by Customer:Job, so this is applied after fetching up to `limit` Bills:
        `limit` counts Bills scanned, not matched. Add a date range or Vendor to scan fewer Bills.
        Needs include_lines."""
        if customer_job and not include_lines:
            raise ToolError("customer_job filters Bill lines, so it needs include_lines=true.")
        if ref_number:
            (bill_rs,) = await qb.lookup(bills.by_ref_number(ref_number, include_lines))
            found = [b for b in bill_rs.records if bills.matches(b, from_date, to_date, vendor, paid_status)]
        else:
            (bill_rs,) = await qb.lookup(
                bills.filtered(from_date, to_date, vendor, paid_status, include_lines, limit)
            )
            found = bill_rs.records
        # The cap applies to Bills scanned; report it even if `customer_job` leaves fewer than `limit`.
        out = capped([bills.bill(b, include_lines) for b in found], limit)
        if customer_job:
            matched = bills.charged_to(out["results"], customer_job)
            out |= {"results": matched, "count": len(matched)}
        return out

    @mcp.tool()
    @user_facing
    async def list_customer_jobs(
        name_contains: str | None = None, status: ListStatus = "active", limit: int = DEFAULT_LIMIT
    ) -> dict[str, Any]:
        """List customers and their Customer:Jobs. Each has its full `name` ("Customer:Job" for a job),
        `kind` ("customer" or "job"), `parent`, `job_status`, own `balance` and `total_balance`
        (including its Customer:Jobs). Optional: `name_contains` (case-insensitive substring),
        `status` (default active), `limit` (default 100); if more match, `more_available` is true."""
        return await list_lookup("CustomerQueryRq", records.customer_job, name_contains, status, limit)

    @mcp.tool()
    @user_facing
    async def list_items(
        name_contains: str | None = None, status: ListStatus = "active", limit: int = DEFAULT_LIMIT
    ) -> dict[str, Any]:
        """List Items of every kind (service, inventory, non_inventory, other_charge, discount, group, ...),
        each with its full `name`, `kind`, purchase `description`, `cost` and `expense_account`
        (COGS account for inventory items). Optional: `name_contains` (case-insensitive substring),
        `status` (default active), `limit` (default 100); if more match, `more_available` is true."""
        return await list_lookup("ItemQueryRq", records.item, name_contains, status, limit)

    @mcp.tool()
    @user_facing
    async def list_accounts(
        account_type: AccountType | None = None,
        name_contains: str | None = None,
        status: ListStatus = "active",
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """List chart-of-accounts Accounts with full `name`, `account_type`, `account_number`,
        own `balance` and `total_balance` (including subaccounts). Optional: `account_type`
        (e.g. Expense, CostOfGoodsSold, Bank), `name_contains` (case-insensitive substring),
        `status` (default active), `limit` (default 100); if more match, `more_available` is true."""
        extra = [el("AccountType", account_type)] if account_type else []
        return await list_lookup("AccountQueryRq", records.account, name_contains, status, limit, *extra)

    @mcp.tool()
    @user_facing
    async def run_job_report(
        report: JobReport,
        from_date: date,
        to_date: date,
        customer_job: str | None = None,
    ) -> dict[str, Any]:
        """Run a QuickBooks job report (profitability, or estimates vs. actuals; summary or detail)
        for a date range, optionally limited to one Customer:Job (e.g. "Acme:Main St Paving") or a
        customer and all its Customer:Jobs (e.g. "Acme"). The summary reports cover every job when
        `customer_job` is omitted; the detail reports require it (QuickBooks runs them for one
        Customer:Job). Returns the report's column titles and its rows: each row has a `kind`
        (header, data, subtotal, total), a `label`, the full `name` when the row is for a customer,
        Customer:Job or Item, and `values` keyed by column title. Empty report -> no rows."""
        if report.endswith("_detail") and not customer_job:
            raise ToolError(
                f"{report} needs customer_job: QuickBooks runs detail job reports for one Customer:Job "
                f"(or one customer with its jobs). For all jobs use {report.removesuffix('_detail')}_summary."
            )
        children = [
            el("JobReportType", _job_report_type(report)),
            el("ReportPeriod", None, el("FromReportDate", from_date.isoformat()), el("ToReportDate", to_date.isoformat())),
        ]
        if customer_job:
            children.append(el("ReportEntityFilter", None, el("FullNameWithChildren", customer_job)))
        (rs,) = await qb.lookup(query("JobReportQueryRq", *children))
        return parse_report(rs.records[0] if rs.records else None)

    @mcp.tool()
    @user_facing
    async def run_query(query: str, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
        """Run one raw qbXML query. Prefer the dedicated tools; use this only for questions they
        don't cover (invoices, checks, purchase orders, ...).
        `query` is exactly one `*QueryRq` element (qbXML 16.0 schema, children in schema order),
        without the <?xml?>/<QBXML>/<QBXMLMsgsRq> envelope, e.g.
        `<InvoiceQueryRq><MaxReturned>101</MaxReturned><TxnDateRangeFilter><FromTxnDate>2026-01-01</FromTxnDate></TxnDateRangeFilter></InvoiceQueryRq>`.
        Include MaxReturned (limit + 1) on transaction queries. Anything other than a single query
        element is refused. Returns the `*Ret` records (sensitive fields removed), capped at `limit`."""
        (rs,) = await qb.lookup(parse_query_element(query))
        return capped(rs.records, limit)

    return mcp


def _job_report_type(report: JobReport) -> str:
    """`job_estimates_vs_actuals_summary` -> `JobEstimatesVsActualsSummary`."""
    return "".join(word.capitalize() for word in report.split("_"))


def _list_filters(name_contains: str | None, status: ListStatus, limit: int) -> list[Any]:
    """Shared list-query filter group, in schema order: MaxReturned, ActiveStatus, NameFilter."""
    filters = [el("MaxReturned", fetch_limit(limit)), el("ActiveStatus", ACTIVE_STATUS[status])]
    if name_contains:
        filters.append(el("NameFilter", None, el("MatchCriterion", "Contains"), el("Name", name_contains)))
    return filters




def default_transport() -> Transport:
    """COM on Windows. Replay only when a developer selects it (QB_MCP_TRANSPORT=replay and
    QB_MCP_REPLAY_DIR=<fixtures dir>). Anything else off Windows can't reach QuickBooks, so
    every tool (and qb_status) says so rather than the server failing to start."""
    if os.environ.get("QB_MCP_TRANSPORT") == "replay":
        fixtures = os.environ.get("QB_MCP_REPLAY_DIR")
        if not fixtures:
            return UnavailableTransport(
                TransportError(TransportFailure.OTHER, "QB_MCP_TRANSPORT=replay needs QB_MCP_REPLAY_DIR set")
            )
        from qb_mcp.transports.replay import ReplayTransport

        return ReplayTransport(Path(fixtures), company_file="(replay)")
    if sys.platform != "win32":
        return UnavailableTransport(TransportError(TransportFailure.NOT_WINDOWS))
    from qb_mcp.transports.com import ComTransport

    return ComTransport()


def main() -> None:
    try:
        configure_logging()
    except OSError as e:  # never let logging stop the server
        print(f"qb-mcp: log file disabled ({e})", file=sys.stderr)
    build_server(default_transport()).run()
