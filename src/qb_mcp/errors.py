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
    INVALID_REQUEST = (
        "QuickBooks couldn't read the request: it doesn't match the qbXML schema "
        "(check element names, spelling and order)."
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


class UnexpectedResponse(QBError):
    """QuickBooks' response wasn't the shape expected: not qbXML, a malformed status, or a
    record missing a field we rely on."""

    def __init__(self, cause: Exception):
        if isinstance(cause, KeyError):
            detail = f"missing {cause.args[0]}"
        elif isinstance(cause, IndexError):
            detail = "no record returned"
        else:
            detail = str(cause) or type(cause).__name__
        super().__init__(
            f"QuickBooks sent a response this server didn't expect ({detail}). "
            "Try again; if it keeps happening, tell Sumanth."
        )


class QuickBooksStatusError(QBError):
    """QuickBooks answered with an error status code."""

    def __init__(self, request_type: str, code: int, message: str):
        self.request_type = request_type
        self.code = code
        self.status_message = message
        super().__init__(f"QuickBooks returned error {code} for {request_type}: {message}")
