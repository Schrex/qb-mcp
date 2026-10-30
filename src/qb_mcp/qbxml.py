"""Builds qbXML request documents.

Callers assemble `*QueryRq` elements with `query()` / `el()` in the order the
qbXML schema requires (it is a strict sequence), then wrap them with
`envelope()`. ElementTree escapes all text, so user input is safe.
"""

import xml.etree.ElementTree as ET

QBXML_VERSION = "16.0"


def el(tag: str, text: str | int | None = None, *children: ET.Element) -> ET.Element:
    """An element with optional text and children, e.g. `el("MaxReturned", 101)`."""
    e = ET.Element(tag)
    if text is not None:
        e.text = str(text)
    e.extend(children)
    return e


def query(rq_type: str, *children: ET.Element, **attrs: str) -> ET.Element:
    """A request element, e.g. `query("VendorQueryRq", el("MaxReturned", 101))`."""
    rq = ET.Element(rq_type, attrs)
    rq.extend(children)
    return rq


def envelope(*requests: ET.Element, on_error: str = "stopOnError") -> str:
    """Wrap requests in one message set; requestIDs are their 1-based positions."""
    msgs = ET.Element("QBXMLMsgsRq", onError=on_error)
    for i, rq in enumerate(requests, start=1):
        rq.set("requestID", str(i))
        msgs.append(rq)
    root = ET.Element("QBXML")
    root.append(msgs)
    body = ET.tostring(root, encoding="unicode")
    return f'<?xml version="1.0" encoding="utf-8"?>\n<?qbxml version="{QBXML_VERSION}"?>\n{body}'
