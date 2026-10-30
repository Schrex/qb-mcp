"""Bills: the BillQueryRq to send, the shape `query_bills` returns, and the filters
QuickBooks can't apply itself (by Customer:Job; the others alongside an exact RefNumber)."""

import xml.etree.ElementTree as ET
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from qb_mcp.output import fetch_limit
from qb_mcp.qbxml import el, query
from qb_mcp.records import is_true, ref_name

PaidStatus = Literal["paid", "unpaid", "all"]
PAID_STATUS = {"paid": "PaidOnly", "unpaid": "NotPaidOnly", "all": "All"}

# Linked transactions that pay down a Bill. Others (item receipts, purchase orders) only link quantities.
APPLIED_TO_BILL = frozenset({"BillPaymentCheck", "BillPaymentCreditCard", "VendorCredit", "JournalEntry"})
PAID_TOLERANCE = Decimal("0.005")


def _bool(value: bool) -> str:
    return "true" if value else "false"


def by_ref_number(ref_number: str, include_lines: bool) -> ET.Element:
    """qbXML allows an exact RefNumber only on its own; the other filters are applied
    to its matches with `matches`."""
    return query(
        "BillQueryRq", el("RefNumber", ref_number), el("IncludeLineItems", _bool(include_lines)), el("IncludeLinkedTxns", "true")
    )


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
    filters.append(el("IncludeLinkedTxns", "true"))  # payments and credits, for `open_amount`
    return query("BillQueryRq", *filters)


def matches(b: dict[str, Any], from_date: date | None, to_date: date | None, vendor: str | None) -> bool:
    """Client-side equivalent of `filtered`'s date/vendor filters (QuickBooks names are case-insensitive)."""
    txn_date = date.fromisoformat(b["TxnDate"])
    if (from_date and txn_date < from_date) or (to_date and txn_date > to_date):
        return False
    return not vendor or (ref_name(b.get("VendorRef")) or "").casefold() == vendor.casefold()


def with_paid_status(bills: list[dict[str, Any]], paid_status: PaidStatus) -> list[dict[str, Any]]:
    """Bills (as returned by `bill`) whose computed `is_paid` agrees with `paid_status`. QuickBooks'
    own PaidStatus filter is sent too; this keeps the results consistent with `open_amount`."""
    return [b for b in bills if paid_status == "all" or b["is_paid"] == (paid_status == "paid")]


def open_balance(b: dict[str, Any]) -> Decimal:
    """The Bill's own open balance: AmountDue less the bill payments and credits linked to it
    (their LinkedTxn Amounts are negative; the sign is ignored). QuickBooks' BillRet/OpenAmount is
    not used: on a real Company File it held one Vendor-wide figure on every Bill (#14).
    A Bill QuickBooks marks paid is 0."""
    if is_true(b.get("IsPaid")):
        return Decimal("0.00")
    applied = sum(
        (abs(Decimal(t["Amount"])) for t in b.get("LinkedTxn", []) if t.get("TxnType") in APPLIED_TO_BILL),
        Decimal("0"),
    )
    return max(Decimal(b.get("AmountDue") or "0") - applied, Decimal("0")).quantize(Decimal("0.01"))


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
    open_amount = open_balance(b)
    header = {
        "vendor": ref_name(b.get("VendorRef")),
        "ap_account": ref_name(b.get("APAccountRef")),
        "ref_number": b.get("RefNumber"),
        "date": b.get("TxnDate"),
        "due_date": b.get("DueDate"),
        "amount": b.get("AmountDue"),
        "open_amount": f"{open_amount:.2f}",
        "memo": b.get("Memo"),
        "is_paid": open_amount <= PAID_TOLERANCE,
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
