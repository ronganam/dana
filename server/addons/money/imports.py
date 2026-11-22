"""Statement files → transactions (docs/money.md, "Importing statement files").

Any bank or card company lets its customers download their statement as a
file; importing one fills an account without a provider addon. Three
formats, read with the standard library only:

- **CSV**: the layout is guessed (:func:`guess_mapping`: a header row with a
  date, a description, an amount or debit / credit columns and an optional
  balance, after any preamble rows) and shown as a preview the user can
  correct (:class:`ColumnMap`); the mapping is remembered per account
  (``money_import_maps``).
- **OFX** (1.x SGML and 2.x XML; also QFX): each ``<STMTTRN>``'s date, amount,
  name, memo and ``FITID``, the account's ledger balance.
- **QIF**: ``!Type:Bank`` / ``CCard`` / ``Cash`` records (``D`` date, ``T`` amount,
  ``P`` payee, ``M`` memo).

Rows go into one of the user's bank or card accounts, or a new **manual**
account (company :data:`MANUAL`, no source: nothing syncs it). They are
statement rows like a provider's (``source = 'scrape'``, keyed ``file:…``),
de-duplicated against the account's rows by date, amount and description
(:func:`fingerprint`, counted, so two equal coffees on one day stay two and
importing the same file again adds nothing), text scrubbed, the user's rules
applied, then classified by the after-sync hooks like a sync's rows."""

import csv
import dataclasses
import datetime
import hashlib
import html
import io
import json
import re
import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from core.users import User
from addons.money import after_sync, ingest, providers, store

MANUAL = store.MANUAL
MAX_BYTES = 5 * 1024 * 1024
PREVIEW_ROWS = 20
# How many rows a CSV header may come after (a bank's preamble: its name, the account, the period).
HEADER_SEARCH = 15
DATE_FORMATS = ("auto", "dmy", "mdy", "ymd")
LABEL_MAX = 60


class BadFile(ValueError):
    """A file that cannot be imported: the message says why in plain words (400 ``bad_file``). For a
    CSV whose columns could not be read, ``layout`` holds ``{columns, sample, delimiter}`` so the
    columns can be chosen by hand."""

    def __init__(self, message: str, layout: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.layout = layout


class BadMapping(ValueError):
    """A CSV mapping that does not fit the file (400 ``bad_mapping``)."""


# --- Reading the file -------------------------------------------------------------------------

def decode(data: bytes) -> str:
    """The file's text: UTF-8 (with or without a BOM), UTF-16 with a BOM, else Windows-1252 / Latin-1.

    Raises:
        BadFile: an empty file or a binary one (a PDF, a spreadsheet).
    """
    if not data.strip():
        raise BadFile("The file is empty.")
    if data.startswith(b"%PDF"):
        raise BadFile("This is a PDF. Download the statement as CSV, OFX or QIF instead (most banks offer one of them).")
    if data.startswith(b"PK\x03\x04") or data.startswith(b"\xd0\xcf\x11\xe0"):
        raise BadFile("This is a spreadsheet file. Save it as CSV, or download the statement as CSV, OFX or QIF.")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    if b"\x00" in data[:4096]:
        raise BadFile("This is not a text file. Download the statement as CSV, OFX or QIF.")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def sniff_format(text: str) -> str:
    """``ofx``, ``qif`` or ``csv`` from the text itself (not the file name).

    Raises:
        BadFile: none of them.
    """
    head = text.lstrip()[:4096].upper()
    if head.startswith("OFXHEADER") or "<OFX>" in head or "<OFX " in head or ("<?OFX" in head):
        return "ofx"
    if head.startswith("!TYPE:") or head.startswith("!ACCOUNT") or head.startswith("!OPTION"):
        return "qif"
    lines = [line for line in text.splitlines() if line.strip()][:HEADER_SEARCH + 5]
    for d in (",", ";", "\t", "|"):
        if sum(1 for line in lines if d in line) >= max(2, len(lines) // 2):
            return "csv"
    raise BadFile("This doesn't look like a bank statement: Dana reads CSV, OFX (or QFX) and QIF files.")


# --- Values -----------------------------------------------------------------------------------

_MONTHS = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                           "nov", "dec"))}
_ISO = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ].*)?$")
_COMPACT = re.compile(r"^\s*(\d{4})(\d{2})(\d{2})(?:\d{0,6}(?:\.\d+)?)?(?:\[.*\])?\s*$")
_NUMERIC = re.compile(r"^\s*(\d{1,4})\s*[/.\-' ]\s*(\d{1,2})\s*[/.\-' ]\s*(\d{1,4})(?:[ T].*)?$")
_NAMED = re.compile(r"^\s*(?:(\d{1,2})[\s\-]+([A-Za-z]{3,9})\.?[\s\-,]+(\d{2,4})|([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{2,4}))\s*$")


def _year(value: int) -> int:
    return value + 2000 if value < 100 else value


def _make(y: int, m: int, d: int) -> Optional[datetime.date]:
    try:
        return datetime.date(_year(y), m, d)
    except ValueError:
        return None


def numeric_parts(text: str) -> Optional[Tuple[int, int, int]]:
    """The three numbers of a date written with separators ("03/11/2026" → (3, 11, 2026)), else None."""
    m = _NUMERIC.match(text or "")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def date_order(values: Sequence[str], default: str = "dmy") -> str:
    """The day / month order of a column of dates: ``ymd`` when the year comes first, ``dmy`` when
    a first number is over 12, ``mdy`` when a second one is, else ``default``."""
    first_big = second_big = year_first = False
    for v in values:
        parts = numeric_parts(v)
        if not parts:
            continue
        a, b, _ = parts
        if a > 31:
            year_first = True
        elif a > 12:
            first_big = True
        elif b > 12:
            second_big = True
    if year_first:
        return "ymd"
    if first_big and not second_big:
        return "dmy"
    if second_big and not first_big:
        return "mdy"
    return default


def parse_date(text: str, order: str = "dmy") -> Optional[datetime.date]:
    """A statement's date: ISO (``2026-11-03``, with a time), OFX's ``20261103…``, numbers with
    separators in ``order`` (``dmy`` / ``mdy`` / ``ymd``; two-digit years are 20xx), or with an English
    month name ("3 Nov 2026", "Nov 3, 2026"); None when it is not a date."""
    text = (text or "").strip().strip('"')
    if not text:
        return None
    for pattern in (_ISO, _COMPACT):
        m = pattern.match(text)
        if m:
            return _make(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    parts = numeric_parts(text)
    if parts:
        a, b, c = parts
        if order == "ymd" or a > 31:
            return _make(a, b, c)
        if order == "mdy":
            return _make(c, a, b)
        return _make(c, b, a)
    m = _NAMED.match(text)
    if m:
        if m.group(1):
            day, month, year = m.group(1), m.group(2), m.group(3)
        else:
            month, day, year = m.group(4), m.group(5), m.group(6)
        number = _MONTHS.get(month[:3].lower())
        return _make(int(year), number, int(day)) if number else None
    return None


_AMOUNT_JUNK = re.compile(r"[^\d,.\-+()]")


def decimal_mark(values: Sequence[str]) -> str:
    """``","`` when the column writes decimals with a comma (``12,50`` / ``1.234,50``), else ``"."``."""
    comma = dot = 0
    for v in values:
        s = _AMOUNT_JUNK.sub("", v or "")
        if not s:
            continue
        last_c, last_d = s.rfind(","), s.rfind(".")
        if last_c > last_d and re.search(r",\d{1,2}\)?-?$", s):
            comma += 1
        elif last_d > last_c and re.search(r"\.\d{1,2}\)?-?$", s):
            dot += 1
    return "," if comma > dot else "."


def parse_amount(text: str, mark: str = ".") -> Optional[Decimal]:
    """A money amount as written on a statement: ``-1,234.50``, ``1.234,50`` (``mark=","``), ``(12.00)``,
    ``12.00-``, ``12.00 CR`` / ``DR``, with or without a currency sign or code; None when there is none."""
    raw = (text or "").strip()
    if not raw:
        return None
    upper = raw.upper()
    bare = _AMOUNT_JUNK.sub("", raw.replace("−", "-").replace("–", "-"))
    negative = (bare.startswith("(") and bare.endswith(")")) or bare.startswith("-") or bare.endswith("-") \
        or upper.endswith("DR")
    credit = upper.endswith("CR")
    s = bare.strip("()+-")
    if not s or not re.search(r"\d", s):
        return None
    if mark == ",":
        s = s.replace(".", "").replace(",", ".")
    else:
        s = s.replace(",", "")
    try:
        value = Decimal(s)
    except InvalidOperation:
        return None
    return value if credit else (-value if negative else value)


def cents(value: Decimal) -> int:
    """An amount as integer cents (the money tables' unit)."""
    return int(ingest.agorot(str(value)) or 0)


# --- A parsed row -----------------------------------------------------------------------------

@dataclass
class Row:
    """One statement line: its date, signed amount in cents (money out is negative), description,
    memo, the running balance after it (cents, when the file gives it) and the file's own id."""
    date: datetime.date
    amount: int
    description: str
    memo: str = ""
    balance: Optional[int] = None
    ext_id: str = ""


@dataclass
class Parsed:
    """What a file holds: its format, rows, how many lines had no date or amount, and what the file
    says about its account (``kind`` bank / card, ``balance`` in cents, ``number``)."""
    format: str
    rows: List[Row]
    skipped: int = 0
    kind: Optional[str] = None
    balance: Optional[int] = None
    number: str = ""
    columns: List[str] = field(default_factory=list)
    sample: List[List[str]] = field(default_factory=list)
    mapping: Optional["ColumnMap"] = None


# --- CSV --------------------------------------------------------------------------------------

@dataclass
class ColumnMap:
    """Which CSV column is what (0-based indexes; ``None`` when absent) and how to read them.

    ``header``: the row (counted from the file's first, 0-based) holding the column names, the data
    starting on the next one; -1 when there is none. Either ``amount`` or at least one of ``debit`` /
    ``credit`` is set. ``negate``: the file writes money out as positive amounts. ``date_format``:
    ``auto`` (from the column's values) / ``dmy`` / ``mdy`` / ``ymd``; ``decimal``: ``auto`` / ``.`` / ``,``."""
    date: int
    description: int
    amount: Optional[int] = None
    debit: Optional[int] = None
    credit: Optional[int] = None
    balance: Optional[int] = None
    memo: Optional[int] = None
    header: int = 0
    delimiter: str = ","
    date_format: str = "auto"
    decimal: str = "auto"
    negate: bool = False

    def to_json(self) -> Dict[str, Any]:
        """The mapping as stored and sent to the page."""
        return asdict(self)

    @classmethod
    def from_json(cls, value: Any) -> "ColumnMap":
        """A mapping from the page or the store.

        Raises:
            BadMapping: missing or malformed fields.
        """
        if not isinstance(value, Mapping):
            raise BadMapping("The column mapping must be an object.")

        def column(name: str, required: bool = False) -> Optional[int]:
            v = value.get(name)
            if v is None or v == "":
                if required:
                    raise BadMapping(f"Choose the {name} column.")
                return None
            if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v < 200:
                raise BadMapping(f"The {name} column must be a column number.")
            return v
        header = value.get("header", 0)
        if isinstance(header, bool) or not isinstance(header, int) or not -1 <= header <= 200:
            raise BadMapping("The header row must be a row number, or -1 for none.")
        delimiter = value.get("delimiter", ",")
        if delimiter not in (",", ";", "\t", "|"):
            raise BadMapping("The column separator must be a comma, semicolon, tab or bar.")
        date_format = value.get("date_format", "auto")
        if date_format not in DATE_FORMATS:
            raise BadMapping(f"The date format must be one of {', '.join(DATE_FORMATS)}.")
        mark = value.get("decimal", "auto")
        if mark not in ("auto", ".", ","):
            raise BadMapping("The decimal mark must be auto, '.' or ','.")
        out = cls(date=column("date", True) or 0, description=column("description", True) or 0,
                  amount=column("amount"), debit=column("debit"), credit=column("credit"),
                  balance=column("balance"), memo=column("memo"), header=header, delimiter=delimiter,
                  date_format=date_format, decimal=mark, negate=bool(value.get("negate", False)))
        if out.amount is None and out.debit is None and out.credit is None:
            raise BadMapping("Choose the amount column, or the debit and credit columns.")
        return out


_WORDS = {
    "date": ("date", "transaction date", "posting date", "posted date", "booking date", "trans date", "value date",
             "posted", "datum", "fecha", "data"),
    "description": ("description", "details", "payee", "merchant", "narrative", "name", "transaction",
                    "transaction description", "particulars", "reference", "beschreibung", "concepto", "text"),
    "amount": ("amount", "value", "sum", "transaction amount", "amount (local)", "betrag", "importe", "montant"),
    "debit": ("debit", "debits", "withdrawal", "withdrawals", "paid out", "money out", "out", "debit amount",
              "charge", "charges"),
    "credit": ("credit", "credits", "deposit", "deposits", "paid in", "money in", "in", "credit amount"),
    "balance": ("balance", "running balance", "closing balance", "saldo", "account balance"),
    "memo": ("memo", "notes", "note", "comment", "additional info", "category"),
}


def _norm(cell: str) -> str:
    return " ".join(re.sub(r"[^\w()]+", " ", (cell or "").casefold()).split())


def _role(cell: str) -> Optional[str]:
    n = _norm(cell)
    if not n:
        return None
    for role, words in _WORDS.items():
        if n in words:
            return role
    for role in ("date", "balance", "debit", "credit", "amount", "description"):  # "Booking Date (UTC)", "Amount EUR"
        if any(n.startswith(w + " ") or n.endswith(" " + w) for w in _WORDS[role] if len(w) > 3):
            return role
    return None


def read_csv(text: str, delimiter: str = "") -> Tuple[List[List[str]], str]:
    """The CSV's rows (cells stripped) and its delimiter (sniffed among ``, ; tab |`` when not given)."""
    if not delimiter:
        sample = "\n".join(text.splitlines()[:HEADER_SEARCH + 10])
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            counts = {d: sample.count(d) for d in (",", ";", "\t", "|")}
            delimiter = max(counts, key=lambda d: counts[d])
    rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(text), delimiter=delimiter)]
    return [r for r in rows if any(r)], delimiter


def _is_amount(cell: str) -> bool:
    return bool(cell) and parse_amount(cell, decimal_mark([cell])) is not None and not parse_date(cell)


def guess_mapping(rows: Sequence[Sequence[str]], delimiter: str) -> Optional[ColumnMap]:
    """The likely layout: a header row (within the first :data:`HEADER_SEARCH`) naming a date, a
    description and an amount or debit / credit column; else, without a header, the columns whose
    values read as dates, amounts and text. None when nothing fits."""
    for i, row in enumerate(rows[:HEADER_SEARCH]):
        roles: Dict[str, int] = {}
        for j, cell in enumerate(row):
            role = _role(cell)
            if role and role not in roles:
                roles[role] = j
        if "date" in roles and ("amount" in roles or "debit" in roles or "credit" in roles):
            description = roles.get("description")
            if description is None:
                used = set(roles.values())
                description = next((j for j in range(len(row)) if j not in used), roles["date"])
            return ColumnMap(date=roles["date"], description=description, amount=roles.get("amount"),
                             debit=None if "amount" in roles else roles.get("debit"),
                             credit=None if "amount" in roles else roles.get("credit"),
                             balance=roles.get("balance"), memo=roles.get("memo"), header=i, delimiter=delimiter)
    data = [r for r in rows if r]
    if not data:
        return None
    width = max(len(r) for r in data)

    def share(test: Any, j: int) -> float:
        cells = [r[j] for r in data if j < len(r) and r[j]]
        return sum(1 for c in cells if test(c)) / len(cells) if cells else 0.0
    dates = [j for j in range(width) if share(lambda c: parse_date(c) is not None, j) >= 0.8]
    amounts = [j for j in range(width) if j not in dates and share(_is_amount, j) >= 0.8]
    texts = [j for j in range(width) if j not in dates and j not in amounts]
    if not dates or not amounts:
        return None
    description = max(texts, key=lambda j: sum(len(r[j]) for r in data if j < len(r))) if texts else dates[0]
    return ColumnMap(date=dates[0], description=description, amount=amounts[0],
                     balance=amounts[-1] if len(amounts) > 1 else None, header=-1, delimiter=delimiter)


def _cell(row: Sequence[str], j: Optional[int]) -> str:
    return row[j] if j is not None and j < len(row) else ""


def parse_csv(text: str, mapping: Optional[ColumnMap] = None) -> Parsed:
    """A CSV statement read with ``mapping`` (guessed when None).

    Raises:
        BadFile: no layout could be guessed, or no row has a date and an amount.
        BadMapping: the mapping names a column the file does not have.
    """
    rows, delimiter = read_csv(text, mapping.delimiter if mapping else "")
    width = max((len(r) for r in rows), default=0)
    sample = [list(r) for r in rows[:PREVIEW_ROWS + 1]]

    def names_at(header: int) -> List[str]:
        columns = rows[header] if 0 <= header < len(rows) else []
        return [(columns[j] if j < len(columns) and columns[j] else f"Column {j + 1}") for j in range(width)]
    if mapping is None:
        mapping = guess_mapping(rows, delimiter)
        if mapping is None:
            raise BadFile("Dana couldn't find the date and amount columns in this CSV: choose them by hand.",
                          {"columns": names_at(0), "sample": sample, "delimiter": delimiter})
    names = names_at(mapping.header)
    data = rows[mapping.header + 1:] if mapping.header >= 0 else rows
    for name in ("date", "description", "amount", "debit", "credit", "balance", "memo"):
        j = getattr(mapping, name)
        if j is not None and j >= width:
            raise BadMapping(f"The file has no column {j + 1} (for the {name}).")
    order = mapping.date_format if mapping.date_format != "auto" else date_order([_cell(r, mapping.date) for r in data])
    money_cols = [j for j in (mapping.amount, mapping.debit, mapping.credit, mapping.balance) if j is not None]
    mark = mapping.decimal if mapping.decimal != "auto" else decimal_mark([_cell(r, j) for r in data for j in money_cols])
    out: List[Row] = []
    skipped = 0
    for r in data:
        when = parse_date(_cell(r, mapping.date), order)
        if mapping.amount is not None:
            value = parse_amount(_cell(r, mapping.amount), mark)
            if value is not None and mapping.negate:
                value = -value
        else:
            debit = parse_amount(_cell(r, mapping.debit), mark)
            credit = parse_amount(_cell(r, mapping.credit), mark)
            value = None if debit is None and credit is None else abs(credit or 0) - abs(debit or 0)
        if when is None or value is None:
            skipped += 1
            continue
        balance = parse_amount(_cell(r, mapping.balance), mark) if mapping.balance is not None else None
        out.append(Row(when, cents(value), _cell(r, mapping.description), _cell(r, mapping.memo),
                       cents(balance) if balance is not None else None))
    if not out:
        raise BadFile("No row of this CSV has both a date and an amount in the chosen columns.",
                      {"columns": names, "sample": sample, "delimiter": delimiter})
    return Parsed("csv", out, skipped, columns=names, sample=sample,
                  mapping=dataclasses.replace(mapping, date_format=order, delimiter=delimiter))


# --- OFX --------------------------------------------------------------------------------------

_TAG = re.compile(r"<([A-Za-z0-9.]+)>([^<\r\n]*)")


def _fields(block: str) -> Dict[str, str]:
    """The leaf elements of an OFX block (``<NAME>value``, with or without its end tag)."""
    out: Dict[str, str] = {}
    for m in _TAG.finditer(block):
        value = html.unescape(m.group(2)).strip()
        if value and m.group(1).upper() not in out:
            out[m.group(1).upper()] = value
    return out


def parse_ofx(text: str) -> Parsed:
    """An OFX / QFX statement, SGML (1.x: leaf elements without end tags) or XML (2.x).

    Raises:
        BadFile: no ``<OFX>`` body, an investment statement, or no transaction in it.
    """
    start = text.upper().find("<OFX")
    if start < 0:
        raise BadFile("This OFX file has no <OFX> section.")
    body = text[start:]
    upper = body.upper()
    if "<INVSTMTRS>" in upper:
        raise BadFile("This is an investment statement: Dana imports bank and card statements.")
    kind = "card" if "<CCSTMTRS>" in upper or "<CCACCTFROM>" in upper else "bank"
    account = _fields(body)
    rows: List[Row] = []
    skipped = 0
    for part in re.split(r"<STMTTRN>", body, flags=re.I)[1:]:
        end = re.search(r"</STMTTRN>|</BANKTRANLIST>", part, re.I)
        f = _fields(part[:end.start()] if end else part)
        when = parse_date(f.get("DTPOSTED") or f.get("DTUSER") or "")
        value = parse_amount(f.get("TRNAMT", ""), decimal_mark([f.get("TRNAMT", "")]))
        if when is None or value is None:
            skipped += 1
            continue
        name = f.get("NAME") or f.get("PAYEE") or ""
        memo = f.get("MEMO", "")
        rows.append(Row(when, cents(value), name or memo, memo if name else "", None, f.get("FITID", "")))
    if not rows:
        raise BadFile("This OFX file has no transactions.")
    ledger = re.search(r"<LEDGERBAL>(.*?)(?:</LEDGERBAL>|<AVAILBAL>|$)", body, re.S | re.I)
    balance = None
    if ledger:
        amount = parse_amount(_fields(ledger.group(1)).get("BALAMT", ""))
        balance = cents(amount) if amount is not None else None
    number = re.sub(r"\D", "", account.get("ACCTID", ""))[-4:]
    return Parsed("ofx", rows, skipped, kind=kind, balance=balance, number=number)


# --- QIF --------------------------------------------------------------------------------------

def parse_qif(text: str, date_format: str = "auto") -> Parsed:
    """A QIF statement (``!Type:Bank`` / ``CCard`` / ``Cash`` / ``Oth A`` / ``Oth L``); dates are read in
    ``date_format`` (``auto``: from the file's dates, month first when they cannot tell, as QIF is).

    Raises:
        BadFile: an investment or list section, or no transaction in it.
    """
    lines = [line.rstrip("\r") for line in text.splitlines()]
    kind = "bank"
    records: List[Dict[str, str]] = []
    current: Dict[str, str] = {}
    for line in lines:
        if not line.strip():
            continue
        if line.startswith("!"):
            head = line[1:].strip().lower()
            if head.startswith("type:"):
                section = head[5:].strip()
                if section in ("invst", "memorized", "cat", "class", "prices"):
                    raise BadFile("This QIF file holds no bank or card transactions (an investment or list section).")
                kind = "card" if section in ("ccard", "oth l") else "bank"
            continue
        if line.startswith("^"):
            if current:
                records.append(current)
            current = {}
            continue
        code, value = line[0].upper(), line[1:].strip()
        if code in ("D", "T", "U", "P", "M", "N") and code not in current:
            current[code] = value
    if current:
        records.append(current)
    order = date_format if date_format != "auto" else date_order([r.get("D", "") for r in records], default="mdy")
    mark = decimal_mark([r.get("T") or r.get("U") or "" for r in records])
    rows: List[Row] = []
    skipped = 0
    for r in records:
        when = parse_date(r.get("D", "").replace("'", "/"), order)
        value = parse_amount(r.get("T") or r.get("U") or "", mark)
        if when is None or value is None:
            skipped += 1
            continue
        payee, memo = r.get("P", ""), r.get("M", "")
        rows.append(Row(when, cents(value), payee or memo, memo if payee else ""))
    if not rows:
        raise BadFile("This QIF file has no transactions.")
    return Parsed("qif", rows, skipped, kind=kind)


def parse(data: bytes, mapping: Optional[ColumnMap] = None) -> Parsed:
    """The file in whichever format it is (``mapping`` is for a CSV: guessed when None).

    Raises:
        BadFile: not a statement this can read (the message says why).
        BadMapping: a CSV mapping that does not fit the file.
    """
    if len(data) > MAX_BYTES:
        raise BadFile(f"The file is over {MAX_BYTES // (1024 * 1024)} MB.")
    text = decode(data)
    fmt = sniff_format(text)
    if fmt == "ofx":
        return parse_ofx(text)
    if fmt == "qif":
        return parse_qif(text, mapping.date_format if mapping else "auto")
    return parse_csv(text, mapping)


# --- Accounts and mappings ----------------------------------------------------------------------

def importable(account: Mapping[str, Any]) -> bool:
    """Whether statement files can go into an account (bank accounts and cards)."""
    return account.get("kind") in ("bank", "card")


def accounts(user: User) -> List[Dict[str, Any]]:
    """The user's bank and card accounts files can go into: ``{id, label, kind, number, manual, mapping}``
    (``mapping``: the CSV mapping last used for it, or None), manual accounts first."""
    with store.db() as conn:
        maps = {r["account_id"]: r["mapping"] for r in conn.execute(
            "SELECT account_id, mapping FROM money_import_maps WHERE user_id = ?", (user.id,))}
    out = []
    for a in store.list_accounts(user):
        if not importable(a) or a.get("hidden"):
            continue
        manual = a["company"] == MANUAL
        out.append({"id": a["id"], "kind": a["kind"], "manual": manual,
                    "label": store.account_label(a["company"], a["kind"], a["number"], a.get("label"))
                    if not manual else (a.get("label") or "Imported account"),
                    "number": store.account_payload(a)["number"],
                    "balance": a.get("balance"), "balance_at": a.get("balance_at"),
                    "mapping": json.loads(maps[a["id"]]) if a["id"] in maps else None})
    return sorted(out, key=lambda a: (not a["manual"], a["kind"], a["label"].casefold()))


def remembered(user: User, account_id: Optional[str]) -> Optional[ColumnMap]:
    """The CSV mapping last used for the user's account, None without one."""
    if not account_id:
        return None
    with store.db() as conn:
        row = conn.execute("SELECT mapping FROM money_import_maps WHERE user_id = ? AND account_id = ?",
                           (user.id, account_id)).fetchone()
    if row is None:
        return None
    try:
        return ColumnMap.from_json(json.loads(row["mapping"]))
    except (BadMapping, ValueError):
        return None


def new_account(conn: Any, user: User, label: str, kind: str, now: str) -> Dict[str, Any]:
    """A manual account of the user's (company :data:`MANUAL`, no source) named ``label``.

    Raises:
        BadMapping: no name, or a kind other than bank / card.
    """
    label = " ".join((label or "").split())[:LABEL_MAX]
    if not label:
        raise BadMapping("Give the new account a name.")
    if kind not in ("bank", "card"):
        raise BadMapping("A new account is a bank account or a card.")
    account_id = uuid.uuid4().hex
    conn.execute("INSERT INTO money_accounts (id, user_id, source_id, company, kind, number, label, created_at, updated_at) "
                 "VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?)",
                 (account_id, user.id, MANUAL, kind, f"{MANUAL}:{account_id}", label, now, now))
    return {"id": account_id, "company": MANUAL, "kind": kind, "label": label, "number": f"{MANUAL}:{account_id}"}


# --- De-duplicating and storing ------------------------------------------------------------------

def fingerprint(date: str, amount: int, description: str) -> Tuple[str, int, str]:
    """What makes two statement rows the same: the date, the amount and the description's merchant key."""
    return date, amount, ingest.merchant_key(description)


def _existing(conn: Any, user: User, account_id: str) -> Tuple[Counter, set]:
    counts: Counter = Counter()
    keys = set()
    for r in conn.execute("SELECT purchase_date, amount, description, ext_key FROM money_transactions "
                          "WHERE user_id = ? AND account_id = ? AND source = 'scrape'", (user.id, account_id)):
        counts[fingerprint(r["purchase_date"], r["amount"], r["description"] or "")] += 1
        keys.add(r["ext_key"])
    return counts, keys


def _plan(parsed: Parsed, counts: Counter, keys: set) -> Tuple[List[Tuple[Row, str]], int]:
    """The rows to add with their ``ext_key``, and how many are already there."""
    seen: Counter = Counter()
    add: List[Tuple[Row, str]] = []
    duplicates = 0
    for row in parsed.rows:
        fp = fingerprint(row.date.isoformat(), row.amount, ingest.scrub(row.description).strip())
        n = seen[fp]
        seen[fp] += 1
        if n < counts[fp]:
            duplicates += 1
            continue
        digest = hashlib.sha1("|".join((fp[0], str(fp[1]), fp[2])).encode()).hexdigest()[:16]
        key = f"file:{ingest.scrub(row.ext_id)[:64]}" if row.ext_id else f"file:{digest}:{n}"
        if key in keys:
            duplicates += 1
            continue
        keys.add(key)
        add.append((row, key))
    return add, duplicates


def _stored(row: Row, key: str, company: Optional[providers.Company], fmt: str) -> Dict[str, Any]:
    """A parsed row in the fields :func:`ingest.insert_row` takes (text scrubbed)."""
    description = ingest.scrub(row.description).strip()
    memo = ingest.scrub(row.memo).strip()
    payee = ingest.transfer_payee(company, memo) if row.amount < 0 else None
    raw = {"file": fmt, "date": row.date.isoformat(), "amount": row.amount, "description": row.description,
           "memo": row.memo, "balance": row.balance, "id": row.ext_id or None}
    return {"ext_key": key, "status": "completed", "occurred_at": None, "purchase_date": row.date.isoformat(),
            "charge_date": None, "amount": row.amount, "orig_amount": None, "orig_currency": None,
            "description": description, "merchant_key": ingest.merchant_key(payee[0]) if payee else ingest.merchant_key(description),
            "memo": memo or None, "payee": payee[0] if payee else None, "installment_number": None,
            "installment_total": None,
            "raw": json.dumps(ingest.scrub_value(raw), ensure_ascii=False, sort_keys=True)}


def _account_or_404(conn: Any, user: User, account_id: str) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM money_accounts WHERE id = ? AND user_id = ?", (account_id, user.id)).fetchone()
    if row is None:
        raise LookupError("No such account.")
    account = dict(row)
    if not importable(account):
        raise BadMapping("Statement files go into a bank account or a card.")
    return account


def _rows_payload(rows: Sequence[Row]) -> List[Dict[str, Any]]:
    return [{"date": r.date.isoformat(), "description": r.description, "memo": r.memo or None, "amount": r.amount,
             "balance": r.balance} for r in rows]


def _parse(user: User, data: bytes, account_id: Optional[str], mapping: Optional[ColumnMap]) -> Parsed:
    """The file read with ``mapping``, else the account's remembered one, else a guess (when the
    remembered one fits another layout)."""
    if mapping is not None:
        return parse(data, mapping)
    saved = remembered(user, account_id)
    if saved is not None:
        try:
            return parse(data, saved)
        except (BadFile, BadMapping):
            pass
    return parse(data, None)


def preview(user: User, data: bytes, account_id: Optional[str] = None,
            mapping: Optional[ColumnMap] = None) -> Dict[str, Any]:
    """What importing ``data`` would do, nothing stored: ``{format, rows (the first
    :data:`PREVIEW_ROWS`), count, skipped, duplicates (into ``account_id``, when given), new, kind,
    columns, sample, mapping}`` (``columns`` / ``sample`` / ``mapping``: a CSV's, the mapping given,
    else the one remembered for the account, else the guess).

    Raises:
        BadFile, BadMapping: as :func:`parse`; LookupError: not the user's account.
    """
    parsed = _parse(user, data, account_id, mapping)
    duplicates = 0
    if account_id:
        with store.db() as conn:
            _account_or_404(conn, user, account_id)
            counts, keys = _existing(conn, user, account_id)
        duplicates = _plan(parsed, counts, keys)[1]
    return {"format": parsed.format, "rows": _rows_payload(parsed.rows[:PREVIEW_ROWS]), "count": len(parsed.rows),
            "skipped": parsed.skipped, "duplicates": duplicates, "new": len(parsed.rows) - duplicates,
            "kind": parsed.kind, "columns": parsed.columns, "sample": parsed.sample,
            "mapping": parsed.mapping.to_json() if parsed.mapping else None}


async def run(user: User, data: bytes, *, account_id: Optional[str] = None, new_label: str = "",
              new_kind: str = "", mapping: Optional[ColumnMap] = None) -> Dict[str, Any]:
    """Import ``data`` into the user's account ``account_id``, or into a new manual account named
    ``new_label`` (``new_kind``: bank / card; the file's own kind when empty). Returns ``{format,
    account {id, label, kind}, added, duplicates, skipped}``; the new rows are then classified
    (the after-sync hooks, as the user).

    Raises:
        BadFile, BadMapping: as :func:`parse`, or no account given; LookupError: not the user's account.
    """
    import asyncio  # the parsing and storing run in a worker thread
    result = await asyncio.to_thread(_store, user, data, account_id, new_label, new_kind, mapping)
    if result["new_ids"]:
        await after_sync.run(user, "", result["new_ids"], [])
    result.pop("new_ids")
    return result


def _store(user: User, data: bytes, account_id: Optional[str], new_label: str, new_kind: str,
           mapping: Optional[ColumnMap]) -> Dict[str, Any]:
    if not account_id and not new_label.strip():
        raise BadMapping("Choose an account, or name a new one.")
    parsed = _parse(user, data, account_id, mapping)
    now = store.now_iso()
    with store.db() as conn:
        if account_id:
            account = _account_or_404(conn, user, account_id)
        else:
            account = new_account(conn, user, new_label, new_kind or parsed.kind or "bank", now)
        company = providers.company(account["company"])
        counts, keys = _existing(conn, user, account["id"])
        add, duplicates = _plan(parsed, counts, keys)
        rules = ingest.rules_of(conn, user)
        new_ids = [ingest.insert_row(conn, user, account["id"], _stored(row, key, company, parsed.format), rules, now)
                   for row, key in add]
        if account["company"] == MANUAL and account["kind"] == "bank":
            latest = max(parsed.rows, key=lambda r: r.date)
            balance = parsed.balance if parsed.balance is not None else latest.balance
            if balance is not None:
                conn.execute("UPDATE money_accounts SET balance = ?, balance_at = ?, updated_at = ? "
                             "WHERE id = ? AND user_id = ?", (balance, now, now, account["id"], user.id))
        if parsed.format == "csv" and parsed.mapping is not None:
            conn.execute("INSERT INTO money_import_maps (user_id, account_id, mapping, updated_at) VALUES (?, ?, ?, ?) "
                         "ON CONFLICT(user_id, account_id) DO UPDATE SET mapping = excluded.mapping, "
                         "updated_at = excluded.updated_at",
                         (user.id, account["id"], json.dumps(parsed.mapping.to_json()), now))
    label = account.get("label") or store.account_label(account["company"], account["kind"], account["number"])
    return {"format": parsed.format, "account": {"id": account["id"], "label": label, "kind": account["kind"],
                                                 "manual": account["company"] == MANUAL},
            "added": len(new_ids), "duplicates": duplicates, "skipped": parsed.skipped, "new_ids": new_ids}
