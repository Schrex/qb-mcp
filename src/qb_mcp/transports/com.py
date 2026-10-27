"""COM transport: real QuickBooks Desktop through the QBXMLRP2 request processor.

Each call opens a connection and a session against whichever Company File is
open, does its work, and closes both. Every COM call runs on one dedicated
worker thread that has COM initialized (QBXMLRP2 is apartment-threaded), so
the MCP event loop never touches COM. Windows-only modules (pywin32) are
imported lazily, on that thread, so this module imports anywhere.
"""

import asyncio
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

from qb_mcp.errors import TransportError, TransportFailure

# Shown in QuickBooks' authorization dialog and stored in its Integrated
# Applications prefs. Never change it: QuickBooks would ask to re-authorize.
APP_NAME = "Schreiber QuickBooks Lookup"
LOCAL_QBD = 1  # QBXMLRPConnectionType.localQBD
FILE_MODE_DO_NOT_CARE = 2  # attach in whatever mode the open file is in
DEFAULT_TIMEOUT = 120.0  # BeginSession blocks while QuickBooks shows a dialog

T = TypeVar("T")
log = logging.getLogger(__name__)

_DIALOG = (
    "QuickBooks has a window or dialog open (possibly an authorization prompt) and is waiting on it. "
    "Close or answer it in QuickBooks, then try again."
)
_BUSY = "QuickBooks is busy or still starting up. Wait a moment, then try again."
_SAME_USER = "QuickBooks must be open as the same Windows user as Claude, and not as administrator."
_SDK_MISSING = "QuickBooks' SDK components are missing or not installed correctly."
_NOT_REGISTERED = "QuickBooks' request processor isn't registered on this PC."

# HRESULT -> (cause, extra hint). Codes from Intuit's SDK error list; some
# runtime mappings are unverified on real QuickBooks (see ticket #3 notes).
_HRESULTS: dict[int, tuple[TransportFailure, str]] = {
    0x80040400: (TransportFailure.INVALID_REQUEST, ""),  # error parsing the qbXML text stream
    0x80040408: (TransportFailure.QB_NOT_RUNNING, _SAME_USER),
    0x80040416: (TransportFailure.QB_NOT_RUNNING, ""),
    0x80040417: (TransportFailure.NO_COMPANY_FILE, ""),
    0x8004040A: (TransportFailure.OTHER, "A different Company File is open in QuickBooks."),
    0x80040418: (TransportFailure.NOT_AUTHORIZED, "A QuickBooks admin must approve it the first time."),
    0x80040419: (TransportFailure.NOT_AUTHORIZED, "Certificate invalid."),
    0x8004041E: (TransportFailure.NOT_AUTHORIZED, "Certificate expired."),
    0x8004042C: (TransportFailure.NOT_AUTHORIZED, "Certificate revoked."),
    0x8004041A: (TransportFailure.NOT_AUTHORIZED, "Check Preferences > Integrated Applications."),
    0x8004041D: (TransportFailure.NOT_AUTHORIZED, "Not allowed to log in automatically."),
    0x80040420: (TransportFailure.NOT_AUTHORIZED, ""),
    0x80040414: (TransportFailure.OTHER, _DIALOG),
    0x8004041B: (TransportFailure.OTHER, _BUSY),
    0x8004041C: (TransportFailure.OTHER, _BUSY),
    0x80040424: (TransportFailure.OTHER, _BUSY),
    0x80040401: (TransportFailure.OTHER, _SDK_MISSING),
    0x80040405: (TransportFailure.OTHER, _SDK_MISSING),
    0x80040407: (TransportFailure.OTHER, _SDK_MISSING),
    0x800401F3: (TransportFailure.OTHER, _NOT_REGISTERED),
    0x80040154: (TransportFailure.OTHER, _NOT_REGISTERED),
}


def error_from_hresult(hresult: int, qb_message: str = "") -> TransportError:
    """A clear `TransportError` for a COM failure; unknown codes keep the code and QuickBooks' message."""
    code = hresult & 0xFFFFFFFF
    cause, hint = _HRESULTS.get(code, (TransportFailure.OTHER, ""))
    parts = [p for p in (hint, qb_message.strip(), f"code 0x{code:08X}") if p]
    return TransportError(cause, "; ".join(parts))


def error_from_com_error(e: BaseException) -> TransportError:
    """Unpack a pywintypes.com_error: args == (hresult, text, excepinfo, argerror).

    Late-bound calls report DISP_E_EXCEPTION and carry QuickBooks' own code in
    excepinfo[5] and its message in excepinfo[2].
    """
    hresult, text, excepinfo = (list(e.args) + [0, "", None])[:3]
    if excepinfo and len(excepinfo) > 5:
        return error_from_hresult(excepinfo[5] or hresult, excepinfo[2] or text or "")
    return error_from_hresult(hresult or 0, text or "")


class Win32Com:
    """The real pywin32 bindings, imported only when first used (on the COM thread)."""

    def co_initialize(self) -> None:
        import pythoncom

        pythoncom.CoInitialize()

    def dispatch(self) -> Any:
        import win32com.client

        return win32com.client.Dispatch("QBXMLRP2.RequestProcessor")

    @property
    def com_error(self) -> type[BaseException]:
        import pywintypes

        return pywintypes.com_error


class ComTransport:
    def __init__(self, timeout: float = DEFAULT_TIMEOUT, com: Any = None):
        self.timeout = timeout
        self._com = com or Win32Com()
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="qb-com", initializer=self._com.co_initialize
        )

    # --- Transport -----------------------------------------------------
    async def send(self, request: str) -> str:
        return await self._run(lambda rp, ticket: rp.ProcessRequest(ticket, request))

    async def company_file(self) -> str:
        return await self._run(lambda rp, ticket: rp.GetCurrentCompanyFileName(ticket))

    # --- internals -----------------------------------------------------
    async def _run(self, work: Callable[[Any, str], T]) -> T:
        future = self._executor.submit(self._in_session, work)
        try:
            return await asyncio.wait_for(asyncio.wrap_future(future), self.timeout)
        except TimeoutError:
            raise TransportError(
                TransportFailure.OTHER, f"QuickBooks didn't answer within {self.timeout:.0f}s. {_DIALOG}"
            ) from None

    def _in_session(self, work: Callable[[Any, str], T]) -> T:
        """Runs on the COM thread: connect, begin session, work, always clean up."""
        com_error = self._com.com_error

        def cleanup(step: Callable[[], Any]) -> None:
            try:
                step()
            except com_error:
                log.warning("QuickBooks cleanup call failed", exc_info=True)

        try:
            rp = self._com.dispatch()
            rp.OpenConnection2("", APP_NAME, LOCAL_QBD)
            try:
                ticket = rp.BeginSession("", FILE_MODE_DO_NOT_CARE)
                try:
                    return work(rp, ticket)
                finally:
                    cleanup(lambda: rp.EndSession(ticket))
            finally:
                cleanup(rp.CloseConnection)
        except com_error as e:
            raise error_from_com_error(e) from e
