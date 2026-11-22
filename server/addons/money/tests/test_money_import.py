"""Money statement files: CSV / OFX / QIF parsing, column mapping, de-duplication and the import API (docs/money.md, "Importing statement files").
Run: bin/test test_money_import

Every file is made up. Covers: the value readers (amounts with either decimal
mark, brackets, trailing minus, CR / DR; dates in ISO, OFX, day- / month-first
and month names) and the order / decimal guesses; format sniffing; CSV layout
detection (a header after a preamble, debit / credit columns, no header at
all), a mapping given, negated and remembered per account; OFX 1.x SGML and
2.x XML (bank and card, FITIDs, the ledger balance); QIF (bank, card, a
two-digit year); wrong files refused with a clear message (a PDF, plain text,
an empty file, a CSV without dates or amounts, an investment statement, a
file over the limit); importing into a new manual account (its balance from
the file) and into a synced account; re-importing the same file adds nothing,
an overlapping one only what is new, two equal rows on one day stay two, a
row the provider already brought is not added again; the user's merchant rule
applied and the new rows handed to classification; two users with ``money``
isolated both ways (another user's account → 404) and one without
(403 ``not_connected``).
"""

import asyncio
import datetime
import json
import os
import sys
import uuid
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("money_import_test")
ROOT = str(testenv.SERVER)
from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()
os.environ["API_TOKEN"] = "money-import-test-env-token"  # a test value: with it set, DB tokens are honoured
os.environ["TIMEZONE"] = "UTC"
os.environ["OWNER_NAME"] = "Robin"
sys.path.insert(0, ROOT)

from core import db, users  # noqa: E402

db.migrate()
users.bootstrap()
from addons import bootstrap as addons_bootstrap  # noqa: E402
addons_bootstrap.bootstrap()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import moneyfakes  # noqa: E402  (the made-up provider: moneyfakes.py)

import aiohttp  # noqa: E402
from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from core.auth import setup_auth  # noqa: E402
from core.addons import store as connections  # noqa: E402
from addons.money import api as money_api, classify, imports, store  # noqa: E402

results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: Any = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


ROBIN = users.first_admin()
assert ROBIN is not None
BOB = users.create("Bob")
CARL = users.create("Carl")
connections.connect(ROBIN, "money")
connections.connect(BOB, "money")
connections.connect(ROBIN, moneyfakes.PROVIDER)
TOKENS = {u.id: users.issue_token(u.id, "test")[0] for u in (ROBIN, BOB, CARL)}
STAMP = "2026-11-01T00:00:00+00:00"

# --- The files (all made up) ----------------------------------------------------------------------

CSV_US = b"""Date,Description,Amount,Balance
11/03/2026,Corner Grocery,-42.50,"1,957.50"
11/04/2026,Payroll Example Co,"2,500.00","4,457.50"
11/05/2026,Coffee Spot,-3.75,"4,453.75"
11/05/2026,Coffee Spot,-3.75,"4,450.00"
11/14/2026,Corner Grocery,-18.20,"4,431.80"
"""

CSV_US_LATER = b"""Date,Description,Amount,Balance
11/05/2026,Coffee Spot,-3.75,"4,450.00"
11/14/2026,Corner Grocery,-18.20,"4,431.80"
11/20/2026,Bookshop Example,-12.00,"4,419.80"
"""

CSV_EU = """Example Bank statement
Account;12345678
Period;01.11.2026 - 30.11.2026

Booking date;Text;Debit;Credit;Balance
03.11.2026;Bakery Sample;12,50;;1.234,50
15.11.2026;Salary Example;;2.000,00;3.234,50
20.11.2026;Rent Example;1.100,00;;2.134,50
""".encode("cp1252")

CSV_BARE = b"""2026-11-02,"Hardware Store Example",-75.00
2026-11-09,"Refund Hardware Store Example",15.00
2026-11-10,"Garden Centre Example",-30.25
"""

CSV_CARD = b"""Date,Merchant,Amount
2026-11-06,Streaming Example,9.99
2026-11-07,Payment Thank You,-200.00
2026-11-08,Cinema Example,24.00
"""

CSV_CARD_NEXT = b"""Date,Merchant,Amount
2026-11-21,Streaming Example,9.99
2026-11-22,Bistro Example,31.40
"""

OFX_SGML = b"""OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEUID:NONE
NEWFILEUID:NONE

<OFX>
<SIGNONMSGSRSV1><SONRS><STATUS><CODE>0<SEVERITY>INFO</STATUS><DTSERVER>20261120120000<LANGUAGE>ENG</SONRS></SIGNONMSGSRSV1>
<BANKMSGSRSV1><STMTTRNRS><TRNUID>1<STATUS><CODE>0<SEVERITY>INFO</STATUS>
<STMTRS><CURDEF>USD
<BANKACCTFROM><BANKID>000000000<ACCTID>99990001<ACCTTYPE>CHECKING</BANKACCTFROM>
<BANKTRANLIST><DTSTART>20261101<DTEND>20261120
<STMTTRN><TRNTYPE>DEBIT<DTPOSTED>20261102120000[0:GMT]<TRNAMT>-25.00<FITID>EX1001<NAME>Bookshop Example<MEMO>Card purchase</STMTTRN>
<STMTTRN><TRNTYPE>CREDIT<DTPOSTED>20261110<TRNAMT>1200.00<FITID>EX1002<NAME>Payroll Example Co</STMTTRN>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20261112
<TRNAMT>-60.10
<FITID>EX1003
<NAME>Fuel Example &amp; Co
</STMTTRN>
</BANKTRANLIST>
<LEDGERBAL><BALAMT>1500.00<DTASOF>20261120</LEDGERBAL>
<AVAILBAL><BALAMT>1400.00<DTASOF>20261120</AVAILBAL>
</STMTRS></STMTTRNRS></BANKMSGSRSV1>
</OFX>
"""

OFX_XML = b"""<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<?OFX OFXHEADER="200" VERSION="211" SECURITY="NONE" OLDFILEUID="NONE" NEWFILEUID="NONE"?>
<OFX>
  <CREDITCARDMSGSRSV1>
    <CCSTMTTRNRS>
      <TRNUID>1</TRNUID>
      <CCSTMTRS>
        <CURDEF>EUR</CURDEF>
        <CCACCTFROM><ACCTID>0000111122223333</ACCTID></CCACCTFROM>
        <BANKTRANLIST>
          <DTSTART>20261101</DTSTART><DTEND>20261120</DTEND>
          <STMTTRN>
            <TRNTYPE>DEBIT</TRNTYPE>
            <DTPOSTED>20261104000000.000[-5:EST]</DTPOSTED>
            <TRNAMT>-14.90</TRNAMT>
            <FITID>CC-77</FITID>
            <NAME>Pizza Example</NAME>
          </STMTTRN>
          <STMTTRN>
            <TRNTYPE>CREDIT</TRNTYPE>
            <DTPOSTED>20261115</DTPOSTED>
            <TRNAMT>100.00</TRNAMT>
            <FITID>CC-78</FITID>
            <PAYEE><NAME>Payment received</NAME></PAYEE>
            <MEMO>Thank you</MEMO>
          </STMTTRN>
        </BANKTRANLIST>
        <LEDGERBAL><BALAMT>-250.00</BALAMT><DTASOF>20261120</DTASOF></LEDGERBAL>
      </CCSTMTRS>
    </CCSTMTTRNRS>
  </CREDITCARDMSGSRSV1>
</OFX>
"""

QIF_BANK = b"""!Type:Bank
D11/03/2026
T-42.50
PCorner Grocery
MWeekly shop
^
D11/04'26
T2,500.00
PPayroll Example Co
^
D11/18/2026
U-7.25
PCoffee Spot
LDining
^
"""

QIF_CARD = b"""!Type:CCard
D2026-11-09
T-19.99
PMusic Example
^
"""

PDF = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n"
TEXT = b"Dear customer,\nthank you for banking with us.\nKind regards\n"
NO_DATES = b"name,age\nAlex Example,30\nSam Example,41\n"
QIF_INVEST = b"!Type:Invst\nD11/03/2026\nNBuy\nYEXMPL\nI10.00\nQ5\n^\n"
OFX_INVEST = b"OFXHEADER:100\n\n<OFX><INVSTMTMSGSRSV1><INVSTMTTRNRS><INVSTMTRS><CURDEF>USD</INVSTMTRS></INVSTMTTRNRS></INVSTMTMSGSRSV1></OFX>\n"


# --- Pure parts -----------------------------------------------------------------------------------

def test_values() -> None:
    d = datetime.date
    check("amount: plain and signed", imports.parse_amount("-42.50") == Decimal("-42.50")
          and imports.parse_amount("+7") == Decimal("7"))
    check("amount: thousands with a dot mark", imports.parse_amount("1,234.50") == Decimal("1234.50"))
    check("amount: comma mark", imports.parse_amount("1.234,50", ",") == Decimal("1234.50")
          and imports.parse_amount("12,5", ",") == Decimal("12.5"))
    check("amount: brackets and trailing minus are negative",
          imports.parse_amount("(12.00)") == Decimal("-12.00") and imports.parse_amount("12.00-") == Decimal("-12.00"))
    check("amount: DR negative, CR positive",
          imports.parse_amount("30.00 DR") == Decimal("-30.00") and imports.parse_amount("30.00 CR") == Decimal("30.00"))
    check("amount: currency signs and codes dropped",
          imports.parse_amount("$ 9.99") == Decimal("9.99") and imports.parse_amount("EUR -3,10", ",") == Decimal("-3.10"))
    check("amount: none in text or empty", imports.parse_amount("") is None and imports.parse_amount("n/a") is None)
    check("cents", imports.cents(Decimal("-42.505")) == -4251 and imports.cents(Decimal("2500")) == 250000)
    check("decimal mark guessed", imports.decimal_mark(["1.234,50", "12,00"]) == ","
          and imports.decimal_mark(["1,234.50", "-3.75"]) == "." and imports.decimal_mark(["12"]) == ".")
    check("date: ISO with a time", imports.parse_date("2026-11-03T10:20:00") == d(2026, 11, 3))
    check("date: OFX compact with a zone", imports.parse_date("20261104000000.000[-5:EST]") == d(2026, 11, 4))
    check("date: day first / month first / year first",
          imports.parse_date("03/11/2026", "dmy") == d(2026, 11, 3)
          and imports.parse_date("11/03/2026", "mdy") == d(2026, 11, 3)
          and imports.parse_date("2026/11/03", "dmy") == d(2026, 11, 3))
    check("date: dots, a two-digit year", imports.parse_date("03.11.26", "dmy") == d(2026, 11, 3))
    check("date: month names", imports.parse_date("3 Nov 2026") == d(2026, 11, 3)
          and imports.parse_date("Nov 3, 2026") == d(2026, 11, 3) and imports.parse_date("03-Nov-26") == d(2026, 11, 3))
    check("date: impossible and non-dates are None", imports.parse_date("31/02/2026") is None
          and imports.parse_date("Coffee") is None and imports.parse_date("42.50") is None)
    check("date order guessed", imports.date_order(["03/11/2026", "14/11/2026"]) == "dmy"
          and imports.date_order(["11/03/2026", "11/14/2026"]) == "mdy"
          and imports.date_order(["2026.11.03"]) == "ymd" and imports.date_order(["01/02/2026"], "mdy") == "mdy")
    check("fingerprint: punctuation and branch numbers ignored",
          imports.fingerprint("2026-11-03", -4250, "Corner Grocery #12") == imports.fingerprint("2026-11-03", -4250, "corner grocery"))


def test_formats() -> None:
    check("sniff: csv / ofx sgml / ofx xml / qif",
          [imports.sniff_format(imports.decode(b)) for b in (CSV_US, OFX_SGML, OFX_XML, QIF_BANK)]
          == ["csv", "ofx", "ofx", "qif"])
    check("decode: UTF-8 with a BOM, UTF-16, Windows-1252",
          imports.decode("﻿Date,Description".encode("utf-8")) == "Date,Description"
          and imports.decode("Date,Café".encode("utf-16")) == "Date,Café"
          and imports.decode("Date,Café".encode("cp1252")) == "Date,Café")

    us = imports.parse(CSV_US)
    check("csv: header found, amount and balance columns", us.mapping is not None
          and (us.mapping.date, us.mapping.description, us.mapping.amount, us.mapping.balance, us.mapping.header)
          == (0, 1, 2, 3, 0), us.mapping)
    check("csv: month-first dates settled", us.mapping is not None and us.mapping.date_format == "mdy")
    check("csv: rows", [(r.date.isoformat(), r.amount, r.description, r.balance) for r in us.rows[:2]]
          == [("2026-11-03", -4250, "Corner Grocery", 195750), ("2026-11-04", 250000, "Payroll Example Co", 445750)],
          us.rows[:2])
    check("csv: column names and a sample for the mapping", us.columns == ["Date", "Description", "Amount", "Balance"]
          and len(us.sample) == 6)

    eu = imports.parse(CSV_EU)
    check("csv: semicolons, a preamble, debit / credit columns",
          eu.mapping is not None and eu.mapping.delimiter == ";" and eu.mapping.header == 3
          and eu.mapping.debit == 2 and eu.mapping.credit == 3 and eu.mapping.amount is None, eu.mapping)
    check("csv: comma decimals, day-first dates, debit negative",
          [(r.date.isoformat(), r.amount, r.balance) for r in eu.rows]
          == [("2026-11-03", -1250, 123450), ("2026-11-15", 200000, 323450), ("2026-11-20", -110000, 213450)],
          [(r.date, r.amount, r.balance) for r in eu.rows])

    bare = imports.parse(CSV_BARE)
    check("csv: no header — date, text and amount columns by their values",
          bare.mapping is not None and bare.mapping.header == -1
          and (bare.mapping.date, bare.mapping.description, bare.mapping.amount) == (0, 1, 2)
          and [r.amount for r in bare.rows] == [-7500, 1500, -3025], bare.mapping)

    negated = imports.parse(CSV_CARD, imports.ColumnMap(date=0, description=1, amount=2, negate=True))
    check("csv: a mapping given, charges negated", [r.amount for r in negated.rows] == [-999, 20000, -2400])
    check("csv: a skipped row is counted",
          imports.parse(CSV_US + b"Totals,,,\n").skipped == 1)

    sgml = imports.parse(OFX_SGML)
    check("ofx sgml: rows with FITIDs, names, memos",
          [(r.date.isoformat(), r.amount, r.description, r.memo, r.ext_id) for r in sgml.rows]
          == [("2026-11-02", -2500, "Bookshop Example", "Card purchase", "EX1001"),
              ("2026-11-10", 120000, "Payroll Example Co", "", "EX1002"),
              ("2026-11-12", -6010, "Fuel Example & Co", "", "EX1003")],
          [(r.date, r.amount, r.description, r.memo, r.ext_id) for r in sgml.rows])
    check("ofx sgml: a bank account, the ledger balance (not the available one), the last digits",
          (sgml.kind, sgml.balance, sgml.number) == ("bank", 150000, "0001"), (sgml.kind, sgml.balance, sgml.number))
    xml = imports.parse(OFX_XML)
    check("ofx xml: a card, payee name, memo, balance",
          (xml.kind, xml.balance) == ("card", -25000)
          and [(r.date.isoformat(), r.amount, r.description, r.memo) for r in xml.rows]
          == [("2026-11-04", -1490, "Pizza Example", ""), ("2026-11-15", 10000, "Payment received", "Thank you")],
          [(r.date, r.amount, r.description, r.memo) for r in xml.rows])

    qif = imports.parse(QIF_BANK)
    check("qif: bank rows, the U amount, a two-digit year",
          (qif.kind, [(r.date.isoformat(), r.amount, r.description, r.memo) for r in qif.rows])
          == ("bank", [("2026-11-03", -4250, "Corner Grocery", "Weekly shop"),
                       ("2026-11-04", 250000, "Payroll Example Co", ""), ("2026-11-18", -725, "Coffee Spot", "")]),
          [(r.date, r.amount, r.description) for r in qif.rows])
    check("qif: a card section", imports.parse(QIF_CARD).kind == "card")


def refused(data: bytes, words: str) -> bool:
    try:
        imports.parse(data)
    except imports.BadFile as e:
        return words in str(e)
    return False


def test_refused() -> None:
    check("refused: a PDF, saying what to download instead", refused(PDF, "CSV, OFX or QIF"))
    check("refused: plain text", refused(TEXT, "doesn't look like a bank statement"))
    check("refused: an empty file", refused(b"  \n", "empty"))
    check("refused: a spreadsheet", refused(b"PK\x03\x04rest-of-a-zip", "spreadsheet"))
    check("refused: a CSV without dates and amounts", refused(NO_DATES, "date and amount columns"))
    check("refused: a QIF investment section", refused(QIF_INVEST, "investment"))
    check("refused: an OFX investment statement", refused(OFX_INVEST, "investment statement"))
    check("refused: over the size limit", refused(b"x" * (imports.MAX_BYTES + 1), "over 5 MB"))
    try:
        imports.ColumnMap.from_json({"date": 0, "description": 1})
        bad = False
    except imports.BadMapping as e:
        bad = "amount column" in str(e)
    check("mapping: an amount or debit / credit column is required", bad)
    try:
        imports.parse(CSV_US, imports.ColumnMap(date=0, description=1, amount=9))
        bad = False
    except imports.BadMapping as e:
        bad = "no column 10" in str(e)
    check("mapping: a column the file does not have", bad)


# --- The API --------------------------------------------------------------------------------------

def form(data: Optional[bytes], name: str = "statement.csv", **fields: Any) -> aiohttp.FormData:
    f = aiohttp.FormData()
    if data is not None:
        f.add_field("file", data, filename=name, content_type="application/octet-stream")
    for k, v in fields.items():  # text/plain: multipart even without a file
        f.add_field(k, json.dumps(v) if isinstance(v, dict) else str(v), content_type="text/plain")
    return f


async def call(client: TestClient, method: str, path: str, who, data: Any = None) -> Tuple[int, Any]:
    r = await client.request(method, path, headers={"Authorization": f"Bearer {TOKENS[who.id]}"}, data=data)
    text = await r.text()
    try:
        return r.status, json.loads(text)
    except ValueError:
        return r.status, text


def rows_of(user, account_id: str) -> List[Dict[str, Any]]:
    with store.db() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM money_transactions WHERE user_id = ? AND account_id = ? ORDER BY purchase_date, amount",
            (user.id, account_id))]


def synced_account(user, company: str, kind: str, number: str) -> str:
    sid, aid = uuid.uuid4().hex, uuid.uuid4().hex
    with store.db() as conn:
        conn.execute("INSERT INTO money_sources (id, user_id, company, state, created_at, updated_at) "
                     "VALUES (?, ?, ?, 'ok', ?, ?)", (sid, user.id, company, STAMP, STAMP))
        conn.execute("INSERT INTO money_accounts (id, user_id, source_id, company, kind, number, created_at, updated_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (aid, user.id, sid, company, kind, number, STAMP, STAMP))
    return aid


async def test_api(client: TestClient, classified: List[str]) -> None:
    # Robin: a synced bank account that already holds one row the file has too, and a rule for a merchant.
    bank = synced_account(ROBIN, moneyfakes.BANK, "bank", "12-345-678901")
    with store.db() as conn:
        conn.execute("INSERT INTO money_transactions (id, user_id, account_id, ext_key, source, status, purchase_date, "
                     "amount, description, merchant, merchant_key, created_at, updated_at) "
                     "VALUES (?, ?, ?, 'sb:abc', 'scrape', 'completed', '2026-11-03', -4250, 'Corner Grocery', "
                     "'Corner Grocery', 'corner grocery', ?, ?)", (uuid.uuid4().hex, ROBIN.id, bank, STAMP, STAMP))
        conn.execute("INSERT INTO money_rules (id, user_id, merchant_key, category, merchant, created_at) "
                     "VALUES (?, ?, 'coffee spot', 'restaurants', 'The Coffee Spot', ?)", (uuid.uuid4().hex, ROBIN.id, STAMP))

    status, body = await call(client, "GET", "/api/money/import/accounts", ROBIN)
    check("accounts: the synced bank account is listed", status == 200
          and [(a["id"], a["kind"], a["manual"], a["label"], a["mapping"]) for a in body["accounts"]]
          == [(bank, "bank", False, "Sample Bank ••••901", None)], body)

    status, body = await call(client, "POST", "/api/money/import/preview", ROBIN, form(CSV_US, account=bank))
    check("preview: format, counts, the duplicate the provider brought", status == 200
          and (body["format"], body["count"], body["duplicates"], body["new"], body["skipped"]) == ("csv", 5, 1, 4, 0)
          and body["columns"] == ["Date", "Description", "Amount", "Balance"] and body["mapping"]["amount"] == 2
          and body["rows"][0] == {"date": "2026-11-03", "description": "Corner Grocery", "memo": None,
                                  "amount": -4250, "balance": 195750}, body)
    check("preview: nothing stored", len(rows_of(ROBIN, bank)) == 1)

    status, body = await call(client, "POST", "/api/money/import", ROBIN, form(CSV_US, account=bank))
    check("import into a synced account: the provider's row not added again", status == 200
          and (body["format"], body["added"], body["duplicates"]) == ("csv", 4, 1)
          and body["account"] == {"id": bank, "label": "Sample Bank ••••901", "kind": "bank", "manual": False}, body)
    stored = rows_of(ROBIN, bank)
    coffee = [r for r in stored if r["description"] == "Coffee Spot"]
    check("import: two equal coffees on one day stay two", len(coffee) == 2)
    check("import: the user's rule applied", all(r["category"] == "restaurants" and r["category_source"] == "rule"
                                                  and r["merchant"] == "The Coffee Spot" for r in coffee), coffee)
    check("import: statement rows keyed file:, raw kept",
          all(r["source"] == "scrape" and r["status"] == "completed" and r["ext_key"].startswith("file:")
              for r in stored if r["ext_key"] != "sb:abc")
          and json.loads(coffee[0]["raw"])["file"] == "csv")
    await asyncio.sleep(0.05)
    new_ids = {r["id"] for r in stored if r["ext_key"] != "sb:abc"}
    check("import: the new rows handed to classification", set(classified) == new_ids, (classified, new_ids))
    with store.db() as conn:
        balance = conn.execute("SELECT balance FROM money_accounts WHERE id = ?", (bank,)).fetchone()["balance"]
    check("import: a synced account's balance is the provider's", balance is None)

    status, body = await call(client, "POST", "/api/money/import", ROBIN, form(CSV_US, account=bank))
    check("re-import of the same file adds nothing", status == 200 and (body["added"], body["duplicates"]) == (0, 5),
          body)
    status, body = await call(client, "POST", "/api/money/import", ROBIN, form(CSV_US_LATER, account=bank))
    check("an overlapping file adds only what is new", (body["added"], body["duplicates"]) == (1, 2)
          and len(rows_of(ROBIN, bank)) == 6, body)
    status, body = await call(client, "GET", "/api/money/import/accounts", ROBIN)
    check("accounts: the CSV mapping is remembered for the account",
          body["accounts"][0]["mapping"]["amount"] == 2 and body["accounts"][0]["mapping"]["date_format"] == "mdy",
          body)

    # A new manual account from an OFX file: its kind and balance from the file; FITIDs keep re-imports out.
    status, body = await call(client, "POST", "/api/money/import", ROBIN,
                              form(OFX_SGML, "export.ofx", new_label="  Everyday   account "))
    manual = body.get("account", {}).get("id", "")
    check("import into a new manual account (OFX SGML)", status == 200
          and (body["format"], body["added"], body["account"]["label"], body["account"]["kind"], body["account"]["manual"])
          == ("ofx", 3, "Everyday account", "bank", True), body)
    with store.db() as conn:
        acct = dict(conn.execute("SELECT * FROM money_accounts WHERE id = ?", (manual,)).fetchone())
    check("manual account: no source, company manual, the file's ledger balance",
          (acct["source_id"], acct["company"], acct["balance"]) == (None, imports.MANUAL, 150000), acct)
    check("manual account: FITIDs as keys", sorted(r["ext_key"] for r in rows_of(ROBIN, manual))
          == ["file:EX1001", "file:EX1002", "file:EX1003"])
    status, body = await call(client, "POST", "/api/money/import", ROBIN, form(OFX_SGML, account=manual))
    check("re-import of an OFX file adds nothing", (body["added"], body["duplicates"]) == (0, 3), body)
    status, body = await call(client, "POST", "/api/money/import", ROBIN, form(QIF_CARD, new_label="Travel card"))
    check("a new card account from a QIF card section", status == 200 and body["account"]["kind"] == "card"
          and body["added"] == 1, body)
    status, body = await call(client, "GET", "/api/money/import/accounts", ROBIN)
    check("accounts: manual accounts first, by kind then name",
          [(a["label"], a["manual"]) for a in body["accounts"]]
          == [("Everyday account", True), ("Travel card", True), ("Sample Bank ••••901", False)], body)

    # Bob: a card CSV whose charges are positive, imported negated; the mapping is remembered.
    status, body = await call(client, "POST", "/api/money/import/preview", BOB, form(CSV_CARD))
    check("preview without an account: the guess, no duplicates counted", status == 200
          and [r["amount"] for r in body["rows"]] == [999, -20000, 2400] and body["duplicates"] == 0, body)
    mapping = {**body["mapping"], "negate": True}
    status, body = await call(client, "POST", "/api/money/import", BOB,
                              form(CSV_CARD, new_label="Bob's card", new_kind="card", mapping=mapping))
    bob_card = body.get("account", {}).get("id", "")
    check("import with a mapping given (negated)", status == 200 and body["added"] == 3
          and [r["amount"] for r in rows_of(BOB, bob_card)] == [-999, 20000, -2400], body)
    status, body = await call(client, "POST", "/api/money/import/preview", BOB, form(CSV_CARD_NEXT, account=bob_card))
    check("the next file of the account uses its remembered mapping", status == 200 and body["mapping"]["negate"] is True
          and [r["amount"] for r in body["rows"]] == [-999, -3140], body)
    status, body = await call(client, "POST", "/api/money/import/preview", BOB, form(CSV_EU, account=bob_card))
    check("a remembered mapping that does not fit another layout: guessed afresh", status == 200
          and body["mapping"]["delimiter"] == ";" and body["count"] == 3, body)

    # Isolation both ways, and a user without money.
    status, body = await call(client, "POST", "/api/money/import", BOB, form(CSV_US, account=bank))
    check("Bob can't import into Robin's account (404)", status == 404 and len(rows_of(ROBIN, bank)) == 6, body)
    status, body = await call(client, "POST", "/api/money/import/preview", ROBIN, form(CSV_CARD, account=bob_card))
    check("Robin can't preview against Bob's account (404)", status == 404, body)
    status, body = await call(client, "GET", "/api/money/import/accounts", BOB)
    check("Bob lists only his own accounts", [a["id"] for a in body["accounts"]] == [bob_card], body)
    for method, path in (("GET", "/api/money/import/accounts"), ("POST", "/api/money/import/preview"),
                         ("POST", "/api/money/import")):
        status, body = await call(client, method, path, CARL, form(CSV_US, new_label="x") if method == "POST" else None)
        check(f"no money: {method} {path} → 403 not_connected", status == 403 and body.get("error") == "not_connected",
              (status, body))

    # Refusals through the API.
    status, body = await call(client, "POST", "/api/money/import", ROBIN, form(PDF, "statement.pdf", account=bank))
    check("a PDF → 400 bad_file with a message", status == 400 and body["error"] == "bad_file"
          and "CSV, OFX or QIF" in body["message"], body)
    status, body = await call(client, "POST", "/api/money/import/preview", ROBIN, form(TEXT, account=bank))
    check("plain text → 400 bad_file", status == 400 and body["error"] == "bad_file", body)
    status, body = await call(client, "POST", "/api/money/import", ROBIN, form(None, account=bank))
    check("no file → 400 bad_file", status == 400 and body["error"] == "bad_file", body)
    status, body = await call(client, "POST", "/api/money/import", ROBIN, form(CSV_US))
    check("no account and no name → 400 bad_mapping", status == 400 and body["error"] == "bad_mapping", body)
    status, body = await call(client, "POST", "/api/money/import", ROBIN,
                              form(CSV_US, account=bank, mapping={"date": 0, "description": 1}))
    check("a mapping without an amount → 400 bad_mapping", status == 400 and body["error"] == "bad_mapping"
          and "amount" in body["message"], body)
    status, body = await call(client, "POST", "/api/money/import", ROBIN,
                              form(CSV_US, new_label="Savings", new_kind="investment"))
    check("a new account of another kind → 400 bad_mapping", status == 400 and body["error"] == "bad_mapping", body)
    status, body = await call(client, "POST", "/api/money/import", ROBIN,
                              form(b"Date,Description,Amount\n" + b"2026-11-01,x,1\n" * 400_000, account=bank))
    check("over the limit → 413 too_large", status == 413 and body.get("error") == "too_large", (status, body))
    status, body = await call(client, "POST", "/api/money/import", ROBIN, json.dumps({"account": bank}).encode())
    check("not multipart → 400", status == 400, body)
    check("no refusal stored anything", len(rows_of(ROBIN, bank)) == 6)


async def main() -> None:
    test_values()
    test_formats()
    test_refused()
    classified: List[str] = []

    async def fake_run(user, ids=None, ref: str = "") -> Dict[str, int]:
        classified.extend(ids or [])
        return {"rows": len(ids or []), "model_rows": 0, "model": 0}

    app = web.Application()
    setup_auth(app)
    money_api.setup(app)
    with patch.object(classify, "run", fake_run):
        async with TestClient(TestServer(app)) as client:
            await test_api(client, classified)


asyncio.run(main())

passed = sum(1 for _, ok in results if ok)
print(f"\n{passed}/{len(results)} passed")
if passed != len(results):
    sys.exit(1)
