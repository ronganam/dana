/**
 * Contract test for Settings → Needs attention (static/attention.js) and the Addons page's Reconnect
 * (static/addons.js).
 *
 * Feeds them the payloads the API answered, captured in tests/fixtures/attention_web.json
 * by `WRITE_WEB_FIXTURE=1 bin/test test_attention` (a Home Assistant connection to reconnect, a failing push
 * with its server fix, a phone to reconnect, a fix's answers, a user with nothing failing).
 *
 * Checks: the rows (failing checks only, reconnects first, title / why / since / ignored), the badge count,
 * what each fix does on the web (server, connect, screen, https url; nothing else), the HTML (the fix
 * button, "Not today" for a reconnect, "Show on Now again" for an ignored one, user text escaped, All
 * clear), and the Addons page: the Reconnect status with its reason, and its button (the addon's fields to
 * enter again, a screen, a sign-in link).
 *
 * Run: bin/test test_web_attention [path-to-fixture]
 */

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const SERVER = path.join(here, "..");  // the server folder
const FIXTURE = process.argv[2] || path.join(SERVER, "tests", "fixtures", "attention_web.json");
const F = JSON.parse(fs.readFileSync(FIXTURE, "utf8"));

const sandbox = { console, URL, Set, Map, JSON, Object, Array, String, Number, Boolean, RegExp, Math, Date };
vm.createContext(sandbox);
for (const file of ["static/attention.js", "static/addons.js"]) {
    vm.runInContext(fs.readFileSync(path.join(SERVER, file), "utf8"), sandbox, { filename: file });
}
const A = sandbox.DanaAttentionCore;
const C = sandbox.DanaAddonsCore;
const plain = (v) => JSON.parse(JSON.stringify(v));
const eq = (a, b) => JSON.stringify(plain(a)) === JSON.stringify(plain(b));

let pass = 0;
const failures = [];
function check(name, cond, detail = "") {
    if (cond) { pass++; console.log(`[PASS] ${name}`); }
    else { failures.push(name); console.log(`[FAIL] ${name}${detail ? " — " + String(detail).slice(0, 400) : ""}`); }
}

// The fixture's times are all one made-up moment; "now" is two hours later.
const NOW = Date.parse("2026-11-22T10:30:00+02:00");

// --- The rows ---------------------------------------------------------------------------------------
const reconnect = F.checks_reconnect.body;
const items = A.itemsOf(reconnect, NOW);
check("rows: only the failing checks, the reconnect first",
      items.length === reconnect.checks.filter((c) => !c.ok).length && items[0].key === "reconnect:homeassistant"
      && items[0].reconnect === true && items.slice(1).every((i) => !i.reconnect), items.map((i) => i.key));
check("rows: the reconnect's title, why, since and Reconnect fix",
      items[0].title === "Reconnect Home Assistant" && /refused the token/.test(items[0].why) && items[0].since === "since 2 h ago"
      && eq(items[0].target, { type: "connect", addon: "homeassistant" }), plain(items[0]));
check("rows: a check without a title reads label: detail", A.itemsOf({ checks: [{ key: "x", label: "Wake signal", ok: false, detail: "none for 4 days" }] }, NOW)[0].title === "Wake signal: none for 4 days");
check("rows: nothing failing → no rows (Maya)", A.itemsOf(F.checks_maya.body, NOW).length === 0);
check("badge: failing checks whose card is not ignored",
      A.countOf(reconnect, NOW) === items.length
      && A.countOf({ checks: [{ key: "a", ok: false, ignored_until: "2026-11-23T10:00:00+02:00" }, { key: "b", ok: false }, { key: "c", ok: true }] }, NOW) === 1);
check("since: minutes, hours, then a date",
      A.sinceText(new Date(NOW - 5 * 60000).toISOString(), NOW) === "since 5 min ago"
      && A.sinceText(new Date(NOW - 3 * 86400000).toISOString(), NOW).startsWith("since ") && A.sinceText(null, NOW) === "");

// --- Fixes --------------------------------------------------------------------------------------------
const pushFix = F.checks_push_failing.body.checks[0].fix;
check("fix: a server fix posts its action", eq(A.fixTarget(pushFix), { type: "server", action: "test" }), plain(pushFix));
check("fix: a screen fix opens the screen", eq(A.fixTarget(F.checks_phone_reconnect.body.checks[0].fix), { type: "screen", id: "devices" }));
check("fix: an https link opens; any other link or kind does nothing",
      eq(A.fixTarget({ kind: "url", url: "https://example.com/help", label: "Help" }), { type: "url", url: "https://example.com/help" })
      && A.fixTarget({ kind: "url", url: "javascript:alert(1)", label: "x" }) === null && A.fixTarget({ kind: "magic", label: "x" }) === null
      && A.fixTarget(null) === null);
check("fix: the answers carry a message (done) or an error (refused)",
      F.fix_ok.status === 200 && typeof F.fix_ok.body.message === "string"
      && F.fix_refused.status === 409 && typeof F.fix_refused.body.error === "string");

// --- HTML -----------------------------------------------------------------------------------------------
const html = A.renderList(items, {});
check("html: each row has its fix button with the fix's label",
      html.includes('data-fix="reconnect:homeassistant"') && html.includes(">Reconnect</button>"));
check("html: a reconnect is dismissed for a day (Not today), another check for a week",
      /data-ignore="reconnect:homeassistant" data-days="1">Not today/.test(html)
      && A.renderItem({ key: "push", title: "t", why: "", label: "Push", since: "", reconnect: false, target: null }, {}).includes('data-days="7">Ignore for a week'));
check("html: an ignored card can be shown on Now again",
      A.renderItem({ ...items[0], ignoredUntil: "2026-11-23T10:00:00+02:00" }, {}).includes('data-days="0">Show on Now again'));
check("html: user text is escaped", !A.renderItem({ key: "k", title: "<img src=x>", why: "<b>", label: "l", target: null }, {}).includes("<img"));
check("html: a fix in progress is disabled; its result shows on its row",
      A.renderItem(items[0], { busy: items[0].key }).includes("disabled")
      && A.renderItem(items[0], { status: { key: items[0].key, tone: "ok", text: "Your phone accepted it" } }).includes("Your phone accepted it"));
check("html: nothing failing → All clear", A.renderList([], {}).includes("All clear."));

// --- The Addons page's Reconnect ------------------------------------------------------------------------
const ha = F.addon_reconnect.body.addons[0];
const st = C.statusOf(ha, {});
check("addons: a connection to reconnect is the Reconnect status with its reason",
      st.key === "reconnect" && st.label === "Reconnect" && /refused the token/.test(st.reason) && /connect it again/.test(st.reason), plain(st));
check("addons: its row shows the reason", C.renderRow(ha, {}).includes("itg-row-reason") && C.renderRow(ha, {}).includes("Reconnect"));
check("addons: a credentials addon's Reconnect brings its fields (they are on the same view)",
      eq(C.reconnectAction(ha), { kind: "fields", label: "Reconnect" }) && C.renderDetail(ha, {}).includes('data-act="reconnect"'));
const money = { ...ha, name: "money", connect_url: null, credentials: [],
                reconnect: [{ part: "s1", label: "Test Bank", reason: "Test Bank refused the login details", since: "", fix: { kind: "screen", label: "Fix the login", screen: "money" } }] };
check("addons: a source's reconnect names the source and opens its page",
      C.reconnectText(money).startsWith("Test Bank: Test Bank refused the login details.")
      && eq(C.reconnectAction(money), { kind: "screen", id: "money", label: "Fix the login" })
      && C.renderDetail(money, {}).includes('data-screen="money"'));
const google = { ...ha, name: "calendar", connect_url: "/api/google/connect", credentials: [],
                 reconnect: [{ part: "", label: "", reason: "Google refused the refresh token", since: "", fix: { kind: "connect", label: "Reconnect", addon: "calendar" } }] };
check("addons: a sign-in addon's Reconnect is its sign-in link",
      eq(C.reconnectAction(google), { kind: "link", href: "/api/google/connect", label: "Reconnect" })
      && C.renderDetail(google, {}).includes('href="/api/google/connect"'));
check("addons: no reconnect → the usual status, no Reconnect button",
      C.statusOf({ ...ha, reconnect: undefined }, {}).key === "on" && C.reconnectAction({ ...ha, reconnect: undefined }) === null);
check("addons: someone who cannot change it gets no button", C.reconnectAction({ ...ha, can_change: false }) === null);

console.log(`\n${pass}/${pass + failures.length} passed`);
if (failures.length) process.exit(1);
