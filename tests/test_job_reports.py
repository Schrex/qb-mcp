import pytest

pytestmark = pytest.mark.anyio


async def test_job_profitability_summary_returns_columns_and_marked_rows(call):
    # Recorded from Rock Castle Construction: 63 customers, 82 jobs, 209 rows in all.
    result = await call(
        "run_job_report", report="job_profitability_summary", from_date="2000-01-01", to_date="2030-12-31"
    )

    assert not result.is_error
    report = result.structured_content
    assert report["title"] == "Job Profitability Summary"
    assert report["subtitle"] == "January 2000 through December 2030"
    assert report["basis"] == "Accrual"
    assert report["columns"] == ["Act. Cost", "Act. Revenue", "($) Diff."]
    assert report["rows"][:5] == [
        {"kind": "header", "label": "Abercrombie, Kristy"},
        {
            "kind": "data",
            "label": "Family Room",
            "name": "Abercrombie, Kristy:Family Room",
            "values": {"Act. Cost": "2150.00", "Act. Revenue": "2961.05", "($) Diff.": "811.05"},
        },
        {
            "kind": "data",
            "label": "Kitchen",
            "name": "Abercrombie, Kristy:Kitchen",
            "values": {"Act. Cost": "2645.00", "Act. Revenue": "4792.00", "($) Diff.": "2147.00"},
        },
        {
            "kind": "data",
            "label": "Remodel Bathroom",
            "name": "Abercrombie, Kristy:Remodel Bathroom",
            "values": {"Act. Cost": "5416.23", "Act. Revenue": "6749.50", "($) Diff.": "1333.27"},
        },
        {
            "kind": "subtotal",
            "label": "Total Abercrombie, Kristy",
            "name": "Abercrombie, Kristy",
            "values": {"Act. Cost": "10211.23", "Act. Revenue": "14502.55", "($) Diff.": "4291.32"},
        },
    ]
    assert report["rows"][-1] == {
        "kind": "total",
        "label": "TOTAL",
        "values": {"Act. Cost": "442875.80", "Act. Revenue": "669343.84", "($) Diff.": "226468.04"},
    }
    assert len(report["rows"]) == 209
    assert [sum(1 for r in report["rows"] if r["kind"] == k) for k in ("header", "data", "subtotal", "total")] == [
        63,
        82,
        63,
        1,
    ]


@pytest.mark.parametrize(
    ("report", "qb_type"),
    [
        ("job_profitability_summary", "JobProfitabilitySummary"),
        ("job_profitability_detail", "JobProfitabilityDetail"),
        ("job_estimates_vs_actuals_summary", "JobEstimatesVsActualsSummary"),
        ("job_estimates_vs_actuals_detail", "JobEstimatesVsActualsDetail"),
    ],
)
async def test_each_report_type_runs_for_one_customer_job_and_date_range(call, replay, report, qb_type):
    result = await call(
        "run_job_report", report=report, from_date="2024-01-01", to_date="2024-06-30", customer_job="Acme:Main St Paving"
    )

    assert not result.is_error
    (sent,) = replay.sent  # schema-validated by the Replay transport when the XSD is present
    assert f"<JobReportType>{qb_type}</JobReportType>" in sent
    assert "<FromReportDate>2024-01-01</FromReportDate><ToReportDate>2024-06-30</ToReportDate>" in sent
    assert "<FullNameWithChildren>Acme:Main St Paving</FullNameWithChildren>" in sent


async def test_report_without_customer_job_covers_all_jobs(call, replay):
    await call("run_job_report", report="job_profitability_summary", from_date="2024-01-01", to_date="2024-12-31")

    assert "ReportEntityFilter" not in replay.sent[0]


@pytest.mark.parametrize("report", ["job_profitability_detail", "job_estimates_vs_actuals_detail"])
async def test_detail_reports_need_a_customer_job_and_say_so_before_asking_quickbooks(call, replay, report):
    # Real QuickBooks (Enterprise 2024, #4) answers error 3150 'missing element: "ReportEntityFilter"'
    # for a detail report without one; the tool explains that instead of forwarding the error.
    result = await call("run_job_report", report=report, from_date="2024-01-01", to_date="2024-12-31")

    assert result.is_error
    message = " ".join(c.text for c in result.content)
    assert "customer_job" in message and "detail" in message and "summary" in message
    assert replay.sent == []


async def test_empty_report_is_one_total_row_of_zeros_not_an_error(call, replay):
    # Recorded from Enterprise 2024 (#4): a period with no activity still returns a TOTAL row.
    replay.respond_with("JobReportQueryRq", "JobReportQueryRq.empty.xml")

    result = await call(
        "run_job_report",
        report="job_profitability_detail",
        from_date="2031-01-01",
        to_date="2031-01-31",
        customer_job="Abercrombie, Kristy:Remodel Bathroom",
    )

    assert not result.is_error
    report = result.structured_content
    assert report["title"] == "Job Profitability Detail for Abercrombie, Kristy:Remodel Bathroom"
    assert report["subtitle"] == "January 2031"
    assert report["columns"] == ["Act. Cost", "Act. Revenue", "($) Diff."]
    assert report["rows"] == [
        {"kind": "total", "label": "TOTAL", "values": {"Act. Cost": "0.00", "Act. Revenue": "0.00", "($) Diff.": "0.00"}}
    ]


async def test_status_1_no_match_returns_no_rows_not_an_error(call, replay):
    replay.respond_with("JobReportQueryRq", "JobReportQueryRq.nomatch.xml")

    result = await call(
        "run_job_report",
        report="job_profitability_detail",
        from_date="2030-01-01",
        to_date="2030-01-31",
        customer_job="Nobody",
    )

    assert not result.is_error
    assert result.structured_content["rows"] == []


async def test_unknown_customer_job_is_reported_as_quickbooks_error_3120(call, replay):
    # Recorded from Enterprise 2024 (#4): QuickBooks answers 3120, not an empty report.
    replay.respond_with("JobReportQueryRq", "JobReportQueryRq.error3120.xml")

    result = await call(
        "run_job_report",
        report="job_profitability_detail",
        from_date="2000-01-01",
        to_date="2030-12-31",
        customer_job="No Such Customer:No Such Job",
    )

    assert result.is_error
    message = " ".join(c.text for c in result.content)
    assert "3120" in message and '"No Such Customer:No Such Job"' in message and "cannot be found" in message


async def test_estimates_vs_actuals_summary_keeps_both_diff_columns_distinct(call, replay):
    replay.respond_with("JobReportQueryRq", "JobReportQueryRq.estimates.xml")

    result = await call(
        "run_job_report", report="job_estimates_vs_actuals_summary", from_date="2000-01-01", to_date="2030-12-31"
    )

    report = result.structured_content
    assert report["title"] == "Job Estimates vs. Actuals Summary"
    assert report["subtitle"] == "January 2000 through December 2030"
    assert report["columns"] == ["Est. Cost", "Act. Cost", "($) Diff.", "Est. Revenue", "Act. Revenue", "($) Diff. (2)"]
    assert report["rows"][:3] == [
        {"kind": "header", "label": "Abercrombie, Kristy"},
        {
            "kind": "data",
            "label": "Remodel Bathroom",
            "name": "Abercrombie, Kristy:Remodel Bathroom",
            "values": {
                "Est. Cost": "3000.00",
                "Act. Cost": "5416.23",
                "($) Diff.": "2416.23",
                "Est. Revenue": "3114.00",
                "Act. Revenue": "6749.50",
                "($) Diff. (2)": "3635.50",
            },
        },
        {
            "kind": "subtotal",
            "label": "Total Abercrombie, Kristy",
            "name": "Abercrombie, Kristy",
            "values": {
                "Est. Cost": "3000.00",
                "Act. Cost": "5416.23",
                "($) Diff.": "2416.23",
                "Est. Revenue": "3114.00",
                "Act. Revenue": "6749.50",
                "($) Diff. (2)": "3635.50",
            },
        },
    ]
    assert [r["name"] for r in report["rows"] if r["kind"] == "data"] == [
        "Abercrombie, Kristy:Remodel Bathroom",
        "Cook, Brian:2nd story addition",
        "Cook, Brian:Kitchen",
        "Ecker Designs:Office Repairs",
        "Pretell Real Estate:75 Sunset Rd.",
    ]
    assert report["rows"][-1] == {
        "kind": "total",
        "label": "TOTAL",
        "values": {
            "Est. Cost": "55770.90",
            "Act. Cost": "34519.83",
            "($) Diff.": "-21251.07",
            "Est. Revenue": "61615.93",
            "Act. Revenue": "66284.79",
            "($) Diff. (2)": "4668.86",
        },
    }
    assert len(report["rows"]) == 14


async def test_estimates_vs_actuals_keeps_repeated_and_multi_row_column_titles_distinct(call, replay):
    # Real QuickBooks titles every column on one row; this hand-written report splits
    # "Est. Cost" over two title rows (listed out of order) and leaves cells empty.
    replay.respond_with("JobReportQueryRq", "JobReportQueryRq.estimates_multirow.xml")

    result = await call(
        "run_job_report", report="job_estimates_vs_actuals_summary", from_date="2024-01-01", to_date="2024-12-31"
    )

    report = result.structured_content
    assert report["columns"] == ["Est. Cost", "Act. Cost", "($) Diff.", "Est. Revenue", "Act. Revenue", "($) Diff. (2)"]
    assert report["rows"] == [
        {
            "kind": "data",
            "label": "Kitchen",
            "name": "Abercrombie, Kristy:Kitchen",
            "values": {"Est. Cost": "2000.00", "Act. Cost": "2520.00", "($) Diff.": "520.00", "Act. Revenue": "3111.28"},
        }
    ]
