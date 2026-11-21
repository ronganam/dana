/**
 * The bank-worker's Pluxee (Cibus) meal card client (bank-worker/src/pluxee.js) on a fake browser page.
 *
 * A session left in the profile (no SMS), the code sign-in (the phone once, `logging_in` only once the SMS is
 * out, the code once), the refusals (reCAPTCHA → GENERIC before the SMS, an unknown phone, a wrong code with
 * the tries left, a blocked user), a bad phone never sent, the read-only allow-list, the reads' dates
 * (dd/MM/yyyy) and the result's shape (the active card's last 4, what is left of the budget, the site's rows
 * without the card number). Values are made up. No network, no browser.
 *
 * Run: node test_bank_worker_pluxee.mjs
 */

import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { runPluxee, normalizePhone, refusal, ddmmyyyy, read, READS } = require("./bank-worker/src/pluxee.js");
const { parseJob } = require("./bank-worker/src/server.js");

let pass = 0;
const failures = [];
function check(name, cond, detail = "") {
  if (cond) pass += 1;
  else failures.push(`${name}${detail ? ` — ${detail}` : ""}`);
}

const BUDGETS = { data: [{ CreatioBudget: "900.00", CurrBudget: "310.5", CreationDate: "22/10/2026", ExpirationDate: "21/11/2026", SCard: "5555000011112222" }], code: 0 };
const CARDS = { cards: [{ cibus_card_number: "9999", activation_status: "inactive" }, { cibus_card_number: "4321", activation_status: "active" }], code: 0 };
const DEALS = { list: [{ deal_id: 1001, rest_name: "Made-up Diner", date: "14/11/2026", time: "12:30", price: 47.3, otl_price: 0 }], code: 0 };

/**
 * A fake page: the site's API answers through `evaluate` (by the call's `type`), the sign-in service's
 * answers through `waitForResponse` after a click on "continue" (sendOTP) or after the code is typed
 * (authToken). Records what was sent.
 */
function fakePage({ signedIn = false, sendOTP = { status: 201, body: { data: { method: "phone" } } },
  authToken = { status: 201, body: { data: { token: "t" } } }, autoSubmit = true } = {}) {
  const state = { signedIn, calls: [], typed: [], clicks: 0, waiting: [], codeTyped: "" };
  const respond = (path, answer) => {
    const res = { url: () => `https://api.capir.pluxee.co.il/auth/${path}`, request: () => ({ method: () => "POST" }),
      status: () => answer.status, json: async () => answer.body };
    for (const w of state.waiting.splice(0)) {
      if (w.predicate(res)) w.resolve(res); else state.waiting.push(w);
    }
    if (path === "authToken" && answer.status === 201) state.signedIn = true;
  };
  const field = sel => ({
    type: async (text) => {
      state.typed.push({ sel, text });
      if (sel === "code") {
        state.codeTyped += text;
        if (autoSubmit && state.codeTyped.length === 6) setTimeout(() => respond("authToken", authToken), 5);
      }
    },
  });
  const page = {
    state,
    goto: async () => {},
    setUserAgent: async () => {},
    click: async () => { throw new Error("no banner"); },
    type: async (sel, text) => { state.typed.push({ sel, text }); },
    keyboard: { press: async () => {} },
    waitForSelector: async () => field("code"),
    $$: async () => [1, 2, 3, 4, 5, 6].map(() => field("code")),
    waitForResponse: (predicate) => new Promise(resolve => state.waiting.push({ predicate, resolve })),
    evaluate: async (fn, ...args) => {
      const src = String(fn);
      if (src.includes("cib-btn")) {
        state.clicks += 1;
        if (state.clicks === 1) setTimeout(() => respond("sendOTP", sendOTP), 5);
        else if (!autoSubmit) setTimeout(() => respond("authToken", authToken), 5);
        return true;
      }
      if (src.includes("fetch(")) {
        const body = args[2];
        state.calls.push(body);
        if (!state.signedIn) return { status: 200, text: JSON.stringify({ code: 172, msg: "not logged in" }) };
        const answer = { prx_get_budgets: BUDGETS, get_user_cards: CARDS, prx_user_deals: DEALS }[body.type];
        return { status: 200, text: JSON.stringify(answer) };
      }
      if (src.includes("#firstInput")) { state.tabPolls = (state.tabPolls || 0) + 1; return state.tabPolls >= 2; } // drawn on the second look
      return undefined;
    },
  };
  return page;
}

function fakeBrowser(page) {
  return { newPage: async () => page, userAgent: async () => "Mozilla/5.0 HeadlessChrome/131" };
}

function ctxFor({ code = "482913" } = {}) {
  const ctx = { states: [], codesAsked: 0, signal: new AbortController().signal };
  ctx.setState = s => ctx.states.push(s);
  ctx.requestCode = async () => { ctx.codesAsked += 1; return code; };
  ctx.log = () => {};
  return ctx;
}

const START = new Date(2025, 10, 1);

// ---- pure parts ----
check("phone: 05X…, with spaces / dashes / +972", normalizePhone("052-000 1234") === "0520001234"
  && normalizePhone("+972520001234") === "0520001234" && normalizePhone("972520001234") === "0520001234");
check("phone: not a mobile number", normalizePhone("03-1234567") === null && normalizePhone("") === null
  && normalizePhone("05200012345") === null);
check("dates as the site asks: dd/MM/yyyy", ddmmyyyy(new Date(2026, 0, 5)) === "05/01/2026");
check("reads: only the three", [...READS].sort().join() === "get_user_cards,prx_get_budgets,prx_user_deals");
let refused = null;
try { await read(fakePage({ signedIn: true }), "prx_apply_order", {}); } catch (e) { refused = e; }
check("an order (or any other call) is refused before it leaves", refused && /refused/.test(refused.message));
check("refusal: reCAPTCHA is a GENERIC failure", refusal("phone", 400, { error: { message: "Invalid reCAPTCHA. Please try again" } }).errorType === "GENERIC");
check("refusal: an unknown phone is INVALID_PASSWORD", refusal("phone", 404, { error: { message: "User not found" } }).errorType === "INVALID_PASSWORD");
check("refusal: blocked", refusal("phone", 403, { error: { message: "המשתמש נחסם" } }).errorType === "ACCOUNT_BLOCKED");
const wrong = refusal("code", 401, { error: { message: { remainingLoginAttempts: 2 } } });
check("refusal: a wrong code, with the tries left", wrong.errorType === "CODE_REJECTED" && /2 tries left/.test(wrong.message), wrong.message);
check("refusal: a server error is GENERIC", refusal("phone", 502, null).errorType === "GENERIC");
check("the job: pluxee takes a phone", parseJob({ company: "pluxee", profile: "p1", start_date: "2025-11-01",
  credentials: { phone: "0520001234", extra: "x" } }).credentials.phone === "0520001234"
  && parseJob({ company: "pluxee", profile: "p1", start_date: "2025-11-01", credentials: {} }).error === "credentials.phone missing");

// ---- a session left in the profile: no SMS ----
{
  const page = fakePage({ signedIn: true });
  const ctx = ctxFor();
  const result = await runPluxee(fakeBrowser(page), { credentials: { phone: "0520001234" }, startDate: START }, ctx);
  check("session: signed in without a code", result.success && ctx.codesAsked === 0 && !page.state.typed.length, JSON.stringify(result));
  check("session: straight to scraping", ctx.states.join() === "scraping", ctx.states.join());
  const a = result.accounts[0];
  check("result: the active card's last 4, kind meal, what is left", a.kind === "meal" && a.accountNumber === "4321" && a.balance === 310.5);
  check("result: the budgets without the card number", a.budgets.length === 1 && !("SCard" in a.budgets[0]) && a.budgets[0].CurrBudget === "310.5");
  check("result: the deals as the site gave them; no txns", a.deals.length === 1 && a.deals[0].deal_id === 1001 && a.txns.length === 0);
  const deals = page.state.calls.find(c => c.type === "prx_user_deals");
  check("deals: from the start date to today, dd/MM/yyyy", deals && deals.from_date === "01/11/2025" && /^\d{2}\/\d{2}\/\d{4}$/.test(deals.to_date), JSON.stringify(deals));
  check("only allowed calls were made", page.state.calls.every(c => READS.has(c.type)));
}

// ---- the code sign-in ----
{
  const page = fakePage();
  const ctx = ctxFor();
  const result = await runPluxee(fakeBrowser(page), { credentials: { phone: "052-000-1234" }, startDate: START }, ctx);
  check("code: signed in and read", result.success && result.accounts[0].accountNumber === "4321", JSON.stringify(result));
  check("code: the phone typed once, normalised", page.state.typed.filter(t => t.sel === "#firstInput").map(t => t.text).join() === "0520001234");
  check("code: asked once, typed digit by digit", ctx.codesAsked === 1 && page.state.codeTyped === "482913");
  check("code: logging_in only once the SMS was out, then scraping", ctx.states.join() === "logging_in,scraping", ctx.states.join());
  check("code: the tab is clicked until the phone field shows", page.state.tabPolls === 2);
}
{
  const page = fakePage({ autoSubmit: false });
  const ctx = ctxFor();
  const result = await runPluxee(fakeBrowser(page), { credentials: { phone: "0520001234" }, startDate: START }, ctx);
  check("code: a form that does not send by itself gets continue pressed", result.success && page.state.clicks === 2, JSON.stringify(result));
}

// ---- refusals ----
async function failing(options, expected, label, { states } = {}) {
  const ctx = ctxFor();
  let err = null;
  try { await runPluxee(fakeBrowser(fakePage(options)), { credentials: { phone: "0520001234" }, startDate: START }, ctx); } catch (e) { err = e; }
  check(label, err && err.errorType === expected, err ? `${err.errorType}: ${err.message}` : "no error");
  if (states !== undefined) check(`${label}: states ${states || "(none)"}`, ctx.states.join() === states, ctx.states.join());
  return ctx;
}
const robot = await failing({ sendOTP: { status: 400, body: { error: { message: "Invalid reCAPTCHA. Please try again" } } } },
  "GENERIC", "reCAPTCHA: GENERIC", { states: "" });
check("reCAPTCHA: no code asked", robot.codesAsked === 0);
await failing({ sendOTP: { status: 404, body: { error: { message: "User not found" } } } }, "INVALID_PASSWORD", "unknown phone", { states: "" });
const rejected = await failing({ authToken: { status: 401, body: { error: { message: { remainingLoginAttempts: 1 } } } } },
  "CODE_REJECTED", "wrong code", { states: "logging_in" });
check("wrong code: one code only", rejected.codesAsked === 1);
{
  const ctx = ctxFor();
  const page = fakePage();
  const result = await runPluxee(fakeBrowser(page), { credentials: { phone: "12345" }, startDate: START }, ctx);
  check("a bad phone is refused before anything is sent", !result.success && result.errorType === "INVALID_PASSWORD"
    && !page.state.calls.length && !page.state.typed.length);
}

if (failures.length) {
  console.log(`FAIL ${failures.length} of ${pass + failures.length}`);
  for (const f of failures) console.log(`  ✗ ${f}`);
  process.exit(1);
}
console.log(`PASS ${pass} checks`);
