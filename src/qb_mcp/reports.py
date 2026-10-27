"""Turns a parsed `ReportRet` into titled columns and labelled, kind-marked rows.

Generic over ColDesc / TextRow / DataRow / SubtotalRow / TotalRow: column
titles are read from the report (never hard-coded), Label columns become the
row's `label`, every other column a `values` entry keyed by its title. Rows
are sparse, so empty cells are omitted.
"""

from typing import Any

ROW_KINDS = {"TextRow": "header", "DataRow": "data", "SubtotalRow": "subtotal", "TotalRow": "total"}


def _column_titles(col_descs: list[dict[str, Any]]) -> dict[str, str]:
    """colID -> unique title for each non-Label column (multi-row titles joined)."""
    titles: dict[str, str] = {}
    seen: dict[str, int] = {}
    for desc in col_descs:
        if desc.get("ColType") == "Label":
            continue
        parts = sorted(desc.get("ColTitle", []), key=lambda t: int(t.get("titleRow", 0)))
        title = " ".join(t["value"] for t in parts if t.get("value")) or desc.get("ColType") or f"Column {desc['colID']}"
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
