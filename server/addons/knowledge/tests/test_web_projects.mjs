/**
 * Contract test for the web Projects panel's pure part (addons/knowledge/web/projects-core.js, DanaProjectsCore).
 *
 * Loads it without a browser and feeds it the *actual payloads* the API answered, captured in
 * tests/fixtures/projects_web.json by `WRITE_WEB_FIXTURE=1 bin/test test_knowledge_sections`: the owner's
 * project list and items (a wedding with a decision section — one option chosen, one shortlisted, one
 * rejected with a price range — a notes section and a second decision) and a read share's items. Checks
 * numbers and money parsing, value formatting per column type, the best value of a compared column, the
 * order options are shown in, a section card's words, the budget, the countdown, the fields a value edit
 * sends and the section editor's columns.
 *
 * Run: bin/test test_web_projects [path-to-fixture]
 */

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const SERVER = path.join(here, "..", "..", "..");  // the server folder
const FIXTURE = process.argv[2] || path.join(SERVER, "tests", "fixtures", "projects_web.json");
const F = JSON.parse(fs.readFileSync(FIXTURE, "utf8"));
const src = fs.readFileSync(path.join(SERVER, "addons/knowledge/web/projects-core.js"), "utf8");

const sandbox = { console, URL, Set, Map, JSON, Object, Array, String, Number, Boolean, RegExp, Math, Date, Error, Intl };
sandbox.window = sandbox;
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "addons/knowledge/web/projects-core.js" });
const C = sandbox.DanaProjectsCore;

let pass = 0;
const failures = [];
function check(name, cond, detail) {
  if (cond) { pass++; console.log(`[PASS] ${name}`); }
  else { failures.push(name); console.log(`[FAIL] ${name}${detail !== undefined ? " — " + JSON.stringify(detail) : ""}`); }
}
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

const items = F.items_owner;
const photographers = items.sections.find((s) => s.title === "צלמים");
const notes = items.sections.find((s) => s.kind === "notes");
const opts = items.entries.filter((e) => e.section_id === photographers.id);
const byTitle = Object.fromEntries(opts.map((e) => [e.title, e]));

// numbers
check("asNumber: plain, commas, shekel sign and word", eq([C.asNumber(13400), C.asNumber("13,400"), C.asNumber("₪ 13,400"), C.asNumber("10500 ש\"ח"), C.asNumber("10500 ש״ח")], [13400, 13400, 13400, 10500, 10500]));
check("asNumber: a range, text, a boolean and nothing are not numbers", eq([C.asNumber("10500-16500"), C.asNumber("soon"), C.asNumber(true), C.asNumber(null), C.asNumber("")], [null, null, null, null, null]));

// formatting by type
const money = photographers.columns.find((c) => c.type === "money");
const phone = photographers.columns.find((c) => c.type === "phone");
check("the project carries its owner's currency", items.project.currency === "ILS", items.project.currency);
C.setCurrency(items.project.currency);
check("valueHtml: money in the project's currency (₪ for ILS) with thousands", C.valueHtml(money, 13400).includes("₪13,400")
  && C.TYPE_LABELS.money === "Price (₪)");
check("currency: $ for USD, a code and a space without a narrow symbol, nothing for a bad code",
  C.currencySymbol("USD") === "$" && C.currencySymbol("CHF") === "CHF " && C.currencySymbol("shekel") === "" && C.currencySymbol(undefined) === "");
C.setCurrency("USD");
check("setCurrency: money and the column label follow", C.money(13400) === "$13,400" && C.shortMoney(150000) === "$150k"
  && C.TYPE_LABELS.money === "Price ($)");
C.setCurrency("bad");
check("setCurrency: a bad code keeps the last one", C.money(5) === "$5");
C.setCurrency(items.project.currency);
check("valueHtml: a money range stays as written", C.valueHtml(money, "10500-16500").includes("10500-16500"));
check("valueHtml: empty is a dash", C.valueHtml(money, null).includes("—") && C.valueHtml(money, "").includes("—"));
check("valueHtml: phone links to tel:", C.valueHtml(phone, "052-0000000").includes('href="tel:0520000000"'));
check("valueHtml: checkbox, rating, date, url", C.valueHtml({ type: "checkbox" }, true).includes("Yes")
  && C.valueHtml({ type: "rating" }, 4).includes("★★★★") && C.valueHtml({ type: "date" }, "2027-09-20").includes("20.9.2027")
  && C.valueHtml({ type: "url" }, "www.example-venue.com").includes('href="https://www.example-venue.com"'));
check("valueHtml: text is escaped", C.valueHtml({ type: "text" }, "<b>x</b>").includes("&lt;b&gt;"));
check("valueHtml: a javascript: url is not a link", !C.valueHtml({ type: "url" }, "javascript:alert(1)").includes("href"));
check("phoneLinks: an Israeli number → wa.me digits with 972", eq(C.phoneLinks("052-6000123"), { tel: "0526000123", wa: "972526000123" }));

// compare
const best = C.bestIds(photographers, opts);
check("bestIds: the lowest price among live options (the rejected range is out)",
  best[money.key] && best[money.key].size === 1 && best[money.key].has(byTitle["אורי עשור"].id), best[money.key] && [...best[money.key]]);
check("bestIds: no column without a direction (phone)", !best[phone.key]);
check("orderOptions: chosen, shortlisted, rejected", eq(C.orderOptions(opts).map((e) => e.decision), ["chosen", "shortlist", "rejected"]));

// section cards
const st = C.sectionState(photographers, items.entries);
check("sectionState: chosen named, counts, price range of live options",
  st.decided && st.line === "Chosen: אורי עשור" && st.count === 3 && st.rejected === 1 && eq(st.price, { min: 13400, max: 13900 }), st);
const dj = items.sections.find((s) => s.title === "DJ");
check("sectionState: an undecided decision", C.sectionState(dj, items.entries).line === "1 option · 1 shortlisted · not decided", C.sectionState(dj, items.entries).line);
check("sectionState: notes count items", C.sectionState(notes, items.entries).line === "Nothing yet");

// budget and date
const listed = F.list_owner[0];
check("budgetInfo: from the list payload", eq(C.budgetInfo(listed), { budget: 150000, committed: 13400, left: 136600, pct: 9 }), C.budgetInfo(listed));
check("budgetInfo: computed from the items matches the server's", eq(C.budgetInfo(items.project, items.sections, items.entries), C.budgetInfo(listed)));
check("budgetInfo: none without budget or commitments", C.budgetInfo({ budget: null, committed: 0 }) === null);
const cd = C.countdown(items.project.target_date, new Date(2026, 10, 19));
check("countdown: days to the date", cd.days === 305 && cd.label === "in 305 days" && cd.date === "20.9.2027", cd);
check("countdown: today / tomorrow / past", C.countdown("2026-11-19", new Date(2026, 10, 19)).label === "Today"
  && C.countdown("2026-11-20", new Date(2026, 10, 19)).label === "Tomorrow" && C.countdown("2026-11-17", new Date(2026, 10, 19)).label === "2 days ago");

// editing values
const ori = byTitle["אורי עשור"];
const changed = C.setField(ori.fields, money, "12,900");
check("setField: a money edit is stored as a number under the column's (current) name",
  changed.find((f) => f.name === money.name).value === 12900 && changed.length === ori.fields.length, changed);
const added = C.setField(ori.fields, { name: "Rating", type: "rating" }, "4");
check("setField: a column without a value yet is appended", added.length === ori.fields.length + 1 && added.at(-1).value === 4);
check("setField: a range stays text", C.setField([], money, "1-2")[0].value === "1-2");
check("extraFields: fields that are not columns", eq(C.extraFields({ fields: [{ name: "x", value: 1 }, { name: money.name, value: 2 }] }, photographers).map((f) => f.name), ["x"]));
check("firstPhone: from the phone column", C.firstPhone(ori, photographers)?.wa === "972520000000", C.firstPhone(ori, photographers));

// the section editor
const payload = C.columnsPayload([{ key: "k1", name: " מחיר ", type: "money", role: "price" }, { name: "" }, { name: "מחיר", type: "text" }, { name: "Phone", type: "weird" }]);
check("columnsPayload: trimmed, empties and duplicates dropped, keys and roles kept, unknown type → text",
  eq(payload, [{ name: "מחיר", type: "money", key: "k1", role: "price" }, { name: "Phone", type: "text" }]), payload);

// files and the read share
const file = ori.files[0];
check("files: kind, icon and size", C.fileKind(file.mime) === "pdf" && C.FILE_ICONS.pdf === "file-text" && C.fileSize(file.size) === "14 B"
  && C.fileKind("audio/ogg") === "audio" && C.fileSize(2.5 * 1024 * 1024) === "2.5 MB");
check("read share: the items carry access read on the project and its entries",
  F.items_read.project.access === "read" && F.items_read.entries.every((e) => e.access === "read"));
check("Hebrew: decision labels and a section's line", C.decisionLabel("chosen", "he") === "נבחר" && C.decisionLabel("", "en") === "Option"
  && C.sectionState(dj, items.entries, "he").line === "אפשרות אחת · 1 ברשימה הקצרה · טרם הוחלט", C.sectionState(dj, items.entries, "he").line);
check("Hebrew: countdown words", C.countdown("2027-09-20", new Date(2027, 8, 10), "he").label === "בעוד 10 ימים" && C.countdown("2027-09-20", new Date(2027, 8, 19), "he").label === "מחר");
check("isRtl: Hebrew titles", C.isRtl(items.project.title) && !C.isRtl("Wedding"));

console.log(`\n${pass}/${pass + failures.length} passed`);
for (const f of failures) console.log(`  FAILED: ${f}`);
process.exit(failures.length ? 1 : 0);
