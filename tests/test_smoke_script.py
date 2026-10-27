"""scripts/smoke_test.py, run against the Replay transport (the real run uses COM on the VM)."""

import pytest
import smoke_test

from qb_mcp.errors import TransportError, TransportFailure

pytestmark = pytest.mark.anyio


async def test_smoke_test_passes_and_shows_what_it_found(replay, capsys):
    code = await smoke_test.run(replay)

    out = capsys.readouterr().out
    assert code == 0
    assert "PASS qb_status" in out and "PASS list_vendors" in out
    assert "Rock Castle Construction" in out
    assert "FAIL" not in out


async def test_smoke_test_fails_with_the_reason_when_quickbooks_is_closed(replay, capsys):
    replay.fail_with(TransportError(TransportFailure.QB_NOT_RUNNING))

    code = await smoke_test.run(replay)

    out = capsys.readouterr().out
    assert code == 1
    assert "FAIL qb_status" in out and "FAIL list_vendors" in out
    assert "QuickBooks isn't running" in out


async def test_smoke_test_fails_when_only_the_vendor_query_fails(replay, capsys):
    replay.respond_with("VendorQueryRq", "InvoiceQueryRq.error3120.xml")

    code = await smoke_test.run(replay)

    out = capsys.readouterr().out
    assert code == 1
    assert "PASS qb_status" in out and "FAIL list_vendors" in out
    assert "error 3120" in out


def test_off_windows_it_says_so(monkeypatch, capsys):
    monkeypatch.setattr(smoke_test.sys, "platform", "darwin")

    assert smoke_test.main() == 1
    assert "only runs on Windows" in capsys.readouterr().out
