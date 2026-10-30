"""The trust stamp: what every tool result is based on, so Jeri (or her accountant) can check it.

`stamped` wraps every tool (see `build_server`'s `tool`), so no tool can omit the stamp. It fills
what it can work out centrally:

- `filters`: the tool's filter arguments as applied (defaults included; `None` and "all" left out,
  as they filter nothing; `NOT_FILTERS` are output options, not filters).
- `complete` / `cap_notice`: from the result's `more_available` (see `output.capped`). A capped result
  gets a plain-words notice and loses any `totals`: no total is ever returned over a capped set.
- `closing_date` / `period`: the Company File's closing date (from `closing_date`, read once per
  session by `build_server`) and whether the answer's date range is closed or may still change.
- `voided_excluded`: how many listed records are voided (`is_void`); no total includes them (#28).

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

Tool = Callable[..., Awaitable[Any]]
ClosingDate = Callable[[], Awaitable[date | None]]

# Arguments that shape the output rather than choose which records are in it.
NOT_FILTERS = frozenset({"limit", "include_lines", "report", "query"})
DEFAULT_NARROW_BY = "date, Vendor or Customer:Job"
RANGE_END = frozenset({"to_date", "ToTxnDate", "ToReportDate"})


@dataclass(frozen=True)
class TrustStamp:
    filters: dict[str, Any] = field(default_factory=dict)
    complete: bool = True
    cap_notice: str | None = None
    closing_date: str | None = None  # ISO date; None if QuickBooks returned none
    period: str = "closing date unknown"  # or "closed" / "may still change"
    voided_excluded: int = 0


@dataclass
class Answer:
    """A tool result plus stamp facts only the tool knows (keys are `TrustStamp` fields)."""

    result: dict[str, Any]
    stamp: dict[str, Any] = field(default_factory=dict)


def cap_notice(shown: int, narrow_by: str) -> str:
    return f"Showing the first {shown}; more exist; totals incomplete. Narrow by {narrow_by}."


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


def _voided(result: dict[str, Any]) -> int:
    return sum(1 for r in result.get("results", []) if isinstance(r, dict) and r.get("is_void") is True)


def _period(filters: dict[str, Any], closed_through: date) -> str:
    """"closed" if the answer's date range ends on or before the closing date, else "may still change"
    (no end date: it runs to today)."""
    end = _range_end(filters)
    return "closed" if end and date.fromisoformat(end) <= closed_through else "may still change"


def _range_end(filters: dict[str, Any]) -> str | None:
    """The date range's end among the filters: a tool's `to_date`, or a raw query's ToTxnDate/ToReportDate."""
    for name, value in filters.items():
        if name in RANGE_END and isinstance(value, str):
            return value
        if isinstance(value, dict) and (end := _range_end(value)):
            return end
    return None


def stamped(narrow_by: str = DEFAULT_NARROW_BY, closing_date: ClosingDate | None = None) -> Callable[[Tool], Tool]:
    """Add `stamp` to the tool's result. `narrow_by` is the capped notice's hint; `closing_date`
    reads the Company File's closing date (None: unknown)."""

    def decorate(tool: Tool) -> Tool:
        @wraps(tool)
        async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            out = await tool(*args, **kwargs)
            answer = out if isinstance(out, Answer) else Answer(out)
            result = dict(answer.result)
            arguments = _arguments(tool, args, kwargs)
            stamp = TrustStamp(filters=_applied_filters(arguments), voided_excluded=_voided(result))
            if result.get("more_available"):
                shown = arguments.get("limit", result.get("count"))
                stamp = replace(stamp, complete=False, cap_notice=cap_notice(shown, narrow_by))
                result.pop("totals", None)
            stamp = replace(stamp, **answer.stamp)
            closed_through = await closing_date() if closing_date else None
            if closed_through:
                stamp = replace(
                    stamp, closing_date=closed_through.isoformat(), period=_period(stamp.filters, closed_through)
                )
            return result | {"stamp": asdict(stamp)}

        # MCP builds the output schema from the return annotation; a tool returning `Answer` returns a dict here.
        wrapper.__signature__ = inspect.signature(tool).replace(return_annotation=dict[str, Any])  # type: ignore[attr-defined]
        return wrapper

    return decorate
