"""Turns qbXML response documents into plain JSON-friendly data.

Status 0 is success, 1 ("no match") is an empty result, warnings (e.g. 500,
partial match) still return data plus a `warning` message, errors raise
`QuickBooksStatusError`.
"""

import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from qb_mcp.errors import QuickBooksStatusError
from qb_mcp.output import is_sensitive
from qb_mcp.reports import drop_sensitive_columns

# Elements that are lists in the schema even when only one occurs. Extend as needed.
REPEATED = frozenset(
    {
        "SupportedQBXMLVersion",
        "ExpenseLineRet",
        "ItemLineRet",
        "ItemGroupLineRet",
        "LinkedTxn",
        "DataExtRet",
        "ColDesc",
        "ColTitle",
        "ColData",
        "DataRow",
        "TextRow",
        "SubtotalRow",
        "TotalRow",
        "ContactsRet",
        "AdditionalContactRef",
        "ShipToAddress",
    }
)

NO_MATCH = 1


@dataclass
class QueryResult:
    """One `*Rs` element: its `*Ret` children as (tag, data) pairs, and QuickBooks' warning if any."""

    request_type: str  # e.g. "VendorQueryRq"
    rets: list[tuple[str, Any]] = field(default_factory=list)  # tag kept for mixed types (ItemServiceRet, ...)
    warning: str | None = None

    @property
    def records(self) -> list[Any]:
        return [data for _, data in self.rets]


def to_data(element: ET.Element) -> Any:
    """Leaf -> text; otherwise a dict of children, with repeated (or REPEATED) tags as lists.

    Attributes (report rows/columns carry their values in them) become keys of
    the dict; a leaf with attributes becomes a dict of just its attributes.
    Sensitive elements (`output.is_sensitive`) are dropped with their subtrees.
    """
    if len(element) == 0 and not element.attrib:
        return (element.text or "").strip()
    children = [child for child in element if not is_sensitive(child.tag, element.tag)]
    counts = Counter(child.tag for child in children)
    data: dict[str, Any] = dict(element.attrib)
    for child in children:
        value = to_data(child)
        if counts[child.tag] > 1 or child.tag in REPEATED:
            data.setdefault(child.tag, []).append(value)
        else:
            data[child.tag] = value
    return data


def _ret_data(ret: ET.Element) -> Any:
    data = to_data(ret)
    return drop_sensitive_columns(data) if ret.tag == "ReportRet" else data


def parse_response(response: str) -> list[QueryResult]:
    """One `QueryResult` per `*Rs` in the response, in document order."""
    root = ET.fromstring(response.encode("utf-8"))
    results = []
    for msgs in root:
        for rs in msgs:
            request_type = rs.tag.removesuffix("Rs") + "Rq"
            code = int(rs.get("statusCode", "0"))
            severity, message = rs.get("statusSeverity"), rs.get("statusMessage", "")
            if severity == "Error":
                raise QuickBooksStatusError(request_type, code, message)
            # "Warn" (e.g. 500: a name in a filter wasn't found) still comes with usable
            # data, so it is returned, not raised; its message travels with the result
            # so the tool's output says the answer may be incomplete.
            warning = f"QuickBooks warning {code} for {request_type}: {message}" if severity == "Warn" else None
            rets = [] if code == NO_MATCH else [(ret.tag, _ret_data(ret)) for ret in rs]
            results.append(QueryResult(request_type, rets, warning))
    return results
