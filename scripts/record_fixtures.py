"""Record real QuickBooks responses as Replay fixtures (Windows VM, COM transport).

    uv run python scripts/record_fixtures.py [--out tests/fixtures/replay] [--dry-run]
                                             [--i-confirm-not-live-data]

Calls the real server tools in-process (so the requests are exactly the ones the
tools build), captures each raw qbXML response under the server's parsing, and
writes one fixture per `*Rs`: `<RequestType>[.<variant>].xml`.

Safety:
- Refuses to record at all unless the open Company File looks like the Rock Castle
  sample: "rock castle" in the file name, or Intuit's `sample_*` naming
  (`--i-confirm-not-live-data` overrides). Never record from a live file.
- Refuses to write any fixture holding a non-empty sensitive value: an element on the
  server's deny-list (`output.is_sensitive`, incl. a Vendor's AccountNumber), a payee
  address (`output.is_payee_address`: a fixture can't show the payee isn't an Employee) or a report
  column titled like one (`reports.SENSITIVE_COLUMN_TITLE`). It lists what it found
  (element or column names, never the values).
- Writes only `<RequestType>.xml` and the variants named in TOOL_CALLS, never a
  `*.sensitive.xml` fixture: those are hand-written and hold the made-up sensitive values
  the stripping tests need. Other hand-written variants are left untouched.
Exit code 0 only if every fixture was written (or would be, with `--dry-run`).
"""

import argparse
import asyncio
import ntpath
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import Client

from qb_mcp.output import is_payee_address, is_sensitive
from qb_mcp.reports import SENSITIVE_COLUMN_TITLE
from qb_mcp.server import build_server
from qb_mcp.transport import Transport

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "replay"
SAMPLE_COMPANY_FILE = "rock castle"  # in the file name, any case
# Intuit names the shipped sample files `sample_<kind> business.qbw` (the Enterprise 2024
# trial writes Rock Castle as `sample_product-based business.QBW`, verified in #4).
SAMPLE_FILE_PREFIX = "sample_"
REPORT_PERIOD = {"from_date": "2000-01-01", "to_date": "2030-12-31"}
# Detail job reports need one Customer:Job (QuickBooks error 3150 otherwise); a Rock Castle job.
DETAIL_JOB = {**REPORT_PERIOD, "customer_job": "Abercrombie, Kristy:Remodel Bathroom"}
LIMIT = 25
HAND_WRITTEN_ONLY = "sensitive"  # variant never recorded: `<RequestType>.sensitive.xml`


@dataclass(frozen=True)
class ToolCall:
    tool: str
    args: dict[str, Any]
    variant: str | None = None  # fixture `<RequestType>.<variant>.xml`; None -> `<RequestType>.xml`


# Every request type the tools send. get_company_info sends CompanyQueryRq, recorded via qb_status.
TOOL_CALLS = [
    ToolCall("qb_status", {}),
    ToolCall("list_vendors", {"status": "all", "limit": LIMIT}),
    ToolCall("list_customer_jobs", {"status": "all", "limit": LIMIT}),
    ToolCall("list_items", {"status": "all", "limit": LIMIT}),
    ToolCall("list_accounts", {"status": "all", "limit": LIMIT}),
    ToolCall("query_bills", {"limit": 10}),
    ToolCall("run_query", {"query": "<InvoiceQueryRq><MaxReturned>11</MaxReturned></InvoiceQueryRq>", "limit": 10}),
    ToolCall("run_query", {"query": "<CreditCardChargeQueryRq><MaxReturned>11</MaxReturned></CreditCardChargeQueryRq>", "limit": 10}),
    ToolCall("run_query", {"query": "<CreditCardCreditQueryRq><MaxReturned>11</MaxReturned></CreditCardCreditQueryRq>", "limit": 10}),
    ToolCall("run_job_report", {"report": "job_profitability_summary", **REPORT_PERIOD}),
    ToolCall("run_job_report", {"report": "job_profitability_detail", **DETAIL_JOB}, "profitability_detail"),
    ToolCall("run_job_report", {"report": "job_estimates_vs_actuals_summary", **REPORT_PERIOD}, "estimates"),
    ToolCall("run_job_report", {"report": "job_estimates_vs_actuals_detail", **DETAIL_JOB}, "estimates_detail"),
]


class RecordingTransport:
    """Passes everything through to the real transport, keeping each raw response."""

    def __init__(self, inner: Transport):
        self.inner = inner
        self.responses: list[str] = []

    async def send(self, request: str) -> str:
        response = await self.inner.send(request)
        self.responses.append(response)
        return response

    async def company_file(self) -> str:
        return await self.inner.company_file()


def is_sample_company_file(path: str) -> bool:
    name = ntpath.basename(path).lower()
    return SAMPLE_COMPANY_FILE in name or name.startswith(SAMPLE_FILE_PREFIX)


def sensitive_findings(rs: ET.Element) -> list[str]:
    """What sensitive values `rs` holds, by name and count (never the values themselves)."""
    found: Counter[str] = Counter()
    for parent in rs.iter():
        for child in parent:
            if (is_sensitive(child.tag, parent.tag) or is_payee_address(child.tag, parent.tag)) and "".join(
                child.itertext()
            ).strip():
                found[f"{parent.tag}/{child.tag}"] += 1
    for report in rs.iter("ReportRet"):
        titles = {d.get("colID"): _column_title(d) for d in report.iter("ColDesc")}
        sensitive = {col: t for col, t in titles.items() if SENSITIVE_COLUMN_TITLE.search(t)}
        for cell in report.iter("ColData"):
            if cell.get("colID") in sensitive and (cell.get("value") or "").strip():
                found[f"report column '{sensitive[cell.get('colID')]}'"] += 1
    return [f"{what} ({n} non-empty)" for what, n in found.items()]


def _column_title(col_desc: ET.Element) -> str:
    parts = sorted(col_desc.iter("ColTitle"), key=lambda t: int(t.get("titleRow", 0)))
    return " ".join(t.get("value") for t in parts if t.get("value"))


def fixture_document(rs: ET.Element) -> str:
    """A Replay fixture: a QBXML response document holding just `rs`."""
    body = ET.tostring(rs, encoding="unicode").strip()
    return f'<?xml version="1.0" ?>\n<QBXML>\n<QBXMLMsgsRs>\n{body}\n</QBXMLMsgsRs>\n</QBXML>\n'


async def run(
    transport: Transport, out: Path, dry_run: bool = False, confirm_not_live: bool = False
) -> int:
    """Record every request type through `transport` into `out`; print a report; return the exit code."""
    company_file = await transport.company_file()
    print(f"Company File: {company_file}")
    if not is_sample_company_file(company_file) and not confirm_not_live:
        print(
            "REFUSED: the open Company File doesn't look like the Rock Castle sample. Recordings must "
            "never come from live data. Open the sample file, or pass --i-confirm-not-live-data if "
            "this really is a test file. Nothing was recorded."
        )
        return 1

    recorder = RecordingTransport(transport)
    fixtures: dict[str, ET.Element] = {}
    failed = False
    async with Client(build_server(recorder)) as client:
        for call in TOOL_CALLS:
            recorder.responses.clear()
            result = await client.call_tool(call.tool, call.args)
            content = result.structured_content or {}
            if result.is_error or content.get("reachable") is False:
                detail = content.get("error") or " ".join(getattr(c, "text", "") for c in result.content)
                print(f"ERROR {call.tool} {call.args}: {detail}")
                failed = True
                continue
            for response in recorder.responses:
                for msgs in ET.fromstring(response.encode("utf-8")):
                    for rs in msgs:
                        rq_type = rs.tag.removesuffix("Rs") + "Rq"
                        name = f"{rq_type}.{call.variant}.xml" if call.variant else f"{rq_type}.xml"
                        fixtures.setdefault(name, rs)

    for name, rs in fixtures.items():
        findings = sensitive_findings(rs)
        if name.endswith(f".{HAND_WRITTEN_ONLY}.xml"):
            print(f"REFUSED {name}: *.{HAND_WRITTEN_ONLY}.xml fixtures are hand-written, never recorded")
            failed = True
        elif findings:
            print(f"REFUSED {name}: sensitive values found: {'; '.join(findings)}")
            failed = True
        elif dry_run:
            print(f"WOULD WRITE {name}")
        else:
            out.mkdir(parents=True, exist_ok=True)
            (out / name).write_text(fixture_document(rs), encoding="utf-8")
            print(f"WROTE {out / name}")

    print("\nRECORDING INCOMPLETE (see ERROR/REFUSED above)" if failed else "\nRECORDING OK")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="fixtures directory (default: %(default)s)")
    parser.add_argument("--dry-run", action="store_true", help="query and check, but write nothing")
    parser.add_argument(
        "--i-confirm-not-live-data",
        action="store_true",
        help="record even though the open Company File isn't named like the Rock Castle sample",
    )
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        print(
            "record_fixtures.py only runs on Windows, on the VM where QuickBooks Desktop is installed "
            "with the Rock Castle sample Company File open. Nothing was recorded."
        )
        return 1
    from qb_mcp.transports.com import ComTransport

    return asyncio.run(run(ComTransport(), args.out, args.dry_run, args.i_confirm_not_live_data))


if __name__ == "__main__":
    sys.exit(main())
