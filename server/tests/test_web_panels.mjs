/**
 * The web shell's addon pages: the navigation built from a GET /api/addons payload (static/app-panels.js:
 * panelEntries, routeOf) and a page registering with the SDK (static/dana.js: Dana.panel, Dana.load, the
 * api a page gets).
 *
 * The payloads are the real ones captured in tests/fixtures/addons_web.json by
 * `WRITE_WEB_FIXTURE=1 bin/test test_addons_api`. dana.js runs against a small fake document that "runs"
 * a script by calling a function registered for its URL, with document.currentScript set as a browser does.
 *
 * Run: bin/test test_web_panels
 */

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const SERVER = path.join(here, "..");  // the server folder
const F = JSON.parse(fs.readFileSync(path.join(SERVER, "tests", "fixtures", "addons_web.json"), "utf8"));

let pass = 0;
const failures = [];
function check(name, cond, detail = "") {
    if (cond) { pass++; console.log(`[PASS] ${name}`); }
    else { failures.push(name); console.log(`[FAIL] ${name}${detail ? " — " + JSON.stringify(detail) : ""}`); }
}
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

// ---------- the navigation from /api/addons (app-panels.js) ----------
const shellSb = { console, Number, String, JSON, Map, Promise,
                  window: { Dana: { attach() {} } }, document: { addEventListener() {} } };
vm.createContext(shellSb);
vm.runInContext(fs.readFileSync(path.join(SERVER, "static/app-panels.js"), "utf8")
    + "\nthis.api = { panelEntries, routeOf, badgeText };", shellSb);
const { panelEntries, routeOf, badgeText } = shellSb.api;

for (const key of ["list_member", "list_admin", "list_admin_for_member", "list_member_disabled"]) {
    const addons = F[key].body.addons;
    const got = panelEntries(F[key].body);
    const want = addons.filter(a => a.state === "on" && a.web && a.web.panel).map(a => a.name);
    check(`${key}: one entry per addon on for the user with a page`, eq(got.map(e => e.addon).sort(), [...want].sort()), got.map(e => e.addon));
    check(`${key}: nothing off, available, disabled or failed`,
          got.length > 0 && got.every(e => addons.find(a => a.name === e.addon).state === "on"));
    check(`${key}: in Nav order`, got.every((e, i) => i === 0 || got[i - 1].order <= e.order), got.map(e => e.order));
}
const admin = panelEntries(F.list_admin.body);
check("the admin's pages: shopping, inbox, people, projects, memory", eq(admin.map(e => e.addon), ["shopping", "phone", "people", "knowledge", "about"]), admin.map(e => e.addon));
const shopping = admin.find(e => e.addon === "shopping");
check("an entry carries the manifest's label, icon and the files' URLs",
      shopping && shopping.label === "Shopping" && shopping.icon === "shopping-cart" && shopping.nav === true
      && shopping.panel === "/addons/shopping/web/shopping.js" && shopping.style === "/addons/shopping/web/shopping.css", shopping);
const member = panelEntries(F.list_member.body);
check("the member's only page is the calendar, a page without a style",
      eq(member.map(e => e.addon), ["calendar"]) && member[0].style === null && member[0].panel === "/addons/calendar/web/calendar.js", member);
check("the admin's view of another user is that user's pages (what they have on)",
      eq(panelEntries(F.list_admin_for_member.body).map(e => e.addon), member.map(e => e.addon)));
check("an addon disabled on the server has no page",
      F.list_member_disabled.body.addons.some(a => a.name === "shopping" && a.state === "disabled-on-server")
      && !panelEntries(F.list_member_disabled.body).some(e => e.addon === "shopping"));

const synthetic = { addons: [
    { name: "zeta", title: "Zeta", state: "on", web: { panel: "/addons/zeta/web/z.js", style: null, nav: { label: "Zeta", icon: "", order: 5 } } },
    { name: "alpha", title: "Alpha", state: "on", web: { panel: "/addons/alpha/web/a.js", style: "/addons/alpha/web/a.css", nav: { label: "Alpha", icon: "a", order: 5 } } },
    { name: "hidden", title: "Hidden page", state: "on", web: { panel: "/addons/hidden/web/h.js", style: null, nav: null } },
    { name: "styleonly", title: "Style", state: "on", web: { panel: null, style: "/addons/styleonly/web/s.css", nav: { label: "S", icon: "", order: 1 } } },
    { name: "later", title: "Later", state: "available", web: null },
    { name: "off", title: "Off", state: "disabled-on-server", web: { panel: "/addons/off/web/o.js", style: null, nav: { label: "Off", icon: "", order: 1 } } },
    { name: "broken", title: "Broken", state: "failed", web: null },
] };
const syn = panelEntries(synthetic);
check("ties in order go by label; no Nav sorts last (order 100)", eq(syn.map(e => e.addon), ["alpha", "zeta", "hidden"]), syn.map(e => e.addon));
check("no Nav: listed (it can be opened) but not shown in the navigation", syn[2].nav === false && syn[2].label === "Hidden page");
check("an icon falls back to a generic one", syn[1].icon === "puzzle" && syn[0].icon === "a");
check("a style without a panel is no page; a disabled addon has none even with its web", !syn.some(e => e.addon === "styleonly" || e.addon === "off"));
check("an empty or broken payload has no pages", eq(panelEntries(null), []) && eq(panelEntries({}), []));

check("route: /shopping", routeOf("/shopping", "") === "shopping");
check("route: a hash wins, with its own state after the id", routeOf("/", "#money/spending?month=2026-10") === "money");
check("route: / and deeper paths name no page", routeOf("/", "") === null && routeOf("/addons/x/web/a.js", "") === null);
check("badge: counts, 99+, hidden at zero", badgeText(3) === "3" && badgeText(120) === "99+" && badgeText(0) === "" && badgeText(null) === "" && badgeText("new") === "new");

// ---------- the SDK (dana.js) ----------
const scripts = new Map();      // URL → the script's body (a function), run when the fake document "loads" it
const appended = [];
const fakeDocument = {
    currentScript: null,
    head: {
        appendChild(el) {
            appended.push(el.src);
            queueMicrotask(() => {
                const body = scripts.get(el.src);
                if (!body) { el.onerror?.(); return; }
                fakeDocument.currentScript = { src: el.src };
                try { body(); } finally { fakeDocument.currentScript = null; }
                el.onload?.();
            });
        },
    },
    createElement: () => ({}),
};
const fetches = [];
const logged = [];
const sdkSb = {
    console: { log: console.log, warn: console.warn, error: (...args) => logged.push(args.map(String).join(" ")) }, URL, Map, Promise, Error, JSON, String, Object, Boolean, queueMicrotask,
    document: fakeDocument,
    location: { href: "https://dana.example/" },
    localStorage: { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); }, removeItem() {} },
    fetch: async (url, init) => {
        fetches.push({ url, init });
        if (url === "/offline") throw new TypeError("network down");
        if (url === "/missing") return { status: 404, json: async () => ({ error: "not_found" }) };
        return { status: 200, json: async () => ({ ok: true, url }) };
    },
};
sdkSb.window = sdkSb;
vm.createContext(sdkSb);
vm.runInContext(fs.readFileSync(path.join(SERVER, "static/dana.js"), "utf8"), sdkSb);
const Dana = sdkSb.Dana;

const shellCalls = [];
Dana.attach({
    me: () => ({ id: "u1", name: "Robin" }),
    toast: (t) => shellCalls.push(["toast", t]),
    open: (id, arg) => shellCalls.push(["open", id, arg]),
    openSession: (sid) => shellCalls.push(["session", sid]),
    badge: (addon, value, tone) => shellCalls.push(["badge", addon, value, tone]),
    visible: (addon) => addon === "shopping",
});

let threw = "";
try { Dana.panel({ id: "stray", mount() {} }); } catch (e) { threw = e.message; }
check("a page not loaded by the shell cannot register", /not loaded by the shell/.test(threw), threw);

const order = [];
let startedWith = null;
const PANEL = "https://dana.example/addons/shopping/web/shopping.js?v=7";
scripts.set("https://dana.example/addons/shopping/web/shopping-core.js?v=7", () => { order.push("core"); sdkSb.DanaShoppingCore = { ok: true }; });
scripts.set(PANEL, () => {
    order.push("panel");
    Dana.panel({
        id: "shopping", title: "Shopping", icon: "shopping-cart", scripts: ["shopping-core.js"],
        start(api) { order.push("start"); startedWith = { api, core: sdkSb.DanaShoppingCore }; },
        mount() {},
    });
});
const reg = await Dana.load("shopping", "/addons/shopping/web/shopping.js?v=7");
await reg.ready;
check("Dana.load: the page registers for the addon that loaded it", reg.addon === "shopping" && reg.def.id === "shopping"
      && Dana.registered("shopping") === reg, reg.addon);
check("its scripts load first, in order, beside it with its version; then start", eq(order, ["panel", "core", "start"])
      && startedWith && startedWith.core && startedWith.core.ok === true, order);
const api = reg.api;
check("start gets the page's api", startedWith.api === api && api.addon === "shopping"
      && api.base === "https://dana.example/addons/shopping/web/");
check("api.me is the shell's /api/me", api.me && api.me.name === "Robin");
api.badge(4, "quiet"); api.badge(0); api.open("people", "p1"); api.openSession("s9"); api.toast("Saved");
check("api.badge / open / openSession / toast reach the shell, badges named by the addon",
      eq(shellCalls, [["badge", "shopping", 4, "quiet"], ["badge", "shopping", 0, ""], ["open", "people", "p1"], ["session", "s9"], ["toast", "Saved"]]), shellCalls);
check("api.visible asks the shell about this page", api.visible() === true);
check("api.esc escapes HTML", api.esc(`<a href="x">'&`) === "&lt;a href=&quot;x&quot;&gt;&#39;&amp;");
check("api.store never throws when storage is blocked", api.store.get("k", "fallback") === "fallback" && api.store.set("k", 1) === undefined);

const ok = await api.json("POST", "/api/thing", { a: 1 });
const sent = fetches.at(-1);
check("api.json: JSON in, {status, body} out, signed in like the page, never cached",
      ok.status === 200 && ok.body.ok === true && sent.init.method === "POST" && sent.init.body === '{"a":1}'
      && sent.init.headers["Content-Type"] === "application/json" && sent.init.credentials === "same-origin" && sent.init.cache === "no-store", sent.init);
check("api.json: no answer is status 0, never a throw", eq(await api.json("GET", "/offline"), { status: 0, body: null }));
let err = null;
try { await api.call("/missing"); } catch (e) { err = e; }
check("api.call: throws on an error answer, with the status and the server's error", err && err.status === 404 && err.message === "not_found", err && err.message);
check("api.call: returns the body on success", (await api.call("/api/x", { body: {} })).url === "/api/x" && fetches.at(-1).init.method === "POST");

threw = "";
scripts.set("https://dana.example/addons/shopping/web/again.js", () => Dana.panel({ id: "shopping2", mount() {} }));
Dana.expect("/addons/shopping/web/again.js", "shopping");
fakeDocument.currentScript = { src: "https://dana.example/addons/shopping/web/again.js" };
try { Dana.panel({ id: "shopping2", mount() {} }); } catch (e) { threw = e.message; }
fakeDocument.currentScript = null;
check("an addon has one page", /already has a page/.test(threw), threw);

const tryRegister = (addon, def) => {
    Dana.expect(`/addons/${addon}/web/p.js`, addon);
    fakeDocument.currentScript = { src: `https://dana.example/addons/${addon}/web/p.js` };
    try { Dana.panel(def); return ""; } catch (e) { return e.message; } finally { fakeDocument.currentScript = null; }
};
check("a page needs an id of lowercase letters", /an id/.test(tryRegister("bad1", { id: "Bad Id", mount() {} })));
check("a page needs mount", /mount\(el, api\) is required/.test(tryRegister("bad2", { id: "bad2" })));
check("a failing start does not stop the page", tryRegister("flaky", { id: "flaky", start() { throw new Error("boom"); }, mount() {} }) === ""
      && (await Dana.registered("flaky").ready) === undefined && logged.some(l => l.includes("flaky: start failed")), logged);

scripts.set("https://dana.example/addons/silent/web/s.js", () => {});
let loadErr = "";
try { await Dana.load("silent", "/addons/silent/web/s.js"); } catch (e) { loadErr = e.message; }
check("a script that never calls Dana.panel fails its load", /did not call Dana.panel/.test(loadErr), loadErr);
loadErr = "";
try { await Dana.load("gone", "/addons/gone/web/none.js"); } catch (e) { loadErr = e.message; }
check("a script that cannot be fetched fails its load", /could not load/.test(loadErr), loadErr);
const before = appended.length;
await Dana.load("shopping", "/addons/shopping/web/shopping.js?v=7");
check("a page loads once", appended.length === before);

console.log(`\n${failures.length ? `${failures.length} check(s) failed` : "All checks passed"} (${pass} passed)`);
process.exit(failures.length ? 1 : 0);
