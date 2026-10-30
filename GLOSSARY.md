# QuickBooks Desktop Access

Lets Jeri's Claude read Schreiber's QuickBooks Desktop books for budgeting and job-cost questions. Read-only for now.

## Language

**Company File**:
The single QuickBooks Desktop data file holding Schreiber's books, on Jeri's Windows PC.
_Avoid_: database, QB file, the books

**Vendor**:
A supplier Schreiber owes money to.
_Avoid_: supplier, payee

**Customer:Job**:
A job, always named under its parent customer (e.g. `Acme:Main St Paving`); the unit costs are tracked against.
_Avoid_: project, job (alone)

**Item**:
A QuickBooks list entry describing what was bought or sold (material, service, etc.), used on bill and invoice lines.
_Avoid_: product, material code

**Bill**:
A Vendor's invoice as recorded in QuickBooks; money Schreiber owes.
_Avoid_: invoice (that is money owed *to* Schreiber), expense

**Open Balance**:
What is still owed on one Bill after the payments and credits applied to it; the Open Balance column of QuickBooks' A/P Aging Detail report.
_Avoid_: open amount, balance due, amount remaining

**Posting / Non-posting**:
A posting transaction (Bill, Invoice, Check) changes account balances; a non-posting one (Estimate, Purchase Order, Sales Order) only records a quote or commitment and is never summed with posting ones.
_Avoid_: actual / committed (as kinds of transaction), draft

**Basis**:
Whether a figure counts income and expenses when entered (accrual) or when paid (cash).
_Avoid_: method, accounting mode

**Closing Date**:
The date set in the Company File on or before which transactions are locked; figures for a later (open) period may still change.
_Avoid_: lock date, period end, cutoff

**Trust Stamp**:
The statement carried by every answer of what it is based on: basis, dates, Closing Date, filters, what was excluded, and where in QuickBooks to check it.
_Avoid_: metadata, footer, disclaimer

**Attachment**:
A source file (PDF, scan) linked to a QuickBooks transaction through QuickBooks' own Attach File feature.
_Avoid_: document, upload

**Lookup**:
Any read of the Company File that cannot change it.
_Avoid_: sync, pull, import
