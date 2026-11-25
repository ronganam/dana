/**
 * The web Live strip's pure part (static/app-live-core.js, DanaLive) against the wire contract
 * (tests/fixtures/live_web.json): the shown fields (top-level overridden by the selected choice's), next / prev
 * bounds, the timer's words and formatting ("Departs in 7:42", "1 h 05", count-up, passed), the progress
 * geometry (segments, fill, points, tracker), where a tap goes, a reply folded into the list, the card's HTML
 * (escaped, labelled buttons, an aria-live="off" timer with the absolute time for screen readers, the ended
 * card), and the shell wiring (index.html, sw.js, Now's hooks).
 *
 * Run: bin/test test_web_live
 */

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const SERVER = path.join(here, "..");

let pass = 0;
const failures = [];
function check(name, cond, detail = "") {
    if (cond) { pass++; console.log(`[PASS] ${name}`); }
    else { failures.push(name); console.log(`[FAIL] ${name}${detail !== "" ? " — " + JSON.stringify(detail) : ""}`); }
}
const plain = (v) => JSON.parse(JSON.stringify(v));
const eq = (a, b) => JSON.stringify(plain(a)) === JSON.stringify(b);

const sb = { console };
vm.createContext(sb);
vm.runInContext(fs.readFileSync(path.join(SERVER, "static/app-live-core.js"), "utf8"), sb);
const L = sb.DanaLive;
check("app-live-core.js sets globalThis.DanaLive", Boolean(L && L.view && L.cardHtml));

const fixture = JSON.parse(fs.readFileSync(path.join(SERVER, "tests/fixtures/live_web.json"), "utf8"));
const acts = L.parse(fixture);
const [rail, agent] = acts;
check("the fixture parses: a running train and an ended agent session", acts.length === 2 && rail.id === "rail:watch-12" && agent.ended === true);
check("bad entries are left out", L.parse({ activities: [null, { id: "" }, { title: "no id" }, { id: "a:b" }] }).length === 1
      && L.parse(null).length === 0 && L.parse({}).length === 0);

// --- What is shown ---------------------------------------------------------------------------------
const v404 = L.view(rail);
check("the selected choice is shown: its fields over the top-level ones",
      v404.key === "404" && v404.index === 0 && v404.count === 2 && v404.title === "Departs 17:46 (+4)" && v404.tone === "warn");
check("fields a choice leaves out keep the top-level ones (context, icon, progress, open)",
      v404.context === "Central → Harbour · Train 404" && v404.icon === "train-front" && v404.progress && v404.open === "/rail");
const v406 = L.view(rail, "406");
check("a pick on this client overrides `selected`",
      v406.key === "406" && v406.title === "Departs 17:52" && v406.text === "Platform 1 · Arrives 18:41" && v406.tone === "good"
      && v406.countdown.to === "2026-11-25T17:52:00+02:00" && v406.context === v404.context);
check("an unknown pick falls back to `selected`, then the first", L.view(rail, "999").key === "404"
      && L.view({ ...rail, selected: "nope" }).key === "404" && L.view({ ...rail, selected: "406" }).key === "406");
check("a choice's timer replaces the other kind",
      !("countdown" in plain(L.view({ id: "x", title: "t", tone: "neutral", countdown: { to: "2026-11-25T18:00:00+02:00" },
                                      choices: [{ key: "a", title: "t", tone: "neutral", since: "2026-11-25T17:00:00+02:00" }], selected: "a" }))));
check("no choices: index -1, count 0, the activity as it is",
      eq([L.view({ id: "a", title: "T", tone: "bad" }).index, L.view({ id: "a", title: "T", tone: "bad" }).count, L.view({ id: "a", title: "T", tone: "bad" }).tone], [-1, 0, "bad"]));
check("an unknown tone is neutral, a bad icon name the generic one",
      L.view({ id: "a", title: "T", tone: "purple", icon: "<svg>" }).tone === "neutral" && L.view({ id: "a", title: "T", icon: "<svg>" }).icon === "activity");
const ended = L.view(agent);
check("an ended one shows its final (title, text, tone) and no choices",
      ended.ended && ended.title === "Done · Fix the parser" && ended.text === "All 42 tests pass" && ended.tone === "good" && ended.count === 0);
check("a final without a tone is muted", L.view({ id: "a", title: "T", ended: true, final: { title: "Over" } }).tone === "muted");

// --- Next / previous -------------------------------------------------------------------------------
check("next from the first choice is the second; previous from it is nothing",
      L.stepChoice(rail, undefined, 1) === "406" && L.stepChoice(rail, undefined, -1) === null);
check("from the last: next is nothing, previous the first", L.stepChoice(rail, "406", 1) === null && L.stepChoice(rail, "406", -1) === "404");
check("without choices there is nowhere to go", L.stepChoice({ id: "a" }, "", 1) === null && L.stepChoice(agent, "", -1) === null);

// --- The timer -------------------------------------------------------------------------------------
check("durations: m:ss under an hour, h mm under a day, then days",
      eq(["0:00", "0:05", "7:42", "59:59", "1 h 00", "1 h 05", "23 h 59", "1 d", "2 d 3 h"],
         [0, 5, 462, 3599, 3600, 3900, 86399, 86400, 2 * 86400 + 3 * 3600].map(L.duration)));
check("a negative or broken duration is 0:00", L.duration(-5) === "0:00" && L.duration("x") === "0:00");
const at = (iso) => Date.parse(iso);
const dep = L.timer(v404, at("2026-11-25T17:38:18+02:00"));
check("a countdown: the label + \"in\", the time left, the absolute time for screen readers",
      eq(dep, { kind: "countdown", label: "Departs in", value: "7:42", at: "at 17:46", past: false }), dep);
check("seconds round up while counting down (never \"0:00\" before it is due)",
      L.timer(v404, at("2026-11-25T17:45:59.400+02:00")).value === "0:01");
check("over an hour away: \"1 h 05\"", L.timer(v404, at("2026-11-25T16:41:00+02:00")).value === "1 h 05");
const gone = L.timer(v404, at("2026-11-25T17:46:00+02:00"));
check("once it passes: the label alone and \"now\"", gone.past && gone.label === "Departs" && gone.value === "now");
check("a countdown without a label: \"In\"",
      L.timer({ countdown: { to: "2026-11-25T17:46:00+02:00" } }, at("2026-11-25T17:45:00+02:00")).label === "In");
const up = L.timer({ since: agent.since }, at("2026-11-25T17:29:14+02:00"));
check("since counts up", eq(up, { kind: "since", label: "Elapsed", value: "28:14", at: "since 17:01", past: false }), up);
check("no timer, or a broken time: null", L.timer({ title: "x" }, 0) === null && L.timer({ countdown: { to: "soon" } }, 0) === null);
check("the absolute time is the payload's own clock", L.clockOf("2026-11-25T17:46:00+02:00") === "17:46" && L.clockOf("") === "");
check("the strip ticks only while a running one has a timer",
      L.ticking(acts, {}) && !L.ticking([agent], {}) && !L.ticking([{ id: "a", title: "T" }], {}));

// --- Progress --------------------------------------------------------------------------------------
const g = L.progress(rail.progress);
check("the total is the sum of the segment lengths; each segment's place and width in percent",
      g.total === 10 && eq(g.segments.map(s => [s.start, s.width, s.tone]), [[0, 40, "warn"], [40, 60, "warn"]]), g);
check("styled by progress at value 0: nothing filled; the point at 40 % not reached; the tracker at 0",
      eq(g.segments.map(s => s.fill), [0, 0]) && eq(g.points, [{ at: 40, tone: "neutral", reached: false }]) && g.pct === 0 && g.tracker === "train-front");
const half = L.progress({ ...rail.progress, value: 7 });
check("value 7 of 10: the first segment full, the second half, the point reached, the tracker at 70 %",
      eq(half.segments.map(s => s.fill), [1, 0.5]) && half.points[0].reached && half.pct === 70);
const flat = L.progress({ ...rail.progress, value: 7, styled_by_progress: false });
check("not styled by progress: every segment at full strength", eq(flat.segments.map(s => s.fill), [1, 1]) && flat.pct === 70);
const bare = L.progress({ value: 25, indeterminate: false, styled_by_progress: false });
check("no segments: one track of 100 in the card's own tone, filled to the value", bare.total === 100 && eq(bare.segments, [{ start: 0, width: 100, tone: "", fill: 0.25 }]) && bare.tracker === "");
check("the value and points are kept on the bar", L.progress({ value: 500, points: [{ at: -3, tone: "bad" }] }).pct === 100
      && L.progress({ value: 500, points: [{ at: -3, tone: "bad" }] }).points[0].at === 0);
check("indeterminate is passed on; no progress is null", L.progress({ value: 0, indeterminate: true }).indeterminate && L.progress(undefined) === null);

// --- Where a tap goes, replies ---------------------------------------------------------------------
check("a Dana path opens its screen", eq(L.openTarget("/rail"), { type: "screen", id: "rail", arg: null })
      && eq(L.openTarget("/now"), { type: "screen", id: "now", arg: null }) && eq(L.openTarget("/rail/404"), { type: "screen", id: "rail", arg: "404" }));
check("an https address opens in a new tab", eq(L.openTarget("https://example.com/t?x=1"), { type: "url", url: "https://example.com/t?x=1" }));
check("anything else goes nowhere", [null, "", "http://example.com", "javascript:alert(1)", "//evil.example", "rail"].every(o => L.openTarget(o) === null));
const replaced = L.applyReply(acts, "rail:watch-12", { ...rail, rev: 5, selected: "406" });
check("a reply replaces the activity in place", replaced.length === 2 && replaced[0].rev === 5 && replaced[1] === agent);
check("a null reply (it ended) removes it", eq(L.applyReply(acts, "rail:watch-12", null).map(a => a.id), ["phone:agent-claude-abc"]));
check("a reply for one not listed goes in front", L.applyReply([agent], "rail:watch-12", rail)[0] === rail);

// --- The card --------------------------------------------------------------------------------------
const now = at("2026-11-25T17:38:18+02:00");
const html = L.cardHtml(rail, { now });
check("the card: tone class, labelled by its title, the icon", html.includes('class="lv-card lv-tone-warn can-open"') && html.includes('aria-labelledby="lv-rail_watch-12-t"')
      && html.includes('data-lucide="train-front"'));
check("the timer: label, value with aria-live=off, the absolute time visually hidden",
      html.includes('<span class="lv-timer-label">Departs in</span>') && html.includes('<span class="lv-timer-value" aria-live="off">7:42</span>')
      && html.includes('<span class="nav-sr-only lv-timer-at">at 17:46</span>'));
check("the bar: a progressbar with its segments, point and tracker",
      html.includes('role="progressbar"') && (html.match(/class="lv-seg lv-tone-warn"/g) || []).length === 2 && html.includes("lv-point") && html.includes('class="lv-tracker"'));
check("Earlier is disabled on the first choice, Next is not; the position 1/2",
      /data-live-act="prev" disabled/.test(html) && !/data-live-act="next" disabled/.test(html) && html.includes(">1/2<") && html.includes("option 1 of 2"));
check("on the last choice Next is disabled", /data-live-act="next" disabled/.test(L.cardHtml(rail, { key: "406", now })));
check("Stop is labelled with what it stops", html.includes('aria-label="Stop: Departs 17:46 (+4)"'));
check("a path to open: the title is a button", html.includes('<button type="button" class="lv-open" data-live-open>Departs 17:46 (+4)</button>'));
check("an https address: the title is a link to a new tab",
      L.cardHtml({ id: "a:b", title: "T", tone: "neutral", open: "https://example.com" }, { now }).includes('<a class="lv-open" href="https://example.com" target="_blank" rel="noopener">'));
check("nowhere to go: plain title, not openable", !L.cardHtml({ id: "a:b", title: "T", tone: "neutral" }, { now }).includes("can-open"));
const evil = L.cardHtml({ id: "x:<y>", title: "<img src=x onerror=alert(1)>", text: "a & b", tone: "bad", chip: "\"3", icon: "bad\"icon",
                          actions: [{ id: "go\"", label: "<b>Go</b>", kind: "server" }] }, { now });
check("everything from the payload is escaped", !evil.includes("<img") && !evil.includes("<b>") && evil.includes("&lt;img") && evil.includes("a &amp; b")
      && evil.includes('data-live-action="go&quot;"') && evil.includes('data-lucide="activity"'));
const busy = L.cardHtml({ id: "a:b", title: "T", tone: "neutral", actions: [{ id: "snooze", label: "Snooze", kind: "server" }] }, { now, busy: new Set(["snooze"]) });
check("a server action is a pill; while it runs it is disabled and busy", busy.includes('class="dn-pill small" data-live-act="server" data-live-action="snooze" disabled aria-busy="true"'));
const doneHtml = L.cardHtml(agent, { now });
check("the ended card: muted class, Done, the final's text, a labelled dismiss, no timer or bar",
      doneHtml.includes("lv-card lv-tone-good ended") && doneHtml.includes(">Done<") && doneHtml.includes("All 42 tests pass")
      && doneHtml.includes('aria-label="Dismiss Done · Fix the parser"') && !doneHtml.includes("lv-timer") && !doneHtml.includes("lv-bar"));
check("the chip shows on a running one", L.cardHtml({ ...agent, ended: false, final: undefined }, { now }).includes('<span class="lv-chip">3/7</span>'));
check("the signature changes with the rev, the pick and busy actions, not the clock",
      L.signature(rail, "", null) === L.signature(rail, "", null) && L.signature(rail, "406", null) !== L.signature(rail, "", null)
      && L.signature({ ...rail, rev: 5 }, "", null) !== L.signature(rail, "", null) && L.signature(rail, "", new Set(["x"])) !== L.signature(rail, "", null));

// --- The shell wiring ------------------------------------------------------------------------------
const index = fs.readFileSync(path.join(SERVER, "static/index.html"), "utf8");
const sw = fs.readFileSync(path.join(SERVER, "static/sw.js"), "utf8");
const nowJs = fs.readFileSync(path.join(SERVER, "static/app-now.js"), "utf8");
const liveJs = fs.readFileSync(path.join(SERVER, "static/app-live.js"), "utf8");
check("index.html loads the live files after Now and before app.js; sw.js caches them",
      index.indexOf("app-now.js") < index.indexOf("app-live-core.js") && index.indexOf("app-live-core.js") < index.indexOf("app-live.js")
      && index.indexOf("app-live.js") < index.indexOf("/static/app.js") && sw.includes('"/static/app-live-core.js"') && sw.includes('"/static/app-live.js"'));
check("Now hosts the strip and starts / stops it", nowJs.includes('id="now-live"') && nowJs.includes("liveShow()") && nowJs.includes("liveHide()") && nowJs.includes("liveDraw()"));
check("the strip uses the live API's routes", ["/api/live", '"/select"', '"/act"', '"DELETE"'].every(s => liveJs.includes(s)));

console.log(`\n${failures.length ? `${failures.length} check(s) failed` : "All checks passed"} (${pass} passed)`);
process.exit(failures.length ? 1 : 0);
