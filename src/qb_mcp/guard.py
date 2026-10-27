"""Read-only guard (ADR-0001): only `*QueryRq` requests may reach QuickBooks."""

import xml.etree.ElementTree as ET

from qb_mcp.errors import ReadOnlyViolation


def ensure_read_only(request: str) -> None:
    """Raise `ReadOnlyViolation` unless every request element is a `*QueryRq`."""
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
    disallowed = sorted({rq.tag for rq in requests if not rq.tag.endswith("QueryRq")})
    if disallowed:
        raise ReadOnlyViolation(
            "Refused: this server is read-only and only sends *QueryRq requests; "
            f"got {', '.join(disallowed)}."
        )
