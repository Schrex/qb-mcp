"""Read-only guard (ADR-0001): only `*QueryRq` requests may reach QuickBooks."""

import re
import xml.etree.ElementTree as ET

from qb_mcp.errors import ReadOnlyViolation


def ensure_read_only(request: str) -> list[str]:
    """Raise `ReadOnlyViolation` unless every request element is a `*QueryRq`; returns their tags."""
    if "<!DOCTYPE" in request.upper():
        raise ReadOnlyViolation("Refused: qbXML requests may not contain a DOCTYPE.")
    try:
        root = ET.fromstring(request.encode("utf-8"))
    except ET.ParseError as e:
        raise ReadOnlyViolation(f"Refused: request is not well-formed XML ({e}).") from e

    if root.tag != "QBXML" or len(root) == 0 or any(c.tag != "QBXMLMsgsRq" for c in root):
        raise ReadOnlyViolation("Refused: request must be a QBXML document of QBXMLMsgsRq message sets.")
    requests = [rq for msgs in root for rq in msgs]
    if not requests:
        raise ReadOnlyViolation("Refused: request contains no query.")
    # Every request element, including any nested inside another request.
    nested = [e for rq in requests for e in rq.iter() if e is not rq and e.tag.endswith("Rq")]
    disallowed = sorted({rq.tag for rq in requests + nested if not rq.tag.endswith("QueryRq")})
    if disallowed:
        raise ReadOnlyViolation(
            "Refused: this server is read-only and only sends *QueryRq requests; "
            f"got {', '.join(disallowed)}."
        )
    return [rq.tag for rq in requests]


QUERY_TAG = re.compile(r"[A-Z][A-Za-z0-9]*QueryRq")

# The only query types `run_query` sends (#12). With Admin in the QuickBooks window
# nothing in QuickBooks narrows reads (ADR-0001), so this list is what keeps employee,
# payroll and card data out of reach. Types a dedicated tool covers (Vendor, Bill,
# Customer, Item, Account, JobReport, Company) are left to that tool.
# Deliberately absent: Employee*, Paycheck, Payroll* (pay, SSN, bank data);
# BillPaymentCreditCard, ARRefundCreditCard (card data);
# Transaction, GeneralDetailReport, CustomDetail/SummaryReport (can list paychecks per employee).
ALLOWED_QUERIES = frozenset(
    {
        # money owed to Schreiber / job revenue
        "InvoiceQueryRq",  # open invoices, billed to date per job
        "SalesReceiptQueryRq",  # cash sales against a job
        "CreditMemoQueryRq",  # credits that reduce job revenue
        "ReceivePaymentQueryRq",  # what customers have paid (card details are stripped)
        "EstimateQueryRq",  # job budgets as estimated
        # job costs not on a Bill
        "PurchaseOrderQueryRq",  # committed costs, received or not
        "ItemReceiptQueryRq",  # material received before its Bill
        "VendorCreditQueryRq",  # credits that reduce job cost
        "BillPaymentCheckQueryRq",  # which Bills were paid, when
        "CheckQueryRq",  # direct-paid costs; bank reconciliation input
        "CreditCardChargeQueryRq",  # fuel and parts on company cards (#29); card data is stripped        "DepositQueryRq",  # bank reconciliation input
        "JournalEntryQueryRq",  # cost reallocations and accruals between jobs
        "TimeTrackingQueryRq",  # hours by Customer:Job (labour job costing); no pay amounts
        # lists the dedicated tools don't cover
        "ItemInventoryQueryRq",  # quantity on hand, average cost
        "ClassQueryRq",  # class names used to split costs
        # reports
        "AgingReportQueryRq",  # A/R and A/P aging
        "GeneralSummaryReportQueryRq",  # P&L, Balance Sheet (payroll appears only as account totals)
        "BudgetSummaryReportQueryRq",  # budget vs. actual
    }
)
# Attributes a caller may put on its query element. `requestID` is overwritten by
# the envelope; `iterator`/`iteratorID` (stateful) and `onError` are refused.
QUERY_ATTRIBUTES = frozenset({"requestID", "metaData"})


def parse_query_element(text: str) -> ET.Element:
    """Parse the one caller-supplied `*QueryRq` element `run_query` sends.

    Refuses (with `ReadOnlyViolation`) anything else: other or several elements,
    an envelope or nested request, attributes that change processing, and all
    `<!...>` / `<?...?>` markup (DOCTYPE, entities, comments, CDATA, processing
    instructions), so no DTD or entity is ever parsed. The element is later
    re-serialized by `qbxml.envelope`, never sent as the caller's text.
    """
    if "<!" in text or "<?" in text:
        raise ReadOnlyViolation(
            "Refused: the query may not contain DOCTYPE, entities, comments, CDATA or processing "
            "instructions; send just one *QueryRq element."
        )
    try:
        rq = ET.fromstring(text)
    except ET.ParseError as e:
        raise ReadOnlyViolation(f"Refused: the query must be exactly one well-formed *QueryRq element ({e}).") from e
    if not QUERY_TAG.fullmatch(rq.tag):
        raise ReadOnlyViolation(
            f"Refused: this server is read-only; run_query only sends a *QueryRq element, got {rq.tag}."
        )
    if rq.tag not in ALLOWED_QUERIES:
        raise ReadOnlyViolation(
            f"Refused: run_query does not send {rq.tag}. Allowed: {', '.join(sorted(ALLOWED_QUERIES))}."
        )
    extra = sorted(set(rq.attrib) - QUERY_ATTRIBUTES)
    if extra:
        raise ReadOnlyViolation(f"Refused: attribute(s) {', '.join(extra)} not allowed on the query.")
    for child in rq.iter():
        if child is rq:
            continue
        if child.tag.startswith("QBXML") or child.tag.endswith(("Rq", "Rs")):
            raise ReadOnlyViolation(f"Refused: the query may not contain another request ({child.tag}).")
        if child.attrib:
            raise ReadOnlyViolation(f"Refused: attributes are not allowed inside the query ({child.tag}).")
    return rq
