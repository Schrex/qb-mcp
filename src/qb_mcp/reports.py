"""Turns a parsed `ReportRet` into titled columns and labelled, kind-marked rows.

Generic over ColDesc / TextRow / DataRow / SubtotalRow / TotalRow: column
titles are read from the report (never hard-coded), Label columns become the
row's `label`, every other column a `values` entry keyed by its title. Rows
are sparse, so empty cells are omitted.
"""

import re
from typing import Any

ROW_KINDS = {"TextRow": "header", "DataRow": "data", "SubtotalRow": "subtotal", "TotalRow": "total"}

# Report cells carry values in attributes (`<ColData value=...>`), which element-name
# stripping can't see, so sensitive report columns are recognised by their title.
SENSITIVE_COLUMN_TITLE = re.compile(
    r"tax\s*id|\bssn\b|soc(ial)?\.?\s*sec|\bein\b|fed(eral)?\.?\s*id"
    r"|\b(account|acct)\.?\s*(no\b|num|#)|card|bank|routing",
    re.IGNORECASE,
)


def _title(desc: dict[str, Any]) -> str:
    """A ColDesc's title, multi-row titles joined in row order ("" if untitled)."""
    parts = sorted(desc.get("ColTitle", []), key=lambda t: int(t.get("titleRow", 0)))
    return " ".join(t["value"] for t in parts if t.get("value"))


def drop_sensitive_columns(report: dict[str, Any]) -> dict[str, Any]:
    """`report` (a parsed `ReportRet`) without columns titled like tax IDs, account,
    card or bank numbers: their ColDesc and every row's ColData for them are removed."""
    col_descs = report.get("ColDesc", [])
    dropped = {d["colID"] for d in col_descs if SENSITIVE_COLUMN_TITLE.search(_title(d))}
    if not dropped:
        return report

    def without_dropped(row: dict[str, Any]) -> dict[str, Any]:
        if "ColData" not in row:
            return row
        return row | {"ColData": [c for c in row["ColData"] if c["colID"] not in dropped]}

    # Every row kind is REPEATED, so ReportData maps row tag -> list of rows.
    data = report.get("ReportData") or {}
    return report | {
        "ColDesc": [d for d in col_descs if d["colID"] not in dropped],
        "ReportData": {tag: [without_dropped(row) for row in rows] for tag, rows in data.items()},
    }


def _column_titles(col_descs: list[dict[str, Any]]) -> dict[str, str]:
    """colID -> unique title for each non-Label column (multi-row titles joined)."""
    titles: dict[str, str] = {}
    seen: dict[str, int] = {}
    for desc in col_descs:
        if desc.get("ColType") == "Label":
            continue
        title = _title(desc) or desc.get("ColType") or f"Column {desc['colID']}"
        seen[title] = seen.get(title, 0) + 1
        if seen[title] > 1:  # e.g. "($) Diff." for cost and again for revenue
            title = f"{title} ({seen[title]})"
        titles[desc["colID"]] = title
    return titles


def parse_report(report: dict[str, Any] | None) -> dict[str, Any]:
    if not report:
        return {"title": None, "subtitle": None, "basis": None, "columns": [], "rows": []}
    col_descs = report.get("ColDesc", [])
    titles = _column_titles(col_descs)
    label_cols = {d["colID"] for d in col_descs if d.get("ColType") == "Label"}

    tagged = [
        (int(row["rowNumber"]), tag, row)
        for tag in ROW_KINDS
        for row in (report.get("ReportData") or {}).get(tag, [])
    ]
    rows = []
    for _, tag, row in sorted(tagged, key=lambda r: r[0]):
        out: dict[str, Any] = {"kind": ROW_KINDS[tag]}
        if tag == "TextRow":
            out["label"] = row.get("value", "")
            rows.append(out)
            continue
        cells = {c["colID"]: c.get("value", "") for c in row.get("ColData", [])}
        out["label"] = " ".join(cells[c] for c in cells if c in label_cols and cells[c])
        if row.get("RowData", {}).get("value"):
            out["name"] = row["RowData"]["value"]
        out["values"] = {titles[c]: v for c, v in cells.items() if c in titles and v != ""}
        rows.append(out)

    return {
        "title": report.get("ReportTitle"),
        "subtitle": report.get("ReportSubtitle"),
        "basis": report.get("ReportBasis"),
        "columns": list(titles.values()),
        "rows": rows,
    }
