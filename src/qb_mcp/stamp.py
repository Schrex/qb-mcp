"""The trust stamp: what every tool result is based on, so Jeri (or her accountant) can check it.

`stamped` wraps every tool (see `build_server`'s `tool`), so no tool can omit the stamp. It fills
what it can work out centrally:

- `filters`: the tool's filter arguments as applied (defaults included; `None` and "all" left out,
  as they filter nothing; `NOT_FILTERS` are output options, not filters).
- `complete` / `cap_notice`: from the result's `more_available` (see `output.capped`). A capped result
  gets a plain-words notice and loses any `totals`: no total is ever returned over a capped set.

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

# Arguments that shape the output rather than choose which records are in it.
NOT_FILTERS = frozenset({"limit", "include_lines", "report", "query"})
DEFAULT_NARROW_BY = "date, Vendor or Customer:Job"


@dataclass(frozen=True)
class TrustStamp:
    filters: dict[str, Any] = field(default_factory=dict)
    complete: bool = True
    cap_notice: str | None = None


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


def stamped(narrow_by: str = DEFAULT_NARROW_BY) -> Callable[[Tool], Tool]:
    """Add `stamp` to the tool's result. `narrow_by` is the capped notice's hint."""

    def decorate(tool: Tool) -> Tool:
        @wraps(tool)
        async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            out = await tool(*args, **kwargs)
            answer = out if isinstance(out, Answer) else Answer(out)
            result = dict(answer.result)
            arguments = _arguments(tool, args, kwargs)
            stamp = TrustStamp(filters=_applied_filters(arguments))
            if result.get("more_available"):
                shown = arguments.get("limit", result.get("count"))
                stamp = replace(stamp, complete=False, cap_notice=cap_notice(shown, narrow_by))
                result.pop("totals", None)
            stamp = replace(stamp, **answer.stamp)
            return result | {"stamp": asdict(stamp)}

        # MCP builds the output schema from the return annotation; a tool returning `Answer` returns a dict here.
        wrapper.__signature__ = inspect.signature(tool).replace(return_annotation=dict[str, Any])  # type: ignore[attr-defined]
        return wrapper

    return decorate
