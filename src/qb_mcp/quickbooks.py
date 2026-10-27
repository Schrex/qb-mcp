"""The one path every Lookup takes: read-only guard -> transport -> parse."""

import xml.etree.ElementTree as ET

from qb_mcp import qbxml
from qb_mcp.guard import ensure_read_only
from qb_mcp.responses import QueryResult, parse_response
from qb_mcp.transport import Transport


class QuickBooks:
    def __init__(self, transport: Transport):
        self.transport = transport

    async def lookup(self, *requests: ET.Element) -> list[QueryResult]:
        """Send query elements in one message set; one result per request, same order."""
        return await self.send(qbxml.envelope(*requests))

    async def send(self, request: str) -> list[QueryResult]:
        """Send a complete qbXML document (also the path for a generic query tool)."""
        ensure_read_only(request)
        return parse_response(await self.transport.send(request))

    async def company_file(self) -> str:
        return await self.transport.company_file()
