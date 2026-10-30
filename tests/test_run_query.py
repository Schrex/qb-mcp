import xml.etree.ElementTree as ET

import pytest

from qb_mcp.transports.replay import xsd_dir_from_env

pytestmark = pytest.mark.anyio

INVOICES = "<InvoiceQueryRq><MaxReturned>50</MaxReturned></InvoiceQueryRq>"
# The default fixture holds 11 Invoices recorded from Rock Castle Construction (Intuit's sample company).
ROCK_CASTLE_INVOICES = ["975", "978", "985", "987", "990", "991", "992", "993", "994", "1001", "1002"]


async def test_run_query_returns_parsed_results_for_a_query_element(call):
    result = await call("run_query", query=INVOICES)

    assert not result.is_error
    out = result.structured_content
    assert [i["RefNumber"] for i in out["results"]] == ROCK_CASTLE_INVOICES
    assert out["results"][0]["CustomerRef"] == {"ListID": "800000B8-1197707458", "FullName": "Prentice, Adelaide:Bay Window"}
    assert out["count"] == 11
    assert out["more_available"] is False


async def test_run_query_sends_the_element_inside_the_servers_own_envelope(call, replay):
    await call(
        "run_query",
        query='<InvoiceQueryRq requestID="99" metaData="NoMetaData"><RefNumber>A&amp;B</RefNumber></InvoiceQueryRq>',
    )

    sent = ET.fromstring(replay.sent[-1].encode())
    msgs = sent.find("QBXMLMsgsRq")
    assert msgs.get("onError") == "stopOnError"
    (rq,) = list(msgs)
    assert rq.tag == "InvoiceQueryRq"
    assert rq.attrib == {"requestID": "1", "metaData": "NoMetaData"}
    assert rq.findtext("RefNumber") == "A&B"


async def test_run_query_strips_sensitive_fields(call, replay):
    replay.respond_with("InvoiceQueryRq", "InvoiceQueryRq.sensitive.xml")

    text = (await call("run_query", query=INVOICES)).content[0].text

    assert "4111111111111111" not in text
    assert "CreditCard" not in text and "NameOnCard" not in text


async def test_run_query_caps_results_at_the_limit(call):
    out = (await call("run_query", query=INVOICES, limit=1)).structured_content

    assert [i["RefNumber"] for i in out["results"]] == ["975"]
    assert out["count"] == 1
    assert out["more_available"] is True
    assert "more exist" in out["note"]


async def test_run_query_reports_quickbooks_errors_clearly(call, replay):
    replay.respond_with("InvoiceQueryRq", "InvoiceQueryRq.error3120.xml")

    result = await call("run_query", query="<InvoiceQueryRq><RefNumber>Nope</RefNumber></InvoiceQueryRq>")

    assert result.is_error
    text = result.content[0].text
    assert "3120" in text and "cannot be found" in text


@pytest.mark.skipif(not list(xsd_dir_from_env().glob("qbxmlops*.xsd")), reason="qbXML XSD not available locally")
async def test_run_query_reports_schema_invalid_queries_clearly(call):
    out_of_order = "<InvoiceQueryRq><IncludeLineItems>true</IncludeLineItems><MaxReturned>5</MaxReturned></InvoiceQueryRq>"

    result = await call("run_query", query=out_of_order)

    assert result.is_error
    text = result.content[0].text
    assert "schema validation" in text
    assert not text.startswith("Couldn't talk to QuickBooks")
    assert "doesn't match the qbXML schema" in text


async def test_run_query_description_steers_to_dedicated_tools(client):
    tool = next(t for t in (await client.list_tools()).tools if t.name == "run_query")
    assert "Prefer the dedicated tools" in tool.description
    assert "qbXML 16.0" in tool.description
    assert "bills" not in tool.description.lower()  # query_bills covers them


async def test_run_query_description_lists_the_allowed_query_types(client):
    tool = next(t for t in (await client.list_tools()).tools if t.name == "run_query")
    for tag in ["InvoiceQueryRq", "CheckQueryRq", "PurchaseOrderQueryRq", "EstimateQueryRq", "SalesReceiptQueryRq"]:
        assert tag in tool.description
    assert "EmployeeQueryRq" not in tool.description


# --- Company credit card charges and credits: fuel and parts on cards are job cost (#29) ---

CARD_CHARGES = "<CreditCardChargeQueryRq><MaxReturned>101</MaxReturned></CreditCardChargeQueryRq>"
CARD_CREDITS = "<CreditCardCreditQueryRq><MaxReturned>101</MaxReturned></CreditCardCreditQueryRq>"


async def test_run_query_returns_credit_card_charges_with_vendor_job_account_amount_and_memo(call):
    result = await call("run_query", query=CARD_CHARGES)

    assert not result.is_error
    out = result.structured_content
    assert out["count"] == 2
    fuel, parts = out["results"]
    assert fuel["PayeeEntityRef"]["FullName"] == "Bayshore CalOil Service"
    assert fuel["AccountRef"]["FullName"] == "QuickBooks Credit Card"
    assert fuel["Amount"] == "86.50"
    assert fuel["Memo"] == "Fuel for truck"
    (expense,) = fuel["ExpenseLineRet"]
    assert expense["AccountRef"]["FullName"] == "Job Expenses:Equipment Rental"
    assert expense["CustomerRef"]["FullName"] == "Burch, Jason:Room Addition"
    assert expense["Amount"] == "86.50"
    (item,) = parts["ItemLineRet"]
    assert item["ItemRef"]["FullName"] == "Hardware:Doorknobs Std"
    assert item["CustomerRef"]["FullName"] == "Abercrombie, Kristy:Remodel Bathroom"
    assert parts["Memo"] == "Doorknobs"


async def test_run_query_strips_card_data_from_credit_card_charges(call, replay):
    replay.respond_with("CreditCardChargeQueryRq", "CreditCardChargeQueryRq.sensitive.xml")

    result = await call("run_query", query=CARD_CHARGES)

    assert not result.is_error
    text = result.content[0].text
    assert "Bayshore CalOil Service" in text  # the record itself still comes back
    for card_data in ["4111111111111111", "5500005555555559", "Rock Castle Owner", "94326-1111", "TX-88213"]:
        assert card_data not in text
    for tag in ["CreditCardTxnInfo", "CreditCardNumber", "ExpirationYear", "NameOnCard", "CreditCardTransID"]:
        assert tag not in text


# --- Refusals: nothing caller-supplied but one plain *QueryRq ever reaches QuickBooks ---

XXE = (
    '<!DOCTYPE InvoiceQueryRq [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
    "<InvoiceQueryRq><RefNumber>&x;</RefNumber></InvoiceQueryRq>"
)
BILLION_LAUGHS = (
    '<!DOCTYPE InvoiceQueryRq [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
    "<InvoiceQueryRq><RefNumber>&b;</RefNumber></InvoiceQueryRq>"
)

REFUSED = {
    # non-query requests
    "add": "<InvoiceAddRq><InvoiceAdd><CustomerRef><FullName>Acme</FullName></CustomerRef></InvoiceAdd></InvoiceAddRq>",
    "mod": "<VendorModRq><VendorMod><ListID>1</ListID><EditSequence>1</EditSequence></VendorMod></VendorModRq>",
    "txn delete": "<TxnDelRq><TxnDelType>Invoice</TxnDelType><TxnID>1</TxnID></TxnDelRq>",
    "list delete": "<ListDelRq><ListDelType>Vendor</ListDelType><ListID>1</ListID></ListDelRq>",
    "void": "<TxnVoidRq><TxnVoidType>Check</TxnVoidType><TxnID>1</TxnID></TxnVoidRq>",
    "response element": "<InvoiceQueryRs/>",
    "query lookalike suffix": "<InvoiceQueryRqAdd/>",
    "lowercase": "<invoicequeryrq/>",
    "bare QueryRq": "<QueryRq/>",
    "namespaced": '<x:InvoiceQueryRq xmlns:x="urn:evil"/>',
    # multiple elements / trailing content
    "two queries": "<InvoiceQueryRq/><CheckQueryRq/>",
    "query then write": "<InvoiceQueryRq/><InvoiceAddRq/>",
    "trailing text": "<InvoiceQueryRq/>junk",
    # envelope injection
    "full envelope": '<QBXML><QBXMLMsgsRq onError="continueOnError"><InvoiceAddRq/></QBXMLMsgsRq></QBXML>',
    "message set": "<QBXMLMsgsRq><InvoiceQueryRq/></QBXMLMsgsRq>",
    "close and reopen message set": "<InvoiceQueryRq/></QBXMLMsgsRq><QBXMLMsgsRq><InvoiceAddRq/>",
    "write nested in query": "<InvoiceQueryRq><InvoiceAddRq><InvoiceAdd/></InvoiceAddRq></InvoiceQueryRq>",
    "query nested in query": "<InvoiceQueryRq><CheckQueryRq/></InvoiceQueryRq>",
    "message set nested in query": "<InvoiceQueryRq><QBXMLMsgsRq><BillAddRq/></QBXMLMsgsRq></InvoiceQueryRq>",
    # markup the server never needs
    "xml declaration": '<?xml version="1.0"?><InvoiceQueryRq/>',
    "qbxml processing instruction": '<?qbxml version="2.0"?><InvoiceQueryRq/>',
    "inner processing instruction": "<InvoiceQueryRq><?evil?></InvoiceQueryRq>",
    "comment": "<InvoiceQueryRq><!-- <InvoiceAddRq/> --></InvoiceQueryRq>",
    "cdata": "<InvoiceQueryRq><RefNumber><![CDATA[1042]]></RefNumber></InvoiceQueryRq>",
    "doctype xxe": XXE,
    "billion laughs": BILLION_LAUGHS,
    "external dtd": '<!DOCTYPE x SYSTEM "http://evil.example/x.dtd"><InvoiceQueryRq/>',
    # attributes that change how QuickBooks processes the request
    "onError on query": '<InvoiceQueryRq onError="continueOnError"/>',
    "iterator": '<InvoiceQueryRq iterator="Start"/>',
    "attribute on child": '<InvoiceQueryRq><MaxReturned onError="continueOnError">5</MaxReturned></InvoiceQueryRq>',
    # malformed
    "empty": "",
    "whitespace": "   ",
    "not xml": "show me invoices",
    "unclosed": "<InvoiceQueryRq>",
    "mismatched": "<InvoiceQueryRq></CheckQueryRq>",
}


@pytest.mark.parametrize("query", REFUSED.values(), ids=REFUSED.keys())
async def test_run_query_refuses_anything_but_one_plain_query_element(call, replay, query):
    result = await call("run_query", query=query)

    assert result.is_error
    assert "Refused" in result.content[0].text
    assert replay.sent == []


# --- Allow-list: only query types the budgeting/job-cost use case needs (#12) ---

OFF_LIST = [
    "EmployeeQueryRq",
    "EmployeeDefaultQueryRq",
    "PaycheckQueryRq",
    "PayrollItemWageQueryRq",
    "PayrollItemQueryRq",
    "PayrollSummaryReportQueryRq",
    "BillQueryRq",  # query_bills covers it
    "VendorQueryRq",  # list_vendors covers it
    "TransactionQueryRq",  # lists paychecks
    "GeneralDetailReportQueryRq",  # can list paychecks per employee
]


@pytest.mark.parametrize("tag", OFF_LIST)
async def test_run_query_refuses_query_types_off_the_allow_list(call, replay, tag):
    result = await call("run_query", query=f"<{tag}/>")

    assert result.is_error
    text = result.content[0].text
    assert "Refused" in text and tag in text
    assert "InvoiceQueryRq" in text  # names what is allowed
    assert replay.sent == []
