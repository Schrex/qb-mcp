"""What every tool result passes through: sensitive-field stripping and the result cap.

- Stripping: `SENSITIVE_ELEMENTS` (tax IDs, bank account numbers, card data)
  and `SENSITIVE_CHILDREN` are dropped, with their whole subtree, by
  `responses.to_data`, which every QuickBooks response passes through
  (`QuickBooks.send`), so no tool (list, report, run_query) ever sees them.
  Report columns, whose values are attributes, are dropped by title
  (`reports.drop_sensitive_columns`). Payee addresses (`PAYEE_ADDRESS`) are dropped
  on the same path unless the payee is confirmed a Vendor, Customer or Other Name.
- Result cap: ask QuickBooks for `fetch_limit(limit)` records (one more than
  `limit`), then `capped(records, limit)` keeps `limit` and says whether more exist.
"""

from typing import Any

# Matched by element name, anywhere. `AccountNumber` is absent: on an Account it is the
# chart-of-accounts number, which Jeri needs (a Vendor's is in `SENSITIVE_CHILDREN`).
# The bank account number of a Bank account is `BankNumber`.
SENSITIVE_ELEMENTS = frozenset(
    {
        # tax IDs
        "VendorTaxIdent",
        "SSN",
        "EIN",
        "TaxRegistrationNumber",
        "ResaleNumber",
        # bank
        "BankNumber",
        "BankAccountNumber",
        # credit cards (subtrees and their children, in case a parent changes)
        "CreditCardInfo",
        "CreditCardTxnInfo",
        "CreditCardNumber",
        "ExpirationMonth",
        "ExpirationYear",
        "NameOnCard",
        "CreditCardAddress",
        "CreditCardPostalCode",
        "MerchantAccountNumber",
        "CardSecurityCodeMatch",
        "CreditCardTransID",
        # personal (employees)
        "BirthDate",
        "PagerPIN",
    }
)

# Matched by element name, only directly inside the given parent element.
SENSITIVE_CHILDREN: dict[str, frozenset[str]] = {
    # A Vendor's AccountNumber is Schreiber's account at that Vendor, which for a card
    # issuer (e.g. Amex) is the credit-card number.
    "VendorRet": frozenset({"AccountNumber"}),
    # Hours, employee, Customer:Job and Item stay (job costing); the pay rate and the
    # payroll item reveal pay (#30). `Rate` elsewhere (e.g. an item line's price) is kept.
    "TimeTrackingRet": frozenset({"Rate", "PayrollItemWageRef"}),
}


# Payee address fields, by record. A Check's payee may be an Employee (e.g. an expense
# reimbursement), whose home address is personal data, but `PayeeEntityRef` holds only a
# ListID and name, not the payee's type. So these are kept only for a payee QuickBooks
# confirms is a Vendor, Customer or Other Name (`QuickBooks.send` looks the payees up), and
# dropped otherwise: Employees, and any payee the lookup can't confirm (#30).
PAYEE_ADDRESS: dict[str, frozenset[str]] = {
    "CheckRet": frozenset({"Address", "AddressBlock"}),
}
PAYEE_REF = "PayeeEntityRef"


def is_payee_address(tag: str, parent: str) -> bool:
    return tag in PAYEE_ADDRESS.get(parent, ())


def is_sensitive(tag: str, parent: str) -> bool:
    return tag in SENSITIVE_ELEMENTS or tag in SENSITIVE_CHILDREN.get(parent, ())


DEFAULT_LIMIT = 100


def fetch_limit(limit: int) -> int:
    """`MaxReturned` to request so `capped` can tell whether more results exist."""
    return limit + 1


def capped(records: list[Any], limit: int) -> dict[str, Any]:
    """`{"results", "count", "more_available"[, "note"]}` with at most `limit` results."""
    more = len(records) > limit
    out: dict[str, Any] = {"results": records[:limit], "count": min(len(records), limit), "more_available": more}
    if more:
        out["note"] = _more_note(f"the first {limit}")
    return out


def _more_note(shown: str) -> str:
    return f"Showing {shown} results; more exist. Narrow the question (e.g. a name filter) or raise the limit."


def narrowed(out: dict[str, Any], kept: list[Any], limit: int) -> dict[str, Any]:
    """`capped` output after a filter applied to its results (one QuickBooks can't apply): `results`
    and `count` are what is kept; a capped note says how many of the records scanned matched."""
    out = out | {"results": kept, "count": len(kept)}
    if out["more_available"]:
        out["note"] = _more_note(f"{len(kept)} found in the first {limit} scanned")
    return out
