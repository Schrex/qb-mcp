# Common QuickBooks use cases vs. what the MCP server can do

Tried on 2026-10-29 on the spare Windows PC: QuickBooks Enterprise 2024 (Contractor, trial), Rock Castle sample file, the server launched by Claude Desktop with `uvx` from branch `spec-1-read-only-mcp` (#4, #10). Every call below went through the real MCP connection, not in-process.

The use-case list is the consensus of small-business bookkeeping checklists and contractor job-costing guides (Intuit's accounting checklist and job-costing articles, SDO CPA, Remote Books Online, Marcum, Levelset). None of those sources measures actual feature usage, so this is "what businesses do", not a ranking.

## Summary

Every **lookup** a business does in QuickBooks works through the server. Every **entry** (bill, payment, invoice, reconciliation) is refused at the boundary, by design (ADR-0001): the stone-matching plugin prepares bills on screen, Jeri approves, QuickBooks stays the system of record.

## The table

| Use case | How it was tried | Result |
|---|---|---|
| Enter vendor bills | write | **Refused.** A `VendorAddRq` through `run_query` was blocked before reaching QuickBooks ("this server is read-only; run_query only sends a *QueryRq element"). |
| Check a bill is not entered twice | `query_bills` with `ref_number` | Works. Unknown ref number → empty result plus QuickBooks warning 500. |
| See unpaid bills | `query_bills` `paid_status=unpaid` | Works. See the OpenAmount finding below. |
| Pay bills | write | Not possible. The A/P Aging Summary (`run_query` `AgingReportQueryRq`) that says what to pay works: 26,636.92 total, one vendor in the 1–30 bucket. |
| Create customer invoices | write | Not possible. Open invoices with balance remaining (`InvoiceQueryRq` `PaidStatus=NotPaidOnly`) work. |
| Receive payments | write | Not possible. Listing received payments with method and deposit account (`ReceivePaymentQueryRq`) works. |
| Customer and job list, balances, job status | `list_customer_jobs` | Works, with job descriptions and status (Closed / Pending / InProgress). |
| Vendor list | `list_vendors` | Works. |
| Chart of accounts, bank balances | `list_accounts` `account_type=Bank` | Works: Checking, Savings, Petty Cash with balances in one call. |
| Items, services, inventory on hand | `list_items`; `ItemInventoryQueryRq` | Works, incl. quantity on hand, reorder point, average cost. |
| Estimates | `EstimateQueryRq` | Works. |
| Purchase orders | `PurchaseOrderQueryRq` | Works, incl. `IsFullyReceived`. |
| Job profitability, estimates vs. actuals | `run_job_report` | Works, per customer or all jobs. |
| Profit & Loss, Balance Sheet | `GeneralSummaryReportQueryRq` | Works. Full 2026 P&L returned (net income 113,560.69 in the sample). |
| Bank reconciliation | UI only | Not possible. Its inputs are readable: `CheckQueryRq`, `DepositQueryRq` by account. |
| Payroll and employee records | `EmployeeQueryRq` | **Reachable.** Names, addresses, hire dates came back (sensitive identifiers stripped). See risk note. |
| Time tracking | `TimeTrackingQueryRq` | Works: hours by employee and job with billable status. |
| Journal entries, credit card charges | `JournalEntryQueryRq`, `CreditCardChargeQueryRq` | Works. |
| Backups, sending or printing invoices | UI only | Out of scope. |

## Findings

1. **`BillRet/OpenAmount` is the vendor's total open balance, not the bill's.** Confirmed with a raw `BillQueryRq` for Perry Windows & Doors: all five unpaid bills (1,800 + 2,325 + 50 + 2,400 + 130) report `OpenAmount` 6,705.00, the sum. `query_bills` passes that through as `open_amount` per bill, which misleads. Belongs with #14 (`is_paid` vs `OpenAmount`); the per-bill balance may need `AmountDue` minus applied payments, or the A/P aging detail report.
2. **Employee and payroll data are one query away.** With Admin in the QuickBooks window nothing narrows reads except field stripping; `run_query` can send `EmployeeQueryRq`, `PayrollItemWageQueryRq` and the like. This is #12 (allow-list of query types).
3. **Raw `run_query` answers are verbose** (internal IDs, address blocks, template refs). The dedicated tools are much easier for a chat to use. The most common raw queries, open invoices, P&L / Balance Sheet, and A/R / A/P aging, would be worth dedicated tools in a later slice.
4. **Sample-file quirk, not the server:** Rock Castle's clock is shifted, so due dates sit about a year after bill dates. Live data won't look like that.

## What this means for v1

- The server automates the *reading* half of bookkeeping: "what do we owe", "who owes us", "is this bill already in", "how is this job doing", "what is in the bank". A chat question becomes a query and the answer comes back in seconds.
- The *entering* half stays manual on purpose. Nothing here changes that; the guard refused the one write attempted.
- Before the install visit (#10): fix or document the `open_amount` meaning (#14), and decide the `run_query` allow-list (#12), since the live file has real employees.
