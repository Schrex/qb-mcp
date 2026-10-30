"""The trust stamp: what every tool result is based on, so Jeri (or her accountant) can check it.

`stamped` wraps every tool (see `build_server`'s `tool`), so no tool can omit the stamp. It fills
what it can work out centrally:

- `filters`: the tool's filter arguments as applied (defaults included; `None` and "all" left out,
  as they filter nothing; `NOT_FILTERS` are output options, not filters).
- `complete` / `cap_notice`: from the result's `more_available` (see `output.capped`). A capped result
  gets a plain-words notice and loses any `totals`: no total is ever returned over a capped set.
- `closing_date` / `period`: the Company File's closing date (from `closing_date`, read once per
  session by `build_server`) and whether the answer's date range is closed or may still change;
  "no closing date set" when QuickBooks has none, "closing date unknown" when it couldn't be read.
- `voided_listed` / `non_posting_listed`: how many listed records are voided (`is_void`) or
  non-posting (`posting: false`) (#28).
- `voided_excluded`: how many voided records were left out of the result's `totals`; None when the
  result carries no total (nothing was excluded from one).
- `basis`: "not a report; basis does not apply" unless a report tool says Accrual or Cash.
- `dates`: from `from_date`/`to_date` (transaction date) if the tool takes them, else the tool's
  registered default (`AS_OF_TODAY` for balances, None when no dates apply). A tool whose figures
  are current balances (`live_balances`, e.g. Bills' open balances) adds `balances_as_of: today`
  and is never "closed": payments entered after the closing date still change those balances.
- `check_in_quickbooks`: the QuickBooks report or screen to check against, given when the tool is registered.

A tool adds facts only it knows by returning `Answer(result, stamp={...})`, whose keys override
`TrustStamp` fields. New stamp facts (basis, dates, closing date, excluded counts, where to check in
QuickBooks) are new `TrustStamp` fields with defaults, so every tool carries them at once.
"""

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from functools import wraps
from typing import Any

from qb_mcp.date_macros import FISCAL, end_of

Tool = Callable[..., Awaitable[Any]]
# What a closing-date read gives: the date, NO_CLOSING_DATE (QuickBooks has none set), or None (read failed).
NO_CLOSING_DATE = "none set"
ClosingDate = Callable[[], Awaitable[date | str | None]]
UNKNOWN = "closing date unknown"
NOT_SET = "no closing date set; may still change"

# Arguments that shape the output rather than choose which records are in it.
NOT_FILTERS = frozenset({"limit", "include_lines", "report", "query"})
DEFAULT_NARROW_BY = "date, Vendor or Customer:Job"
RANGE_END = frozenset({"to_date", "ToTxnDate", "ToReportDate"})
DATE_MACROS = frozenset({"DateMacro", "ReportDateMacro"})
FISCAL_MACRO = "unknown (fiscal date macro)"
NO_BASIS = "not a report; basis does not apply"
TXN_DATE = "transaction date"
AS_OF_TODAY = {"as_of": "today"}  # current balances
BALANCES_AS_OF_TODAY = {"balances_as_of": "today"}  # current balances on records chosen by date


@dataclass(frozen=True)
class TrustStamp:
    filters: dict[str, Any] = field(default_factory=dict)
    complete: bool = True
    cap_notice: str | None = None
    closing_date: str | None = None  # ISO date; None if none is set or it couldn't be read
    period: str = UNKNOWN  # or "closed" / "may still change" / NOT_SET
    voided_listed: int = 0
    voided_excluded: int | None = None  # None: no total was computed
    non_posting_listed: int = 0
    # #27: what the figures are based on and where to check them.
    basis: str = NO_BASIS
    # {"from", "to", "field"} for a date range (None = open-ended), {"as_of"} for balances, None if no dates apply.
    dates: dict[str, Any] | None = None
    check_in_quickbooks: str = ""  # a QuickBooks menu path (report or screen); every tool names one
    accounts: str | None = None  # an aging report's A/P or A/R accounts (e.g. "all A/P accounts"); else None


@dataclass
class Answer:
    """A tool result plus stamp facts only the tool knows (keys are `TrustStamp` fields)."""

    result: dict[str, Any]
    stamp: dict[str, Any] = field(default_factory=dict)


def cap_notice(shown: int, narrow_by: str, scanned: int | None = None) -> str:
    """`shown`: results returned; `scanned`: records fetched, when a filter applied after fetching
    (e.g. query_bills' `paid_status`, `customer_job`) left fewer than that."""
    if scanned is not None and shown < scanned:
        first = f"{shown} found in the first {scanned} scanned"
    else:
        first = f"the first {shown}"
    return f"Showing {first}; more exist; totals incomplete. Narrow by {narrow_by}."


def _arguments(tool: Tool, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    bound = inspect.signature(tool).bind(*args, **kwargs)
    bound.apply_defaults()
    return dict(bound.arguments)


def _applied_filters(arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        name: value.isoformat() if isinstance(value, date) else value
        for name, value in arguments.items()
        if name not in NOT_FILTERS and value is not None and value != "all"
    }


def date_range(start: Any, end: Any, field: str = TXN_DATE) -> dict[str, Any]:
    return {"from": start, "to": end, "field": field}


def _dates(arguments: dict[str, Any], default: dict[str, Any] | None, live_balances: bool) -> dict[str, Any] | None:
    """A tool taking `from_date`/`to_date` filters by transaction date; otherwise the tool's `default`."""
    if "from_date" in arguments or "to_date" in arguments:
        filters = _applied_filters(arguments)
        dates = date_range(filters.get("from_date"), filters.get("to_date"))
        return dates | BALANCES_AS_OF_TODAY if live_balances else dates
    return default


def _listed(result: dict[str, Any], field: str, value: bool) -> int:
    """How many listed records have `field` == `value` (e.g. `is_void` true, `posting` false)."""
    return sum(1 for r in result.get("results", []) if isinstance(r, dict) and r.get(field) is value)


def _period(filters: dict[str, Any], closed_through: date, live: bool) -> str:
    """"closed" if the answer's date range ends on or before the closing date, else "may still change"
    (no end date: it runs to today); a fiscal date macro's end isn't known here (`FISCAL_MACRO`).
    `live` figures (current balances) run to today whatever the date range."""
    today = date.today()
    end = today if live else _range_end(filters, today)
    if end == FISCAL:
        return FISCAL_MACRO
    return "closed" if isinstance(end, date) and end <= closed_through else "may still change"


def _range_end(filters: dict[str, Any], today: date) -> date | str | None:
    """The date range's end among the filters: a tool's `to_date`, a raw query's ToTxnDate/ToReportDate,
    or the end of its date macro relative to `today` (`FISCAL` for a fiscal one)."""
    for name, value in filters.items():
        if name in RANGE_END and isinstance(value, str):
            return date.fromisoformat(value)
        if name in DATE_MACROS and isinstance(value, str):
            return end_of(value, today)
        if isinstance(value, dict) and (end := _range_end(value, today)):
            return end
    return None


def stamped(
    check_in: str,
    narrow_by: str = DEFAULT_NARROW_BY,
    closing_date: ClosingDate | None = None,
    dates: dict[str, Any] | None = None,
    live_balances: bool = False,
) -> Callable[[Tool], Tool]:
    """Add `stamp` to the tool's result. `check_in` is where to check it in QuickBooks; `narrow_by` the
    capped notice's hint; `closing_date` reads the Company File's closing date (None: unknown); `dates`
    the stamp's dates for a tool without `from_date`/`to_date` (e.g. `AS_OF_TODAY` for balances);
    `live_balances` for a tool whose figures are current balances (as of today, whatever its dates)."""
    live = live_balances or dates == AS_OF_TODAY

    def decorate(tool: Tool) -> Tool:
        @wraps(tool)
        async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            out = await tool(*args, **kwargs)
            answer = out if isinstance(out, Answer) else Answer(out)
            result = dict(answer.result)
            arguments = _arguments(tool, args, kwargs)
            stamp = TrustStamp(
                filters=_applied_filters(arguments),
                voided_listed=_listed(result, "is_void", True),
                non_posting_listed=_listed(result, "posting", False),
                dates=_dates(arguments, dates, live_balances),
                check_in_quickbooks=check_in,
            )
            if result.get("more_available"):
                shown = result.get("count", len(result.get("results", [])))
                notice = cap_notice(shown, narrow_by, arguments.get("limit"))
                stamp = replace(stamp, complete=False, cap_notice=notice)
                result.pop("totals", None)
            if "totals" in result:
                stamp = replace(stamp, voided_excluded=stamp.voided_listed)
            stamp = replace(stamp, **answer.stamp)
            closed_through = await closing_date() if closing_date else None
            if closed_through == NO_CLOSING_DATE:
                stamp = replace(stamp, period=NOT_SET)
            elif isinstance(closed_through, date):
                stamp = replace(
                    stamp, closing_date=closed_through.isoformat(), period=_period(stamp.filters, closed_through, live)
                )
            return result | {"stamp": asdict(stamp)}

        # MCP builds the output schema from the return annotation; a tool returning `Answer` returns a dict here.
        wrapper.__signature__ = inspect.signature(tool).replace(return_annotation=dict[str, Any])  # type: ignore[attr-defined]
        return wrapper

    return decorate
