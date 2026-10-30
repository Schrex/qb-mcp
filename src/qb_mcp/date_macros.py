"""The last date QuickBooks' date macros (qbXML `DateMacro`, `ReportDateMacro`) cover, relative to today.

Calendar macros resolve to a date. Fiscal macros (`*Fiscal*`) depend on the Company File's fiscal year
start, which isn't read, so they resolve to `FISCAL` rather than a guess. `All` has no end (None).
Weeks run Sunday to Saturday (QuickBooks' US default). `ReportDateMacro`'s ThisQuarter, LastYear, ...
are taken as calendar quarters and years, like `DateMacro`'s ThisCalendarQuarter, LastCalendarYear, ...
"""

from datetime import date, timedelta

FISCAL = "fiscal"


def _month_end(today: date, months_ahead: int) -> date:
    """Last day of the month `months_ahead` months from today's."""
    year, month = divmod(today.year * 12 + today.month - 1 + months_ahead + 1, 12)
    return date(year, month + 1, 1) - timedelta(days=1)


def _same_day_months_back(today: date, months: int) -> date:
    """Today's day of the month, `months` months earlier (clamped to that month's last day)."""
    month_end = _month_end(today, -months)
    return month_end.replace(day=min(today.day, month_end.day))


def _quarter_end(today: date, quarters_ahead: int) -> date:
    months_to_quarter_end = 2 - (today.month - 1) % 3
    return _month_end(today, months_to_quarter_end + 3 * quarters_ahead)


def _week_end(today: date, weeks_ahead: int) -> date:
    """Saturday of the week (Sunday to Saturday) `weeks_ahead` weeks from today's."""
    return today + timedelta(days=(5 - today.weekday()) % 7 + 7 * weeks_ahead)


ENDS = {
    "Yesterday": lambda t: t - timedelta(days=1),
    "ThisWeek": lambda t: _week_end(t, 0),
    "ThisMonth": lambda t: _month_end(t, 0),
    "ThisQuarter": lambda t: _quarter_end(t, 0),
    "ThisYear": lambda t: date(t.year, 12, 31),
    "LastWeek": lambda t: _week_end(t, -1),
    "LastWeekToDate": lambda t: t - timedelta(days=7),
    "LastMonth": lambda t: _month_end(t, -1),
    "LastMonthToDate": lambda t: _same_day_months_back(t, 1),
    "LastQuarter": lambda t: _quarter_end(t, -1),
    "LastQuarterToDate": lambda t: _same_day_months_back(t, 3),
    "LastYear": lambda t: date(t.year - 1, 12, 31),
    "LastYearToDate": lambda t: _same_day_months_back(t, 12),
    "NextWeek": lambda t: _week_end(t, 1),
    "NextFourWeeks": lambda t: _week_end(t, 4),
    "NextMonth": lambda t: _month_end(t, 1),
    "NextQuarter": lambda t: _quarter_end(t, 1),
    "NextYear": lambda t: date(t.year + 1, 12, 31),
}


def end_of(macro: str, today: date) -> date | str | None:
    """The last date `macro` covers; `FISCAL` for a fiscal macro (or one not known here: no guess);
    None for `All` (no end)."""
    name = macro.replace("Calendar", "")
    if name == "All":
        return None
    if name == "Today" or (name.startswith("This") and name.endswith("ToDate")):
        return today
    end = ENDS.get(name)
    return end(today) if end else FISCAL
