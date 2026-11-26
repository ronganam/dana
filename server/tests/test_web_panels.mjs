/**
 * The web shell's addon pages: the navigation built from a GET /api/addons payload (static/app-panels.js:
 * panelEntries, routeOf; an addon with several pages), and pages registering with the SDK (static/dana.js:
 * Dana.panel, Dana.load, two pages of one addon, the api a page gets, a core piece; Dana.widget, a dashboard
 * widget's renderer and its api).
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

const pagesOf = (a) => (a.state === "on" && Array.isArray(a.web) ? a.web : []);
for (const key of ["list_member", "list_admin", "list_admin_for_member", "list_member_disabled"]) {
    const addons = F[key].body.addons;
    const got = panelEntries(F[key].body);
    const want = addons.flatMap(a => pagesOf(a).map(p => p.id));
    check(`${key}: one entry per page of the addons on for the user`, eq(got.map(e => e.id).sort(), [...want].sort()), got.map(e => e.id));
    check(`${key}: nothing off, available, disabled or failed`,
          got.length > 0 && got.every(e => addons.find(a => a.name === e.addon).state === "on"));
    check(`${key}: in Nav order`, got.every((e, i) => i === 0 || got[i - 1].order <= e.order), got.map(e => e.order));
}
const admin = panelEntries(F.list_admin.body);
check("the admin's pages: tasks first, then shopping, inbox, people, watches, projects, memory, calls last",
      eq(admin.map(e => e.id), ["tasks", "shopping", "inbox", "people", "watches", "projects", "memory", "calls"]), admin.map(e => e.id));
check("the phone addon gives two pages, each its own entry",
      eq(admin.filter(e => e.addon === "phone").map(e => [e.id, e.panel, e.style]),
         [["inbox", "/addons/phone/web/inbox.js", "/addons/phone/web/inbox.css"], ["calls", "/addons/phone/web/calls.js", "/addons/phone/web/calls.css"]]),
      admin.filter(e => e.addon === "phone"));
const tasksPage = admin.find(e => e.id === "tasks");
check("the tasks addon's page is the Tasks screen", tasksPage && tasksPage.addon === "tasks" && tasksPage.label === "Tasks"
      && tasksPage.icon === "check-square" && tasksPage.panel === "/addons/tasks/web/tasks.js", tasksPage);
const shopping = admin.find(e => e.addon === "shopping");
check("an entry carries the page id, the manifest's label, icon and the files' URLs",
      shopping && shopping.id === "shopping" && shopping.label === "Shopping" && shopping.icon === "shopping-cart" && shopping.nav === true
      && shopping.panel === "/addons/shopping/web/shopping.js" && shopping.style === "/addons/shopping/web/shopping.css", shopping);
const member = panelEntries(F.list_member.body);
check("the member's only page is the calendar, a page without a style",
      eq(member.map(e => e.id), ["calendar"]) && member[0].style === null && member[0].panel === "/addons/calendar/web/calendar.js", member);
check("the admin's view of another user is that user's pages (what they have on)",
      eq(panelEntries(F.list_admin_for_member.body).map(e => e.id), member.map(e => e.id)));
check("an addon disabled on the server has no page",
      F.list_member_disabled.body.addons.some(a => a.name === "shopping" && a.state === "disabled-on-server")
      && !panelEntries(F.list_member_disabled.body).some(e => e.addon === "shopping"));

const synthetic = { addons: [
    { name: "zeta", title: "Zeta", state: "on", web: [{ id: "zeta", panel: "/addons/zeta/web/z.js", style: null, nav: { label: "Zeta", icon: "", order: 5 } }] },
    { name: "alpha", title: "Alpha", state: "on", web: [
        { id: "alpha", panel: "/addons/alpha/web/a.js", style: "/addons/alpha/web/a.css", nav: { label: "Alpha", icon: "a", order: 5 } },
        { id: "alpha-more", panel: "/addons/alpha/web/more.js", style: null, nav: { label: "More", icon: "", order: 1 } },
        { id: "alpha-hidden", panel: "/addons/alpha/web/h.js", style: null, nav: null },
    ] },
    { name: "noid", title: "No id", state: "on", web: [{ panel: "/addons/noid/web/n.js", style: null, nav: { label: "N", icon: "", order: 1 } }] },
    { name: "nopanel", title: "Style", state: "on", web: [{ id: "nopanel", panel: null, style: "/addons/nopanel/web/s.css", nav: { label: "S", icon: "", order: 1 } }] },
    { name: "later", title: "Later", state: "available", web: [] },
    { name: "off", title: "Off", state: "disabled-on-server", web: [{ id: "off", panel: "/addons/off/web/o.js", style: null, nav: { label: "Off", icon: "", order: 1 } }] },
    { name: "old", title: "Old shape", state: "on", web: { panel: "/addons/old/web/o.js", style: null, nav: null } },
    { name: "broken", title: "Broken", state: "failed", web: [] },
] };
const syn = panelEntries(synthetic);
check("pages of one addon sort among everyone's by their own order; ties by label; no Nav last (order 100)",
      eq(syn.map(e => e.id), ["alpha-more", "alpha", "zeta", "alpha-hidden"]), syn.map(e => e.id));
check("no Nav: listed (it can be opened) but not shown in the navigation, named after its addon",
      syn[3].nav === false && syn[3].label === "Alpha" && syn[3].addon === "alpha");
check("an icon falls back to a generic one", syn[2].icon === "puzzle" && syn[1].icon === "a");
check("a page without an id or a panel is none; a disabled addon has none; web must be a list",
      !syn.some(e => ["noid", "nopanel", "off", "old"].includes(e.addon)));
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
    badge: (page, value, tone) => shellCalls.push(["badge", page, value, tone]),
    visible: (page) => page === "shopping",
    piece: (name, el) => { shellCalls.push(["piece", name, el]); return name === "today"; },
});

let threw = "";
try { Dana.panel({ mount() {} }); } catch (e) { threw = e.message; }
check("a page not loaded by the shell cannot register", /not loaded by the shell/.test(threw), threw);

const order = [];
let startedWith = null;
const PANEL = "https://dana.example/addons/shopping/web/shopping.js?v=7";
scripts.set("https://dana.example/addons/shopping/web/shopping-core.js?v=7", () => { order.push("core"); sdkSb.DanaShoppingCore = { ok: true }; });
scripts.set(PANEL, () => {
    order.push("panel");
    Dana.panel({
        scripts: ["shopping-core.js"],
        start(api) { order.push("start"); startedWith = { api, core: sdkSb.DanaShoppingCore }; },
        mount() {},
    });
});
const reg = await Dana.load("shopping", "shopping", "/addons/shopping/web/shopping.js?v=7");
await reg.ready;
check("Dana.load: the page registers as the page the shell loaded, for its addon", reg.addon === "shopping" && reg.page === "shopping"
      && Dana.registered("shopping") === reg, reg.addon);
check("its scripts load first, in order, beside it with its version; then start", eq(order, ["panel", "core", "start"])
      && startedWith && startedWith.core && startedWith.core.ok === true, order);
const api = reg.api;
check("start gets the page's api", startedWith.api === api && api.addon === "shopping" && api.page === "shopping"
      && api.base === "https://dana.example/addons/shopping/web/");
check("api.me is the shell's /api/me", api.me && api.me.name === "Robin");
api.badge(4, "quiet"); api.badge(0); api.open("people", "p1"); api.openSession("s9"); api.toast("Saved");
check("api.badge / open / openSession / toast reach the shell, badges named by the page",
      eq(shellCalls, [["badge", "shopping", 4, "quiet"], ["badge", "shopping", 0, ""], ["open", "people", "p1"], ["session", "s9"], ["toast", "Saved"]]), shellCalls);
check("api.visible asks the shell about this page", api.visible() === true);
const host = { id: "host" };
shellCalls.length = 0;
check("api.piece: a core piece the shell has draws into the page's element; another is false",
      api.piece("today", host) === true && api.piece("nothing", host) === false
      && eq(shellCalls.map(c => [c[0], c[1], c[2] === host]), [["piece", "today", true], ["piece", "nothing", true]]), shellCalls);
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

// Two pages of one addon: each its own registration and api, badges and visibility by page.
scripts.set("https://dana.example/addons/phone/web/inbox.js", () => Dana.panel({ mount() {} }));
scripts.set("https://dana.example/addons/phone/web/calls.js", () => Dana.panel({ mount() {} }));
const [inbox, calls] = await Promise.all([Dana.load("phone", "inbox", "/addons/phone/web/inbox.js"), Dana.load("phone", "calls", "/addons/phone/web/calls.js")]);
shellCalls.length = 0;
inbox.api.badge(2); calls.api.badge(5);
check("two pages of one addon register apart, each with its own api",
      inbox !== calls && inbox.page === "inbox" && calls.page === "calls" && inbox.addon === "phone" && calls.addon === "phone"
      && Dana.registered("inbox") === inbox && Dana.registered("calls") === calls && inbox.api !== calls.api
      && inbox.api.page === "inbox" && calls.api.addon === "phone");
check("…and their badges are their own", eq(shellCalls, [["badge", "inbox", 2, ""], ["badge", "calls", 5, ""]]), shellCalls);

threw = "";
Dana.expect("/addons/phone/web/again.js", "phone", "calls");
fakeDocument.currentScript = { src: "https://dana.example/addons/phone/web/again.js" };
try { Dana.panel({ mount() {} }); } catch (e) { threw = e.message; }
fakeDocument.currentScript = null;
check("a page registers once", /already registered/.test(threw), threw);

const tryRegister = (addon, def) => {
    Dana.expect(`/addons/${addon}/web/p.js`, addon, addon);
    fakeDocument.currentScript = { src: `https://dana.example/addons/${addon}/web/p.js` };
    try { Dana.panel(def); return ""; } catch (e) { return e.message; } finally { fakeDocument.currentScript = null; }
};
check("a page needs mount", /mount\(el, api, arg\) is required/.test(tryRegister("bad2", {})));
check("a failing start does not stop the page", tryRegister("flaky", { start() { throw new Error("boom"); }, mount() {} }) === ""
      && (await Dana.registered("flaky").ready) === undefined && logged.some(l => l.includes("flaky: start failed")), logged);

scripts.set("https://dana.example/addons/silent/web/s.js", () => {});
let loadErr = "";
try { await Dana.load("silent", "silent", "/addons/silent/web/s.js"); } catch (e) { loadErr = e.message; }
check("a script that never calls Dana.panel fails its load", /did not call Dana.panel/.test(loadErr), loadErr);
loadErr = "";
try { await Dana.load("gone", "gone", "/addons/gone/web/none.js"); } catch (e) { loadErr = e.message; }
check("a script that cannot be fetched fails its load", /could not load/.test(loadErr), loadErr);
const before = appended.length;
await Dana.load("shopping", "shopping", "/addons/shopping/web/shopping.js?v=7");
check("a page loads once", appended.length === before);

// ---------- dashboard widgets (Dana.widget) ----------
const WIDGET = "https://dana.example/addons/tasks/web/widget.js?v=7";
scripts.set(WIDGET, () => { Dana.widget("reminders", { bare: true, mount(el, w) { el.text = w.data.n; } }); });
await Dana.loadScript(WIDGET);
const wdef = Dana.widgetDef("reminders");
check("Dana.widget: registers a widget's renderer for the addon whose web/ folder its script is in",
      wdef && wdef.addon === "tasks" && wdef.url === WIDGET && wdef.def.bare === true && Dana.widgetDef("nope") === null, wdef);
const wapi = Dana.widgetApi("tasks", WIDGET, { size: "medium", refresh() { return "again"; } });
check("Dana.widgetApi: a page's api for the addon plus the dashboard's extras",
      wapi.addon === "tasks" && wapi.base === "https://dana.example/addons/tasks/web/" && wapi.size === "medium"
      && wapi.refresh() === "again" && typeof wapi.esc === "function");
let wthrew = "";
try { Dana.widget("loose", { mount() {} }); } catch (e) { wthrew = e.message; }
check("Dana.widget: refused outside an addon's script", /not loaded from an addon/.test(wthrew), wthrew);
fakeDocument.currentScript = { src: "https://dana.example/addons/tasks/web/again.js" };
const attempt = (key, def) => { try { Dana.widget(key, def); return ""; } catch (e) { return e.message; } };
check("Dana.widget: a key registers once; a renderer needs mount",
      /already registered/.test(attempt("reminders", { mount() {} })) && Dana.widgetDef("reminders").url === WIDGET
      && /mount\(el, w, api\) is required/.test(attempt("x", {})));
fakeDocument.currentScript = null;

console.log(`\n${failures.length ? `${failures.length} check(s) failed` : "All checks passed"} (${pass} passed)`);
process.exit(failures.length ? 1 : 0);
