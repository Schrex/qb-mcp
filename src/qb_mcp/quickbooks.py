"""The one path every Lookup takes: read-only guard -> transport -> parse.

Every failure on this path is logged and remembered as the last error, which
`qb_status` reports. Response bodies are never logged (they may hold
sensitive fields); only request types and errors.
"""

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime

from qb_mcp import qbxml
from qb_mcp.errors import QBError
from qb_mcp.guard import ensure_read_only
from qb_mcp.responses import QueryResult, parse_response
from qb_mcp.transport import Transport

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class LastError:
    message: str
    at: datetime  # UTC

    def as_dict(self) -> dict[str, str]:
        return {"message": self.message, "at": self.at.isoformat()}


class QuickBooks:
    def __init__(self, transport: Transport):
        self.transport = transport
        self.last_error: LastError | None = None

    async def lookup(self, *requests: ET.Element) -> list[QueryResult]:
        """Send query elements in one message set; one result per request, same order."""
        return await self.send(qbxml.envelope(*requests))

    async def send(self, request: str) -> list[QueryResult]:
        """Send a complete qbXML document (also the path for a generic query tool)."""
        try:
            ensure_read_only(request)
            log.info("request %s", _request_types(request))
            return parse_response(await self.transport.send(request))
        except QBError as e:
            self._record(e)
            raise

    async def company_file(self) -> str:
        try:
            return await self.transport.company_file()
        except QBError as e:
            self._record(e)
            raise

    def _record(self, e: QBError) -> None:
        log.error("%s: %s", type(e).__name__, e)
        self.last_error = LastError(str(e), datetime.now(UTC))


def _request_types(request: str) -> str:
    root = ET.fromstring(request.encode("utf-8"))
    return ", ".join(rq.tag for msgs in root for rq in msgs)
