"""The Transport seam: the only boundary between the server and QuickBooks.

Implementations: `transports.replay.ReplayTransport` (tests, macOS) and the
COM transport (Windows, QBXMLRP2). Both raise `errors.TransportError` when no
qbXML response could be obtained.
"""

from typing import Protocol


class Transport(Protocol):
    async def send(self, request: str) -> str:
        """Send one qbXML request document; return the qbXML response document."""
        ...

    async def company_file(self) -> str:
        """Path of the Company File QuickBooks currently has open."""
        ...
