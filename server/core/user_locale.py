"""A user's languages and currency, and the server's phone country code
(docs/configuration.md → Locale; docs/access.md → User).

- **Languages**: the user's own (``users.languages``, a comma list of language
  codes, the first one the default reply language), else the server's
  ``DEFAULT_LANGUAGES`` (default ``en``). They word the agent's language rule
  (core/agent.py) and the transcription prompt (core/llm.py).
- **Currency**: the user's own (``users.currency``, ISO 4217), else the
  server's ``CURRENCY`` (default ``USD``): what money values mean (money's
  amounts, knowledge's budgets and prices). :func:`format_money` writes an
  amount in it ("€1,234.50", "CHF 80.00").
- **Country code**: ``DEFAULT_COUNTRY_CODE`` (server-wide, no default): a
  phone number typed with a leading 0 gets it in place of the 0; unset, a
  number stays as given (people, WhatsApp).
"""

import os
import re
from typing import Iterable, Optional, Tuple

LANGUAGES_ENV = "DEFAULT_LANGUAGES"
CURRENCY_ENV = "CURRENCY"
COUNTRY_CODE_ENV = "DEFAULT_COUNTRY_CODE"
FALLBACK_LANGUAGES: Tuple[str, ...] = ("en",)
FALLBACK_CURRENCY = "USD"

_CODE = re.compile(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})*")
_CURRENCY = re.compile(r"[A-Z]{3}")

# Language code -> (English name, the name in that language when it differs).
NAMES = {
    "en": ("English", ""), "he": ("Hebrew", "עברית"), "ar": ("Arabic", "العربية"), "ru": ("Russian", "русский"),
    "es": ("Spanish", "español"), "fr": ("French", "français"), "de": ("German", "Deutsch"),
    "it": ("Italian", "italiano"), "pt": ("Portuguese", "português"), "nl": ("Dutch", "Nederlands"),
    "pl": ("Polish", "polski"), "tr": ("Turkish", "Türkçe"), "fa": ("Persian", "فارسی"),
    "uk": ("Ukrainian", "українська"), "zh": ("Chinese", "中文"), "ja": ("Japanese", "日本語"),
    "ko": ("Korean", "한국어"), "hi": ("Hindi", "हिन्दी"), "am": ("Amharic", "አማርኛ"), "yi": ("Yiddish", "ייִדיש"),
    "ro": ("Romanian", "română"), "el": ("Greek", "Ελληνικά"), "sv": ("Swedish", "svenska"),
    "da": ("Danish", "dansk"), "no": ("Norwegian", "norsk"), "fi": ("Finnish", "suomi"), "cs": ("Czech", "čeština"),
    "hu": ("Hungarian", "magyar"), "th": ("Thai", "ไทย"), "vi": ("Vietnamese", "Tiếng Việt"),
    "id": ("Indonesian", "Bahasa Indonesia"),
}
# Currency code -> its narrow symbol; a code not listed is written before the amount ("CHF 80.00").
SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "ILS": "₪", "JPY": "¥", "CNY": "¥", "INR": "₹", "KRW": "₩",
           "RUB": "₽", "UAH": "₴", "TRY": "₺", "PLN": "zł ", "BRL": "R$", "CAD": "CA$", "AUD": "A$", "NZD": "NZ$",
           "MXN": "MX$", "PHP": "₱", "VND": "₫", "THB": "฿", "NGN": "₦"}
# Scripts worth naming in a transcription prompt ("transcribe in Hebrew letters").
SCRIPTS = {"he": "Hebrew letters", "yi": "Hebrew letters", "ar": "Arabic letters", "fa": "Persian (Arabic) letters",
           "ru": "Cyrillic letters", "uk": "Cyrillic letters", "el": "Greek letters", "hi": "Devanagari",
           "am": "Ge'ez letters", "th": "Thai letters"}


def parse_languages(value: object) -> Tuple[str, ...]:
    """Language codes from a comma list or a list ("he, en" → ``("he", "en")``),
    lower case, duplicates dropped; ``()`` for nothing.

    Raises:
        ValueError: an entry that is not a language code (``en``, ``pt-br``…).
    """
    items: Iterable[object] = value.split(",") if isinstance(value, str) else (value or [])  # type: ignore[assignment]
    out = []
    for item in items:
        code = str(item).strip().lower().replace("_", "-")
        if not code:
            continue
        if not _CODE.fullmatch(code):
            raise ValueError(f"{str(item).strip()!r} is not a language code (like en, he, pt-br).")
        if code not in out:
            out.append(code)
    return tuple(out)


def check_currency(value: object) -> str:
    """An ISO 4217 code in upper case ("ils" → ``ILS``), ``""`` for nothing.

    Raises:
        ValueError: anything that is not three letters.
    """
    code = str(value or "").strip().upper()
    if code and not _CURRENCY.fullmatch(code):
        raise ValueError(f"{str(value).strip()!r} is not a currency code (three letters, like USD, EUR, ILS).")
    return code


def server_languages() -> Tuple[str, ...]:
    """``DEFAULT_LANGUAGES`` (a malformed value falls back to ``en``)."""
    try:
        return parse_languages(os.getenv(LANGUAGES_ENV, "")) or FALLBACK_LANGUAGES
    except ValueError:
        return FALLBACK_LANGUAGES


def server_currency() -> str:
    """``CURRENCY`` (a malformed value falls back to ``USD``)."""
    try:
        return check_currency(os.getenv(CURRENCY_ENV, "")) or FALLBACK_CURRENCY
    except ValueError:
        return FALLBACK_CURRENCY


def languages(user: Optional[object]) -> Tuple[str, ...]:
    """``user``'s languages (their own, else the server's)."""
    try:
        own = parse_languages(getattr(user, "languages", "") or "")
    except ValueError:
        own = ()
    return own or server_languages()


def currency(user: Optional[object]) -> str:
    """``user``'s currency (their own, else the server's)."""
    try:
        own = check_currency(getattr(user, "currency", "") or "")
    except ValueError:
        own = ""
    return own or server_currency()


def currency_symbol(code: str) -> str:
    """"€" for EUR, "₪" for ILS; a code without a symbol here as "CHF " (with its space)."""
    code = (code or "").upper()
    return SYMBOLS.get(code, f"{code} " if code else "")


def format_money(hundredths: Optional[int], code: str, *, cents: bool = True) -> str:
    """An amount kept in hundredths of the unit (cents, agorot) in the currency
    ``code``: "€1,234.50", "-$50.00", "CHF 80.00"; ``cents=False`` rounds to
    whole units ("€1,235"); "—" for None."""
    if hundredths is None:
        return "—"
    sign = "-" if hundredths < 0 else ""
    value = abs(int(hundredths))
    sym = currency_symbol(code)
    if not cents:
        return f"{sign}{sym}{(value + 50) // 100:,}"
    whole, rest = divmod(value, 100)
    return f"{sign}{sym}{whole:,}.{rest:02d}"


def language_name(code: str, native: bool = True) -> str:
    """"Hebrew (עברית)" for ``he`` (with ``native``), "English" for ``en``,
    the code itself for a language this table does not name."""
    english, own = NAMES.get(code.split("-")[0], (code, ""))
    return f"{english} ({own})" if native and own else english


def join_names(names: Tuple[str, ...], word: str = "and") -> str:
    """"A", "A and B", "A, B and C"."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} {word} {names[-1]}"


def country_code() -> str:
    """``DEFAULT_COUNTRY_CODE`` as digits ("+972" → "972"), ``""`` when unset."""
    return re.sub(r"\D", "", os.getenv(COUNTRY_CODE_ENV, ""))


def international_digits(number: str) -> str:
    """The digits of a phone number in international form: ``00`` dropped,
    and a leading ``0`` replaced by :func:`country_code` when it is set (unset:
    the number stays as given)."""
    digits = re.sub(r"\D", "", number or "")
    if digits.startswith("00"):
        return digits[2:]
    cc = country_code()
    if cc and digits.startswith("0"):
        return cc + digits[1:]
    return digits
