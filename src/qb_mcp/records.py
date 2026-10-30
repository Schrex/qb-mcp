"""QuickBooks list records (parsed `*Ret` data) -> the shapes the list tools return.

Each mapper takes a `*Ret` element's tag and data (the tag tells Item kinds apart).
"""

import re
from decimal import Decimal
from typing import Any


def ref_name(ref: Any) -> str | None:
    """`FullName` of a `*Ref` (e.g. VendorRef), or None."""
    return ref.get("FullName") if isinstance(ref, dict) else None


def full_name(record: dict[str, Any]) -> str | None:
    """Full (colon-joined) name of a list record; `Name` for lists without hierarchy (Vendors)."""
    return record.get("FullName") or record.get("Name")


def is_true(value: Any) -> bool:
    """qbXML booleans are the strings "true"/"false"."""
    return value == "true"


def first_present(*values: Any) -> Any:
    return next((v for v in values if v not in (None, "")), None)


# A transaction's own amount fields (not line amounts), by transaction type.
TXN_AMOUNTS = ("Amount", "AmountDue", "TotalAmount", "Subtotal")


def is_void(txn: dict[str, Any]) -> bool:
    """Whether QuickBooks has voided this transaction. qbXML 16.0 has no void flag; voiding keeps the
    transaction but zeroes its amounts and prefixes its memo "VOID:". Both must hold, so a memo that
    merely starts "VOID:" on a live amount is not taken for a void."""
    memo = txn.get("Memo")
    amounts = [txn[f] for f in TXN_AMOUNTS if txn.get(f) not in (None, "")]
    return (
        isinstance(memo, str)
        and memo.upper().startswith("VOID:")
        and bool(amounts)
        and all(Decimal(a) == 0 for a in amounts)
    )


# Non-posting transactions (GLOSSARY: Posting / Non-posting): quotes and commitments that change no
# account balance. Listed with `posting: false`, never summed with posting transactions.
NON_POSTING = frozenset({"EstimateRet", "PurchaseOrderRet", "SalesOrderRet"})

# Transactions posted to an A/R account; `run_query` names it as `ar_account` (retainage may have its own).
POSTS_TO_AR = frozenset({"InvoiceRet", "ReceivePaymentRet", "CreditMemoRet"})


def raw(tag: str, record: Any) -> Any:
    """A `run_query` record: QuickBooks' own data, plus `ar_account` on A/R transactions and
    `is_void` on every transaction."""
    if not isinstance(record, dict):
        return record
    if tag in POSTS_TO_AR:
        record = record | {"ar_account": ref_name(record.get("ARAccountRef"))}
    if "TxnID" in record:
        record = record | {"posting": tag not in NON_POSTING, "is_void": is_void(record)}
    return record


def vendor(_tag: str, v: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": full_name(v),
        "is_active": is_true(v.get("IsActive")),
        "balance": v.get("Balance"),
        "company_name": v.get("CompanyName"),
        "contact": v.get("Contact"),
        "phone": v.get("Phone"),
        "email": v.get("Email"),
        "address": v.get("VendorAddress", {}),
    }


def customer_job(_tag: str, c: dict[str, Any]) -> dict[str, Any]:
    parent = ref_name(c.get("ParentRef"))
    return {
        "name": full_name(c),
        "kind": "job" if parent else "customer",
        "parent": parent,
        "is_active": is_true(c.get("IsActive")),
        "job_status": c.get("JobStatus"),
        "balance": c.get("Balance"),
        "total_balance": c.get("TotalBalance"),
        "job_description": c.get("JobDesc"),
        "contact": c.get("Contact"),
        "phone": c.get("Phone"),
        "email": c.get("Email"),
        "bill_address": c.get("BillAddress", {}),
    }


def _item_kind(tag: str) -> str:
    """`ItemNonInventoryRet` -> `non_inventory`."""
    words = re.findall(r"[A-Z][a-z]*", tag.removeprefix("Item").removesuffix("Ret"))
    return "_".join(w.lower() for w in words)


def item(tag: str, i: dict[str, Any]) -> dict[str, Any]:
    """Description/cost/expense account live in different places per Item kind:
    one-way items under `SalesOrPurchase`, two-way under `SalesAndPurchase`,
    inventory/fixed-asset at the top level."""
    one_way = i.get("SalesOrPurchase") or {}
    two_way = i.get("SalesAndPurchase") or {}
    return {
        "name": full_name(i),
        "kind": _item_kind(tag),
        "is_active": is_true(i.get("IsActive")),
        "description": first_present(
            two_way.get("PurchaseDesc"), one_way.get("Desc"), i.get("PurchaseDesc"), i.get("ItemDesc")
        ),
        "cost": first_present(
            two_way.get("PurchaseCost"), one_way.get("Price"), one_way.get("PricePercent"), i.get("PurchaseCost"),
            i.get("DiscountRate"), i.get("DiscountRatePercent"),
        ),
        "expense_account": first_present(
            ref_name(two_way.get("ExpenseAccountRef")), ref_name(one_way.get("AccountRef")),
            ref_name(i.get("COGSAccountRef")), ref_name(i.get("AssetAccountRef")), ref_name(i.get("AccountRef")),
        ),
    }


def account(_tag: str, a: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": full_name(a),
        "account_type": a.get("AccountType"),
        "account_number": a.get("AccountNumber"),
        "is_active": is_true(a.get("IsActive")),
        "description": a.get("Desc"),
        "balance": a.get("Balance"),
        "total_balance": a.get("TotalBalance"),
    }
