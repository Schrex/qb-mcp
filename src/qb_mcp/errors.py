"""Errors the tools turn into clear, Jeri-facing messages.

Every failure a tool can report derives from `QBError`; its `str()` is the
message shown to the user.
"""

from enum import Enum


class QBError(Exception):
    """Base for all user-facing failures."""


class TransportFailure(Enum):
    QB_NOT_RUNNING = "QuickBooks isn't running. Open QuickBooks and the Company File, then try again."
    NO_COMPANY_FILE = "QuickBooks is open but no Company File is loaded. Open the Company File, then try again."
    NOT_AUTHORIZED = (
        "QuickBooks hasn't authorized this integration (or the QuickBooks user was denied). "
        "Ask Sumanth to approve it in QuickBooks."
    )
    OTHER = "Couldn't talk to QuickBooks."


class TransportError(QBError):
    """The request never got a qbXML response (QuickBooks closed, not authorized, ...)."""

    def __init__(self, cause: TransportFailure, detail: str = ""):
        self.cause = cause
        self.detail = detail
        super().__init__(f"{cause.value} ({detail})" if detail else cause.value)


class ReadOnlyViolation(QBError):
    """A request contained something other than `*QueryRq` elements (ADR-0001)."""


class QuickBooksStatusError(QBError):
    """QuickBooks answered with an error status code."""

    def __init__(self, request_type: str, code: int, message: str):
        self.request_type = request_type
        self.code = code
        self.status_message = message
        super().__init__(f"QuickBooks returned error {code} for {request_type}: {message}")
