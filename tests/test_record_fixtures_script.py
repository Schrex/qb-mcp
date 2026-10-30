"""scripts/record_fixtures.py, run against the Replay transport standing in for QuickBooks
(the real run uses COM on the VM). The Replay fixtures play the part of QuickBooks' answers."""

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
    "CreditCardChargeQueryRq.xml",
    "CreditCardCreditQueryRq.xml",
    "JobReportQueryRq.xml",
    "JobReportQueryRq.profitability_detail.xml",
    "JobReportQueryRq.estimates.xml",
    "JobReportQueryRq.estimates_detail.xml",
    "PreferencesQueryRq.xml",  # the trust stamp's closing-date read (#26)
}


@pytest.fixture
def clean_quickbooks(tmp_path: Path) -> ReplayTransport:
    """QuickBooks with the sample file open. Its default answers (the base fixtures) hold no
    sensitive values; only the `*.sensitive.xml` ones do."""
    src = tmp_path / "quickbooks"
    shutil.copytree(FIXTURES, src)
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
    clean_quickbooks.respond_with("VendorQueryRq", "VendorQueryRq.sensitive.xml")

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


@pytest.mark.parametrize("rq_type", ["CreditCardChargeQueryRq", "CreditCardCreditQueryRq"])
async def test_refuses_a_fixture_with_card_data_and_never_prints_it(clean_quickbooks, out, capsys, rq_type):
    sensitive = {
        "CreditCardChargeQueryRq": "CreditCardChargeQueryRq.sensitive.xml",
        "CreditCardCreditQueryRq": "CreditCardCreditQueryRq.sensitive.xml",
    }
    clean_quickbooks.respond_with(rq_type, sensitive[rq_type])

    code = await record_fixtures.run(clean_quickbooks, out)

    printed = capsys.readouterr().out
    assert code == 1
    assert f"REFUSED {rq_type}.xml" in printed and "CreditCardTxnInputInfo/CreditCardNumber" in printed
    assert "4111111111111111" not in printed and "Rock Castle Owner" not in printed
    assert not (out / f"{rq_type}.xml").exists()


@pytest.mark.parametrize(
    ("path", "is_sample"),
    [
        (SAMPLE_FILE, True),
        # What the Enterprise 2024 trial's "see a sample Company File" flow writes (verified, #4).
        (r"C:\Users\Sumanth\Desktop\sample_product-based business.QBW", True),
        (r"C:\QB\sample_service-based business.qbw", True),
        (r"C:\QB\Schreiber Excavating.QBW", False),
        (r"C:\QB\samples\Schreiber Excavating.QBW", False),  # only the file name counts
        (r"C:\QB\resample_data.QBW", False),
    ],
)
def test_recognises_rock_castle_and_intuit_sample_file_names(path, is_sample):
    assert record_fixtures.is_sample_company_file(path) is is_sample


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


async def test_leaves_every_hand_written_variant_untouched(clean_quickbooks):
    out = clean_quickbooks.fixtures_dir
    before = {p.name: p.read_bytes() for p in out.glob("*.xml") if p.name not in EXPECTED_FILES}
    assert any(name.endswith(".sensitive.xml") for name in before)

    assert await record_fixtures.run(clean_quickbooks, out) == 0

    assert {p.name: p.read_bytes() for p in out.glob("*.xml") if p.name not in EXPECTED_FILES} == before


async def test_never_records_a_hand_written_only_variant(clean_quickbooks, out, monkeypatch, capsys):
    calls = [*record_fixtures.TOOL_CALLS, record_fixtures.ToolCall("list_vendors", {}, "sensitive")]
    monkeypatch.setattr(record_fixtures, "TOOL_CALLS", calls)

    code = await record_fixtures.run(clean_quickbooks, out)

    assert code == 1
    assert "REFUSED VendorQueryRq.sensitive.xml" in capsys.readouterr().out
    assert not (out / "VendorQueryRq.sensitive.xml").exists()


def test_off_windows_it_says_so_and_records_nothing(monkeypatch, out, capsys):
    monkeypatch.setattr(record_fixtures.sys, "platform", "darwin")

    assert record_fixtures.main(["--out", str(out)]) == 1
    assert "only runs on Windows" in capsys.readouterr().out
    assert not out.exists()
