# Windows VM runbook (#4)

Prove the COM path against real QuickBooks before the install visit, answer the view-only-user question, and record replay fixtures from the **Rock Castle Construction** sample Company File. Never record from Jeri's live Company File.

Items marked **(unverified)** come from research, not from running them; correct this file when the VM shows otherwise.

## 1. Machine

**Chosen: a spare office Windows PC** (no cloud cost). The cloud-VM notes below apply only if that changes.

Spare-PC rules:
- **Windows 10/11 Pro x64**, ≥ 16 GB RAM, ~20 GB free disk.
- **No production QuickBooks on it**, and not Jeri's PC. Installing the 2024 trial next to a licensed install can disturb that install's registration and request processor.
- **Never point it at Schreiber's live Company File** or the network share it sits on. Use only the Rock Castle sample file stored locally.
- Use a dedicated local Windows user for this work; you need admin rights only to install software.
- When #4 is done, uninstall the QuickBooks trial and SDK (or reimage) so the PC doesn't keep an expired trial around.

Cloud VM (if used instead):

- **OS:** Windows 11 Pro x64, or Windows Server 2022 x64 (Desktop Experience). QuickBooks Enterprise 2024 lists Windows 10/11 and Server 2016+ as supported **(unverified for Server 2022 with the trial)**; prefer Windows 11 if the cloud offers it.
- **Size:** ≥ 4 vCPU, 16 GB RAM, ≥ 128 GB SSD. (Azure `D4s_v5`, AWS `m6i.xlarge`, or equivalent.)
- **Access:** RDP from Sumanth's IP only (restrict the security group / NSG); strong local admin password.
- **Cost control:** stop (deallocate) the VM whenever not in use; set an auto-shutdown schedule (Azure: Auto-shutdown; AWS: an EventBridge/Instance Scheduler stop) and a budget alert. A stopped VM still bills for its disk. Delete it after #4 and #10 are done (snapshot the disk first if useful).

## 2. Accounts

Do everything below as one normal Windows user (not "Run as administrator"). QuickBooks and the Python process must run as the **same Windows user at the same elevation**, or COM calls fail with "QuickBooks isn't running... must be open as the same Windows user...". The user needs to be in the local Administrators group only so the QuickBooks installer can elevate through UAC (verified on the spare PC: user `Sumanth`, Windows 11 Pro 10.0.22631 x64, 32 GB RAM, 300 GB free).

**Verified 2026-10-28:** the QuickBooks installer launches QuickBooks **elevated** when it finishes. In that state a non-elevated Python gets **no error and no Application Certificate prompt**: `BeginSession` blocks until the 120 s timeout ("QuickBooks didn't answer within 120s..."), and every later request queues behind it and times out too. Close QuickBooks (File > Exit) and reopen it from the Start menu before the first smoke test. To check: `(Get-Process QBW).Path` is empty from a normal PowerShell when QuickBooks is elevated.

## 3. Install QuickBooks Enterprise 2024 (trial)

1. Download the QuickBooks Desktop Enterprise 2024 trial from Intuit (trial download page; needs an Intuit account). Verified 2026-10-28: the trial is available and runs **30 days**; the installer asks for the industry edition (pick Contractor) and installs to `C:\Program Files\Intuit\QuickBooks Enterprise Solutions 24.0\`. It reports itself as "Intuit QuickBooks Enterprise Solutions: Contractor 24.0 Trial Version", version 34.0, qbXML versions 1.0 through **17.0**.
2. Install with defaults; choose the **Contractor** edition when asked (matches Jeri's Enterprise Contractor 2024).
3. Apply updates: **Help > Update QuickBooks Desktop > Update Now**.

## 4. Install the QuickBooks SDK 16.0 and get the XSD

1. Download QuickBooks SDK 16.0 from the Intuit Developer site (developer.intuit.com, Desktop SDK; needs a free developer login) and install with defaults.
2. Find the schema files **(unverified location and names)**; expected under `C:\Program Files (x86)\Intuit\IDN\`:
   ```bat
   dir /s /b "C:\Program Files (x86)\Intuit\IDN\*.xsd"
   ```
   Expect `qbxmlops160.xsd` plus the files it includes (`qbxml160.xsd`, `qbxmltypes160.xsd`, `qbxmlso160.xsd`). Copy the **whole set** to one folder (the entry file includes the others by relative path).
3. Copy that folder to the Mac (e.g. `~/qbxml-xsd/16.0/`) and point `QBXML_XSD_DIR` at it. On the VM, either set `QBXML_XSD_DIR` or put the files in `<repo>\xsd\`.
4. **Never commit the XSD** (Intuit licence). `xsd/` and `*.xsd` are git-ignored; keep it that way.
5. On the Mac: `QBXML_XSD_DIR=~/qbxml-xsd/16.0 uv run pytest` — every request the tools build must validate against 16.0 (until now validated against 13.0 at best).

The SDK also has **SDKTestPlus3** (Start menu, under QuickBooks SDK) for sending hand-written qbXML; useful for the user-attribution test in step 8.

## 5. Open Rock Castle Construction

**File > Open or Restore Company > Open a sample file > Rock Castle Construction**, or on the trial's first-run screen **"I want to see a sample Company File"**. Log in as **Admin** (sample files have no password by default). QuickBooks asks to update the file format first (**Update Company > Yes**); fine for the sample.

Verified 2026-10-28: the trial's first-run flow puts the file on the **Desktop** as `sample_product-based business.QBW` (53 MB; QuickBooks ships no `Sample Company Files` folder under Public Documents). The company name inside is "Rock Castle Construction" but the file name isn't, so the recorder recognises Intuit's `sample_*` naming as well as "rock castle".

Sample files are fine to record from: fictional data. The fixture recorder refuses any other Company File unless given `--i-confirm-not-live-data`; don't use that flag for this ticket.

## 6. Install uv, git, and the repo

```powershell
winget install --id=astral-sh.uv -e
winget install --id=Git.Git -e
# new PowerShell window
git clone https://github.com/Schrex/qb-mcp
cd qb-mcp
uv sync
```

**Python version (verified 2026-10-28 on the spare PC):** the repo pins `3.12` in `.python-version`. Without the pin, uv 0.13.6 picked CPython 3.15.2 and `uv sync` failed with `Missing expected target directory for Python minor version link` (the 3.15 install was left incomplete). `uv python install 3.12` printed the same error but produced a working interpreter; `uv sync` then installed everything (pywin32 312) and `uv run pytest` passed (188 passed, 2 skipped: the XSD tests). Keep this in mind for the `uvx` install on Jeri's PC (#10): if `uvx` hits the same error, pass `--python 3.12`.

Check that uv picked a 64-bit Python: `uv run python -c "import struct; print(struct.calcsize('P')*8)"` → `64` (verified: `3.12.16 [MSC v.1944 64 bit (AMD64)]`). QuickBooks 2024 is 64-bit; 64-bit Python with the 64-bit request processor **works (verified 2026-10-28: smoke test passed with CPython 3.12.16 x64, pywin32 312)**. If `Dispatch` fails with "request processor isn't registered" (0x800401F3 / 0x80040154) *with QuickBooks installed*, try a 32-bit Python (`uv python install cpython-3.12-windows-x86` and `uv sync --python <that>`) and record the result here. Note: with QuickBooks **not installed at all**, the smoke test fails with exactly that message (`Invalid class string; code 0x800401F3`, verified), so the message alone doesn't distinguish "not installed" from a bitness mismatch.

## 7. Authorize and smoke-test

1. With QuickBooks open on Rock Castle as Admin, run:
   ```powershell
   uv run python scripts/smoke_test.py
   ```
2. QuickBooks shows the Application Certificate prompt for **Schreiber QuickBooks Lookup**. Pick **Yes, whenever this QuickBooks company file is open**; **Login as:** the view-only user from step 8 if you've created it (otherwise Admin for now; you can change it later under Integrated Applications); leave the **personal data** checkbox unchecked.
3. If the script timed out waiting on the prompt (120 s), run it again. Expect `PASS` (`qb_status` reachable + one Vendor lookup). Verified: the call that raises the certificate prompt fails with `The QuickBooks user has denied access.; code 0x80040420` even when you answer Yes; the next call in the same run already succeeds, and the next run passes cleanly.
4. Note in #4: the exact `qb_status` output (product, version, supported qbXML versions), and any COM error codes seen.
5. **Capture the real HRESULTs (unverified mapping):** close the Company File (leave QuickBooks open) and run the smoke test; then close QuickBooks entirely and run it again. Record the message and `code 0x...` of each in #4 so `transports/com.py`'s mapping can be pinned. Already verified: QuickBooks not installed → `Invalid class string; code 0x800401F3`, mapped to "request processor isn't registered"; Company File closed, QuickBooks open → `code 0x80040417` ("If the QuickBooks company data file is not open, a call to the \"BeginSession\" method must include the name of the data file."), mapped to "no Company File is loaded" as expected; QuickBooks closed → `Could not start QuickBooks.; code 0x80040408`, mapped to "QuickBooks isn't running" as expected (the SDK does not launch QuickBooks). Both smoke runs also print `Win32 exception occurred releasing IUnknown` at interpreter exit after a failed session: pywin32 releasing the COM object during shutdown, harmless for the one-shot script.

## 8. Does the view-only user enforce read-only? (#4 comment)

Question: while QuickBooks is open, do SDK requests run as the integration's **Login as** user, or as whoever is logged into the QuickBooks window? If the latter, the view-only user adds nothing and the server's query-only guard is the only enforcement.

1. Create the view-only user: **Company > Users > Set Up Users and Roles > User List > New**, name `Claude Lookup`, role **View-Only** only.
2. **Edit > Preferences > Integrated Applications > Company Preferences** → select **Schreiber QuickBooks Lookup** → **Properties** → allow access, **Login as `Claude Lookup`**. Note whether QuickBooks offers "Login as" for the "whenever this company file is open" choice at all.
3. Make sure **audit trail** is on (Enterprise: always on).
4. Stay logged into the QuickBooks window as **Admin**.
5. **Write test, outside the server** (the server's guard would refuse it, so use SDKTestPlus3 with the same app name `Schreiber QuickBooks Lookup`, or a throwaway script calling `OpenConnection2("", "Schreiber QuickBooks Lookup", 1)`): send a harmless add to the **sample** file, e.g.
   ```xml
   <?xml version="1.0"?><?qbxml version="16.0"?>
   <QBXML><QBXMLMsgsRq onError="stopOnError">
     <VendorAddRq><VendorAdd><Name>ZZ View-only test</Name></VendorAdd></VendorAddRq>
   </QBXMLMsgsRq></QBXML>
   ```
   - Status **3260** "Insufficient permission level" → requests run as `Claude Lookup`; the view-only user **does** enforce read-only.
   - Vendor created → requests run as the UI user; it does **not**.
   (SDKTestPlus3 uses its own app name unless you can set one; if it can't, use the throwaway script. Do not add this script to the repo.)
6. **Attribution check:** **Reports > Accountant & Taxes > Audit Trail** (today): note which user the `ZZ View-only test` Vendor (if created) is attributed to. Also check **Company > Users > View Users** / the multi-user list for a `Claude Lookup` session while the smoke test runs **(unverified whether it shows)**.
7. Repeat steps 5–6 logged into the QuickBooks window as a **non-admin full-access** user, to confirm the pattern.
8. Delete the test Vendor (sample file only).
9. Record the result in #4. If the view-only user is **not** enforced: update ADR-0001 ("read-only is enforced by the server's guard; the view-only user is defence-in-depth only for unattended mode") and the caveat in README's install section.
10. Also check that `run_job_report` works under `Claude Lookup` (if requests run as that user). If it returns error 3260, note which role areas the job reports need, for the install visit.

## 9. Record fixtures

```powershell
uv run python scripts/record_fixtures.py --dry-run
uv run python scripts/record_fixtures.py --out tests/fixtures/replay
```

`--dry-run` first to see what will be written. The script refuses to write files containing sensitive values and refuses a non-sample Company File.

- **A run that refuses any file (or hits any error) exits 1** and prints `RECORDING INCOMPLETE`; the other files are still written. Exit 0 only if every file was written.
- **Rock Castle contains a company EIN, Vendor account numbers and tax IDs, and one bank account number.** Verified 2026-10-28: the recorder refuses `CompanyQueryRq.xml` (`CompanyRet/EIN`), `VendorQueryRq.xml` (`VendorRet/AccountNumber` x11, `VendorRet/VendorTaxIdent` x2) and `AccountQueryRq.xml` (`AccountRet/BankNumber` x1); the other nine fixtures record cleanly (Host, Customer, Item, Bill, Invoice, and the four job reports). Expected: leave the hand-written fixture in place (it is already free of sensitive values), note in #4 which files were refused and why, and treat the exit 1 as accepted for those files only. Don't blank the values in QuickBooks or pass flags to get around it.
- **`*.sensitive.xml` fixtures are hand-written and never recorded.** They hold the made-up sensitive values the stripping tests feed in; the script refuses to write that name. Every other fixture must stay free of sensitive values (`tests/test_fixture_hygiene.py` checks this with the script's own scanner).

Detail job reports need a Customer:Job (QuickBooks error 3150 `missing element: "ReportEntityFilter"` otherwise, verified); the recorder runs them for `Abercrombie, Kristy:Remodel Bathroom`.

Job report facts (verified 2026-10-28, Enterprise 2024):
- Column titles: profitability (summary and detail) `Act. Cost`, `Act. Revenue`, `($) Diff.`; estimates vs. actuals (summary and detail) `Est. Cost`, `Act. Cost`, `($) Diff.`, `Est. Revenue`, `Act. Revenue`, `($) Diff.` (the second `($) Diff.` is reported as `($) Diff. (2)`). Detail titles read "Job Profitability Detail for <Customer:Job>".
- An **empty** report (period with no activity) is not "no rows": QuickBooks returns one `TotalRow` labelled `TOTAL` with `0.00` in every column.
- A Customer:Job that **doesn't exist** is QuickBooks error **3120** "Object ... cannot be found", not an empty report.

Review before committing:

1. `git diff --stat` and read every changed fixture. Check: Rock Castle data only; no tax IDs, bank account or card numbers (the script refuses these, but look anyway); no file paths or names identifying Schreiber or Jeri.
2. Keep the hand-made fixtures that tests rely on for edge cases (`*.error3260.xml`, `*.malformed.xml`, `*.nomatch.xml`, `*.warn.xml`, etc.) unless the recording replaces them deliberately. The script never touches these or `*.sensitive.xml`.
3. On the Mac: `QBXML_XSD_DIR=~/qbxml-xsd/16.0 uv run pytest`. Fix tests that assumed hand-written data; don't weaken assertions to make them pass.
4. Commit fixtures on a branch, PR, review the diff once more on GitHub.

## 10. Shut down

Stop/deallocate the VM. Update #4 with: smoke test result, `qb_status` output, observed HRESULTs, Python bitness, the view-only answer, and the XSD file names/location found.
