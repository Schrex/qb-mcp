# Handoff: ticket #4 on the spare Windows PC

For a Claude Code session running on the Windows test PC. Delete this file once #4 is closed.

## Read first

1. `README.md`: what the server is, the tools, install, troubleshooting.
2. `docs/vm-runbook.md`: the procedure for this ticket. Section 1 ("Machine") applies; the cloud VM notes don't.
3. `GLOSSARY.md` (use its terms) and `docs/adr/0001-sdk-for-reads-ui-for-writes.md`.
4. GitHub: spec `Schrex/qb-mcp#1`, this ticket `#4` (read its comments), PR `#11`.

## Where things stand (2026-10-28)

- All the agent tickets (#2, #3, #5–#9) are done on branch `spec-1-read-only-mcp` (PR #11, under review, not merged). 190 tests pass on macOS against a qbXML **13.0** XSD; none of it has run against real QuickBooks yet.
- The prep for #4 is on the same branch: `scripts/smoke_test.py`, `scripts/record_fixtures.py` and the runbook.
- **Machine:** a spare office Windows PC with no production QuickBooks. QuickBooks Enterprise 2024 (Contractor) trial and the Rock Castle Construction sample file. Never Jeri's PC, never Schreiber's live Company File or its network share.
- Work on branch `spec-1-read-only-mcp`. Pull before starting; the Mac session may push too.

## Goal of #4

1. Prove the COM path: `uv run python scripts/smoke_test.py` passes.
2. Pin the real error codes (runbook 7.5): close the Company File, then QuickBooks, running the smoke test each time. Record message and `code 0x...` for each, and fix the HRESULT mapping in `src/qb_mcp/transports/com.py` to match.
3. Answer the view-only-user question (runbook section 8). If the view-only user isn't enforced while QuickBooks is open, update ADR-0001 and the README caveat.
4. Validate requests against the real **16.0** XSD: set `QBXML_XSD_DIR` to a local folder holding the full 16.0 set, then run `uv run pytest`.
5. Record fixtures (runbook section 9): `--dry-run` first. It's expected that Company and Vendor files may be refused; keep the hand-written ones then.
6. Post the results on #4: smoke output, `qb_status` output, error codes, Python bitness, the view-only answer, XSD file names and location.

## Rules

- **Read-only.** The server must never send anything but `*QueryRq`. The one write allowed is the throwaway VendorAdd test in runbook 8.5, against the sample file only, run outside the server. Don't add that script to the repo.
- **Never commit Intuit XSD files** (`xsd/` and `*.xsd` are git-ignored).
- **Fixtures:** never record from a non-sample file and never use `--i-confirm-not-live-data`. `*.sensitive.xml` files are hand-written; don't touch them. Don't weaken test assertions to fit recorded data.
- **Same Windows user, same elevation** for QuickBooks and Python. Don't use "Run as administrator".
- Don't rename `APP_NAME` ("Schreiber QuickBooks Lookup"). Renaming it forces QuickBooks to ask for authorization again.
- Code changes are test-first through the tool seam (see spec #1 "Testing Decisions"); commit small, push to the branch.

## Known unknowns to confirm or correct

- 64-bit Python and QuickBooks' request processor (the runbook has a 32-bit fallback).
- The XSD folder and file names. The runbook's guess is `C:\Program Files (x86)\Intuit\IDN\`, `qbxmlops160.xsd` and friends.
- The sample file path and the trial's availability and length.
- Job report column titles and what an empty report looks like (`run_job_report`).
- The 120-second COM timeout behaviour.

Correct `docs/vm-runbook.md` wherever reality differs; it's marked **(unverified)** in those places.
