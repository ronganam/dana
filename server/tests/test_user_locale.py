"""A user's languages and currency, the server's defaults and the phone country code (core/user_locale.py).
Run: bin/test test_user_locale

Covers: parsing and validating language / currency codes; the server's
``DEFAULT_LANGUAGES`` / ``CURRENCY`` (defaults ``en`` / ``USD``, a malformed
value falls back); the first admin's ``OWNER_LANGUAGES`` / ``OWNER_CURRENCY``
(the m0004 columns); the agent's language rule and the transcription prompt
worded for one language and for several (the Hebrew alias only for a Hebrew
speaker); the knowledge prompt's ``{currency}``; ``DEFAULT_COUNTRY_CODE``
unset (numbers stay as given) and set (a leading 0 replaced), in people and
WhatsApp; ``GET`` / ``PUT /api/profile`` (each user their own, 400 on a bad
code, back to the server's with ``[]`` / ``""``) and ``GET /api/me``'s
``addons`` / ``languages`` / ``currency``.
"""

import asyncio
import os

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("user_locale_test")
os.environ["API_TOKEN"] = "user-locale-env-token"  # a test value: the first admin's token (a headless install)

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from core import agent, db, llm, profile_api, addons_api, user_locale, users  # noqa: E402
from core.auth import setup_auth  # noqa: E402
from core.db import get_db_connection  # noqa: E402

db.migrate()
users.bootstrap()
from addons import bootstrap as addons_bootstrap  # noqa: E402
addons_bootstrap.bootstrap()

from addons.people.values import norm_phone, shown_phone  # noqa: E402
from addons.whatsapp.client import normalize_to_international  # noqa: E402

failures = 0


def check(name: str, cond: bool, detail: object = "") -> None:
    global failures
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    failures += 0 if cond else 1


def raises(fn, *args) -> bool:
    try:
        fn(*args)
    except ValueError:
        return True
    return False


# --- codes ---------------------------------------------------------------------------------
check("languages: a comma list, lower case, duplicates dropped",
      user_locale.parse_languages(" HE, en ,he,pt_BR") == ("he", "en", "pt-br"))
check("languages: a list too, nothing is ()", user_locale.parse_languages(["es"]) == ("es",)
      and user_locale.parse_languages("") == () and user_locale.parse_languages(None) == ())
check("languages: a malformed code is refused", raises(user_locale.parse_languages, "hebrew!")
      and raises(user_locale.parse_languages, "e"))
check("currency: upper case, '' for nothing", user_locale.check_currency(" ils ") == "ILS"
      and user_locale.check_currency(None) == "")
check("currency: anything but three letters is refused", raises(user_locale.check_currency, "shekel")
      and raises(user_locale.check_currency, "₪"))

# --- the server's defaults -----------------------------------------------------------------
check("server: en / USD when unset", user_locale.server_languages() == ("en",) and user_locale.server_currency() == "USD")
os.environ.update({"DEFAULT_LANGUAGES": "fr,en", "CURRENCY": "eur"})
check("server: DEFAULT_LANGUAGES / CURRENCY", user_locale.server_languages() == ("fr", "en")
      and user_locale.server_currency() == "EUR")
os.environ.update({"DEFAULT_LANGUAGES": "??", "CURRENCY": "euros"})
check("server: a malformed value falls back to en / USD", user_locale.server_languages() == ("en",)
      and user_locale.server_currency() == "USD")
del os.environ["DEFAULT_LANGUAGES"], os.environ["CURRENCY"]

# --- the first admin and a member ----------------------------------------------------------
ADMIN = users.first_admin()
assert ADMIN is not None
BOB = users.create("Bob")
with get_db_connection() as conn:
    row = conn.execute("SELECT languages, currency FROM users WHERE id = ?", (ADMIN.id,)).fetchone()
check("first admin: OWNER_LANGUAGES / OWNER_CURRENCY stored (users.languages / currency)",
      tuple(row) == ("he,en", "ILS") and ADMIN.languages == "he,en" and ADMIN.currency == "ILS", tuple(row))
check("member: nothing of their own, the server's apply", BOB.languages == "" and BOB.currency == ""
      and user_locale.languages(BOB) == ("en",) and user_locale.currency(BOB) == "USD")
check("languages(None): the server's", user_locale.languages(None) == ("en",))

# --- the agent's rule 5 and the transcription prompt ---------------------------------------
he = agent.instructions(ADMIN)
en = agent.instructions(BOB)
check("agent, he+en: the two-language rule names Hebrew and English, and the Hebrew alias",
      "5. LANGUAGE CONSISTENCY: The user communicates in Hebrew (עברית) and English." in he
      and "(or דנה in Hebrew)" in he and "'בוצע.'" in he)
check("agent, en only: a one-language rule, no Hebrew anywhere in it",
      "5. LANGUAGE: The user communicates in English. Respond in English" in en
      and "LANGUAGE CONSISTENCY" not in en and "Hebrew" not in en and "דנה" not in en)
es = users.update(BOB.id, languages="es,en")
check("agent, es+en: Spanish named, its confirmation word in the examples",
      "communicates in Spanish (español) and English" in agent.instructions(es) and "'Hecho.'" in agent.instructions(es))
check("transcription, he+en: Hebrew letters, Arabic among the languages to avoid",
      "primarily speaks Hebrew (עברית) or English" in llm.transcription_prompt(("he", "en"))
      and "transcribe in Hebrew letters" in llm.transcription_prompt(("he", "en"))
      and "Arabic" in llm.transcription_prompt(("he", "en")))
p = llm.transcription_prompt(("es",))
check("transcription, es: one language, no Hebrew", "The speaker primarily speaks Spanish (español). " in p
      and "Hebrew" not in p and "code-switching" not in p, p)

# --- knowledge's {currency} ----------------------------------------------------------------
testenv.connect_admin("knowledge")
from core.addons import store  # noqa: E402
store.connect(es, "knowledge")
check("knowledge part: money in the user's currency (ILS for the admin, USD for a member)",
      "plain numbers in ILS" in agent.instructions(users.get(ADMIN.id))
      and "plain numbers in USD" in agent.instructions(users.get(es.id)))

# --- the phone country code ----------------------------------------------------------------
check("country code: unset, a local number stays as given", user_locale.country_code() == ""
      and norm_phone("050-123-4567") == "0501234567" and shown_phone("0501234567") == "0501234567"
      and normalize_to_international("050-1234567") == "0501234567")
check("country code: 00 and + are international anyway", norm_phone("00972501234567") == "972501234567"
      and norm_phone("+44 20 7946 0958") == "442079460958" and shown_phone("442079460958") == "+442079460958")
os.environ["DEFAULT_COUNTRY_CODE"] = "+972"
check("country code: set, a leading 0 becomes it (people, WhatsApp)", user_locale.country_code() == "972"
      and norm_phone("050-123-4567") == "972501234567"
      and normalize_to_international("050-1234567") == "972501234567")
del os.environ["DEFAULT_COUNTRY_CODE"]


# --- /api/profile and /api/me --------------------------------------------------------------
async def api() -> None:
    app = web.Application()
    setup_auth(app)
    addons_api.setup(app)
    profile_api.setup(app)
    admin_auth = {"Authorization": f"Bearer {users.issue_token(ADMIN.id, 'test')[0]}"}
    bob_auth = {"Authorization": f"Bearer {users.issue_token(BOB.id, 'test')[0]}"}
    async with TestClient(TestServer(app)) as c:
        r = await c.get("/api/profile", headers=admin_auth)
        body = await r.json()
        check("profile: the admin's own and the server's defaults", r.status == 200 and body == {
            "name": ADMIN.name, "languages": ["he", "en"], "currency": "ILS",
            "own": {"languages": ["he", "en"], "currency": "ILS"},
            "defaults": {"languages": ["en"], "currency": "USD"}}, body)
        r = await c.put("/api/profile", headers=bob_auth, json={"languages": "de, EN", "currency": "chf"})
        body = await r.json()
        check("profile: a member changes theirs", r.status == 200 and body["languages"] == ["de", "en"]
              and body["currency"] == "CHF" and users.get(BOB.id).languages == "de,en", body)
        check("profile: the admin's untouched", users.get(ADMIN.id).languages == "he,en"
              and users.get(ADMIN.id).currency == "ILS")
        r = await c.get("/api/me", headers=bob_auth)
        me = await r.json()
        check("me: addons, languages and currency (kinds repeats addons for the old app)", r.status == 200 and me["languages"] == ["de", "en"]
              and me["currency"] == "CHF" and "essentials" in me["addons"] and me["kinds"] == me["addons"], me)
        r = await c.put("/api/profile", headers=bob_auth, json={"currency": "francs"})
        body = await r.json()
        check("profile: a bad currency is 400 with the field, nothing changed",
              r.status == 400 and body.get("field") == "currency" and users.get(BOB.id).currency == "CHF", body)
        r = await c.put("/api/profile", headers=bob_auth, json={"languages": ["de", "x!"]})
        body = await r.json()
        check("profile: a bad language is 400 with the field", r.status == 400 and body.get("field") == "languages"
              and users.get(BOB.id).languages == "de,en", body)
        r = await c.put("/api/profile", headers=bob_auth, json={"languages": [], "currency": ""})
        body = await r.json()
        check("profile: [] / '' go back to the server's", r.status == 200 and body["own"] == {"languages": [], "currency": ""}
              and body["languages"] == ["en"] and body["currency"] == "USD", body)
        r = await c.get("/api/profile")
        check("profile: needs credentials", r.status == 401)


asyncio.run(api())

print(f"\n{'All checks passed' if not failures else f'{failures} check(s) failed'}")
raise SystemExit(1 if failures else 0)
