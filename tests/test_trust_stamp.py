"""The trust stamp (#25): one `stamp` on every tool result saying what the answer is based on
(filters applied) and whether it is complete or capped."""

import shutil
from datetime import date, timedelta

import pytest
from conftest import FIXTURES, tool_requests
from mcp import Client

from qb_mcp.errors import TransportError, TransportFailure
from qb_mcp.server import build_server
from qb_mcp.transports.replay import ReplayTransport

pytestmark = pytest.mark.anyio

INVOICES = "<InvoiceQueryRq><MaxReturned>50</MaxReturned></InvoiceQueryRq>"

# The fewest arguments each tool needs; a tool missing here fails the coverage test below.
MINIMAL_ARGS = {
    "qb_status": {},
    "get_company_info": {},
    "list_vendors": {},
    "query_bills": {},
    "list_customer_jobs": {},
    "list_items": {},
    "list_accounts": {},
    "run_job_report": {"report": "job_profitability_summary", "from_date": "2024-01-01", "to_date": "2024-12-31"},
    "run_query": {"query": INVOICES},
}


async def test_every_tool_is_covered_by_the_stamp_tests(client):
    assert {t.name for t in (await client.list_tools()).tools} == set(MINIMAL_ARGS)


@pytest.mark.parametrize("tool", sorted(MINIMAL_ARGS))
async def test_every_tool_result_carries_a_trust_stamp(call, tool):
    result = await call(tool, **MINIMAL_ARGS[tool])

    assert not result.is_error
    stamp = result.structured_content["stamp"]
    assert isinstance(stamp["filters"], dict)
    assert isinstance(stamp["complete"], bool)


async def test_stamp_lists_the_filters_applied_and_not_output_options(call):
    out = (
        await call(
            "query_bills", from_date="2024-01-01", vendor="Timberloft Lumber", paid_status="unpaid",
            customer_job="Cook, Brian", include_lines=True, limit=50,
        )
    ).structured_content

    assert out["stamp"]["filters"] == {
        "from_date": "2024-01-01",
        "vendor": "Timberloft Lumber",
        "paid_status": "unpaid",
        "customer_job": "Cook, Brian",
    }


async def test_stamp_lists_default_filters_that_still_apply(call):
    jobs = (await call("list_customer_jobs")).structured_content
    accounts = (await call("list_accounts", account_type="Expense", status="all")).structured_content

    assert jobs["stamp"]["filters"] == {"status": "active"}
    assert accounts["stamp"]["filters"] == {"account_type": "Expense"}


async def test_run_query_stamp_lists_the_filters_in_the_query(call):
    query = (
        "<InvoiceQueryRq><MaxReturned>50</MaxReturned>"
        "<TxnDateRangeFilter><FromTxnDate>2024-01-01</FromTxnDate></TxnDateRangeFilter>"
        "<EntityFilter><FullName>Cook, Brian</FullName></EntityFilter>"
        "<IncludeLineItems>true</IncludeLineItems></InvoiceQueryRq>"
    )

    out = (await call("run_query", query=query)).structured_content

    assert out["stamp"]["filters"] == {
        "TxnDateRangeFilter": {"FromTxnDate": "2024-01-01"},
        "EntityFilter": {"FullName": "Cook, Brian"},
    }


async def test_capped_result_says_so_in_plain_words_and_how_to_narrow(call, replay):
    replay.respond_with("BillQueryRq", "BillQueryRq.capped.xml")

    out = (await call("query_bills", vendor="Perry Windows & Doors", limit=2)).structured_content

    assert [b["ref_number"] for b in out["results"]] == ["CAP-1", "CAP-2"]
    assert out["stamp"]["complete"] is False
    assert out["stamp"]["cap_notice"] == (
        "Showing the first 2; more exist; totals incomplete. Narrow by date or Vendor."
    )


async def test_cap_notice_counts_the_results_actually_returned_not_the_limit(call, replay):
    # 2 Bills scanned (limit 2), more exist; none is paid, so the paid filter returns none of them.
    replay.respond_with("BillQueryRq", "BillQueryRq.capped.xml")

    out = (await call("query_bills", vendor="Perry Windows & Doors", paid_status="paid", limit=2)).structured_content

    assert out["count"] == 0
    assert out["stamp"]["complete"] is False
    assert out["stamp"]["cap_notice"] == (
        "Showing 0 found in the first 2 scanned; more exist; totals incomplete. Narrow by date or Vendor."
    )
    assert "first 2 results" not in out.get("note", "")


async def test_capped_result_carries_no_total(call, replay):
    replay.respond_with("BillQueryRq", "BillQueryRq.capped.xml")

    out = (await call("query_bills", vendor="Perry Windows & Doors", limit=2)).structured_content

    assert not [key for key in out if "total" in key.casefold() or "sum" in key.casefold()]


async def test_uncapped_result_says_it_is_complete(call, replay):
    replay.respond_with("BillQueryRq", "BillQueryRq.capped.xml")

    out = (await call("query_bills", vendor="Perry Windows & Doors", limit=3)).structured_content

    assert out["count"] == 3
    assert out["stamp"]["complete"] is True
    assert out["stamp"]["cap_notice"] is None


async def test_each_capped_tool_names_its_own_way_to_narrow(call):
    vendors = (await call("list_vendors", limit=1)).structured_content
    invoices = (await call("run_query", query=INVOICES, limit=1)).structured_content

    assert vendors["stamp"]["cap_notice"] == (
        "Showing the first 1; more exist; totals incomplete. Narrow by name_contains or status."
    )
    assert invoices["stamp"]["cap_notice"] == (
        "Showing the first 1; more exist; totals incomplete. Narrow by date, Vendor or Customer:Job."
    )


# --- #27: basis, dates, where to check in QuickBooks -------------------------------------------


async def test_job_report_stamp_states_the_basis_quickbooks_ran_it_on(call):
    out = (await call("run_job_report", **MINIMAL_ARGS["run_job_report"])).structured_content

    assert out["stamp"]["basis"] == "Accrual"


PROFIT_AND_LOSS = (
    "<GeneralSummaryReportQueryRq><GeneralSummaryReportType>ProfitAndLossStandard</GeneralSummaryReportType>"
    "<ReportPeriod><FromReportDate>2024-01-01</FromReportDate><ToReportDate>2024-12-31</ToReportDate></ReportPeriod>"
    "{basis}</GeneralSummaryReportQueryRq>"
)


async def test_report_query_runs_on_accrual_basis_unless_told_otherwise(call, replay):
    replay.respond_with("GeneralSummaryReportQueryRq", "GeneralSummaryReportQueryRq.xml")

    out = (await call("run_query", query=PROFIT_AND_LOSS.format(basis=""))).structured_content

    (sent,) = tool_requests(replay)  # schema-validated by the Replay transport
    assert "<ReportBasis>Accrual</ReportBasis>" in sent
    assert out["stamp"]["basis"] == "Accrual"


async def test_report_query_basis_can_be_overridden_per_call(call, replay):
    replay.respond_with("GeneralSummaryReportQueryRq", "GeneralSummaryReportQueryRq.cash.xml")

    out = (
        await call("run_query", query=PROFIT_AND_LOSS.format(basis="<ReportBasis>Cash</ReportBasis>"))
    ).structured_content

    (sent,) = tool_requests(replay)
    assert "<ReportBasis>Cash</ReportBasis>" in sent
    assert "Accrual" not in sent
    assert out["stamp"]["basis"] == "Cash"


async def test_transaction_lists_say_basis_does_not_apply(call):
    bills = (await call("query_bills")).structured_content
    invoices = (await call("run_query", query=INVOICES)).structured_content

    assert bills["stamp"]["basis"] == "not a report; basis does not apply"
    assert invoices["stamp"]["basis"] == "not a report; basis does not apply"


async def test_stamp_states_the_date_range_and_the_date_field_it_filtered(call):
    report = (await call("run_job_report", **MINIMAL_ARGS["run_job_report"])).structured_content
    bills = (await call("query_bills", from_date="2024-01-01")).structured_content
    all_bills = (await call("query_bills")).structured_content

    assert report["stamp"]["dates"] == {"from": "2024-01-01", "to": "2024-12-31", "field": "transaction date"}
    assert bills["stamp"]["dates"] == {
        "from": "2024-01-01", "to": None, "field": "transaction date", "balances_as_of": "today",
    }
    assert all_bills["stamp"]["dates"] == {
        "from": None, "to": None, "field": "transaction date", "balances_as_of": "today",
    }


async def test_balances_are_stamped_as_of_today(call):
    for tool in ("list_vendors", "list_customer_jobs", "list_accounts"):
        out = (await call(tool)).structured_content
        assert out["stamp"]["dates"] == {"as_of": "today"}, tool


async def test_tools_without_dates_say_so(call):
    for tool in ("qb_status", "get_company_info", "list_items"):
        out = (await call(tool)).structured_content
        assert out["stamp"]["dates"] is None, tool


async def test_run_query_stamp_states_the_dates_in_the_query(call, replay):
    by_txn = (
        "<InvoiceQueryRq><MaxReturned>50</MaxReturned><TxnDateRangeFilter><FromTxnDate>2024-01-01</FromTxnDate>"
        "<ToTxnDate>2024-03-31</ToTxnDate></TxnDateRangeFilter></InvoiceQueryRq>"
    )
    by_modified = (
        "<InvoiceQueryRq><MaxReturned>50</MaxReturned><ModifiedDateRangeFilter>"
        "<FromModifiedDate>2024-02-01</FromModifiedDate></ModifiedDateRangeFilter></InvoiceQueryRq>"
    )
    replay.respond_with("GeneralSummaryReportQueryRq", "GeneralSummaryReportQueryRq.xml")

    txn = (await call("run_query", query=by_txn)).structured_content
    modified = (await call("run_query", query=by_modified)).structured_content
    undated = (await call("run_query", query=INVOICES)).structured_content
    report = (await call("run_query", query=PROFIT_AND_LOSS.format(basis=""))).structured_content

    assert txn["stamp"]["dates"] == {"from": "2024-01-01", "to": "2024-03-31", "field": "transaction date"}
    assert modified["stamp"]["dates"] == {"from": "2024-02-01", "to": None, "field": "modified date"}
    assert undated["stamp"]["dates"] == {"from": None, "to": None, "field": "transaction date"}
    assert report["stamp"]["dates"] == {"from": "2024-01-01", "to": "2024-12-31", "field": "transaction date"}


async def test_run_query_stamp_names_a_date_macro_rather_than_claiming_all_dates(call):
    this_month = (
        "<InvoiceQueryRq><MaxReturned>50</MaxReturned><TxnDateRangeFilter><DateMacro>ThisMonth</DateMacro>"
        "</TxnDateRangeFilter></InvoiceQueryRq>"
    )

    out = (await call("run_query", query=this_month)).structured_content

    assert out["stamp"]["dates"] == {"period": "ThisMonth", "field": "transaction date"}


@pytest.mark.parametrize("tool", sorted(MINIMAL_ARGS))
async def test_every_tool_says_where_to_check_it_in_quickbooks(call, tool):
    stamp = (await call(tool, **MINIMAL_ARGS[tool])).structured_content["stamp"]

    assert " > " in stamp["check_in_quickbooks"]  # a QuickBooks menu path


async def test_bills_are_checked_against_the_payables_reports(call):
    stamp = (await call("query_bills")).structured_content["stamp"]

    assert stamp["check_in_quickbooks"] == (
        "Reports > Vendors & Payables > Unpaid Bills Detail (open Bills) or A/P Aging Detail (Open Balance); "
        "one Bill: Vendors > Enter Bills"
    )


@pytest.mark.parametrize(
    ("report", "menu_path"),
    [
        ("job_profitability_summary", "Reports > Jobs, Time & Mileage > Job Profitability Summary"),
        ("job_profitability_detail", "Reports > Jobs, Time & Mileage > Job Profitability Detail"),
        ("job_estimates_vs_actuals_summary", "Reports > Jobs, Time & Mileage > Job Estimates vs. Actuals Summary"),
        ("job_estimates_vs_actuals_detail", "Reports > Jobs, Time & Mileage > Job Estimates vs. Actuals Detail"),
    ],
)
async def test_each_job_report_names_the_same_report_in_quickbooks(call, report, menu_path):
    args = {"from_date": "2024-01-01", "to_date": "2024-12-31", "customer_job": "Acme:Main St Paving"}

    stamp = (await call("run_job_report", report=report, **args)).structured_content["stamp"]

    assert stamp["check_in_quickbooks"] == menu_path


async def test_run_query_names_where_to_check_each_kind_of_query(call, replay):
    replay.respond_with("GeneralSummaryReportQueryRq", "GeneralSummaryReportQueryRq.xml")

    invoices = (await call("run_query", query=INVOICES)).structured_content["stamp"]
    profit_and_loss = (await call("run_query", query=PROFIT_AND_LOSS.format(basis=""))).structured_content["stamp"]

    assert invoices["check_in_quickbooks"] == (
        "Reports > Customers & Receivables > Open Invoices; one Invoice: Customers > Create Invoices"
    )
    assert profit_and_loss["check_in_quickbooks"] == "Reports > Company & Financial > Profit & Loss Standard"

# --- closing date (#26) -------------------------------------------------------------------------
# Hand-written Preferences fixtures: closing date 2024-06-30, and none set.
CLOSING_DATE = "PreferencesQueryRq.closingdate.xml"
NO_CLOSING_DATE = "PreferencesQueryRq.noclosingdate.xml"


@pytest.mark.parametrize("tool", sorted(MINIMAL_ARGS))
async def test_every_tool_result_carries_the_closing_date(call, replay, tool):
    replay.respond_with("PreferencesQueryRq", CLOSING_DATE)

    stamp = (await call(tool, **MINIMAL_ARGS[tool])).structured_content["stamp"]

    assert stamp["closing_date"] == "2024-06-30"


@pytest.mark.parametrize(
    "from_date,to_date,period",
    [
        ("2024-01-01", "2024-03-31", "closed"),  # before the closing date
        ("2024-01-01", "2024-06-30", "closed"),  # ends on it
        ("2024-01-01", "2024-12-31", "may still change"),  # spans it
        ("2024-07-01", "2024-12-31", "may still change"),  # after it
    ],
)
async def test_stamp_says_whether_the_date_range_is_closed(call, replay, from_date, to_date, period):
    replay.respond_with("PreferencesQueryRq", CLOSING_DATE)

    report = (
        await call("run_job_report", report="job_profitability_summary", from_date=from_date, to_date=to_date)
    ).structured_content

    assert report["stamp"]["period"] == period


async def test_bills_open_balances_are_live_so_never_closed_by_their_bill_dates(call, replay):
    # Bills dated before the closing date can still be paid after it, changing open_balance/is_paid.
    replay.respond_with("PreferencesQueryRq", CLOSING_DATE)

    stamp = (await call("query_bills", from_date="2024-01-01", to_date="2024-03-31")).structured_content["stamp"]

    assert stamp["period"] == "may still change"
    assert stamp["dates"] == {
        "from": "2024-01-01", "to": "2024-03-31", "field": "transaction date", "balances_as_of": "today",
    }


@pytest.mark.parametrize("to_date,period", [("2024-05-31", "closed"), ("2024-07-31", "may still change")])
async def test_run_query_stamp_says_whether_the_query_date_range_is_closed(call, replay, to_date, period):
    replay.respond_with("PreferencesQueryRq", CLOSING_DATE)
    query = (
        "<InvoiceQueryRq><MaxReturned>50</MaxReturned><TxnDateRangeFilter>"
        f"<FromTxnDate>2024-01-01</FromTxnDate><ToTxnDate>{to_date}</ToTxnDate></TxnDateRangeFilter></InvoiceQueryRq>"
    )

    out = (await call("run_query", query=query)).structured_content

    assert out["stamp"]["period"] == period


async def test_results_up_to_today_may_still_change(call, replay):
    replay.respond_with("PreferencesQueryRq", CLOSING_DATE)

    bills = (await call("query_bills", from_date="2024-01-01")).structured_content
    vendors = (await call("list_vendors")).structured_content

    assert bills["stamp"]["period"] == "may still change"
    assert vendors["stamp"]["period"] == "may still change"


async def test_stamp_says_no_closing_date_is_set_when_quickbooks_has_none(call, replay):
    replay.respond_with("PreferencesQueryRq", NO_CLOSING_DATE)

    stamp = (await call("query_bills", from_date="2024-01-01", to_date="2024-03-31")).structured_content["stamp"]

    assert stamp["closing_date"] is None
    assert stamp["period"] == "no closing date set; may still change"


async def test_stamp_says_closing_date_unknown_when_it_cannot_be_read(call, replay):
    replay.respond_with("PreferencesQueryRq", "PreferencesQueryRq.error3260.xml")

    stamp = (await call("query_bills", from_date="2024-01-01", to_date="2024-03-31")).structured_content["stamp"]

    assert stamp["closing_date"] is None
    assert stamp["period"] == "closing date unknown"


async def test_closing_date_is_read_once_per_session(call, replay):
    replay.respond_with("PreferencesQueryRq", CLOSING_DATE)

    await call("list_vendors")
    await call("query_bills", to_date="2024-03-31")
    last = (await call("get_company_info")).structured_content["stamp"]

    # A *QueryRq, so the read-only guard lets it through; the Replay transport schema-validates it
    # (an invalid request would leave the closing date unknown).
    assert sum("<PreferencesQueryRq" in rq for rq in replay.sent) == 1
    assert last["closing_date"] == "2024-06-30"


async def test_run_query_still_refuses_preferences_queries(call, replay):
    result = await call("run_query", query="<PreferencesQueryRq></PreferencesQueryRq>")

    assert result.is_error
    assert replay.sent == []


async def test_closing_date_is_read_again_after_quickbooks_was_unreachable(call, replay):
    replay.respond_with("PreferencesQueryRq", CLOSING_DATE)
    replay.fail_with(TransportError(TransportFailure.QB_NOT_RUNNING))
    down = (await call("qb_status")).structured_content["stamp"]
    replay.fail_with(None)

    up = (await call("list_vendors")).structured_content["stamp"]

    assert down["period"] == "closing date unknown"
    assert up["closing_date"] == "2024-06-30"


# --- date macros (DateMacro / ReportDateMacro): the period they end on, relative to today ---------


@pytest.fixture
async def closed_through_last_month(tmp_path):
    """A client over the Replay fixtures whose closing date is the last day of last month (relative
    to today, as QuickBooks resolves date macros)."""
    last_month_end = date.today().replace(day=1) - timedelta(days=1)
    fixtures = tmp_path / "replay"
    shutil.copytree(FIXTURES, fixtures)
    closing = (FIXTURES / CLOSING_DATE).read_text().replace("2024-06-30", last_month_end.isoformat())
    (fixtures / "PreferencesQueryRq.xml").write_text(closing)
    replay = ReplayTransport(fixtures, company_file="(replay)")
    replay.respond_with("GeneralSummaryReportQueryRq", "GeneralSummaryReportQueryRq.xml")
    async with Client(build_server(replay), raise_exceptions=True) as client:
        yield client


TXN_MACRO = (
    "<InvoiceQueryRq><MaxReturned>50</MaxReturned><TxnDateRangeFilter><DateMacro>{}</DateMacro>"
    "</TxnDateRangeFilter></InvoiceQueryRq>"
)
REPORT_MACRO = (
    "<GeneralSummaryReportQueryRq><GeneralSummaryReportType>ProfitAndLossStandard</GeneralSummaryReportType>"
    "<ReportDateMacro>{}</ReportDateMacro></GeneralSummaryReportQueryRq>"
)
FISCAL_MACRO = "unknown (fiscal date macro)"


@pytest.mark.parametrize(
    "query,period",
    [
        (TXN_MACRO.format("LastMonth"), "closed"),
        (TXN_MACRO.format("LastCalendarQuarter"), "closed"),
        (TXN_MACRO.format("LastCalendarYear"), "closed"),
        (TXN_MACRO.format("ThisMonth"), "may still change"),
        (TXN_MACRO.format("ThisCalendarYearToDate"), "may still change"),
        (TXN_MACRO.format("Today"), "may still change"),
        (TXN_MACRO.format("All"), "may still change"),
        (TXN_MACRO.format("ThisFiscalYear"), FISCAL_MACRO),
        (TXN_MACRO.format("LastFiscalQuarter"), FISCAL_MACRO),
        (REPORT_MACRO.format("LastMonth"), "closed"),
        (REPORT_MACRO.format("LastQuarter"), "closed"),
        (REPORT_MACRO.format("LastYear"), "closed"),
        (REPORT_MACRO.format("ThisMonthToDate"), "may still change"),
        (REPORT_MACRO.format("NextYear"), "may still change"),
    ],
)
async def test_date_macro_period_is_resolved_against_the_closing_date(closed_through_last_month, query, period):
    out = (await closed_through_last_month.call_tool("run_query", {"query": query})).structured_content

    assert out["stamp"]["period"] == period


# --- aging reports: which A/P or A/R accounts they include ---------------------------------------

AGING = "<AgingReportQueryRq><AgingReportType>{}</AgingReportType><ReportDateMacro>Today</ReportDateMacro>{}</AgingReportQueryRq>"


@pytest.mark.parametrize(
    "report_type,account_filter,accounts",
    [
        ("APAgingSummary", "", "all A/P accounts"),
        ("APAgingDetail", "", "all A/P accounts"),
        ("ARAgingSummary", "", "all A/R accounts"),
        (
            "APAgingSummary",
            "<ReportAccountFilter><FullName>Accounts Payable - Retainage</FullName></ReportAccountFilter>",
            "Accounts Payable - Retainage",
        ),
        (
            "ARAgingDetail",
            "<ReportAccountFilter><FullNameWithChildren>Accounts Receivable</FullNameWithChildren></ReportAccountFilter>",
            "Accounts Receivable and its subaccounts",
        ),
    ],
)
async def test_aging_report_stamp_names_the_accounts_it_includes(call, report_type, account_filter, accounts):
    out = (await call("run_query", query=AGING.format(report_type, account_filter))).structured_content

    assert out["stamp"]["accounts"] == accounts


async def test_other_answers_name_no_accounts(call):
    assert (await call("query_bills")).structured_content["stamp"]["accounts"] is None
