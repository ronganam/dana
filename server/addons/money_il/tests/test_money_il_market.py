"""money_il price checks: llm.search and its sources, the check pipeline, validation, rate limits and storage per user (docs/money-il.md, "Price checks").
Run: bin/test test_money_il_market

No network and no real model: ``litellm.acompletion`` answers with built
LiteLLM responses in each provider's grounding shape (Gemini's grounding
metadata on the response or only in its hidden params, OpenAI's
``url_citation`` annotations, Anthropic's citations), ``llm.complete_json``
is a fake that answers from a table, and the grounding-redirect resolver is
patched (its real code runs once against an ``httpx.MockTransport``).
Checked: ``llm.search`` (sources in order without repeats, the ``[n]``
passage marks, the call's options and usage tag, one retry, ``NoSearch``);
``llm.can_search``; params validation (unknown keys, ranges, canonical
form); privacy (the search prompt is the same for two users with different
data, built from the params alone: no names, merchants, account numbers or
amounts but ``current_price``); validation (a page the search did not
return or a non-web page, prices / discounts / fees out of range, repeats,
the 12-month average, at most 8, sorting); arnona found / not found / an
unsourced "found"; start → task → latest ok with resolved pages; Running
while in flight; TooSoon with ``retry_after`` (6 h, 5 min after a failure);
the failed paths (model error, no search, nothing sourced, timeout) keeping
the last good result; stale; an old "checking" row reading failed;
Unavailable; two users isolated both ways; ``store.delete_all``.
All values are invented (city "גבעת אורנים", made-up providers and prices).
"""

import asyncio
import datetime
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import patch

import testenv  # first: clean env, temp DB removed at exit, no .env (see testenv.py)
testenv.isolate("money_market_test")
os.environ["TIMEZONE"] = "Asia/Jerusalem"
os.environ["OWNER_NAME"] = "Tamar Shaked"
os.environ["SEARCH_MODEL"] = "gemini/gemini-flash-latest"

from core import db, users  # noqa: E402

db.migrate()
users.bootstrap()

import httpx  # noqa: E402
import litellm  # noqa: E402

from core import clock, llm  # noqa: E402
from core.db import get_db_connection  # noqa: E402
from addons.money import providers, store  # noqa: E402
from addons.money_il import market  # noqa: E402

results: List[Tuple[str, bool]] = []


def check(name: str, condition: Any, detail: Any = "") -> None:
    results.append((name, bool(condition)))
    print(f"[{'PASS' if condition else 'FAIL'}] {name}" + (f" — {detail}" if detail and not condition else ""))


ADMIN = users.first_admin()
assert ADMIN is not None
BOB = users.create("Bob Karmeli")
CAROL = users.create("Carol Levinson")

# Each user's own money rows (invented): none of it may reach a prompt.
PRIVATE = {
    ADMIN.id: {"account": "12-345-678901", "merchant": "מאפיית הגבעה הירוקה", "amount": -18765},
    BOB.id: {"account": "98-765-432109", "merchant": "מוסך הכרמל הצפוני", "amount": -45321},
}
_now = store.now_iso()
with get_db_connection() as _conn:
    for _uid, _p in PRIVATE.items():
        _conn.execute("INSERT INTO money_accounts (id, user_id, company, kind, number, created_at, updated_at) "
                      "VALUES (?, ?, 'hapoalim', 'bank', ?, ?, ?)", (f"acc-{_uid}", _uid, _p["account"], _now, _now))
        _conn.execute("INSERT INTO money_transactions (id, user_id, account_id, ext_key, source, status, purchase_date, "
                      "amount, description, merchant, created_at, updated_at) VALUES (?, ?, ?, 'k1', 'scrape', "
                      "'completed', '2026-11-02', ?, ?, ?, ?, ?)",
                      (f"txn-{_uid}", _uid, f"acc-{_uid}", _p["amount"], _p["merchant"], _p["merchant"], _now, _now))
    _conn.commit()

REDIRECT = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/"
PAGES = {  # redirect → the page it leads to (the patched resolver)
    REDIRECT + "aaa": "https://www.example-cellular.co.il/plans/5g",
    REDIRECT + "bbb": "https://compare.example.co.il/mobile",
    REDIRECT + "ccc": "https://www.example-mobile.co.il/deals",
}


async def fake_resolve(client: httpx.AsyncClient, url: str) -> str:
    return PAGES.get(url, url)


def gemini(text: str, chunks: List[Tuple[str, str]], supports: Tuple[Tuple[str, List[int]], ...] = (),
           hidden_only: bool = False) -> litellm.ModelResponse:
    """A Gemini reply as LiteLLM builds it: grounding metadata on the response and in its hidden params."""
    response = litellm.ModelResponse(choices=[{"message": {"role": "assistant", "content": text},
                                               "finish_reason": "stop"}])
    meta = [{"webSearchQueries": ["mobile plans israel"],
             "groundingChunks": [{"web": {"uri": uri, "title": title}} for title, uri in chunks],
             "groundingSupports": [{"segment": {"text": passage}, "groundingChunkIndices": idx}
                                   for passage, idx in supports]}]
    if not hidden_only:
        setattr(response, "vertex_ai_grounding_metadata", meta)
    response._hidden_params["vertex_ai_grounding_metadata"] = meta
    return response


def openai(text: str, cites: List[Tuple[str, str]]) -> litellm.ModelResponse:
    annotations = [{"type": "url_citation", "url_citation": {"url": url, "title": title, "start_index": 0,
                                                             "end_index": 1}} for title, url in cites]
    return litellm.ModelResponse(choices=[{"message": {"role": "assistant", "content": text,
                                                       "annotations": annotations}, "finish_reason": "stop"}])


def anthropic(text: str, cites: List[Tuple[str, str]]) -> litellm.ModelResponse:
    citations = [[{"type": "web_search_result_location", "url": url, "title": title, "cited_text": "x"}]
                 for title, url in cites]
    return litellm.ModelResponse(choices=[{"message": {"role": "assistant", "content": text,
                                                       "provider_specific_fields": {"citations": citations}},
                                           "finish_reason": "stop"}])


class Fail(Exception):
    def __init__(self, status: Optional[int]) -> None:
        super().__init__(f"provider said {status}")
        self.status_code = status


class FakeCompletion:
    """``litellm.acompletion``: answers with the scripted replies in order, recording each call."""

    def __init__(self, *replies: Any, delay: float = 0.0) -> None:
        self.replies = list(replies)
        self.calls: List[Dict[str, Any]] = []
        self.delay = delay

    async def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply


class FakeExtract:
    """``llm.complete_json``: the answer for the acting user (or a default), recording each prompt."""

    def __init__(self, answers: Dict[Optional[str], Any]) -> None:
        self.answers = answers
        self.calls: List[Dict[str, Any]] = []

    async def __call__(self, prompt: str, schema: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        self.calls.append({"prompt": prompt, "schema": schema, "user": users.current_id(), **kwargs})
        answer = self.answers.get(users.current_id(), self.answers.get(None))
        if isinstance(answer, Exception):
            raise answer
        return answer


SEARCH_TEXT = ("Example Cellular offers its 5G plan at ₪29.90 a month for the first 6 months, then ₪59.90. "
               "Example Mobile sells an unlimited plan for ₪39.90 with no commitment.")
TELECOM_REPLY = gemini(SEARCH_TEXT, [("example-cellular.co.il", REDIRECT + "aaa"), ("compare.example.co.il", REDIRECT + "bbb"),
                                     ("example-mobile.co.il", REDIRECT + "ccc")],
                       supports=(("Example Cellular offers its 5G plan at ₪29.90 a month for the first 6 months, then ₪59.90.", [0, 1]),
                                 ("Example Mobile sells an unlimited plan for ₪39.90 with no commitment.", [2])))


def telecom_answer(extra: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    items = [
        {"provider": "Example Cellular", "plan": "5G 300GB", "price": 59.9, "promo_price": 29.9, "promo_months": 6,
         "conditions": "no commitment", "source": 1},
        {"provider": "Example Mobile", "plan": "Unlimited", "price": 39.9, "promo_price": None, "promo_months": None,
         "conditions": "", "source": 3},
        {"provider": "Made Up Telecom", "plan": "Basic", "price": 19.9, "promo_price": None, "promo_months": None,
         "conditions": "", "source": 7},  # page 7 was never returned
        {"provider": "Pricey", "plan": "Gold", "price": 899, "promo_price": None, "promo_months": None,
         "conditions": "", "source": 2},  # out of range
    ]
    return {"items": items + (extra or [])}


TELECOM = {"service": "mobile", "lines": 2, "current_price": 129}


def set_row(user: users.User, topic: str, params: Dict[str, Any], **fields: Any) -> None:
    key = market.params_key(market.normalize(topic, params))
    columns = ", ".join(f"{k} = ?" for k in fields)
    with get_db_connection() as conn:
        conn.execute(f"UPDATE money_market_checks SET {columns} WHERE user_id = ? AND topic = ? AND params_key = ?",
                     (*fields.values(), user.id, topic, key))
        conn.commit()


def ago(**delta: float) -> str:
    return store.iso(store.utc_now() - datetime.timedelta(**delta))


async def settle() -> None:
    """Wait for every running check."""
    tasks = list(market._running.values())
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


# --- llm.search ----------------------------------------------------------------------------------

async def test_search() -> None:
    fake = FakeCompletion(gemini(SEARCH_TEXT, [("a.example", REDIRECT + "aaa"), ("b.example", REDIRECT + "bbb"),
                                               ("a.example", REDIRECT + "aaa")],
                                 supports=(("then ₪59.90.", [0, 1]), ("no commitment.", [1]))))
    with patch.object(llm.litellm, "acompletion", fake), users.acting_as(BOB):
        out = await llm.search("find plans", purpose="money_market", ref="money_market:telecom", source="app",
                               timeout=33)
    check("search: Gemini grounding chunks → sources in order, a repeat dropped",
          out["sources"] == [{"title": "a.example", "url": REDIRECT + "aaa"}, {"title": "b.example", "url": REDIRECT + "bbb"}],
          out["sources"])
    check("search: [n] after each passage the grounding ties to a source",
          "then ₪59.90. [1] [2]" in out["text"] and "no commitment. [2]" in out["text"], out["text"])
    call = fake.calls[0]
    check("search: the call (search model, web_search_options medium, drop_params, one user message, timeout)",
          call["model"] == "gemini/gemini-flash-latest" and call["web_search_options"] == {"search_context_size": "medium"}
          and call["drop_params"] is True and call["messages"] == [{"role": "user", "content": "find plans"}]
          and call["timeout"] == 33, {k: v for k, v in call.items() if k != "litellm_metadata"})
    tag = call["litellm_metadata"]["usage"]
    check("search: usage tag (purpose, ref, source, the acting user)",
          tag["purpose"] == "money_market" and tag["ref"] == "money_market:telecom" and tag["source"] == "app"
          and tag["user_id"] == BOB.id, tag)

    hidden = gemini("text", [("c.example", "https://c.example/p")], hidden_only=True)
    with patch.object(llm.litellm, "acompletion", FakeCompletion(hidden)):
        out = await llm.search("q", purpose="money_market")
    check("search: grounding only in the hidden params is read too",
          out["sources"] == [{"title": "c.example", "url": "https://c.example/p"}], out)

    reply = openai("Plans [Example](https://x.example/a)", [("X plans", "https://x.example/a"), ("X plans", "https://x.example/a"),
                                                            ("", "https://y.example/b")])
    with patch.object(llm.litellm, "acompletion", FakeCompletion(reply)):
        out = await llm.search("q", purpose="money_market")
    check("search: OpenAI url_citation annotations → sources (repeat dropped, no title → the URL)",
          out["sources"] == [{"title": "X plans", "url": "https://x.example/a"},
                             {"title": "https://y.example/b", "url": "https://y.example/b"}], out["sources"])

    with patch.object(llm.litellm, "acompletion", FakeCompletion(anthropic("t", [("Z", "https://z.example/")]))):
        out = await llm.search("q", purpose="money_market")
    check("search: Anthropic citations → sources", out["sources"] == [{"title": "Z", "url": "https://z.example/"}], out)

    bare = litellm.ModelResponse(choices=[{"message": {"role": "assistant", "content": "I think ₪30"}, "finish_reason": "stop"}])
    try:
        with patch.object(llm.litellm, "acompletion", FakeCompletion(bare)):
            await llm.search("q", purpose="money_market")
        check("search: no sources → NoSearch", False)
    except llm.NoSearch:
        check("search: no sources → NoSearch", True)

    async def no_wait(_s: float) -> None:
        return None

    flaky = FakeCompletion(Fail(503), hidden)
    with patch.object(llm.litellm, "acompletion", flaky), patch.object(llm.asyncio, "sleep", no_wait):
        out = await llm.search("q", purpose="money_market")
    check("search: a 503 is retried once", len(flaky.calls) == 2 and out["sources"], len(flaky.calls))
    refused = FakeCompletion(Fail(400))
    try:
        with patch.object(llm.litellm, "acompletion", refused):
            await llm.search("q", purpose="money_market")
        check("search: a 400 is not retried (RuntimeError)", False)
    except RuntimeError as e:
        check("search: a 400 is not retried (RuntimeError)", len(refused.calls) == 1 and not isinstance(e, llm.NoSearch))
    twice = FakeCompletion(Fail(None))
    try:
        with patch.object(llm.litellm, "acompletion", twice), patch.object(llm.asyncio, "sleep", no_wait):
            await llm.search("q", purpose="money_market")
        check("search: a network error twice → RuntimeError after 2 calls", False)
    except RuntimeError:
        check("search: a network error twice → RuntimeError after 2 calls", len(twice.calls) == 2)

    with patch.dict(os.environ, {"SEARCH_MODEL": "", "MODEL": "openai/gpt-test"}):
        check("search model falls back to MODEL", llm.model("search") == "openai/gpt-test", llm.model("search"))
    with patch.object(llm, "_missing_keys", return_value=[]), patch.object(llm.litellm, "supports_web_search", return_value=True):
        ok = llm.can_search()
    with patch.object(llm, "_missing_keys", return_value=[]), patch.object(llm.litellm, "supports_web_search", return_value=False):
        no_search = llm.can_search()
    with patch.object(llm, "_missing_keys", return_value=["GEMINI_API_KEY"]), \
            patch.object(llm.litellm, "supports_web_search", return_value=True):
        no_key = llm.can_search()
    with patch.object(llm, "_missing_keys", return_value=[]), \
            patch.object(llm.litellm, "supports_web_search", side_effect=Exception("unknown model")):
        unknown = llm.can_search()
    check("can_search: key + web search; not without either, not for an unknown model",
          ok and not no_search and not no_key and not unknown)


async def test_resolver() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "vertexaisearch.cloud.google.com":
            if request.url.path.endswith("/dead"):
                raise httpx.ConnectError("unreachable")
            if request.url.path.endswith("/weird"):
                return httpx.Response(302, headers={"location": "ftp://files.example/x"})
            return httpx.Response(302, headers={"location": "https://www.example-cellular.co.il/plans"})
        return httpx.Response(200)

    seen: List[str] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return handler(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(recording), follow_redirects=True) as client:
        ok = await market.resolve_url(client, REDIRECT + "abc")
        dead = await market.resolve_url(client, REDIRECT + "dead")
        weird = await market.resolve_url(client, REDIRECT + "weird")
        before = len(seen)
        plain = await market.resolve_url(client, "https://other.example/p")
    check("resolver: a grounding redirect → the page it leads to (HEAD)", ok == "https://www.example-cellular.co.il/plans", ok)
    check("resolver: an unreachable redirect keeps the redirect URL", dead == REDIRECT + "dead", dead)
    check("resolver: a redirect to a non-web URL keeps the redirect URL", weird == REDIRECT + "weird", weird)
    check("resolver: any other URL as it is, without a request", plain == "https://other.example/p" and len(seen) == before)

    with patch.object(market, "resolve_url", fake_resolve):
        out = await market.resolve_sources([{"title": "a.example", "url": REDIRECT + "aaa"},
                                            {"title": "", "url": "javascript:alert(1)"},
                                            {"title": "https://plain.example/x", "url": "https://plain.example/x"}])
    check("resolve_sources: same order, resolved, ok only for http(s), a bare title → the host",
          out == [{"title": "a.example", "url": PAGES[REDIRECT + "aaa"], "ok": True},
                  {"title": "javascript:alert(1)", "url": "javascript:alert(1)", "ok": False},
                  {"title": "plain.example", "url": "https://plain.example/x", "ok": True}], out)


# --- Params and privacy --------------------------------------------------------------------------

def test_params() -> None:
    def refused(topic: str, params: Any) -> bool:
        try:
            market.normalize(topic, params)
        except ValueError:
            return True
        return False

    year = clock.now().year
    check("params: telecom canonical (lines absent = null)",
          market.normalize("telecom", {"service": "triple", "current_price": 250})
          == {"service": "triple", "lines": None, "current_price": 250}
          and market.params_key(market.normalize("telecom", {"service": "triple", "current_price": 250}))
          == market.params_key(market.normalize("telecom", {"current_price": 250, "lines": None, "service": "triple"})))
    bad_telecom = [{"service": "satellite", "current_price": 100}, {"service": "mobile"},
                   {"service": "mobile", "current_price": 0}, {"service": "mobile", "current_price": 2001},
                   {"service": "mobile", "current_price": "100"}, {"service": "mobile", "current_price": 99.5},
                   {"service": "mobile", "current_price": True}, {"service": "mobile", "current_price": 100, "lines": 0},
                   {"service": "mobile", "current_price": 100, "lines": 11},
                   {"service": "mobile", "current_price": 100, "name": "Tamar"}, "mobile", ["mobile"]]
    check("params: bad telecom params refused (service, price range / type, lines, unknown key, not an object)",
          all(refused("telecom", p) for p in bad_telecom),
          [p for p in bad_telecom if not refused("telecom", p)])
    check("params: electricity / fx_card take nothing (None = {}, any key refused)",
          market.normalize("electricity", None) == {} and market.normalize("fx_card", {}) == {}
          and refused("electricity", {"supplier": "x"}) and refused("fx_card", {"amount": 5}))
    check("params: arnona city trimmed, Hebrew only",
          market.normalize("arnona", {"city": "  גבעת   אורנים ", "year": year})
          == {"city": "גבעת אורנים", "year": year}
          and market.normalize("arnona", {"city": "קריית-ים \"ב\"", "year": year + 1})["city"] == "קריית-ים \"ב\"")
    bad_arnona = [{"city": "Givat Oranim", "year": year}, {"city": "גבעת אורנים 3", "year": year},
                  {"city": "א" * 41, "year": year}, {"city": "  ", "year": year}, {"city": "גבעת אורנים"},
                  {"city": "גבעת אורנים", "year": year + 2}, {"city": "גבעת אורנים", "year": True},
                  {"city": "גבעת אורנים", "year": year, "street": "הדקל"}, {"city": "---", "year": year}]
    check("params: bad arnona params refused (letters, length, year range / type, unknown key)",
          all(refused("arnona", p) for p in bad_arnona), [p for p in bad_arnona if not refused("arnona", p)])
    check("params: unknown topic refused", refused("insurance", {}))


async def test_privacy() -> None:
    year = clock.now().year
    cases = [("telecom", TELECOM, {str(TELECOM["current_price"]), str(TELECOM["lines"])}),
             ("electricity", {}, set()), ("fx_card", {}, set()),
             ("arnona", {"city": "גבעת אורנים", "year": year + 1}, {str(year + 1)})]
    for topic, params, numbers in cases:
        prompts: Dict[str, List[str]] = {}
        for user in (ADMIN, BOB):
            fake = FakeCompletion(TELECOM_REPLY)
            extract = FakeExtract({None: {"items": []} if topic != "arnona" else
                                   {"found": False, "discount_pct": None, "deadline": None, "method": "", "source": None}})
            with patch.object(llm.litellm, "acompletion", fake), patch.object(llm, "complete_json", extract), \
                    patch.object(llm, "can_search", return_value=True), patch.object(market, "resolve_url", fake_resolve):
                market.start(user, topic, params)
                await settle()
            prompts[user.id] = [c["messages"][0]["content"] for c in fake.calls] + [c["prompt"] for c in extract.calls]
        search_a, search_b = prompts[ADMIN.id][0], prompts[BOB.id][0]
        expected = market.search_prompt(topic, market.normalize(topic, params), clock.now().date())
        check(f"privacy ({topic}): the search prompt is the params' template, the same for both users",
              search_a == search_b == expected, search_a)
        everything = " ".join(prompts[ADMIN.id] + prompts[BOB.id])
        leaks = [s for s in (ADMIN.name, BOB.name, "Tamar", "Shaked", "Karmeli", *[
            v for p in PRIVATE.values() for v in (p["account"], p["merchant"], str(abs(p["amount"])),
                                                   f"{abs(p['amount']) / 100:.2f}")]) if s in everything]
        check(f"privacy ({topic}): no name, account, merchant or amount of the users in any prompt", not leaks, leaks)
        digits = set(re.findall(r"\d+", search_a))
        check(f"privacy ({topic}): the only numbers in the search prompt are the params' and the year",
              digits <= numbers | {str(clock.now().year)}, digits)
        with get_db_connection() as conn:
            conn.execute("DELETE FROM money_market_checks")
            conn.commit()


# --- Validation ----------------------------------------------------------------------------------

SOURCES = [{"title": "one.example", "url": "https://one.example/a", "ok": True},
           {"title": "two.example", "url": "https://two.example/b", "ok": True},
           {"title": "bad", "url": "javascript:void(0)", "ok": False},
           {"title": "four.example", "url": "https://four.example/d", "ok": True}]


def test_validation() -> None:
    check("avg_12: 6 months at ₪29.90 then ₪59.90 → ₪44.90", market.avg_12(5990, 2990, 6) == 4490)
    check("avg_12: a 24-month promotion counts 12 months", market.avg_12(4990, 1990, 24) == 1990)
    check("avg_12: 3 free months of ₪40 → ₪30", market.avg_12(4000, 0, 3) == 3000)
    check("avg_12: no promotion → the price", market.avg_12(4990, None, None) == 4990)

    def offer(provider: str, price: Any, source: Any, promo: Any = None, months: Any = None, plan: str = "P") -> Dict[str, Any]:
        return {"provider": provider, "plan": plan, "price": price, "promo_price": promo, "promo_months": months,
                "conditions": "  24   months\ncommitment ", "source": source}

    raw = {"items": [
        offer("Alpha", 59.9, 1, 29.9, 6),
        offer("Ghost", 30, 9),                      # page 9 was never returned
        offer("Script", 30, 3),                     # not a web page
        offer("Huge", 600, 1), offer("Tiny", 3, 2),  # out of ₪5–₪500
        offer("ALPHA", 61, 2, plan="p"),            # a repeat of Alpha's plan
        offer("Beta", 49.9, "2", 19.9, 24),
        offer("Gamma", 45, 4, 50, 3),               # a "promotion" dearer than the price → none
        offer("Delta", 40, 4, 0, 3),                # free months
        offer("Eps", 35, 1, 20, None),              # a promotion of unknown length → none
        offer("Bool", 30, True),
        "not an item",
    ]}
    items = market.validate("telecom", raw, SOURCES, TELECOM)
    names = [i["provider"] for i in items]
    check("validate telecom: unreturned / non-web pages, out-of-range prices and repeats dropped",
          names == ["Beta", "Delta", "Eps", "Alpha", "Gamma"], names)
    by = {i["provider"]: i for i in items}
    check("validate telecom: agorot, avg_12 from code, sorted by avg_12",
          by["Alpha"]["price"] == 5990 and by["Alpha"]["promo_price"] == 2990 and by["Alpha"]["avg_12"] == 4490
          and by["Beta"]["avg_12"] == 1990 and by["Delta"]["avg_12"] == 3000
          and [i["avg_12"] for i in items] == sorted(i["avg_12"] for i in items), items)
    check("validate telecom: a dearer or open-ended promotion is dropped, the offer kept",
          by["Gamma"]["promo_price"] is None and by["Gamma"]["promo_months"] is None and by["Gamma"]["avg_12"] == 4500
          and by["Eps"]["promo_price"] is None and by["Eps"]["avg_12"] == 3500)
    check("validate telecom: exactly the item keys, the cited page as {title, url}, text on one line",
          set(by["Alpha"]) == {"provider", "plan", "price", "promo_price", "promo_months", "avg_12", "lines", "conditions", "source"}
          and by["Alpha"]["source"] == {"title": "one.example", "url": "https://one.example/a"}
          and by["Beta"]["source"]["url"] == "https://two.example/b"
          and by["Alpha"]["conditions"] == "24 months commitment", by["Alpha"])
    many = market.validate("telecom", {"items": [offer(f"P{n}", 20 + n, 1) for n in range(12)]}, SOURCES, TELECOM)
    check("validate telecom: at most 8, cheapest first", len(many) == 8 and many[0]["provider"] == "P0", len(many))

    elec = market.validate("electricity", {"items": [
        {"supplier": "Volt A", "plan": "Night", "discount_pct": 15, "hours": "23:00-07:00", "days": "every day",
         "smart_meter": True, "source": 1},
        {"supplier": "Volt B", "plan": "Flat", "discount_pct": 7, "hours": "all day", "days": "every day",
         "smart_meter": "yes", "source": 2},
        {"supplier": "Volt C", "plan": "Dream", "discount_pct": 35, "hours": "", "days": "", "smart_meter": False, "source": 1},
        {"supplier": "Volt D", "plan": "Zero", "discount_pct": 0, "hours": "", "days": "", "smart_meter": False, "source": 1},
        {"supplier": "Volt E", "plan": "Day", "discount_pct": "20%", "hours": "07:00-17:00", "days": "Sun-Thu",
         "smart_meter": True, "source": 4},
        {"supplier": "Volt F", "plan": "Day", "discount_pct": 10, "hours": "", "days": "", "smart_meter": True, "source": 3},
    ]}, SOURCES, {})
    check("validate electricity: discounts outside (0, 30] and non-web pages dropped, biggest first, smart_meter a bool",
          [(i["supplier"], i["discount_pct"], i["smart_meter"]) for i in elec]
          == [("Volt E", 20.0, True), ("Volt A", 15.0, True), ("Volt B", 7.0, False)]
          and set(elec[0]) == {"supplier", "plan", "discount_pct", "hours", "days", "smart_meter", "source"}, elec)

    fx = market.validate("fx_card", {"items": [
        {"name": "Card One", "kind": "card", "fx_fee_pct": 2.5, "monthly_fee": 14.9, "conditions": "", "source": 1},
        {"name": "Wallet Two", "kind": "wallet", "fx_fee_pct": 0, "monthly_fee": None, "conditions": "", "source": 2},
        {"name": "Greedy", "kind": "card", "fx_fee_pct": 6, "monthly_fee": 0, "conditions": "", "source": 1},
        {"name": "Coin", "kind": "crypto", "fx_fee_pct": 1, "monthly_fee": 0, "conditions": "", "source": 1},
        {"name": "Bank Three", "kind": "bank", "fx_fee_pct": 1.2, "monthly_fee": 0, "conditions": "", "source": 4},
        {"name": "card one", "kind": "card", "fx_fee_pct": 2.9, "monthly_fee": 0, "conditions": "", "source": 4},
    ]}, SOURCES, {})
    check("validate fx_card: fees outside [0, 5] %, unknown kinds and repeats dropped, lowest fee first, monthly fee in agorot",
          [(i["name"], i["fx_fee_pct"], i["monthly_fee"]) for i in fx]
          == [("Wallet Two", 0.0, 0), ("Bank Three", 1.2, 0), ("Card One", 2.5, 1490)], fx)

    params = {"city": "גבעת אורנים", "year": 2027}
    found = market.validate("arnona", {"found": True, "discount_pct": 2, "deadline": "2027-01-31",
                                       "method": "paying the whole year by 31 January", "source": 4}, SOURCES, params)
    check("validate arnona: found, sourced → one item with exactly its keys",
          found == [{"found": True, "discount_pct": 2.0, "deadline": "2027-01-31",
                     "method": "paying the whole year by 31 January",
                     "source": {"title": "four.example", "url": "https://four.example/d"}}], found)
    unsourced = market.validate("arnona", {"found": True, "discount_pct": 2, "deadline": "2027-01-31", "method": "x",
                                           "source": 3}, SOURCES, params)
    check("validate arnona: a 'found' citing no returned page reads not found",
          unsourced == [{"found": False, "discount_pct": None, "deadline": None, "method": "", "source": None}], unsourced)
    odd = market.validate("arnona", {"found": True, "discount_pct": 45, "deadline": "31/01/2027", "method": "early",
                                     "source": 1}, SOURCES, params)
    check("validate arnona: an out-of-range discount and a malformed deadline → null, the finding kept",
          odd[0]["found"] and odd[0]["discount_pct"] is None and odd[0]["deadline"] is None and odd[0]["method"] == "early", odd)
    none = market.validate("arnona", {"found": False, "discount_pct": 3, "deadline": "2027-01-31", "method": "nothing",
                                      "source": 2}, SOURCES, params)
    check("validate arnona: not found → no numbers, the page kept",
          none == [{"found": False, "discount_pct": None, "deadline": None, "method": "",
                    "source": {"title": "two.example", "url": "https://two.example/b"}}], none)


# --- Start, latest, rate limits --------------------------------------------------------------------

async def test_checks() -> None:
    extract = FakeExtract({None: telecom_answer(), BOB.id: telecom_answer([
        {"provider": "Bob Only Mobile", "plan": "Lite", "price": 9.9, "promo_price": None, "promo_months": None,
         "conditions": "", "source": 2}])})
    fake = FakeCompletion(TELECOM_REPLY, delay=0.05)
    patches = (patch.object(llm.litellm, "acompletion", fake), patch.object(llm, "complete_json", extract),
               patch.object(llm, "can_search", return_value=True), patch.object(market, "resolve_url", fake_resolve))
    for p in patches:
        p.start()
    try:
        check("latest: nothing yet → None", market.latest(ADMIN, "telecom", TELECOM) is None)
        started = market.start(ADMIN, "telecom", TELECOM)
        now = market.latest(ADMIN, "telecom", TELECOM)
        check("start → {state: checking}; latest says checking with no items yet",
              started == {"state": "checking"} and now["state"] == "checking" and now["items"] == []
              and now["checked_at"] is None and now["error"] is None, now)
        try:
            market.start(ADMIN, "telecom", dict(TELECOM))
            check("start while in flight → Running", False)
        except providers.PriceCheckRunning:
            check("start while in flight → Running", True)
        check("BOB has no check of ADMIN's while it runs", market.latest(BOB, "telecom", TELECOM) is None)
        await settle()
        done = market.latest(ADMIN, "telecom", TELECOM)
        check("latest after the task: ok, two sourced offers, resolved pages, checked_at, not stale",
              done["state"] == "ok" and [i["provider"] for i in done["items"]] == ["Example Mobile", "Example Cellular"]
              and done["items"][1]["avg_12"] == 4490 and done["items"][0]["source"]["url"] == PAGES[REDIRECT + "ccc"]
              and done["checked_at"] and done["stale"] is False and done["error"] is None
              and done["params"] == TELECOM and done["topic"] == "telecom", done)
        check("latest: the sources are the resolved pages, each once",
              done["sources"] == [{"title": "example-cellular.co.il", "url": PAGES[REDIRECT + "aaa"]},
                                  {"title": "compare.example.co.il", "url": PAGES[REDIRECT + "bbb"]},
                                  {"title": "example-mobile.co.il", "url": PAGES[REDIRECT + "ccc"]}], done["sources"])
        call = extract.calls[-1]
        check("extraction: money job, money_market purpose, as the user, numbered resolved pages and [n] marks",
              call["job"] == "money" and call["purpose"] == "money_market" and call["user"] == ADMIN.id
              and f"[3] example-mobile.co.il — {PAGES[REDIRECT + 'ccc']}" in call["prompt"]
              and "no commitment. [3]" in call["prompt"] and call["schema"] == market.SCHEMAS["telecom"], call["prompt"])
        check("the search ran as the user (usage tag)", fake.calls[-1]["litellm_metadata"]["usage"]["user_id"] == ADMIN.id)
        with get_db_connection() as conn:
            row = conn.execute("SELECT model, params_key FROM money_market_checks WHERE user_id = ?", (ADMIN.id,)).fetchone()
        check("stored: the search model and the canonical params key",
              row["model"] == "gemini/gemini-flash-latest"
              and row["params_key"] == market.params_key(market.normalize("telecom", TELECOM)), dict(row))

        try:
            market.start(ADMIN, "telecom", TELECOM)
            check("start again within 6 h → TooSoon", False)
        except providers.PriceCheckTooSoon as e:
            check("start again within 6 h → TooSoon with retry_after ≈ 6 h", 6 * 3600 - 60 <= e.retry_after <= 6 * 3600,
                  e.retry_after)

        # BOB: his own check, his own result; ADMIN's rate limit does not touch him, and back.
        market.start(BOB, "telecom", TELECOM)
        await settle()
        bob = market.latest(BOB, "telecom", TELECOM)
        admin = market.latest(ADMIN, "telecom", TELECOM)
        check("two users: each sees only their own result",
              bob["items"][0]["provider"] == "Bob Only Mobile"
              and all(i["provider"] != "Bob Only Mobile" for i in admin["items"]), (bob["items"], admin["items"]))
        check("two users: CAROL sees neither", market.latest(CAROL, "telecom", TELECOM) is None)

        # Six hours on: allowed again. A model error → failed, the last good result kept.
        set_row(ADMIN, "telecom", TELECOM, started_at=ago(hours=7))
        extract.answers[None] = RuntimeError("model down")
        market.start(ADMIN, "telecom", TELECOM)
        await settle()
        failed = market.latest(ADMIN, "telecom", TELECOM)
        check("failed: plain-words error, the last good offers and their date kept",
              failed["state"] == "failed" and failed["error"] == market.ERRORS["failed"]
              and failed["items"] == admin["items"] and failed["checked_at"] == admin["checked_at"], failed)
        try:
            market.start(ADMIN, "telecom", TELECOM)
            check("after a failure: TooSoon for 5 minutes", False)
        except providers.PriceCheckTooSoon as e:
            check("after a failure: TooSoon for 5 minutes", 0 < e.retry_after <= 300, e.retry_after)
        check("BOB's result untouched by ADMIN's failure", market.latest(BOB, "telecom", TELECOM)["state"] == "ok")

        # No search: the model answered without sources.
        set_row(ADMIN, "telecom", TELECOM, started_at=ago(minutes=6))
        fake.replies = [litellm.ModelResponse(choices=[{"message": {"role": "assistant", "content": "about ₪40"},
                                                        "finish_reason": "stop"}])]
        extract.answers[None] = telecom_answer()
        market.start(ADMIN, "telecom", TELECOM)
        await settle()
        check("failed: the model did not search", market.latest(ADMIN, "telecom", TELECOM)["error"] == market.ERRORS["no_search"])

        # Nothing survives validation.
        set_row(ADMIN, "telecom", TELECOM, started_at=ago(minutes=6))
        fake.replies = [TELECOM_REPLY]
        extract.answers[None] = {"items": [{"provider": "Ghost", "plan": "", "price": 30, "promo_price": None,
                                            "promo_months": None, "conditions": "", "source": 12}]}
        market.start(ADMIN, "telecom", TELECOM)
        await settle()
        check("failed: no offer with a source", market.latest(ADMIN, "telecom", TELECOM)["error"] == market.ERRORS["nothing"])

        # Too slow.
        set_row(ADMIN, "telecom", TELECOM, started_at=ago(minutes=6))
        fake.delay = 1.0
        with patch.object(market, "CHECK_TIMEOUT_SECONDS", 0.1):
            market.start(ADMIN, "telecom", TELECOM)
            await settle()
        fake.delay = 0.0
        check("failed: a check that takes too long", market.latest(ADMIN, "telecom", TELECOM)["error"] == market.ERRORS["timeout"])

        # Stale after 45 days; a "checking" row older than 5 minutes reads failed and may start again.
        set_row(BOB, "telecom", TELECOM, checked_at=ago(days=46))
        check("stale: a result older than 45 days", market.latest(BOB, "telecom", TELECOM)["stale"] is True)
        set_row(BOB, "telecom", TELECOM, state="checking", started_at=ago(minutes=6))
        cut = market.latest(BOB, "telecom", TELECOM)
        check("an old 'checking' row (a restart) reads failed", cut["state"] == "failed"
              and cut["error"] == market.ERRORS["interrupted"], cut)
        set_row(BOB, "telecom", TELECOM, state="checking", started_at=ago(minutes=1))
        try:
            market.start(BOB, "telecom", TELECOM)
            check("a fresh 'checking' row → Running", False)
        except providers.PriceCheckRunning:
            check("a fresh 'checking' row → Running", True)
        set_row(BOB, "telecom", TELECOM, state="checking", started_at=ago(minutes=6))
        extract.answers[None] = telecom_answer()
        check("an interrupted check may start again", market.start(BOB, "telecom", TELECOM) == {"state": "checking"})
        await settle()
        check("…and ends ok", market.latest(BOB, "telecom", TELECOM)["state"] == "ok")

        # Other params are another check.
        other = {**TELECOM, "lines": 3}
        check("other params: no result yet, start allowed",
              market.latest(ADMIN, "telecom", other) is None and market.start(ADMIN, "telecom", other) == {"state": "checking"})
        await settle()

        # Arnona end to end, found and not found.
        year = clock.now().year
        arnona = {"city": "גבעת אורנים", "year": year}
        extract.answers[None] = {"found": True, "discount_pct": 2, "deadline": f"{year}-01-31",
                                 "method": "paying the whole year by 31 January", "source": 2}
        market.start(ADMIN, "arnona", arnona)
        await settle()
        got = market.latest(ADMIN, "arnona", arnona)
        check("arnona: found, stored as one item with the resolved page",
              got["state"] == "ok" and got["items"] == [{"found": True, "discount_pct": 2.0, "deadline": f"{year}-01-31",
                                                         "method": "paying the whole year by 31 January",
                                                         "source": {"title": "compare.example.co.il",
                                                                    "url": PAGES[REDIRECT + "bbb"]}}], got)
        extract.answers[None] = {"found": False, "discount_pct": None, "deadline": None, "method": "", "source": None}
        market.start(BOB, "arnona", arnona)
        await settle()
        got = market.latest(BOB, "arnona", arnona)
        check("arnona: not found is an ok result",
              got["state"] == "ok" and got["items"] == [{"found": False, "discount_pct": None, "deadline": None,
                                                         "method": "", "source": None}], got)

        try:
            market.start(ADMIN, "telecom", {"service": "mobile", "current_price": 100, "owner": "x"})
            check("start: bad params → ValueError", False)
        except ValueError:
            check("start: bad params → ValueError", True)
    finally:
        for p in patches:
            p.stop()

    with patch.object(llm, "can_search", return_value=False):
        try:
            market.start(CAROL, "electricity", {})
            check("start: no search model → Unavailable, nothing stored", False)
        except providers.PriceCheckUnavailable:
            check("start: no search model → Unavailable, nothing stored", market.latest(CAROL, "electricity", {}) is None)

    store.delete_all(ADMIN)
    check("delete_all: the user's checks go, the other user's stay",
          market.latest(ADMIN, "telecom", TELECOM) is None and market.latest(ADMIN, "arnona", arnona) is None
          and market.latest(BOB, "telecom", TELECOM) is not None)
    check("no check left running", not market._running)


async def main() -> None:
    await test_search()
    await test_resolver()
    test_params()
    await test_privacy()
    test_validation()
    await test_checks()


if __name__ == "__main__":
    asyncio.run(main())
    passed = sum(1 for _, ok in results if ok)
    print(f"\n{passed}/{len(results)} passed")
    raise SystemExit(0 if passed == len(results) else 1)
