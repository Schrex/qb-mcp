# Handoff: back to the Mac after #4 (2026-10-29)

Temporary note for the next session on the Mac. Delete it once read and acted on.

## State

- Ticket #4 is **closed**; the full results are in its last comment (smoke output, the five verified HRESULTs, the view-only answer with evidence both ways, fixture recording, the 16.0 XSD, review follow-ups). Branch `spec-1-read-only-mcp` is pushed at `3b9d0ad`; PR #11 is still the open v1 PR for it. The Windows handoff note is gone.
- On this branch since the Mac last saw it (all `#4` in the subject): `.python-version` pin (3.12), HRESULT hint wording and verified-codes comment in `transports/com.py`, `run_job_report` guard (detail reports need `customer_job`), recorder recognises `sample_*` files, base fixtures replaced with Rock Castle recordings (hand-written edge cases kept as named variants), tests rewritten to exact real values, ADR-0001 and README updated for "no separate QuickBooks user", runbook corrected throughout.
- The spare Windows PC keeps QuickBooks Enterprise 2024 trial (expires ~2026-11-27), Desktop SDK 17.0, and the ignored `xsd/` folder. Rock Castle is clean (test Vendors deleted, unattended access off).

## First steps on the Mac

1. `git pull` on `spec-1-read-only-mcp` before touching anything.
2. Get the 16.0 XSD set onto the Mac: the four files (`qbxmlops160.xsd`, `qbxml160.xsd`, `qbxmltypes160.xsd`, `qbxmlso160.xsd`) are zipped on the Windows PC at `C:\Users\Sumanth\Documents\qbxml-xsd-16.0.zip` (50 KB). Copy it over by hand (AirDrop/USB/drive), unzip to `~/qbxml-xsd/16.0/`, then `QBXML_XSD_DIR=~/qbxml-xsd/16.0 uv run pytest` → expect **210 passed, 0 skipped**. Never commit them (`xsd/`, `*.xsd` are ignored).
3. `uv run pytest` without the XSD still passes with 2 skips; that's expected.

## Follow-ups raised by the #4 review (not ticketed yet)

- `run_query` request-type allow-list: with Admin in the QuickBooks window nothing narrows reads except field stripping; Employee/Payroll/credit-card queries are reachable.
- #10: `uvx` ignores `.python-version`; README config now passes `--python 3.12`; also cap `requires-python` (`>=3.11,<3.15`) in `pyproject.toml`.
- `query_bills` paid filter: Rock Castle has `IsPaid=true` with `OpenAmount > 0`; check on the live file at the install visit, consider deriving `is_paid` from `OpenAmount`.
- `list_items` sales-only items report price as `cost` and the income account as `expense_account`; add a side indicator or resolve the account type.

## Rules still in force

Read-only (`*QueryRq` only); never commit Intuit XSD; `*.sensitive.xml` are hand-written; don't weaken assertions to fit data; `APP_NAME` stays "Schreiber QuickBooks Lookup"; test-first through the tool seam; small commits.
