"""The Transport seam: the only boundary between the server and QuickBooks.

Implementations: `transports.replay.ReplayTransport` (tests, development), the
COM transport (Windows, QBXMLRP2) and `transports.unavailable.UnavailableTransport`
(anywhere QuickBooks can't be reached, e.g. not Windows). All raise `errors.TransportError` when no
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
