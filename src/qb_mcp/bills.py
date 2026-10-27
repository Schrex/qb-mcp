"""Bills: the BillQueryRq to send, the shape `query_bills` returns, and the filters
QuickBooks can't apply itself (by Customer:Job; the others alongside an exact RefNumber)."""

import xml.etree.ElementTree as ET
from datetime import date
from typing import Any, Literal

from qb_mcp.output import fetch_limit
from qb_mcp.qbxml import el, query
from qb_mcp.records import is_true, ref_name

PaidStatus = Literal["paid", "unpaid", "all"]
PAID_STATUS = {"paid": "PaidOnly", "unpaid": "NotPaidOnly", "all": "All"}


def _bool(value: bool) -> str:
    return "true" if value else "false"


def by_ref_number(ref_number: str, include_lines: bool) -> ET.Element:
    """qbXML allows an exact RefNumber only on its own; the other filters are applied
    to its matches with `matches`."""
    return query("BillQueryRq", el("RefNumber", ref_number), el("IncludeLineItems", _bool(include_lines)))


def filtered(
    from_date: date | None, to_date: date | None, vendor: str | None, paid_status: PaidStatus,
    include_lines: bool, limit: int,
) -> ET.Element:
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
    return query("BillQueryRq", *filters)


def matches(
    b: dict[str, Any], from_date: date | None, to_date: date | None, vendor: str | None, paid_status: PaidStatus
) -> bool:
    """Client-side equivalent of `filtered`'s date/vendor/paid filters (QuickBooks names are case-insensitive)."""
    txn_date = date.fromisoformat(b["TxnDate"])
    if (from_date and txn_date < from_date) or (to_date and txn_date > to_date):
        return False
    if vendor and (ref_name(b.get("VendorRef")) or "").casefold() != vendor.casefold():
        return False
    is_paid = is_true(b.get("IsPaid"))
    return paid_status == "all" or is_paid == (paid_status == "paid")


def charged_to(bills: list[dict[str, Any]], customer_job: str) -> list[dict[str, Any]]:
    """Bills (as returned by `bill`) cut down to their lines for `customer_job` or its
    sub-jobs; Bills with no such line are dropped."""
    wanted = customer_job.casefold()

    def on_customer_job(line: dict[str, Any]) -> bool:
        line_customer_job = (line["customer_job"] or "").casefold()
        return line_customer_job == wanted or line_customer_job.startswith(wanted + ":")

    out = []
    for b in bills:
        expense = [line for line in b["expense_lines"] if on_customer_job(line)]
        items = [line for line in b["item_lines"] if on_customer_job(line)]
        if expense or items:
            out.append(b | {"expense_lines": expense, "item_lines": items})
    return out


def _line_cost_tracking(line: dict[str, Any]) -> dict[str, Any]:
    """Where a Bill line's cost is tracked: Customer:Job, class, billable status."""
    return {
        "customer_job": ref_name(line.get("CustomerRef")),
        "class": ref_name(line.get("ClassRef")),
        "billable_status": line.get("BillableStatus"),
    }


def _expense_line(line: dict[str, Any]) -> dict[str, Any]:
    return {
        "account": ref_name(line.get("AccountRef")),
        "amount": line.get("Amount"),
        "memo": line.get("Memo"),
        **_line_cost_tracking(line),
    }


def _item_line(line: dict[str, Any]) -> dict[str, Any]:
    return {
        "item": ref_name(line.get("ItemRef")),
        "description": line.get("Desc"),
        "quantity": line.get("Quantity"),
        "cost": line.get("Cost"),
        "amount": line.get("Amount"),
        **_line_cost_tracking(line),
    }


def bill(b: dict[str, Any], include_lines: bool = True) -> dict[str, Any]:
    header = {
        "vendor": ref_name(b.get("VendorRef")),
        "ref_number": b.get("RefNumber"),
        "date": b.get("TxnDate"),
        "due_date": b.get("DueDate"),
        "amount": b.get("AmountDue"),
        "open_amount": b.get("OpenAmount"),
        "memo": b.get("Memo"),
        "is_paid": is_true(b.get("IsPaid")),
    }
    if not include_lines:
        return header
    # Item groups are flattened: their member item lines carry the Customer:Job/class/billable detail.
    item_lines = list(b.get("ItemLineRet", []))
    for group in b.get("ItemGroupLineRet", []):
        item_lines.extend(group.get("ItemLineRet", []))
    return {
        **header,
        "expense_lines": [_expense_line(line) for line in b.get("ExpenseLineRet", [])],
        "item_lines": [_item_line(line) for line in item_lines],
    }
