"""What every tool result passes through: sensitive-field stripping and the result cap.

- Stripping: `SENSITIVE_ELEMENTS` (tax IDs, bank account numbers, card data)
  are dropped, with their whole subtree, at any depth by `responses.to_data`,
  which every QuickBooks response passes through (`QuickBooks.send`), so no
  tool (list, report, run_query) ever sees them. Report columns, whose values
  are attributes, are dropped by title (`reports.drop_sensitive_columns`). `redact(data)` applies the same
  deny-list to already-converted data, for output built any other way.
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
}


def is_sensitive(tag: str, parent: str) -> bool:
    return tag in SENSITIVE_ELEMENTS or tag in SENSITIVE_CHILDREN.get(parent, ())


def redact(data: Any) -> Any:
    """Copy of `data` with every `SENSITIVE_ELEMENTS` key removed at any depth."""
    if isinstance(data, dict):
        return {k: redact(v) for k, v in data.items() if k not in SENSITIVE_ELEMENTS}
    if isinstance(data, list):
        return [redact(v) for v in data]
    return data


DEFAULT_LIMIT = 100


def fetch_limit(limit: int) -> int:
    """`MaxReturned` to request so `capped` can tell whether more results exist."""
    return limit + 1


def capped(records: list[Any], limit: int) -> dict[str, Any]:
    """`{"results", "count", "more_available"[, "note"]}` with at most `limit` results."""
    more = len(records) > limit
    out: dict[str, Any] = {"results": records[:limit], "count": min(len(records), limit), "more_available": more}
    if more:
        out["note"] = (
            f"Showing the first {limit} results; more exist. "
            "Narrow the question (e.g. a name filter) or raise the limit."
        )
    return out
