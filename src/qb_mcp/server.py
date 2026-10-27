"""MCP surface. `build_server(transport)` registers every tool over one Transport."""

import os
import re
import sys
from collections.abc import Awaitable, Callable
from functools import wraps
from pathlib import Path
from datetime import date
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from qb_mcp.errors import QBError, TransportError, TransportFailure
from qb_mcp.guard import parse_query_element
from qb_mcp.logs import configure_logging
from qb_mcp.output import DEFAULT_LIMIT, capped, fetch_limit
from qb_mcp.qbxml import el, query
from qb_mcp.quickbooks import QuickBooks
from qb_mcp.reports import parse_report
from qb_mcp.transport import Transport
from qb_mcp.transports.unavailable import UnavailableTransport

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

Tool = Callable[..., Awaitable[Any]]

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
        name_contains: str | None = None,
        status: Literal["active", "inactive", "all"] = "active",
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """List Vendors with name, active flag, balance and contact basics.
        Optional: `name_contains` (case-insensitive substring), `status` (default active), `limit` (default 100);
        if more Vendors match than `limit`, `more_available` is true."""
        (vendor_rs,) = await qb.lookup(query("VendorQueryRq", *_list_filters(name_contains, status, limit)))
        return capped([_vendor(v) for v in vendor_rs.records], limit)

    @mcp.tool()
    @user_facing
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
    @user_facing
    async def list_customer_jobs(
        name_contains: str | None = None,
        status: Literal["active", "inactive", "all"] = "active",
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """List customers and their Customer:Jobs. Each has its full `name` ("Customer:Job" for a job),
        `kind` ("customer" or "job"), `parent`, `job_status`, own `balance` and `total_balance`
        (including its jobs). Optional: `name_contains` (case-insensitive substring),
        `status` (default active), `limit` (default 100); if more match, `more_available` is true."""
        (customer_rs,) = await qb.lookup(query("CustomerQueryRq", *_list_filters(name_contains, status, limit)))
        return capped([_customer_job(c) for c in customer_rs.records], limit)

    @mcp.tool()
    @user_facing
    async def list_items(
        name_contains: str | None = None,
        status: Literal["active", "inactive", "all"] = "active",
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """List Items of every kind (service, inventory, non_inventory, other_charge, discount, group, ...),
        each with its full `name`, `kind`, purchase `description`, `cost` and `expense_account`
        (COGS account for inventory items). Optional: `name_contains` (case-insensitive substring),
        `status` (default active), `limit` (default 100); if more match, `more_available` is true."""
        (item_rs,) = await qb.lookup(query("ItemQueryRq", *_list_filters(name_contains, status, limit)))
        return capped([_item(tag, i) for tag, i in item_rs.rets], limit)

    @mcp.tool()
    @user_facing
    async def list_accounts(
        account_type: AccountType | None = None,
        name_contains: str | None = None,
        status: Literal["active", "inactive", "all"] = "active",
        limit: int = DEFAULT_LIMIT,
    ) -> dict[str, Any]:
        """List chart-of-accounts Accounts with full `name`, `account_type`, `account_number`,
        own `balance` and `total_balance` (including subaccounts). Optional: `account_type`
        (e.g. Expense, CostOfGoodsSold, Bank), `name_contains` (case-insensitive substring),
        `status` (default active), `limit` (default 100); if more match, `more_available` is true."""
        filters = _list_filters(name_contains, status, limit)
        if account_type:
            filters.append(el("AccountType", account_type))
        (account_rs,) = await qb.lookup(query("AccountQueryRq", *filters))
        return capped([_account(a) for a in account_rs.records], limit)

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


ACTIVE_STATUS = {"active": "ActiveOnly", "inactive": "InactiveOnly", "all": "All"}
PAID_STATUS = {"paid": "PaidOnly", "unpaid": "NotPaidOnly", "all": "All"}


def _list_filters(name_contains: str | None, status: str, limit: int) -> list[Any]:
    """Shared list-query filter group, in schema order: MaxReturned, ActiveStatus, NameFilter."""
    filters = [el("MaxReturned", fetch_limit(limit)), el("ActiveStatus", ACTIVE_STATUS[status])]
    if name_contains:
        filters.append(el("NameFilter", None, el("MatchCriterion", "Contains"), el("Name", name_contains)))
    return filters


def _ref_name(ref: Any) -> str | None:
    return ref.get("FullName") if isinstance(ref, dict) else None


def _customer_job(c: dict[str, Any]) -> dict[str, Any]:
    parent = _ref_name(c.get("ParentRef"))
    return {
        "name": c.get("FullName") or c.get("Name"),
        "kind": "job" if parent else "customer",
        "parent": parent,
        "is_active": c.get("IsActive") == "true",
        "job_status": c.get("JobStatus"),
        "balance": c.get("Balance"),
        "total_balance": c.get("TotalBalance"),
        "job_description": c.get("JobDesc"),
    }


def _item_kind(tag: str) -> str:
    """`ItemNonInventoryRet` -> `non_inventory`."""
    words = re.findall(r"[A-Z][a-z]*", tag.removeprefix("Item").removesuffix("Ret"))
    return "_".join(w.lower() for w in words)


def _first(*values: Any) -> Any:
    return next((v for v in values if v not in (None, "")), None)


def _item(tag: str, i: dict[str, Any]) -> dict[str, Any]:
    """Description/cost/expense account live in different places per Item kind:
    one-way items under `SalesOrPurchase`, two-way under `SalesAndPurchase`,
    inventory/fixed-asset at the top level."""
    sop = i.get("SalesOrPurchase") or {}
    sap = i.get("SalesAndPurchase") or {}
    return {
        "name": i.get("FullName") or i.get("Name"),
        "kind": _item_kind(tag),
        "is_active": i.get("IsActive") == "true",
        "description": _first(sap.get("PurchaseDesc"), sop.get("Desc"), i.get("PurchaseDesc"), i.get("ItemDesc")),
        "cost": _first(
            sap.get("PurchaseCost"), sop.get("Price"), sop.get("PricePercent"), i.get("PurchaseCost"),
            i.get("DiscountRate"), i.get("DiscountRatePercent"),
        ),
        "expense_account": _first(
            _ref_name(sap.get("ExpenseAccountRef")), _ref_name(sop.get("AccountRef")),
            _ref_name(i.get("COGSAccountRef")), _ref_name(i.get("AssetAccountRef")), _ref_name(i.get("AccountRef")),
        ),
    }


def _account(a: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": a.get("FullName") or a.get("Name"),
        "account_type": a.get("AccountType"),
        "account_number": a.get("AccountNumber"),
        "is_active": a.get("IsActive") == "true",
        "description": a.get("Desc"),
        "balance": a.get("Balance"),
        "total_balance": a.get("TotalBalance"),
    }


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
    if vendor and (_ref_name(b.get("VendorRef")) or "").casefold() != vendor.casefold():
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


def _line_job_fields(line: dict[str, Any]) -> dict[str, Any]:
    return {
        "customer_job": _ref_name(line.get("CustomerRef")),
        "class": _ref_name(line.get("ClassRef")),
        "billable_status": line.get("BillableStatus"),
    }


def _expense_line(line: dict[str, Any]) -> dict[str, Any]:
    return {
        "account": _ref_name(line.get("AccountRef")),
        "amount": line.get("Amount"),
        "memo": line.get("Memo"),
        **_line_job_fields(line),
    }


def _item_line(line: dict[str, Any]) -> dict[str, Any]:
    return {
        "item": _ref_name(line.get("ItemRef")),
        "description": line.get("Desc"),
        "quantity": line.get("Quantity"),
        "cost": line.get("Cost"),
        "amount": line.get("Amount"),
        **_line_job_fields(line),
    }


def _bill(b: dict[str, Any], include_lines: bool = True) -> dict[str, Any]:
    header = {
        "vendor": _ref_name(b.get("VendorRef")),
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
