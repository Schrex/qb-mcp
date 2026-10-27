"""The one path every Lookup takes: read-only guard -> transport -> parse.

Every failure on this path is logged and remembered as the last error, which
`qb_status` reports. Response bodies are never logged (they may hold
sensitive fields); only request types and errors.
"""

import logging
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime

from qb_mcp import qbxml
from qb_mcp.errors import QBError, UnexpectedResponse
from qb_mcp.guard import ensure_read_only
from qb_mcp.responses import QueryResult, parse_response
from qb_mcp.transport import Transport

log = logging.getLogger(__name__)

# What reading a response that isn't the expected shape raises (ET.ParseError for non-XML).
MALFORMED = (ET.ParseError, KeyError, IndexError, TypeError, ValueError, AttributeError)

# Warnings from the Lookups of the tool call in progress (per asyncio task), see `answering`.
_call_warnings: ContextVar[list[str] | None] = ContextVar("call_warnings", default=None)


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
            response = await self.transport.send(request)
            try:
                results = parse_response(response)
            except MALFORMED as e:
                raise UnexpectedResponse(e) from e
        except QBError as e:
            self._record(e)
            raise
        warnings = _call_warnings.get()
        if warnings is not None:
            warnings.extend(r.warning for r in results if r.warning)
        return results

    @contextmanager
    def answering(self) -> Iterator[list[str]]:
        """Wrap one tool call. Yields the list QuickBooks' warnings from its Lookups are
        collected in. A missing or malformed field in response data becomes an
        `UnexpectedResponse`, recorded as the last error, instead of a raw exception."""
        token = _call_warnings.set([])
        try:
            yield _call_warnings.get()
        except MALFORMED as e:
            log.warning("unexpected response shape", exc_info=True)
            error = UnexpectedResponse(e)
            self._record(error)
            raise error from e
        finally:
            _call_warnings.reset(token)

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
