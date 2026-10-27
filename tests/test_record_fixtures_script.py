"""scripts/record_fixtures.py, run against the Replay transport standing in for QuickBooks
(the real run uses COM on the VM). The Replay fixtures play the part of QuickBooks' answers."""

import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
import record_fixtures
from conftest import FIXTURES

from qb_mcp.transports.replay import ReplayTransport

pytestmark = pytest.mark.anyio

SAMPLE_FILE = r"C:\Users\Public\Documents\Intuit\QuickBooks\Sample Rock Castle Construction.QBW"
LIVE_FILE = r"C:\QB\Schreiber Excavating.QBW"

# One fixture per request type the tools send, plus a variant per extra job report.
EXPECTED_FILES = {
    "HostQueryRq.xml",
    "CompanyQueryRq.xml",
    "VendorQueryRq.xml",
    "CustomerQueryRq.xml",
    "ItemQueryRq.xml",
    "AccountQueryRq.xml",
    "BillQueryRq.xml",
    "InvoiceQueryRq.xml",
    "JobReportQueryRq.xml",
    "JobReportQueryRq.profitability_detail.xml",
    "JobReportQueryRq.estimates.xml",
    "JobReportQueryRq.estimates_detail.xml",
}


# Elements holding the made-up secrets in the hand-written fixtures.
SECRET_TAGS = "|".join(
    [
        "AccountNumber", "EIN", "SSN", "VendorTaxIdent", "ResaleNumber", "BankNumber", "CreditCardNumber",
        "ExpirationMonth", "ExpirationYear", "NameOnCard", "CreditCardAddress", "CreditCardPostalCode",
    ]
)


@pytest.fixture
def clean_quickbooks(tmp_path: Path) -> ReplayTransport:
    """QuickBooks with the sample file open and no sensitive values in any answer."""
    src = tmp_path / "quickbooks"
    shutil.copytree(FIXTURES, src)
    for path in src.glob("*.xml"):
        path.write_text(re.sub(rf"<({SECRET_TAGS})>[^<]*</\1>", r"<\1 />", path.read_text()))
    return ReplayTransport(src, company_file=SAMPLE_FILE)


@pytest.fixture
def out(tmp_path: Path) -> Path:
    return tmp_path / "recorded"


async def test_dry_run_lists_every_fixture_it_would_write_and_writes_nothing(clean_quickbooks, out, capsys):
    code = await record_fixtures.run(clean_quickbooks, out, dry_run=True)

    printed = capsys.readouterr().out
    assert code == 0
    for name in EXPECTED_FILES:
        assert f"WOULD WRITE {name}" in printed
    assert not out.exists() or not any(out.iterdir())


async def test_writes_one_replayable_fixture_per_request_type(clean_quickbooks, out):
    code = await record_fixtures.run(clean_quickbooks, out)

    assert code == 0
    assert {p.name for p in out.iterdir()} == EXPECTED_FILES
    vendors = ET.parse(out / "VendorQueryRq.xml").getroot().find("QBXMLMsgsRs/VendorQueryRs")
    assert [v.findtext("Name") for v in vendors] == ["Patton Hardware Supplies", "Timberloft Lumber"]
    # The recordings answer the tools just as QuickBooks did.
    replayed = await record_fixtures.run(ReplayTransport(out, company_file=SAMPLE_FILE), out, dry_run=True)
    assert replayed == 0


async def test_refuses_a_fixture_with_a_vendor_account_number_and_never_prints_it(clean_quickbooks, out, capsys):
    vendors = clean_quickbooks.fixtures_dir / "VendorQueryRq.xml"
    vendors.write_text(vendors.read_text().replace("<AccountNumber />", "<AccountNumber>RC-5543</AccountNumber>", 1))

    code = await record_fixtures.run(clean_quickbooks, out)

    printed = capsys.readouterr().out
    assert code == 1
    assert "REFUSED VendorQueryRq.xml" in printed and "VendorRet/AccountNumber" in printed
    assert "RC-5543" not in printed
    assert not (out / "VendorQueryRq.xml").exists()
    assert (out / "HostQueryRq.xml").exists()  # clean fixtures are still written


async def test_refuses_a_report_with_values_in_sensitive_columns(clean_quickbooks, out, capsys):
    clean_quickbooks.respond_with("JobReportQueryRq", "JobReportQueryRq.sensitive.xml")

    code = await record_fixtures.run(clean_quickbooks, out)

    printed = capsys.readouterr().out
    assert code == 1
    assert "REFUSED JobReportQueryRq.xml" in printed
    for title in ["Tax ID", "Account No.", "Credit Card No.", "Bank Acct. No."]:
        assert f"'{title}'" in printed
    assert "94-1234567" not in printed and "4111111111111111" not in printed
    assert not (out / "JobReportQueryRq.xml").exists()


async def test_refuses_to_record_from_a_company_file_that_isnt_the_sample(clean_quickbooks, out, capsys):
    live = ReplayTransport(clean_quickbooks.fixtures_dir, company_file=LIVE_FILE)

    code = await record_fixtures.run(live, out)

    assert code == 1
    assert "REFUSED" in capsys.readouterr().out
    assert live.sent == []  # not a single query reached QuickBooks
    assert not out.exists()


async def test_records_from_another_file_only_when_confirmed_not_live(clean_quickbooks, out):
    other = ReplayTransport(clean_quickbooks.fixtures_dir, company_file=LIVE_FILE)

    code = await record_fixtures.run(other, out, confirm_not_live=True)

    assert code == 0 and (out / "VendorQueryRq.xml").exists()


def test_off_windows_it_says_so_and_records_nothing(monkeypatch, out, capsys):
    monkeypatch.setattr(record_fixtures.sys, "platform", "darwin")

    assert record_fixtures.main(["--out", str(out)]) == 1
    assert "only runs on Windows" in capsys.readouterr().out
    assert not out.exists()
