"""Where Jeri can check each answer in QuickBooks Desktop Enterprise: the trust stamp's
`check_in_quickbooks`, as a menu path (report or screen)."""

QB_STATUS = "Help > Product Information (press F2 in QuickBooks): product, version and Company File location"
COMPANY_INFO = "Company > My Company"
VENDORS = "Vendors > Vendor Center; balances: Reports > Vendors & Payables > Vendor Balance Summary"
BILLS = (
    "Reports > Vendors & Payables > Unpaid Bills Detail (open Bills) or A/P Aging Detail (Open Balance); "
    "one Bill: Vendors > Enter Bills"
)
CUSTOMER_JOBS = "Customers > Customer Center; balances: Reports > Customers & Receivables > Customer Balance Summary"
ITEMS = "Lists > Item List"
ACCOUNTS = "Lists > Chart of Accounts"
JOB_REPORTS = "Reports > Jobs, Time & Mileage > "
RAW_QUERY = "Reports > Report Center (the report or register matching the query)"

# run_query: by request type, then (reports) by the report type requested.
QUERIES = {
    "InvoiceQueryRq": "Reports > Customers & Receivables > Open Invoices; one Invoice: Customers > Create Invoices",
    "SalesReceiptQueryRq": "Reports > Sales > Sales by Customer Detail; one receipt: Customers > Enter Sales Receipts",
    "CreditMemoQueryRq": "Reports > Customers & Receivables > Transaction List by Customer; "
    "one credit: Customers > Create Credit Memos/Refunds",
    "ReceivePaymentQueryRq": "Reports > Customers & Receivables > Transaction List by Customer; "
    "one payment: Customers > Receive Payments",
    "EstimateQueryRq": "Reports > Jobs, Time & Mileage > Estimates by Job",
    "PurchaseOrderQueryRq": "Reports > Purchases > Open Purchase Orders",
    "ItemReceiptQueryRq": "Reports > Purchases > Purchases by Vendor Detail",
    "VendorCreditQueryRq": "Reports > Vendors & Payables > Transaction List by Vendor",
    "BillPaymentCheckQueryRq": "Reports > Vendors & Payables > Transaction List by Vendor",
    "CheckQueryRq": "Reports > Banking > Check Detail",
    "CreditCardChargeQueryRq": "Lists > Chart of Accounts > (credit card account) register; "
    "one charge: Banking > Enter Credit Card Charges",
    "CreditCardCreditQueryRq": "Lists > Chart of Accounts > (credit card account) register; "
    "one credit: Banking > Enter Credit Card Charges",
    "DepositQueryRq": "Reports > Banking > Deposit Detail",
    "JournalEntryQueryRq": "Reports > Accountant & Taxes > Journal",
    "TimeTrackingQueryRq": "Reports > Jobs, Time & Mileage > Time by Job Detail",
    "ItemInventoryQueryRq": "Reports > Inventory > Inventory Valuation Summary",
    "ClassQueryRq": "Lists > Class List",
    "AgingReportQueryRq": "Reports > Vendors & Payables > A/P Aging, or Reports > Customers & Receivables > A/R Aging",
    "GeneralSummaryReportQueryRq": "Reports > Company & Financial",
    "BudgetSummaryReportQueryRq": "Reports > Budgets & Forecasts",
}
REPORT_TYPES = {
    # AgingReportType
    "APAgingDetail": "Reports > Vendors & Payables > A/P Aging Detail",
    "APAgingSummary": "Reports > Vendors & Payables > A/P Aging Summary",
    "ARAgingDetail": "Reports > Customers & Receivables > A/R Aging Detail",
    "ARAgingSummary": "Reports > Customers & Receivables > A/R Aging Summary",
    # GeneralSummaryReportType
    "ProfitAndLossStandard": "Reports > Company & Financial > Profit & Loss Standard",
    "ProfitAndLossDetail": "Reports > Company & Financial > Profit & Loss Detail",
    "ProfitAndLossByJob": "Reports > Jobs, Time & Mileage > Profit & Loss by Job",
    "ProfitAndLossByClass": "Reports > Company & Financial > Profit & Loss by Class",
    "BalanceSheetStandard": "Reports > Company & Financial > Balance Sheet Standard",
    "BalanceSheetSummary": "Reports > Company & Financial > Balance Sheet Summary",
    "TrialBalance": "Reports > Accountant & Taxes > Trial Balance",
    "VendorBalanceSummary": "Reports > Vendors & Payables > Vendor Balance Summary",
    "CustomerBalanceSummary": "Reports > Customers & Receivables > Customer Balance Summary",
    # BudgetSummaryReportType
    "BalanceSheetBudgetVsActual": "Reports > Budgets & Forecasts > Budget vs. Actual",
    "ProfitAndLossBudgetVsActual": "Reports > Budgets & Forecasts > Budget vs. Actual",
    "ProfitAndLossBudgetOverview": "Reports > Budgets & Forecasts > Budget Overview",
}


JOB_REPORT_NAMES = {  # JobReportType -> QuickBooks' name for the report
    "JobProfitabilitySummary": "Job Profitability Summary",
    "JobProfitabilityDetail": "Job Profitability Detail",
    "JobEstimatesVsActualsSummary": "Job Estimates vs. Actuals Summary",
    "JobEstimatesVsActualsDetail": "Job Estimates vs. Actuals Detail",
}


def job_report(job_report_type: str) -> str:
    return JOB_REPORTS + JOB_REPORT_NAMES[job_report_type]


def query(rq_type: str, report_type: str | None) -> str:
    return REPORT_TYPES.get(report_type or "") or QUERIES.get(rq_type, RAW_QUERY)
