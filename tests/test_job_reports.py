import pytest

pytestmark = pytest.mark.anyio


async def test_job_profitability_summary_returns_columns_and_marked_rows(call):
    result = await call(
        "run_job_report", report="job_profitability_summary", from_date="2024-01-01", to_date="2024-12-31"
    )

    assert not result.is_error
    report = result.structured_content
    assert report["title"] == "Job Profitability Summary"
    assert report["subtitle"] == "January 1 through December 31, 2024"
    assert report["columns"] == ["Act. Cost", "Act. Revenue", "($) Diff."]
    assert report["rows"] == [
        {"kind": "header", "label": "Abercrombie, Kristy"},
        {
            "kind": "data",
            "label": "Kitchen",
            "name": "Abercrombie, Kristy:Kitchen",
            "values": {"Act. Cost": "2520.00", "Act. Revenue": "3111.28", "($) Diff.": "591.28"},
        },
        {
            "kind": "data",
            "label": "Remodel Bathroom",
            "name": "Abercrombie, Kristy:Remodel Bathroom",
            "values": {"Act. Cost": "5600.00", "Act. Revenue": "7633.28", "($) Diff.": "2033.28"},
        },
        {
            "kind": "subtotal",
            "label": "Total Abercrombie, Kristy",
            "name": "Abercrombie, Kristy",
            "values": {"Act. Cost": "8120.00", "Act. Revenue": "10744.56", "($) Diff.": "2624.56"},
        },
        {
            "kind": "total",
            "label": "TOTAL",
            "values": {"Act. Cost": "8120.00", "Act. Revenue": "10744.56", "($) Diff.": "2624.56"},
        },
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


@pytest.mark.parametrize("fixture", ["JobReportQueryRq.empty.xml", "JobReportQueryRq.nomatch.xml"])
async def test_empty_report_returns_no_rows_not_an_error(call, replay, fixture):
    replay.respond_with("JobReportQueryRq", fixture)

    result = await call(
        "run_job_report",
        report="job_profitability_detail",
        from_date="2030-01-01",
        to_date="2030-01-31",
        customer_job="Nobody",
    )

    assert not result.is_error
    assert result.structured_content["rows"] == []


async def test_estimates_vs_actuals_keeps_repeated_and_multi_row_column_titles_distinct(call, replay):
    replay.respond_with("JobReportQueryRq", "JobReportQueryRq.estimates.xml")

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
