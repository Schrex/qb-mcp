# qb-mcp

A local MCP server that lets Claude Desktop (Chat) do **Lookups** against the QuickBooks Desktop **Company File** open on Jeri's Windows PC, through the QuickBooks Desktop SDK (qbXML 16.0 over COM, QBXMLRP2). Target: QuickBooks Desktop Enterprise Contractor 2024.

**Read-only.** Every request is checked before it reaches QuickBooks; anything that isn't a `*QueryRq` is refused. Writes and Attachments stay with the stone-matching plugin's on-screen control ([ADR-0001](docs/adr/0001-sdk-for-reads-ui-for-writes.md)). Sensitive fields (tax IDs, bank account and card numbers) are removed from every result. The server never touches the Company File on disk and has no network listener (stdio only).

Terms (Company File, Vendor, Customer:Job, Item, Bill, Attachment, Lookup) follow [GLOSSARY.md](GLOSSARY.md).

**For Jeri:** [docs/jeri-support-sheet.md](docs/jeri-support-sheet.md) is a one-page, printable sheet: starter prompts, what Claude can't do, how to read the trust stamp, who to call. Every starter prompt is checked by `tests/test_support_sheet.py`.

## Tools

| Tool | What it does |
|---|---|
| `qb_status` | Connection check: reachable?, QuickBooks product and version, supported qbXML versions, company name, Company File path, this server's version (`server_version`), last error (and when). |
| `get_company_info` | Company name, legal name, address, fiscal year start month. |
| `list_vendors` | Vendors with active flag, balance, contact basics. Filters: `name_contains`, `status`, `limit`. |
| `list_customer_jobs` | Customers and their Customer:Jobs with parent, job status, balance, total balance. Same filters. |
| `list_items` | Items of every kind with full name, kind, purchase description, cost, expense account. Same filters. |
| `list_accounts` | Chart-of-accounts Accounts with type, number, balance, total balance. Filters: `account_type`, `name_contains`, `status`, `limit`. |
| `query_bills` | Bills by date range, Vendor, ref number, paid status, Customer:Job; each with its A/P account and open balance, and item and expense lines (default on). |
| `run_job_report` | Job profitability or job estimates-vs-actuals (summary or detail) for a date range, optionally one Customer:Job. |
| `run_query` | One raw qbXML `*QueryRq` element for anything the other tools don't cover (invoices, checks, purchase orders...). Invoices, payments received and credit memos also name their A/R account (`ar_account`). |

List and query tools return at most `limit` (default 100) results and set `more_available` (plus a `note`) when more exist.

Every result carries a trust `stamp`: the `filters` applied and whether it is `complete`. A capped result says so in `cap_notice` ("showing the first N; more exist; totals incomplete", and how to narrow) and never carries a total.

Voided transactions stay listed, marked `is_void`, but are left out of every total (e.g. `query_bills` `totals`); the stamp's `voided_excluded` counts them. qbXML 16.0 has no void flag, so a transaction counts as voided when QuickBooks has zeroed its amount(s) and prefixed its memo `VOID:` (both must hold). Transactions carry `posting`: Estimates, Purchase Orders and Sales Orders are `posting: false` (non-posting) and never summed with posting transactions.

## Install on Jeri's PC

One visit. Needs: Jeri's Windows login, a QuickBooks **Admin** login for the Company File, Claude Desktop already installed and signed in.

### 1. Install uv and git

Open **PowerShell** as Jeri (normal, *not* "Run as administrator"):

```powershell
winget install --id=astral-sh.uv -e
winget install --id=Git.Git -e
```

No winget? uv: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`; git: installer from <https://git-scm.com/download/win> (defaults are fine).

Close PowerShell, open a new one, and check both are on PATH:

```powershell
uv --version
git --version
where.exe uvx
```

Note the full path `where.exe uvx` prints (usually `C:\Users\<Jeri>\.local\bin\uvx.exe`); you need it if Claude can't find `uvx` (see Troubleshooting).

**64-bit:** QuickBooks 2022 and later (incl. Enterprise 2024) is 64-bit, and uv installs 64-bit Python on a 64-bit Windows, which is what the 64-bit SDK request processor needs. Don't install a 32-bit Python for this. (Confirmed on the #4 test PC: 64-bit Python 3.12 talks to Enterprise 2024.)

Optional pre-warm, so Claude's first launch isn't slow:

```powershell
uvx --refresh --from git+https://github.com/Schrex/qb-mcp@v0.1.0 qb-mcp
```

Once downloaded it sits silently waiting for MCP input; press Ctrl+C.

### 2. No separate QuickBooks user

Nothing to create. While QuickBooks and the Company File are open, the SDK runs every request as **whoever is logged into the QuickBooks window** (Jeri), and QuickBooks offers the integration its own "Sign in as" user only for unattended access (QuickBooks not running), which we don't use. Verified on Enterprise 2024 in [#4](https://github.com/Schrex/qb-mcp/issues/4): a view-only user would enforce nothing here. **Read-only is enforced by the server's query-only guard** ([ADR-0001](docs/adr/0001-sdk-for-reads-ui-for-writes.md)); step 5 checks it.

(If unattended access is ever wanted, note that Enterprise 24.0's built-in **View-Only** role can't be given to a local user, it needs an Intuit account; [docs/vm-runbook.md](docs/vm-runbook.md) section 8 has a trimmed custom role that works.)

### 3. Add the server to Claude Desktop

Claude Desktop > **Settings > Developer > Edit Config** opens `%APPDATA%\Claude\claude_desktop_config.json` (create it if missing). Add the `qb` entry under `mcpServers` (merge with any existing entries, don't replace them):

```json
{
  "mcpServers": {
    "qb": {
      "command": "uvx",
      "args": ["--refresh", "--python", "3.12", "--from", "git+https://github.com/Schrex/qb-mcp@v0.1.0", "qb-mcp"]
    }
  }
}
```

Save, then **fully quit** Claude Desktop (system tray icon > Quit, not just closing the window) and reopen it.

**Microsoft Store build (verified on the test PC, #4):** the file is elsewhere and the order matters. The app keeps its settings and the `mcpServers` list in one file under `%LOCALAPPDATA%\Claude-Data\claude_<id>\claude_desktop_config.json` (the `%APPDATA%\Claude` folder stays empty and is not read), and it writes that file back from memory when it quits, so an edit made while it is running is lost. Quit Claude Desktop first, then add the `mcpServers` key (the same `qb` entry as above, pinned to `git+https://github.com/Schrex/qb-mcp@v0.1.0`) next to the existing `preferences` key in that file, then reopen. **Settings > Developer > Edit config** opens the right folder either way.

### 4. First connect: authorize the integration

1. Open QuickBooks and the Company File, logged in as **Admin**. (First authorization is usually only allowed for an Admin; if QuickBooks says it needs single-user mode, switch with **File > Switch to Single-user Mode**, then back after.)
2. In Claude Desktop Chat ask: **"run qb status"**.
3. QuickBooks shows an Application Certificate prompt for **Schreiber QuickBooks Lookup**. Choose:
   - **Yes, whenever this QuickBooks company file is open.**
   - Leave **"Allow this application to access personal data such as Social Security Numbers and customer credit card information"** *unchecked*.
   - Continue / Done.
   Not "Yes, always" (that's unattended access when QuickBooks is closed; out of scope).
4. Claude may report a timeout while the prompt was up (the server waits 120 s), or "The QuickBooks user has denied access" for the request that raised the prompt. Ask "run qb status" again.
5. Then check what QuickBooks recorded: **Edit > Preferences > Integrated Applications > Company Preferences**, select **Schreiber QuickBooks Lookup**, **Properties**. The personal-data box must be **unchecked** (on the test PC it came out checked after the prompt); "even when QuickBooks isn't running" must be unchecked. OK.

### 5. Verify

- **"run qb status"** shows `reachable: true`, product (QuickBooks Enterprise Solutions...), version, supported qbXML versions incl. `16.0`, Jeri's company name and Company File path, and `server_version` matching the tag in the config (`0.1.0`).
- Refusal check: ask Claude to use `run_query` with `<VendorAddRq><VendorAdd><Name>Test</Name></VendorAdd></VendorAddRq>`. It must be refused ("Refused: this server is read-only...").
- Jeri asks one real budgeting question; check the answer against QuickBooks.
- Show Jeri how to send the log file and `qb_status` output (see Troubleshooting).
- Then do the live-file tie-out (step 6) before leaving.

### 6. Live-file tie-out

With Jeri, on her live Company File: check the server's answers against QuickBooks' own reports, and check that every "check in QuickBooks" path the server gives exists. About an hour. Everything happens on Jeri's screen; nothing from her file is copied off the PC.

**Rule: no live data on GitHub.** No Vendor, customer, Customer:Job or Item names, no amounts, no ref numbers, no screenshots from the live file, in issues, comments, commits or PRs. GitHub gets the pass/fail table below (counts only) and, on a mismatch, the pattern in words. If something needs a closer look, write it on paper or in a file that stays on Jeri's PC.

**Before you start**

- `run qb status` works (step 5). Jeri logged into QuickBooks as herself.
- Pick a **closed month**: one on or before the Company File's closing date. In QuickBooks: **Company > Set Closing Date** (or **Edit > Preferences > Accounting > Company Preferences**, Closing Date). If no closing date is set, pick a month Jeri says is finished and note "no closing date".
- Pick **2–3 jobs** Jeri knows well, ideally one in progress and one finished, and their date range (e.g. the job's start to today, or the year to date).
- For every answer, ask Claude to **"show the full trust stamp"** if it doesn't show it.

**What to compare in each trust stamp**

| Stamp field | Must match / check |
|---|---|
| `basis` | Same basis as the QuickBooks report (shown under the report title; **Customize Report > Display > Report Basis**). For job reports the server can't choose it: if QuickBooks' report shows the other basis, switch QuickBooks' report to the stamp's basis before comparing, and note it. |
| `dates` | `from` / `to` / `field` are the dates you set in QuickBooks' report (Dates / From / To). A/P aging: as of today. |
| `closing_date` / `period` | `closing_date` is QuickBooks' closing date (above); `period` is `closed` for the closed month's P&L. `closing date unknown` while QuickBooks has one set is a fail. |
| `filters` | Only the filters you asked for (job, Vendor, `paid_status`...), nothing extra or missing. |
| `voided_excluded` | Plausible: voided Bills show in QuickBooks with a 0 amount and memo `VOID:`. Note the count. |
| `complete` / `cap_notice` | `complete: true` before comparing any total. If `cap_notice` is shown there are no totals: narrow (date range, Vendor) or ask with a bigger `limit`, then ask again. |
| `check_in_quickbooks` | Opens the report you are comparing against (check A). |

**Check A: "check in QuickBooks" menu paths exist.** These were written from memory, not from Enterprise 2024 (source: `src/qb_mcp/check_in.py`). In Jeri's QuickBooks, open each path below and mark it exists / wrong (and what the right path is; menu paths are not live data, they can go on GitHub):

| Tool / query | Path to open |
|---|---|
| `qb_status` | Help > Product Information (F2) |
| `get_company_info` | Company > My Company |
| `list_vendors` | Vendors > Vendor Center; Reports > Vendors & Payables > Vendor Balance Summary |
| `query_bills` | Reports > Vendors & Payables > Unpaid Bills Detail; A/P Aging Detail; Vendors > Enter Bills |
| `list_customer_jobs` | Customers > Customer Center; Reports > Customers & Receivables > Customer Balance Summary |
| `list_items` | Lists > Item List |
| `list_accounts` | Lists > Chart of Accounts |
| `run_job_report` | Reports > Jobs, Time & Mileage > Job Profitability Summary / Detail; Job Estimates vs. Actuals Summary / Detail |
| `run_query` (reports) | Reports > Company & Financial > Profit & Loss Standard / Detail / by Class, Balance Sheet Standard / Summary; Reports > Jobs, Time & Mileage > Profit & Loss by Job; Reports > Vendors & Payables > A/P Aging Summary / Detail, Vendor Balance Summary; Reports > Customers & Receivables > A/R Aging Summary / Detail, Customer Balance Summary; Reports > Accountant & Taxes > Trial Balance, Journal; Reports > Budgets & Forecasts > Budget vs. Actual, Budget Overview |
| `run_query` (lists, transactions) | Reports > Customers & Receivables > Open Invoices, Transaction List by Customer; Reports > Sales > Sales by Customer Detail; Reports > Jobs, Time & Mileage > Estimates by Job, Time by Job Detail; Reports > Purchases > Open Purchase Orders, Purchases by Vendor Detail; Reports > Vendors & Payables > Transaction List by Vendor; Reports > Banking > Check Detail, Deposit Detail; Reports > Inventory > Inventory Valuation Summary; Lists > Class List; Customers > Create Invoices / Enter Sales Receipts / Create Credit Memos/Refunds / Receive Payments; Banking > Enter Credit Card Charges |

**Check B: Job Profitability Summary (2–3 jobs).** For each job, Jeri types:

> Run the job profitability summary for *Customer:Job* from *start* to *end*, and show the trust stamp.

(Claude calls `run_job_report` with `report: "job_profitability_summary"`, `customer_job`, `from_date`, `to_date`.) In QuickBooks: **Reports > Jobs, Time & Mileage > Job Profitability Summary**, set the same Dates, then **Customize Report > Filters > Name** = that job (or open the full report and find the job's row). Compare Act. Cost, Act. Revenue and ($) Diff. for the job and its TOTAL row. Pass = every figure equal to the cent and the stamp checks above hold.

**Check C: A/P Aging Summary (ties to [#19](https://github.com/Schrex/qb-mcp/issues/19)).** Jeri types:

> Use run_query to run `<AgingReportQueryRq><AgingReportType>APAgingSummary</AgingReportType><ReportDateMacro>Today</ReportDateMacro></AgingReportQueryRq>` and show the trust stamp.

In QuickBooks: **Reports > Vendors & Payables > A/P Aging Summary**, as of today. Compare the TOTAL row's columns (Current, 1–30, 31–60, 61–90, > 90, TOTAL) and count the Vendor rows on each side. Then the per-Bill figure the server works out itself (#14, #19):

> Find unpaid Bills with query_bills (`paid_status: "unpaid"`), with totals and the trust stamp.

Its `totals.open_balance` (only if `complete: true`; else narrow by date range and add up) must equal the A/P Aging Summary TOTAL. Then pick **3–5 Bills with an early-pay discount, an applied Vendor credit, or a partial payment** (Jeri knows which), ask:

> Show Bill *ref number* from *Vendor* with query_bills, with its open balance.

and compare each `open_balance` with **Reports > Vendors & Payables > A/P Aging Detail > Open Balance** for that Bill. Pass = all equal to the cent. Record the counts in #19 (Bills checked, with discount / credit / partial payment, mismatches), no names or amounts.

**Check D: one closed month's Profit & Loss.** Jeri types (dates = the closed month):

> Use run_query to run `<GeneralSummaryReportQueryRq><GeneralSummaryReportType>ProfitAndLossStandard</GeneralSummaryReportType><ReportPeriod><FromReportDate>2026-09-01</FromReportDate><ToReportDate>2026-09-30</ToReportDate></ReportPeriod></GeneralSummaryReportQueryRq>` and show the trust stamp.

That runs on accrual basis (stamp `basis: Accrual`). If Jeri's QuickBooks P&L is on cash basis (**Edit > Preferences > Reports & Graphs > Company Preferences > Summary Reports Basis**), add `<ReportBasis>Cash</ReportBasis>` just before `</GeneralSummaryReportQueryRq>` and ask again. In QuickBooks: **Reports > Company & Financial > Profit & Loss Standard**, same From/To, same basis. Compare Total Income, Total COGS, Gross Profit, Total Expense and Net Income, and the stamp's `period: closed`. Pass = all equal to the cent.

**Record the result (this table, and only this, goes on GitHub: comment on [#10](https://github.com/Schrex/qb-mcp/issues/10), and the C rows on [#19](https://github.com/Schrex/qb-mcp/issues/19))**

| Check | Items checked (count) | Matched | Mismatched | Stamp fields wrong (names only, e.g. `basis`) | Pass / fail |
|---|---|---|---|---|---|
| A. Menu paths | paths | | | n/a | |
| B. Job Profitability Summary | jobs | | | | |
| C. A/P Aging Summary (total, Vendor rows) | rows | | | | |
| C. Per-Bill open balance (#19) | Bills (with discount / credit / partial) | | | | |
| D. Closed-month P&L | totals compared | | | | |

Plus: `server_version`, QuickBooks version (from `qb_status`), basis each report ran on, `voided_excluded` counts, whether any answer was capped. A wrong menu path goes in with the right path (no live data in it).

**On a mismatch**

1. **Stop** that check. Don't try other prompts until it "matches", and don't change anything in QuickBooks to make it match.
2. **Note the pattern** on Jeri's PC, not GitHub: which check, which figure (column / total), roughly how far off (cents, one transaction, a whole line), and what the affected transactions have in common (discount, Vendor credit, credit-card or check payment, retainage A/P account, voided, a sub-job, an unusual date). Re-check the stamp first: a different `basis`, date range or filter is a setup mistake, not a bug; fix it and redo the check once.
3. **File a bug without live data**: a new GitHub issue naming the check, the tool and its arguments (with names replaced by placeholders such as *Vendor A*), the stamp fields, and the pattern in words, e.g. "Bills with an applied Vendor credit: open_balance higher than A/P Aging Detail by the credit". Link it from #10 (and #19 for Bill balances). Then go on to the next check.
4. Tell Jeri which kind of answer not to rely on until the bug is fixed.

## Updating

The install is pinned to a release tag (`@v0.1.0`), so merges to `main` never reach Jeri's PC on their own. To update deliberately:

1. In the Claude Desktop config (`%APPDATA%\Claude\claude_desktop_config.json`, or the Store build's file, see step 3), change the tag in `git+https://github.com/Schrex/qb-mcp@v0.1.0` to the new release, e.g. `@v0.2.0`. Save.
2. **Fully quit** Claude Desktop (tray icon > Quit, not just the window) and reopen it. (Store build: quit first, then edit, then reopen.)
3. Ask **"run qb status"**: `server_version` must show the new version.

To roll back, put the previous tag back and restart the same way.

Releasing (maintainer): bump `version` in `pyproject.toml` (that is what `server_version` reports), merge, then tag that commit `vX.Y.Z` and push the tag.

## Troubleshooting

Messages Claude may show, and what to do:

| Message (start) | What to do |
|---|---|
| `QuickBooks isn't running. Open QuickBooks and the Company File...` | Open QuickBooks and the Company File, ask again. If QuickBooks *is* open, the hint "If QuickBooks is open, it must be open as the same Windows user as Claude, and not as administrator" applies: QuickBooks or Claude was started with "Run as administrator" or as another Windows user. Close both, start both normally. |
| `QuickBooks is open but no Company File is loaded...` | Open the Company File. |
| `QuickBooks hasn't authorized this integration (or the QuickBooks user was denied). Ask Sumanth...` | The authorization prompt was answered "No", or was removed. As Admin: **Edit > Preferences > Integrated Applications > Company Preferences**; check **Schreiber QuickBooks Lookup** is listed and allowed (re-check its Properties), or remove it and redo step 4 of the install. Extra hints may mention certificate invalid/expired/revoked, "Not allowed to log in automatically" or "Check Preferences > Integrated Applications". |
| `...QuickBooks has a window or dialog open (possibly an authorization prompt)...` / `QuickBooks didn't answer within 120s` | A QuickBooks window is waiting for an answer (often the authorization prompt, sometimes behind other windows). Answer or close it, ask again. If there is no prompt at all and every request times out, QuickBooks was started "as administrator" (the installer does this when it finishes): close QuickBooks and reopen it from the Start menu. |
| `QuickBooks is busy or still starting up...` | Wait a moment, ask again. |
| `...A different Company File is open in QuickBooks.` | Open the right Company File. |
| `QuickBooks' SDK components are missing or not installed correctly.` | Repair QuickBooks (Control Panel > Programs > QuickBooks > Repair). Tell Sumanth. |
| `QuickBooks' request processor isn't registered on this PC...` | QuickBooks isn't installed on this PC (verified: this is what a PC without QuickBooks reports), or a 32/64-bit mismatch or broken QuickBooks install. Tell Sumanth (send the log). |
| `QuickBooks couldn't read the request: it doesn't match the qbXML schema...` | A malformed `run_query` or a server bug. Ask the question differently; if it came from a built-in tool, tell Sumanth. |
| `Couldn't talk to QuickBooks. (... code 0x...)` | Unrecognised failure. Send Sumanth the log and the code. |
| `QuickBooks returned error <code> for <Request>: <message>` | QuickBooks rejected the query. 3260 "Insufficient permission level" = the QuickBooks user logged into the window lacks access to that area (requests run as that user). Others: send to Sumanth. |
| `QuickBooks sent a response this server didn't expect (...)` | Try again; if repeated, send the log. |
| `Refused: ...` | The read-only guard blocked a non-query request. Expected for any add/modify/delete. |
| `This server only works on Windows...` | Running on a Mac/Linux. Only meaningful on the QuickBooks PC. |
| `qb` tools missing in Claude / "server disconnected" | Claude can't start `uvx`. In the config, replace `"uvx"` with the full path from `where.exe uvx`, e.g. `"C:\\Users\\Jeri\\.local\\bin\\uvx.exe"` (double backslashes in JSON). Check internet access to github.com. Claude's own MCP logs: `%APPDATA%\Claude\logs\mcp-server-qb.log`. |

`qb_status` also reports the server's last error (and time) since Claude was opened.

**Log file:** `%LOCALAPPDATA%\qb-mcp\logs\qb-mcp.log` (paste that into Explorer's address bar). Rolls over at 1 MB, keeps 3 old files (`qb-mcp.log.1`..`.3`). Logs request types and errors only, never QuickBooks data. Set `QB_MCP_LOG_DIR` to log elsewhere.

**Known issue: Bill open balances (#14).** QuickBooks' own `OpenAmount` on a Bill turned out to be one Vendor-wide figure repeated on every Bill, so `query_bills` ignores it and works out each Bill's `open_balance` itself: the Bill amount less the bill payments and Vendor credits linked to it (0 if QuickBooks marks the Bill paid); `paid_status` and `is_paid` follow that figure. Verified to the cent against A/P Aging Detail on the Enterprise 2024 sample (28 unpaid Bills, 0 mismatches, incl. a partly paid Bill); the live file, which may have discounts and credits the sample doesn't, is still to confirm at the install visit ([step 6, check C](#6-live-file-tie-out)). To sanity-check: ask Claude for one Vendor's unpaid Bills and compare each `open_balance` with the Open Balance column of **Reports > Vendors & Payables > A/P Aging Detail** in QuickBooks. If they differ (especially Bills with a credit or discount applied), tell Sumanth which Bill.

**What to send Sumanth:** the `qb-mcp.log` file(s), the full `run qb status` output from Claude, the exact error message, and roughly when it happened.

## Development on macOS

```sh
uv sync            # installs deps incl. dev group (pytest, lxml); pywin32 only on Windows
uv run pytest
```

Tests call the MCP tools in-process with the **Replay** transport (`tests/fixtures/replay/`, one `<RequestType>.xml` per request type); no Windows or QuickBooks needed.

**qbXML schema validation.** Replay validates every outgoing request against the qbXML XSD found in `QBXML_XSD_DIR` (default `<repo>/xsd/`; it loads `qbxmlops*.xsd`). Without it validation is skipped with a warning. Get the 16.0 set from the QuickBooks Desktop SDK 17.0 installer, which ships every version's schemas under `C:\Program Files\Intuit\IDN\Common\tools\validator\` ([docs/vm-runbook.md](docs/vm-runbook.md) section 4). **Never commit Intuit's XSD** (Intuit licence; `xsd/` and `*.xsd` are git-ignored).

```sh
QBXML_XSD_DIR=~/qbxml-xsd/16.0 uv run pytest
```

**Run the server against replay data** (e.g. with MCP Inspector or a local Claude Desktop):

```sh
QB_MCP_TRANSPORT=replay QB_MCP_REPLAY_DIR=tests/fixtures/replay uv run qb-mcp
```

`QB_MCP_TRANSPORT=replay` without `QB_MCP_REPLAY_DIR` makes every tool report that it's missing. Off Windows with no replay selected, every tool says the server only works on Windows.

**Sensitive fixtures.** Base fixtures (`<RequestType>.xml`) and other variants hold no sensitive values, because a recording may replace them. The made-up tax IDs, bank, card and vendor account numbers live only in hand-written `*.sensitive.xml` fixtures, which are never recorded; stripping tests must `replay.respond_with(..., "<RequestType>.sensitive.xml")`. `tests/test_fixture_hygiene.py` enforces both.

**On the Windows VM** (real QuickBooks): `uv run python scripts/smoke_test.py` and `uv run python scripts/record_fixtures.py`; see [docs/vm-runbook.md](docs/vm-runbook.md). A record run that refuses any file exits 1. Rock Castle may contain a company EIN or Vendor account numbers, which block `CompanyQueryRq.xml` / `VendorQueryRq.xml`: leave the hand-written fixture in place and note it in #4.
