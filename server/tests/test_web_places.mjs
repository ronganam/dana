/**
 * The web navigation's places (static/app-places.js): Chat and the pages in Nav order, the default primary places (Nav primary), a saved choice applied (unknown ids skipped, new pages in the rest), what fits on the rail, the places under More, More's badge, pin / unpin / reorder.
 *
 * The pages come from the real payload captured in tests/fixtures/addons_web.json (panelEntries,
 * static/app-panels.js), plus made-up pages for the long lists.
 *
 * Run: bin/test test_web_places
 */

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const SERVER = path.join(here, "..");
const F = JSON.parse(fs.readFileSync(path.join(SERVER, "tests", "fixtures", "addons_web.json"), "utf8"));

let pass = 0;
const failures = [];
function check(name, cond, detail = "") {
    if (cond) { pass++; console.log(`[PASS] ${name}`); }
    else { failures.push(name); console.log(`[FAIL] ${name}${detail ? " — " + JSON.stringify(detail) : ""}`); }
}
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

const sb = { console, Number, String, JSON, Map, Set, Array, Math, Promise,
             window: { Dana: { attach() {} } }, document: { addEventListener() {} } };
vm.createContext(sb);
vm.runInContext(fs.readFileSync(path.join(SERVER, "static/app-places.js"), "utf8")
    + fs.readFileSync(path.join(SERVER, "static/app-panels.js"), "utf8")
    + "\nthis.api = { NAV_MAX_PRIMARY, navPlaces, defaultPrimary, parseChoice, applyChoice, navLayout, railPlan,"
    + " hiddenPlaces, moreBadge, movePlace, pinPlace, unpinPlace, panelEntries };", sb);
const P = sb.api;
const plain = (v) => JSON.parse(JSON.stringify(v));   // out of the vm's realm, for eq / includes

const CHAT = { id: "chat", label: "Chat", icon: "message-square", order: -1 };
const CORE = [{ id: "settings", label: "Settings", icon: "settings" }, { id: "addons", label: "Addons", icon: "plug" },
              { id: "usage", label: "Usage", icon: "receipt" }, { id: "dash", label: "Dash", icon: "x", href: "/dashboard" }];
const ids = (list) => plain(list.map(p => p.id));

// ---------- the places ----------
const entries = P.panelEntries(F.list_admin.body);
const places = P.navPlaces(CHAT, entries, CORE);
check("main places: Chat first, then the pages with a Nav in Nav order",
      eq(ids(places.main), ["chat", ...entries.filter(e => e.nav).map(e => e.id)]), ids(places.main));
check("the core group as given, the dashboard a link", eq(ids(places.core), ["settings", "addons", "usage", "dash"])
      && places.core[3].href === "/dashboard" && places.core[0].href === null);
check("a page without a Nav is no place",
      ids(P.navPlaces(CHAT, [{ id: "a", label: "A", icon: "x", order: 1, nav: true }, { id: "b", label: "B", order: 2, nav: false }], []).main).join() === "chat,a");
check("an id given twice is one place", ids(P.navPlaces(CHAT, [{ id: "chat", label: "Again", nav: true }], []).main).join() === "chat");
check("panelEntries carries the Nav's primary", entries.some(e => e.primary) && entries.every(e => typeof e.primary === "boolean"),
      entries.map(e => [e.id, e.primary]));

// ---------- defaults ----------
const fixturePrimary = entries.filter(e => e.primary).map(e => e.id);
const defaults = plain(P.defaultPrimary(places.main));
check("defaults: Chat, then the pages whose Nav asks for it, filled by Nav order up to four",
      defaults[0] === "chat" && defaults.length === Math.min(4, places.main.length)
      && fixturePrimary.slice(0, 3).every(id => defaults.includes(id)), { defaults, fixturePrimary });
const made = (n, primaries = []) => Array.from({ length: n }, (_, i) => ({ id: `p${i}`, label: `P${i}`, icon: "x", order: i * 10,
                                                                          nav: true, primary: primaries.includes(i) }));
const many = P.navPlaces(CHAT, made(13, [5, 9]), CORE);
check("defaults: the asking pages before the first ones by order", eq(plain(P.defaultPrimary(many.main)), ["chat", "p5", "p9", "p0"]));
check("defaults: none asks → the first places by Nav order", eq(plain(P.defaultPrimary(P.navPlaces(CHAT, made(6), []).main)), ["chat", "p0", "p1", "p2"]));
check("defaults: more pages ask than fit → the first by order", eq(plain(P.defaultPrimary(P.navPlaces(CHAT, made(6, [1, 2, 3, 4]), []).main)), ["chat", "p1", "p2", "p3"]));
check("defaults: fewer places than slots → all of them", eq(plain(P.defaultPrimary(P.navPlaces(CHAT, made(1), []).main)), ["chat", "p0"]));

// ---------- a saved choice ----------
check("parseChoice: a list of ids", eq(plain(P.parseChoice('["p3","chat"]')), ["p3", "chat"]));
check("parseChoice: nothing, junk or not a list → null",
      [null, "", "{", '{"a":1}', 42].every(v => P.parseChoice(v) === null));
check("parseChoice: non-string items dropped", eq(plain(P.parseChoice('["a",1,null,"b"]')), ["a", "b"]));
check("applyChoice: the saved order kept", eq(plain(P.applyChoice(many.main, ["p7", "chat", "p2"])), ["p7", "chat", "p2"]));
check("applyChoice: unknown / removed ids and repeats skipped, at most four",
      eq(plain(P.applyChoice(many.main, ["gone", "p1", "p1", "settings", "p2", "p3", "p4", "p5"])), ["p1", "p2", "p3", "p4"]));
check("applyChoice: nothing known left → the defaults", eq(plain(P.applyChoice(many.main, ["gone"])), plain(P.defaultPrimary(many.main))));
check("applyChoice: no choice → the defaults", eq(plain(P.applyChoice(many.main, null)), plain(P.defaultPrimary(many.main))));
check("applyChoice: Chat may leave the bar", !plain(P.applyChoice(many.main, ["p1", "p2"])).includes("chat"));

const lay = P.navLayout(many, ["p7", "chat"]);
check("layout: primary in the chosen order, the rest in Nav order, the core group last",
      eq(ids(lay.primary), ["p7", "chat"]) && eq(ids(lay.rest), ["p0", "p1", "p2", "p3", "p4", "p5", "p6", "p8", "p9", "p10", "p11", "p12"])
      && eq(ids(lay.core), ["settings", "addons", "usage", "dash"]));
const grown = P.navPlaces(CHAT, [...made(13), { id: "new", label: "New", icon: "x", order: 15, nav: true }], CORE);
const grownLay = P.navLayout(grown, P.applyChoice(grown.main, ["p7", "chat"]));
check("a new page falls into the rest by its Nav order, the choice unchanged",
      eq(ids(grownLay.primary), ["p7", "chat"]) && ids(grownLay.rest).indexOf("new") === ids(grownLay.rest).indexOf("p1") + 1, ids(grownLay.rest));

// ---------- the rail ----------
const S = { item: 56, coreItem: 44, more: 56, edit: 40, divider: 21, sep: 9 };
const counts = { primary: 4, rest: 9, core: 4 };
const tall = P.railPlan(counts, 4000, S);
check("rail: everything fits → every place, the core group, no More", eq(plain(tall), { rest: 9, core: true, more: false }));
const mid = P.railPlan(counts, 700, S);   // 700 - 4*56 - 56 - (4*44 + 21) = 223 → 3 rows
check("rail: too many → the rest that fit, then More, the core group kept", eq(plain(mid), { rest: 3, core: true, more: true }), mid);
const short = P.railPlan(counts, 400, S); // 400 - 224 - 56 - 197 < 0 → the core group under More: 120 → 2 rows
check("rail: not even the core group fits → it goes under More too", eq(plain(short), { rest: 2, core: false, more: true }), short);
check("rail: no room at all → no rest, More holds the rest and the core group", eq(plain(P.railPlan(counts, 100, S)), { rest: 0, core: false, more: true }));
const exact = 4 * 56 + 9 * 56 + 40 + 4 * 44 + 21;
check("rail: the exact height fits", P.railPlan(counts, exact, S).more === false && P.railPlan(counts, exact - 1, S).more === true);
check("rail: only primary places, too short → the core group under More",
      eq(plain(P.railPlan({ primary: 4, rest: 0, core: 4 }, 300, S)), { rest: 0, core: false, more: true }));

// ---------- under More ----------
const hid = P.hiddenPlaces(lay, { rest: 3, core: true, more: true });
check("under More on a phone: the rest and the core group", eq(plain(hid.phone), [...ids(lay.rest), ...ids(lay.core)]));
check("under More on the rail: the rest that did not fit", eq(plain(hid.rail), ids(lay.rest).slice(3)));
check("under More on the rail with the core group folded in",
      eq(plain(P.hiddenPlaces(lay, { rest: 1, core: false, more: true }).rail), [...ids(lay.rest).slice(1), ...ids(lay.core)]));
check("nothing under More on a rail that fits", plain(P.hiddenPlaces(lay, { rest: 12, core: true, more: false }).rail).length === 0);

// ---------- More's badge ----------
const badges = new Map([["p1", { text: "3", tone: "" }], ["p2", { text: "4", tone: "quiet" }], ["p3", { text: "99+", tone: "" }],
                        ["p4", { text: "new", tone: "" }], ["p5", { text: "", tone: "" }], ["p6", { text: "2", tone: "quiet" }]]);
check("badge: counts under More add up", eq(plain(P.moreBadge(["p1", "p2"], badges)), { text: "7", dot: false, tone: "" }));
check("badge: past 99 → 99+", P.moreBadge(["p1", "p3"], badges).text === "99+");
check("badge: a word with no count → a dot", eq(plain(P.moreBadge(["p4", "p5"], badges)), { text: "", dot: true, tone: "" }));
check("badge: a count wins over a word", eq(plain(P.moreBadge(["p4", "p1"], badges)), { text: "3", dot: false, tone: "" }));
check("badge: quiet only when every badge is", P.moreBadge(["p2", "p6"], badges).tone === "quiet" && P.moreBadge(["p2", "p1"], badges).tone === "");
check("badge: places not under More do not count", eq(plain(P.moreBadge(["p5", "p9"], badges)), { text: "", dot: false, tone: "" }));
check("badge: a plain object works too", P.moreBadge(["a", "b"], { a: { text: "1" }, b: { text: "2" } }).text === "3");

// ---------- edit ----------
check("move up", eq(plain(P.movePlace(["a", "b", "c"], "c", -1)), ["a", "c", "b"]));
check("move down", eq(plain(P.movePlace(["a", "b", "c"], "a", 1)), ["b", "a", "c"]));
check("moving past an end or an unknown id changes nothing",
      eq(plain(P.movePlace(["a", "b"], "a", -1)), ["a", "b"]) && eq(plain(P.movePlace(["a", "b"], "b", 1)), ["a", "b"]) && eq(plain(P.movePlace(["a"], "z", 1)), ["a"]));
check("pin adds at the end", eq(plain(P.pinPlace(["a"], "b")), ["a", "b"]));
check("pin: no room past four, no repeat", eq(plain(P.pinPlace(["a", "b", "c", "d"], "e")), ["a", "b", "c", "d"]) && eq(plain(P.pinPlace(["a"], "a")), ["a"]));
check("unpin removes; the last one stays", eq(plain(P.unpinPlace(["a", "b"], "a")), ["b"]) && eq(plain(P.unpinPlace(["a"], "a")), ["a"]));
check("four primary places at most (four in the bar plus More)", P.NAV_MAX_PRIMARY === 4);

console.log(`\n${failures.length ? `${failures.length} check(s) failed` : "All checks passed"} (${pass} passed)`);
process.exit(failures.length ? 1 : 0);
