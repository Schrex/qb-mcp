"""COM transport pieces that can run off Windows: HRESULT -> cause mapping,
and the transport itself over a fake request processor."""

import sys
import threading

import pytest

from qb_mcp.errors import TransportError, TransportFailure
from qb_mcp.transports.com import APP_NAME, ComTransport, error_from_hresult


@pytest.mark.parametrize(
    ("hresult", "cause"),
    [
        (0x80040408, TransportFailure.QB_NOT_RUNNING),
        (0x80040416, TransportFailure.QB_NOT_RUNNING),
        (0x80040417, TransportFailure.NO_COMPANY_FILE),
        (0x80040418, TransportFailure.NOT_AUTHORIZED),
        (0x8004041A, TransportFailure.NOT_AUTHORIZED),
        (0x8004041D, TransportFailure.NOT_AUTHORIZED),
        (0x80040420, TransportFailure.NOT_AUTHORIZED),
        (0x80040414, TransportFailure.OTHER),
        (0x80040400, TransportFailure.INVALID_REQUEST),
    ],
)
def test_known_hresults_map_to_failure_causes(hresult, cause):
    assert error_from_hresult(hresult, "qb text").cause is cause


def test_signed_hresults_are_normalized():
    # pywin32 reports HRESULTs as signed ints.
    assert error_from_hresult(0x80040417 - 2**32, "").cause is TransportFailure.NO_COMPANY_FILE


def test_unknown_hresult_keeps_code_and_quickbooks_message():
    err = error_from_hresult(0x8004BEEF, "Something odd happened.")

    assert isinstance(err, TransportError)
    assert err.cause is TransportFailure.OTHER
    assert "0x8004BEEF" in str(err) and "Something odd happened." in str(err)


def test_modal_dialog_hresult_tells_user_to_close_it():
    assert "dialog" in str(error_from_hresult(0x80040414, "")).lower()


def test_importing_com_transport_does_not_import_windows_modules():
    assert "pythoncom" not in sys.modules and "win32com" not in sys.modules


# --- ComTransport over a fake request processor ----------------------------


class FakeComError(Exception):
    """Shaped like pywintypes.com_error: (hresult, text, excepinfo, argerror)."""


DISP_E_EXCEPTION = 0x80020009 - 2**32


class FakeRequestProcessor:
    def __init__(self, com: "FakeCom"):
        self.com = com

    def _record(self, name: str):
        self.com.calls.append((name, threading.get_ident()))
        if name == self.com.fail_on:
            excepinfo = (0, "QBXMLRP2", self.com.fail_text, None, 0, self.com.fail_code)
            raise FakeComError(DISP_E_EXCEPTION, "Exception occurred.", excepinfo, None)

    def OpenConnection2(self, app_id, app_name, conn_type):
        self.com.app_names.append(app_name)
        self._record("OpenConnection2")

    def BeginSession(self, path, mode):
        self._record("BeginSession")
        return "ticket-1"

    def ProcessRequest(self, ticket, request):
        self._record("ProcessRequest")
        return f"<QBXML>{request}</QBXML>"

    def GetCurrentCompanyFileName(self, ticket):
        self._record("GetCurrentCompanyFileName")
        return r"C:\QB\Schreiber.QBW"

    def EndSession(self, ticket):
        self._record("EndSession")

    def CloseConnection(self):
        self._record("CloseConnection")


class FakeCom:
    com_error = FakeComError

    def __init__(self, fail_on: str = "", fail_code: int = 0, fail_text: str = ""):
        self.calls: list[tuple[str, int]] = []
        self.app_names: list[str] = []
        self.initialized_threads: list[int] = []
        self.fail_on, self.fail_code, self.fail_text = fail_on, fail_code, fail_text

    def co_initialize(self):
        self.initialized_threads.append(threading.get_ident())

    def dispatch(self):
        return FakeRequestProcessor(self)


@pytest.mark.anyio
async def test_com_transport_runs_every_call_on_one_com_initialized_thread():
    com = FakeCom()
    transport = ComTransport(com=com)

    assert await transport.send("<Q/>") == "<QBXML><Q/></QBXML>"
    assert await transport.company_file() == r"C:\QB\Schreiber.QBW"

    threads = {tid for _, tid in com.calls}
    assert threads == set(com.initialized_threads) and len(threads) == 1
    assert threading.get_ident() not in threads


@pytest.mark.anyio
async def test_com_transport_opens_and_closes_a_session_per_request_under_fixed_app_name():
    com = FakeCom()
    await ComTransport(com=com).send("<Q/>")

    assert [name for name, _ in com.calls] == [
        "OpenConnection2",
        "BeginSession",
        "ProcessRequest",
        "EndSession",
        "CloseConnection",
    ]
    assert com.app_names == [APP_NAME] == ["Schreiber QuickBooks Lookup"]


@pytest.mark.anyio
async def test_com_errors_become_clear_transport_errors_and_connection_is_closed():
    com = FakeCom(fail_on="BeginSession", fail_code=0x80040417 - 2**32, fail_text="Company file not open.")

    with pytest.raises(TransportError) as info:
        await ComTransport(com=com).send("<Q/>")

    assert info.value.cause is TransportFailure.NO_COMPANY_FILE
    assert "Company file not open." in str(info.value)
    assert com.calls[-1][0] == "CloseConnection"


@pytest.mark.anyio
async def test_failure_while_closing_does_not_hide_the_real_error():
    class FlakyCloseCom(FakeCom):
        def dispatch(self):
            rp = super().dispatch()

            def close():
                raise FakeComError(-1, "close failed", None, None)

            rp.CloseConnection = close
            return rp

    com = FlakyCloseCom(fail_on="ProcessRequest", fail_code=0x80040414 - 2**32)

    with pytest.raises(TransportError, match="dialog"):
        await ComTransport(com=com).send("<Q/>")
