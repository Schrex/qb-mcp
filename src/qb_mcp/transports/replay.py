"""Replay transport: canned qbXML responses, chosen by request type.

Fixtures live in one directory, one file per request type, named
`<RequestType>.xml` (e.g. `HostQueryRq.xml`), each a full QBXML response
document holding a single `*Rs`. For a message set with several requests the
matching `*Rs` elements are stitched together and `requestID`s echoed.

Every request is validated against the qbXML XSD when one is available
(env `QBXML_XSD_DIR`, else `<repo>/xsd/`; never committed, Intuit licence).
Without it validation is skipped with a warning.
"""

import os
import warnings
import xml.etree.ElementTree as ET
from functools import cache
from pathlib import Path
from typing import Any

from qb_mcp.errors import TransportError, TransportFailure

DEFAULT_XSD_DIR = Path(__file__).resolve().parents[3] / "xsd"


def xsd_dir_from_env() -> Path:
    return Path(os.environ.get("QBXML_XSD_DIR", DEFAULT_XSD_DIR))


@cache
def _load_schema(xsd_dir: Path) -> Any | None:
    """Compiled XSD (lxml) or None, warning once per directory when unavailable."""
    entries = sorted(xsd_dir.glob("qbxmlops*.xsd"))
    if not entries:
        warnings.warn(f"qbXML XSD not found in {xsd_dir}; skipping request schema validation", stacklevel=3)
        return None
    try:
        from lxml import etree
    except ImportError:
        warnings.warn("lxml not installed; skipping request schema validation", stacklevel=3)
        return None
    return etree.XMLSchema(etree.parse(str(entries[-1])))


class ReplayTransport:
    def __init__(self, fixtures_dir: Path, company_file: str = "", xsd_dir: Path | None = None):
        self.fixtures_dir = Path(fixtures_dir)
        self._company_file = company_file
        self._schema = _load_schema(xsd_dir if xsd_dir is not None else xsd_dir_from_env())
        self._overrides: dict[str, str] = {}
        self._failure: TransportError | None = None
        self.sent: list[str] = []  # every request that reached the transport

    # --- test controls -------------------------------------------------
    def respond_with(self, request_type: str, fixture: str) -> None:
        """Answer `request_type` with fixture file `fixture` (name in fixtures_dir) instead of the default."""
        self._overrides[request_type] = fixture

    def fail_with(self, error: TransportError | None) -> None:
        """Make every call raise `error` (e.g. QuickBooks not running); `None` recovers."""
        self._failure = error

    # --- Transport -----------------------------------------------------
    async def company_file(self) -> str:
        if self._failure:
            raise self._failure
        return self._company_file

    async def send(self, request: str) -> str:
        self.sent.append(request)
        if self._failure:
            raise self._failure
        self._validate(request)
        msgs_rs = ET.Element("QBXMLMsgsRs")
        for rq in ET.fromstring(request.encode("utf-8")).iter():
            if rq.tag.endswith("Rq") and rq.tag != "QBXMLMsgsRq":
                rs = self._canned_rs(rq.tag)
                if "requestID" in rq.attrib:
                    rs.set("requestID", rq.get("requestID"))
                msgs_rs.append(rs)
        root = ET.Element("QBXML")
        root.append(msgs_rs)
        return '<?xml version="1.0" ?>\n' + ET.tostring(root, encoding="unicode")

    def _validate(self, request: str) -> None:
        if self._schema is None:
            return
        from lxml import etree

        doc = etree.fromstring(request.encode("utf-8"))
        if not self._schema.validate(doc):
            # Mirrors QuickBooks rejecting an unparseable request (0x80040400).
            raise TransportError(
                TransportFailure.INVALID_REQUEST,
                f"request failed qbXML schema validation: {self._schema.error_log.last_error.message}",
            )

    def _canned_rs(self, request_type: str) -> ET.Element:
        path = self.fixtures_dir / self._overrides.get(request_type, f"{request_type}.xml")
        if not path.exists():
            raise FileNotFoundError(f"No replay fixture for {request_type}: {path}")
        rs = ET.parse(path).getroot().find("QBXMLMsgsRs")[0]
        return rs
