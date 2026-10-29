"""A transport that can't reach QuickBooks and says why on every call, so the
server still starts and `qb_status` (and every tool) shows the reason."""

from qb_mcp.errors import TransportError


class UnavailableTransport:
    def __init__(self, error: TransportError):
        self.error = error

    async def send(self, request: str) -> str:
        raise self.error

    async def company_file(self) -> str:
        raise self.error
