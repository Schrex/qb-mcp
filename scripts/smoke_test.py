"""Smoke test against real QuickBooks (Windows VM, COM transport).

    uv run python scripts/smoke_test.py

Calls the real server tools in-process, as Claude would: `qb_status`, then one
`list_vendors`. Prints PASS/FAIL per step; exit code 0 if every step passed, else 1.
"""

import asyncio
import json
import sys
from typing import Any

from mcp import Client

from qb_mcp.server import build_server
from qb_mcp.transport import Transport

VENDORS_SHOWN = 5


async def run(transport: Transport) -> int:
    """Run every step through `transport`; print a report; return the exit code."""
    async with Client(build_server(transport)) as client:
        results = [await _qb_status(client), await _list_vendors(client)]
    passed = all(results)
    print("\nSMOKE TEST PASSED" if passed else "\nSMOKE TEST FAILED")
    return 0 if passed else 1


async def _qb_status(client: Client) -> bool:
    result = await client.call_tool("qb_status", {})
    status: dict[str, Any] = result.structured_content or {}
    if result.is_error or not status.get("reachable"):
        return _report(False, "qb_status", status.get("error") or _text(result))
    return _report(
        True,
        "qb_status",
        f"{status['product']} (version {status['version']})\n"
        f"  Company: {status['company_name']}\n"
        f"  Company File: {status['company_file']}\n"
        f"  qbXML versions: {', '.join(status['supported_qbxml_versions'])}",
    )


async def _list_vendors(client: Client) -> bool:
    result = await client.call_tool("list_vendors", {"limit": VENDORS_SHOWN})
    if result.is_error:
        return _report(False, "list_vendors", _text(result))
    out = result.structured_content or {}
    names = [v.get("name", "?") for v in out.get("results", [])]
    more = " (more available)" if out.get("more_available") else ""
    lines = "".join(f"\n  - {n}" for n in names)
    return _report(True, "list_vendors", f"{out.get('count', 0)} Vendors returned{more}{lines}")


def _report(passed: bool, step: str, detail: str) -> bool:
    print(f"{'PASS' if passed else 'FAIL'} {step}\n  {detail}")
    return passed


def _text(result: Any) -> str:
    return " ".join(getattr(c, "text", "") for c in result.content) or json.dumps(result.structured_content)


def main() -> int:
    if sys.platform != "win32":
        print(
            "smoke_test.py only runs on Windows, on the PC (or VM) where QuickBooks Desktop is "
            "installed and a Company File is open. Nothing was tested."
        )
        return 1
    from qb_mcp.transports.com import ComTransport

    return asyncio.run(run(ComTransport()))


if __name__ == "__main__":
    sys.exit(main())
