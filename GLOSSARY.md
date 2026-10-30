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

**Attachment**:
A source file (PDF, scan) linked to a QuickBooks transaction through QuickBooks' own Attach File feature.
_Avoid_: document, upload

**Lookup**:
Any read of the Company File that cannot change it.
_Avoid_: sync, pull, import

**Trust Stamp**:
The `stamp` on every tool result saying what the answer is based on: the filters applied, and whether it is complete or capped (then: "showing the first N; more exist; totals incomplete" and how to narrow). Later: basis, dates, closing date, excluded transactions, where to check in QuickBooks.
_Avoid_: metadata, footer
