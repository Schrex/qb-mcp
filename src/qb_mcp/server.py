"""MCP surface. `build_server(transport)` registers every tool over one Transport."""

import os
import sys
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from datetime import date
from functools import wraps
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from qb_mcp import bills, records
from qb_mcp.errors import QBError, TransportError, TransportFailure
from qb_mcp.guard import ALLOWED_QUERIES, parse_query_element
from qb_mcp.logs import configure_logging
from qb_mcp.output import DEFAULT_LIMIT, capped, fetch_limit
from qb_mcp.qbxml import el, query
from qb_mcp.quickbooks import QuickBooks
from qb_mcp.reports import parse_report
from qb_mcp.responses import to_data
from qb_mcp.stamp import DEFAULT_NARROW_BY, Answer, stamped
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

# QuickBooks' JobStatus values (qbXML `JobStatus`), snake_cased; "none" is QuickBooks' "None" (not set).
JobStatus = Literal["pending", "awarded", "in_progress", "closed", "not_awarded", "none"]
JOB_STATUS = {
    "pending": "Pending", "awarded": "Awarded", "in_progress": "InProgress",
    "closed": "Closed", "not_awarded": "NotAwarded", "none": "None",
}

Tool = Callable[..., Awaitable[Any]]
RecordMapper = Callable[[str, dict[str, Any]], dict[str, Any]]

INSTRUCTIONS = (
    "Read-only Lookups against the QuickBooks Desktop Company File that is open on this PC. "
    "Nothing here can change QuickBooks data. Every result has a `stamp` (filters applied, `complete`); "
    "when `complete` is false, say so and don't total the results: follow the stamp's `cap_notice` to narrow."
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


def closing_date_reader(qb: QuickBooks) -> Callable[[], Awaitable[date | None]]:
    """Reads the Company File's closing date (Preferences > Accounting) once per session, then
    caches it. None if QuickBooks has none set; also None, uncached, if the read fails."""
    known: list[date | None] = []

    async def closing_date() -> date | None:
        if not known:
            try:
                (rs,) = await qb.lookup(query("PreferencesQueryRq", el("IncludeRetElement", "AccountingPreferences")))
            except QBError:
                return None  # unknown for now; read again on the next call
            prefs = rs.records[0] if rs.records else {}
            text = prefs.get("AccountingPreferences", {}).get("ClosingDate")
            known.append(date.fromisoformat(text) if text else None)
        return known[0]

    return closing_date


def build_server(transport: Transport) -> MCPServer:
    qb = QuickBooks(transport)
    mcp = MCPServer("qb-mcp", instructions=INSTRUCTIONS)
    user_facing = user_facing_errors(qb)
    closing_date = closing_date_reader(qb)

    def tool(narrow_by: str = DEFAULT_NARROW_BY, description: str | None = None, errors: bool = True):
        """Register a tool. Every tool is `stamped` (trust stamp); `errors` adds `user_facing` errors."""

        def register(fn: Tool) -> Tool:
            wrapped = stamped(narrow_by, closing_date)(fn)
            return mcp.tool(description=description)(user_facing(wrapped) if errors else wrapped)

        return register

    async def list_lookup(
        rq_type: str, to_output: RecordMapper, name_contains: str | None, status: ListStatus, limit: int,
        *extra_filters: Any,
    ) -> dict[str, Any]:
        """The list tools' shared shape: name/status/limit filters -> capped, mapped records."""
        (rs,) = await qb.lookup(query(rq_type, *_list_filters(name_contains, status, limit), *extra_filters))
        return capped([to_output(tag, record) for tag, record in rs.rets], limit)

    async def jobs_in(
        job_status: list[JobStatus], name_contains: str | None, status: ListStatus
    ) -> list[tuple[str, dict[str, Any]]]:
        """Every customer/Customer:Job (tag, record) matching name and active status whose
        JobStatus is one of `job_status`. Uncapped: QuickBooks can't filter by JobStatus."""
        wanted = {JOB_STATUS[s] for s in job_status}
        (rs,) = await qb.lookup(query("CustomerQueryRq", *_list_filters(name_contains, status, None)))
        return [(tag, r) for tag, r in rs.rets if r.get("JobStatus", "None") in wanted]

    @tool(errors=False)
    async def qb_status() -> dict[str, Any]:
        """Check the QuickBooks connection: whether QuickBooks is reachable, its product and version,
        the qbXML versions it supports, which Company File is open, this server's version, and the
        last error (and when)."""
        server_version = package_version("qb-mcp")
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
                    "server_version": server_version,
                }
        except QBError as e:
            return {
                "reachable": False,
                "error": str(e),
                "server_version": server_version,
                "last_error": qb.last_error.as_dict(),
            }
        return status | {"last_error": qb.last_error and qb.last_error.as_dict()}

    @tool()
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

    @tool(narrow_by="name_contains or status")
    async def list_vendors(
        name_contains: str | None = None, status: ListStatus = "active", limit: int = DEFAULT_LIMIT
    ) -> dict[str, Any]:
        """List Vendors with name, active flag, balance and contact basics.
        Optional: `name_contains` (case-insensitive substring), `status` (default active), `limit` (default 100);
        if more Vendors match than `limit`, `more_available` is true."""
        return await list_lookup("VendorQueryRq", records.vendor, name_contains, status, limit)

    @tool(narrow_by="date or Vendor")
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
        true if more exist). Each Bill has vendor, `ap_account` (e.g. a separate retainage A/P account),
        ref number, date, due date, amount, `open_balance`, memo,
        `is_paid` and, with lines, `expense_lines` (account) and `item_lines` (item, quantity, cost),
        each line with amount, Customer:Job, class and billable status.
        `customer_job` (e.g. "Acme:Main St Paving", or "Acme" for the customer and all its
        Customer:Jobs) keeps only Bills with a line charged to it, and only those lines. QuickBooks
        cannot filter Bills by Customer:Job, so this is applied after fetching up to `limit` Bills:
        `limit` counts Bills scanned, not matched. Add a date range or Vendor to scan fewer Bills.
        Needs include_lines.
        `open_balance` is what is still owed on this Bill, meant to match the Open Balance column of
        QuickBooks' A/P Aging Detail report; worked out here: amount less the bill payments and
        Vendor credits linked to the Bill (0 if QuickBooks marks it paid). QuickBooks' own OpenAmount
        is not returned: it showed one Vendor-wide total on every Bill. Not yet verified against a real
        Company File; if a figure matters, check it against A/P Aging Detail.
        `is_paid` means open_balance is 0, and `paid_status` returns only Bills that agree with it.
        `is_void` marks a voided Bill: still listed, but left out of `totals` (sums of `amount` and
        `open_balance`) and counted in the stamp's `voided_excluded`. No `totals` with `customer_job`
        (a Bill's amount includes other Customer:Jobs' lines) or when capped."""
        if customer_job and not include_lines:
            raise ToolError("customer_job filters Bill lines, so it needs include_lines=true.")
        if ref_number:
            (bill_rs,) = await qb.lookup(bills.by_ref_number(ref_number, include_lines))
            found = [b for b in bill_rs.records if bills.matches(b, from_date, to_date, vendor)]
        else:
            (bill_rs,) = await qb.lookup(
                bills.filtered(from_date, to_date, vendor, paid_status, include_lines, limit)
            )
            found = bill_rs.records
        # The cap applies to Bills scanned; report it even if `customer_job` leaves fewer than `limit`.
        out = capped([bills.bill(b, include_lines) for b in found], limit)
        if paid_status != "all":
            kept = bills.with_paid_status(out["results"], paid_status)
            out |= {"results": kept, "count": len(kept)}
        if customer_job:
            matched = bills.charged_to(out["results"], customer_job)
            # No totals: a Bill's amount includes its lines for other Customer:Jobs.
            return out | {"results": matched, "count": len(matched)}
        return out | {"totals": bills.totals(out["results"])}

    @tool(narrow_by="name_contains or status")
    async def list_customer_jobs(
        name_contains: str | None = None,
        status: ListStatus = "active",
        job_status: list[JobStatus] | None = None,
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """List customers and their Customer:Jobs. Each has its full `name` ("Customer:Job" for a job),
        `kind` ("customer" or "job"), `parent`, `job_status`, own `balance` and `total_balance`
        (including its Customer:Jobs), `contact`, `phone`, `email` and `bill_address`. Optional: `name_contains` (case-insensitive substring),
        `status` (default active), `job_status` (keep only these, e.g. ["awarded", "in_progress"];
        combines with `status`), `limit` (default 100); if more match, `more_available` is true."""
        if not job_status:
            return await list_lookup("CustomerQueryRq", records.customer_job, name_contains, status, limit)
        # QuickBooks can't filter by JobStatus, so fetch every name/status match and filter here;
        # the cap then counts matching records, so `count`/`more_available` stay exact.
        kept = [records.customer_job(tag, r) for tag, r in await jobs_in(job_status, name_contains, status)]
        return capped(kept, limit) | {"filters": {"job_status": sorted(set(job_status))}}

    @tool(narrow_by="name_contains or status")
    async def list_items(
        name_contains: str | None = None, status: ListStatus = "active", limit: int = DEFAULT_LIMIT
    ) -> dict[str, Any]:
        """List Items of every kind (service, inventory, non_inventory, other_charge, discount, group, ...),
        each with its full `name`, `kind`, purchase `description`, `cost` and `expense_account`
        (COGS account for inventory items). Optional: `name_contains` (case-insensitive substring),
        `status` (default active), `limit` (default 100); if more match, `more_available` is true."""
        return await list_lookup("ItemQueryRq", records.item, name_contains, status, limit)

    @tool(narrow_by="account_type, name_contains or status")
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

    @tool()
    async def run_job_report(
        report: JobReport,
        from_date: date,
        to_date: date,
        customer_job: str | None = None,
        job_status: list[JobStatus] | None = None,
    ) -> dict[str, Any]:
        """Run a QuickBooks job report (profitability, or estimates vs. actuals; summary or detail)
        for a date range, optionally limited to one Customer:Job (e.g. "Acme:Main St Paving") or a
        customer and all its Customer:Jobs (e.g. "Acme"). The summary reports cover every job when
        `customer_job` is omitted; the detail reports require it (QuickBooks runs them for one
        Customer:Job). Returns the report's column titles and its rows: each row has a `kind`
        (header, data, subtotal, total), a `label`, the full `name` when the row is for a customer,
        Customer:Job or Item, and `values` keyed by column title. A period with no activity gives
        a single TOTAL row of zeros; an unknown Customer:Job is a QuickBooks error (3120).
        Optional `job_status` (e.g. ["awarded", "in_progress"]) limits the report to Customer:Jobs
        (active or not) in those statuses, within `customer_job` if given; `filters` echoes it.
        If none match, no report is run: `rows` is empty and `note` says why."""
        if report.endswith("_detail") and not customer_job:
            raise ToolError(
                f"{report} needs customer_job: QuickBooks runs detail job reports for one Customer:Job "
                f"(or one customer with its jobs). For all jobs use {report.removesuffix('_detail')}_summary."
            )
        children = [
            el("JobReportType", _job_report_type(report)),
            el("ReportPeriod", None, el("FromReportDate", from_date.isoformat()), el("ToReportDate", to_date.isoformat())),
        ]
        if job_status:
            # QuickBooks' reports can't filter by JobStatus either: name the matching Customer:Jobs.
            names = [r["FullName"] for _, r in await jobs_in(job_status, None, "all")]
            if customer_job:  # the Customer:Job itself and everything under it (names are case-insensitive)
                scope = customer_job.lower()
                names = [n for n in names if n.lower() == scope or n.lower().startswith(scope + ":")]
            applied = {"filters": {"job_status": sorted(set(job_status))}}
            if not names:
                where = f" under {customer_job}" if customer_job else ""
                return parse_report(None) | applied | {
                    "note": f"No Customer:Job{where} has job status {', '.join(sorted(set(job_status)))}; no report run."
                }
            children.append(el("ReportEntityFilter", None, *(el("FullName", n) for n in names)))
        elif customer_job:
            children.append(el("ReportEntityFilter", None, el("FullNameWithChildren", customer_job)))
        (rs,) = await qb.lookup(query("JobReportQueryRq", *children))
        report = parse_report(rs.records[0] if rs.records else None)
        return report | applied if job_status else report

    @tool(description=RUN_QUERY_DESCRIPTION)
    async def run_query(query: str, limit: int = DEFAULT_LIMIT) -> Answer:
        element = parse_query_element(query)
        (rs,) = await qb.lookup(element)
        result = capped([records.raw(tag, record) for tag, record in rs.rets], limit)
        return Answer(result, stamp={"filters": _query_filters(element)})

    return mcp


RUN_QUERY_DESCRIPTION = f"""Run one raw qbXML query. Prefer the dedicated tools; use this only for questions they
don't cover (invoices, checks, purchase orders, ...).
`query` is exactly one `*QueryRq` element (qbXML 16.0 schema, children in schema order),
without the <?xml?>/<QBXML>/<QBXMLMsgsRq> envelope, e.g.
`<InvoiceQueryRq><MaxReturned>101</MaxReturned><TxnDateRangeFilter><FromTxnDate>2026-01-01</FromTxnDate></TxnDateRangeFilter></InvoiceQueryRq>`.
Allowed query types: {", ".join(sorted(ALLOWED_QUERIES))}.
Include MaxReturned (limit + 1) on transaction queries. Anything other than a single allowed query
element is refused. Returns the `*Ret` records (sensitive fields removed), capped at `limit`.
Invoices, ReceivePayments and CreditMemos also carry `ar_account`, the A/R account they post to
(retainage receivable may be kept in its own A/R account).
Every transaction carries `posting` (false for Estimates, Purchase Orders and Sales Orders: never add
them to posting transactions) and `is_void` (voided: zero amount, memo "VOID:"; leave out of any sum)."""


def _job_report_type(report: JobReport) -> str:
    """`job_estimates_vs_actuals_summary` -> `JobEstimatesVsActualsSummary`."""
    return "".join(word.capitalize() for word in report.split("_"))


def _query_filters(element: ET.Element) -> dict[str, Any]:
    """A raw query's filter elements, for the stamp: every child but MaxReturned and Include*/OwnerID options."""
    return {
        child.tag: to_data(child)
        for child in element
        if child.tag not in ("MaxReturned", "OwnerID") and not child.tag.startswith("Include")
    }


def _list_filters(name_contains: str | None, status: ListStatus, limit: int | None) -> list[Any]:
    """Shared list-query filter group, in schema order: MaxReturned (omitted when `limit` is
    None: fetch all), ActiveStatus, NameFilter."""
    filters = [el("MaxReturned", fetch_limit(limit))] if limit is not None else []
    filters.append(el("ActiveStatus", ACTIVE_STATUS[status]))
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
