/**
 * Contract test for the web Money panel's pure part (static/money-core.js, DanaMoneyCore).
 *
 * Loads it without a browser and feeds it the *actual payloads* the API answered, captured in
 * tests/fixtures/money_web.json by `WRITE_WEB_FIXTURE=1 bin/test test_money_page` (a synthetic two-user
 * dataset, today 18 Nov 2026): money formatting (negative, money in, refunds, foreign, compact axis values,
 * bidi isolation), dates and months, day grouping (the month's list adds up to the overview's spent and
 * income), badges, kinds and tones, the charts' geometry (everyday pace with its typical band, 12-month bars
 * from months and from trends, cash flow in / out, bank balance and forecast, sparklines), the dashboard's
 * words (tiles, vs typical, coming up and card bill parts, recurring charges by cadence — a bimonthly one
 * never "/ month" —, installment plans, the forecast note), filters and the view ↔ the API's query string
 * and the #money hash, the category picker, "counts as" choices and the one-off / yearly marks, source
 * states, undo and error words; the Investments view from tests/fixtures/money_portfolio_web.json
 * (`WRITE_WEB_FIXTURE=1 bin/test test_money_il_portfolio`): the tab only with an investment account, tiles,
 * the value chart, allocation, holdings and activity words; the Meal card view from tests/fixtures/money_meals_web.json
 * (test_money_il_meals.py: tiles, pace, cycle bars, the tab); the Save view from tests/fixtures/money_savings_web.json
 * (`WRITE_WEB_FIXTURE=1 bin/test test_money_savings`): the tab and its hash, sections (the user's choices always
 * listed), tiles, chips, the actions of each status and their PATCH bodies, the toasts, price check states and
 * offer rows, check answers, outcomes, target progress, subscription lines, links and the sparkline.
 *
 * Run: bin/test test_web_money [path-to-fixture]
 */

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const SERVER = path.join(here, "..", "..", "..");  // the server folder
const FIXTURE = process.argv[2] || path.join(SERVER, "tests", "fixtures", "money_web.json");
const F = JSON.parse(fs.readFileSync(FIXTURE, "utf8"));
const src = fs.readFileSync(path.join(SERVER, "static/money-core.js"), "utf8");

const sandbox = { console, Set, Map, JSON, Object, Array, String, Number, Boolean, RegExp, Math, Date, Error, encodeURIComponent, decodeURIComponent, isFinite };
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "static/money-core.js" });
const C = sandbox.DanaMoneyCore;

let pass = 0;
const failures = [];
function check(name, cond, detail = "") {
    if (cond) { pass++; console.log(`[PASS] ${name}`); }
    else { failures.push(name); console.log(`[FAIL] ${name}${detail !== "" ? " — " + String(detail).slice(0, 500) : ""}`); }
}
const plain = (o) => JSON.parse(JSON.stringify(o));  // values made in the sandbox, compared here
const same = (a, b) => JSON.stringify(plain(a)) === JSON.stringify(b);
const body = (k) => F[k].body;
const params = (req) => Object.fromEntries(new URL("http://x" + req.split(" ")[1]).searchParams);

const ov = body("overview"), past = body("overview_past"), pace = body("pace"), months = body("months");
const trends = body("trends"), trendsPast = body("trends_past"), cf = body("cashflow"), bal = body("balance"), rec = body("recurring");
const taxonomy = body("categories").taxonomy;
const monthList = C.mergeTxns(body("transactions").transactions, body("transactions_next").transactions);
const byMerchant = (m) => monthList.filter((t) => t.merchant === m);
const NOW = Date.parse("2026-11-18T07:01:00Z");
const M = C.MINUS;

// --- money ------------------------------------------------------------------------------------------
check("money: money out with a real minus sign, thousands and agorot", C.money(-150000) === `${M}₪1,500.00` && C.money(-2500) === `${M}₪25.00`);
check("money: money in plain or with +, a refund as + too", C.money(1500000) === "₪15,000.00" && C.money(1500000, { sign: "plus" }) === "+₪15,000.00"
      && C.money(5000, { sign: "plus" }) === "+₪50.00");
check("money: zero has no sign; 'none' is the absolute amount", C.money(0, { sign: "plus" }) === "₪0.00" && C.money(-70900, { sign: "none" }) === "₪709.00");
check("money: whole shekels rounded, '.00' dropped with cents:auto", C.money(477684, { cents: false }) === "₪4,777" && C.money(10000, { cents: "auto" }) === "₪100"
      && C.money(1594, { cents: "auto" }) === "₪15.94" && C.money(123456789) === "₪1,234,567.89");
check("money: plain text isolated left-to-right (LRI … PDI) for use beside Hebrew", C.moneyText(-2500) === `${C.LRI}${M}₪25.00${C.PDI}`);
check("money: HTML is a left-to-right bdi with tabular figures", C.moneyHtml(-2500) === `<bdi dir="ltr" class="mny-amt">${M}₪25.00</bdi>`
      && C.moneyHtml(100, {}, "x").includes('class="mny-amt x"'));
check("compact: axis values ₪0, ₪850, ₪4.8k, ₪20k, ₪1.5M", C.compact(0) === "₪0" && C.compact(85000) === "₪850" && C.compact(480000) === "₪4.8k"
      && C.compact(2000000) === "₪20k" && C.compact(150000000) === "₪1.5M" && C.compact(500000) === "₪5k");
const foreignRow = monthList.find((t) => t.badges.some((b) => b.type === "foreign"));
const fb = foreignRow.badges.find((b) => b.type === "foreign");
check("foreign: the fixture's USD payment reads $5.00; € and an unknown currency", C.foreignText(fb.amount, fb.currency) === "$5.00"
      && C.foreignText(-3500, "EUR") === "€35.00" && C.foreignText(500, "CHF") === "5.00 CHF" && C.foreignText(123456, "GBP") === "£1,234.56");
check("isolate: text of either direction in FSI … PDI", C.isolate("קפה קפה") === `${C.FSI}קפה קפה${C.PDI}`);

// --- dates and months --------------------------------------------------------------------------------
check("day labels: Today, Yesterday, a weekday, another year", C.dayLabel("2026-11-18", "2026-11-18") === "Today" && C.dayLabel("2026-11-17", "2026-11-18") === "Yesterday"
      && C.dayLabel("2026-11-16", "2026-11-18") === "Mon 16 Nov" && C.dayLabel("2025-12-01", "2026-11-18") === "Mon 1 Dec 2025");
check("dates: short and long", C.shortDate("2026-12-10", "2026-11-18") === "10 Dec" && C.shortDate("2025-12-01", "2026-11-18") === "1 Dec 2025"
      && C.longDate("2026-11-17") === "Tue 17 Nov 2026");
check("time: the local time as the server wrote it, whatever the browser's zone", C.timeOf("2026-11-18T08:10:00+02:00") === "08:10" && C.timeOf(null) === "");
check("ago / until / countdown", C.ago("2026-11-18T05:01:00+00:00", NOW) === "2 h ago" && C.ago("2026-11-18T07:00:30+00:00", NOW) === "just now"
      && C.ago("2026-11-17T05:00:00+00:00", NOW) === "yesterday" && C.ago("2026-11-14T05:00:00+00:00", NOW) === "4 days ago"
      && C.until("2026-11-18T17:00:00+00:00", NOW) === "in 10 h" && C.until("2026-11-18T07:20:00+00:00", NOW) === "in 19 min"
      && C.countdown("2026-11-18T07:04:41Z", NOW) === "3:41" && C.countdown("2026-11-18T06:00:00Z", NOW) === "0:00");
check("wait words", C.waitText(45) === "45 s" && C.waitText(420) === "7 min" && C.waitText(61) === "2 min");
check("months: shift across years, labels, bounds (February)", C.shiftMonth("2026-01", -1) === "2025-12" && C.shiftMonth("2026-12", 1) === "2027-01"
      && C.shiftMonth("2026-11", -12) === "2025-11" && C.monthLabel("2026-11") === "November 2026" && C.monthLabel("2026-11", { short: true }) === "Nov 2026"
      && C.monthLabel("2026-10", { short: true, year: false }) === "Oct" && same(C.monthBounds("2026-02"), { start: "2026-02-01", end: "2026-02-28" })
      && C.isMonth("2026-13") === false && C.isMonth(ov.month));

// --- accounts ----------------------------------------------------------------------------------------
check("account names: a card's last 4, the user's own label kept, a bank's last 3 without 'Bank'",
      same(ov.cards.map((a) => C.accountName({ ...a, kind: "card" })), ["Shared card ••4580", "Cal ••8763"])
      && C.accountName({ ...ov.banks[0], kind: "bank" }) === "Hapoalim ••901" && C.accountName(null) === "");

// --- the list: days, tones, badges -------------------------------------------------------------------
check("pages merge without repeats (a refetch may overlap)", monthList.length === 21
      && C.mergeTxns(monthList, body("transactions_next").transactions).length === 21);
const days = C.groupByDay(monthList);
check("days: one group per purchase date, newest first, rows in the server's order",
      days.length === new Set(monthList.map((t) => t.purchase_date)).size && days[0].date === "2026-11-18"
      && days.every((d, i) => i === 0 || d.date < days[i - 1].date) && same(days.flatMap((d) => d.items.map((t) => t.id)), monthList.map((t) => t.id)));
const day = (d) => days.find((x) => x.date === d);
check("days: a day's spending counts its spending rows, pending included (17 Nov)", day("2026-11-17").spent === 150000 + 20000 + 20000 + 4500);
check("days: a refund alone makes the day negative (money back)", day("2026-11-14").spent === -5000);
check("days: unmatched rows, card bills, own transfers and a bank line with no flow count nothing",
      day("2026-11-15").spent === 0 && day("2026-11-02").spent === 0 && day("2026-11-04").spent === 0 && day("2026-11-11").spent === 0);
check("days: income on its day (the salary), apart from spending", day("2026-11-10").income === 1500000 && day("2026-11-10").spent === 50000);
check("days: the whole month's list adds up to the overview's spent and income",
      days.reduce((a, d) => a + d.spent, 0) === ov.spent.total && days.reduce((a, d) => a + d.income, 0) === ov.income.total,
      `${days.reduce((a, d) => a + d.spent, 0)} vs ${ov.spent.total}`);
const uncat = monthList.filter(C.needsCategory);
check("uncategorized filter: card bills and own transfers take no category, so they are not waiting for one",
      same(uncat.map((t) => t.merchant), ["משיכת מזומן", "חנות כלבו"].length === 2 ? uncat.map((t) => t.merchant) : [])
      && !uncat.some((t) => ["card_bill", "own_transfer"].includes(t.flow)) && uncat.length === 3
      && uncat.filter((t) => t.spending).length === ov.uncategorized);
const tone = (m) => C.amountTone(byMerchant(m)[0]);
check("tones: out, in (salary, refund), muted (card bill, own transfer, unmatched, a bank line with no flow)",
      tone("סופר פארם") === "out" && tone("משכורת אופק") === "in" && C.amountTone(monthList.find((t) => t.flow === "refund")) === "in"
      && tone("כאל") === "muted" && tone("העברה לחיסכון") === "muted" && tone("משיכת מזומן") === "muted"
      && C.amountTone(monthList.find((t) => t.status === "unmatched")) === "muted");
const badgeOf = (t) => plain(C.badges(t, "Cal")).map((b) => [b.type, b.text]);
check("badges: provisional waits for the card company", same(badgeOf(byMerchant("קפה קפה")[0]), [["provisional", "Waiting for Cal"]]));
check("badges: pending, unmatched", same(badgeOf(monthList.find((t) => t.status === "pending")), [["pending", "Pending"]])
      && same(badgeOf(monthList.find((t) => t.status === "unmatched")), [["unmatched", "Not charged?"]]));
check("badges: installment n/total, foreign amount, refund", same(badgeOf(byMerchant("ביטוח רכב ישיר")[0]), [["installment", "1/4"]])
      && same(badgeOf(foreignRow), [["foreign", "$5.00"]]) && same(badgeOf(monthList.find((t) => t.flow === "refund")), [["refund", "Refund"]]));
check("badges: without a company name they still read", C.badges(byMerchant("קפה קפה")[0], "")[0].text === "Waiting for the card company");
check("flow chips: card bill and own transfer instead of a category", C.flowChip(byMerchant("כאל")[0]) === "Card bill"
      && C.flowChip(byMerchant("העברה לחיסכון")[0]) === "Between your accounts" && C.flowChip(byMerchant("סופר פארם")[0]) === null);
check("explanations: provisional (time, company), unmatched, card bill, own transfer, a plain purchase has none",
      /at 08:10/.test(C.statusExplain(body("transaction_provisional"), "Cal")) && /when Cal lists it/.test(C.statusExplain(body("transaction_provisional"), "Cal"))
      && /isn't counted/.test(C.statusExplain(monthList.find((t) => t.status === "unmatched"), "Cal"))
      && /counted twice/.test(C.statusExplain(byMerchant("כאל")[0])) && /own accounts/.test(C.statusExplain(byMerchant("העברה לחיסכון")[0]))
      && C.statusExplain(body("transaction_foreign"), "Cal") === "");
check("category sources in words", C.categorySourceLabel("user") === "set by you" && C.categorySourceLabel("model") === "Dana's guess" && C.categorySourceLabel(null) === "");

// --- everyday pace -------------------------------------------------------------------------------
const g = C.paceGeometry(pace, { width: 640, height: 200, left: 52, right: 14, top: 14, bottom: 24 });
const pts = (d) => (d.match(/[ML]/g) || []).length;
check("pace: everyday only — the fixture's month is 30 days, this month stops at today (18 points), typical runs to the end",
      g.n === 30 && pace.days === 30 && pts(g.lines.this) === 18 && pts(g.lines.typical) === 30 && g.lines.this.startsWith("M52,"));
check("pace: one y scale with a round top above this month and the band's high", g.max === 500000 && same(g.ticks.map((t) => t.text), ["₪0", "₪2.5k", "₪5k"])
      && g.ticks[0].y === 176 && g.ticks[2].y === 14 && Math.max(...pace.high) < g.max && Math.max(...pace.this_month.filter((v) => v != null)) < g.max);
check("pace: today on day 18, the end dot at the overview's everyday total", g.today.day === 18 && Math.abs(g.today.x - (52 + (17 / 29) * 574)) < 0.11
      && g.end.i === 17 && g.end.v === ov.everyday.total && g.end.x === g.today.x);
check("pace: the usual range is a closed band (high forward, low back), the area under this month closed on the baseline",
      /Z$/.test(g.band) && pts(g.band) === 60 && /Z$/.test(g.area) && g.area.includes(`L${g.today.x},176`));
check("pace: hover picks the nearest day and reads this month, typical and the range", C.paceIndexAt(g, g.today.x + 3) === 17 && C.paceIndexAt(g, -50) === 0
      && C.paceIndexAt(g, 9999) === 29 && same(C.paceAt(pace, 17), { day: 18, this: 401794, typical: 0, low: 0, high: 108000 })
      && C.paceAt(pace, 20).this === null && C.paceAt(pace, 20).high === 108750 && C.paceAt(pace, 29).high === 116250);
const gp = C.paceGeometry({ ...pace, today: null, this_month: pace.high.slice() }, { width: 640, height: 200 });
check("pace: a past month draws the whole month and no today marker", gp.today === null && pts(gp.lines.this) === 30);
check("pace: x labels at 1, 5, 10 … and the last day, never crowding it", same(g.xLabels.map((l) => l.text), ["1", "5", "10", "15", "20", "25", "30"]));
const gz = C.paceGeometry({ days: 31, today: 1, this_month: [0, ...Array(30).fill(null)], typical: null, low: null, high: null, months: 0 }, {});
check("pace: no whole month before yet — a scale, no typical line, no band", gz.max === 100 && gz.lines.typical === "" && gz.band === "" && gz.area === "" && gz.n === 31);

// --- 12 months ---------------------------------------------------------------------------------------
const mg = C.monthsGeometry(months, { width: 640, height: 180, left: 46, right: 6, top: 8, bottom: 22 });
const nov = mg.bars[mg.bars.length - 1];
check("months: twelve bars, oldest first, the same max as the busiest month rounds to", mg.bars.length === 12 && mg.bars[0].month === "2025-12"
      && nov.month === "2026-11" && mg.max === 1000000);
check("months: segments stacked in the fixed group order; People has its own; finance folds into Other",
      same(nov.segments.map((s) => s.key), ["food", "transport", "bills", "health", "family", "people", "other", "uncategorized"])
      && nov.segments.find((s) => s.key === "people").value + nov.segments.find((s) => s.key === "other").value === 25000 + 1200,
      nov.segments.map((s) => [s.key, s.value]));
check("months: each bar's segments add up to its total", mg.bars.every((b) => b.segments.reduce((a, s) => a + s.value, 0) === b.total));
const seen = nov.segments.filter((s) => s.h >= 1), topSeg = nov.segments.find((s) => s.top);
check("months: segments sit on each other with a 2px gap; the highest visible one has the rounded top",
      nov.segments.every((s, i) => i === 0 ? Math.abs(s.y + s.h - mg.base) < 0.11 : s.h === 0 || Math.abs(s.y + s.h + 2 - nov.segments[i - 1].y) < 0.21)
      && nov.segments.filter((s) => s.top).length === 1 && topSeg === seen[seen.length - 1]
      && /Q/.test(C.barPath(topSeg)) && !/Q/.test(C.barPath(nov.segments[0])));
check("months: an empty month has no segments; heights follow the totals",
      mg.bars[1].segments.length === 0 && Math.abs((mg.base - nov.segments[nov.segments.length - 1].y) - (nov.total / 1000000) * mg.plotH) < 0.5);
check("months: the legend lists only the series present, in order, with the payload's labels",
      same(mg.legend.map((l) => l.key), ["food", "housing", "transport", "bills", "shopping", "health", "family", "people", "other", "uncategorized"])
      && mg.legend[0].label === "Food" && mg.legend.find((l) => l.key === "other").label === "Other");
check("months: x labels every other month, ending with the current one", mg.xLabels[11].show && mg.xLabels[11].text === "Nov" && !mg.xLabels[10].show
      && mg.xLabels.filter((l) => l.show).length === 6);
check("months: colour follows the group (a group's series key never depends on rank)", C.seriesOf("food") === "food" && C.seriesOf("people") === "people"
      && C.seriesOf(null) === "uncategorized" && C.seriesOf("finance") === "other");
const tm = C.trendsAsMonths(trends);
check("everyday bars: trends' groups as 12 months ending with the trend's month (keys from its labels), the same geometry",
      tm.months.length === 12 && tm.months[11].month === "2026-11" && tm.months[0].month === "2025-12" && tm.months[0].label === "Dec 2025"
      && C.trendsAsMonths(trendsPast).months[11].month === "2026-10" && same(tm.groups.map((x) => x.key), trends.groups.map((x) => x.key))
      && C.monthsGeometry(tm, {}).bars.length === 12);
check("everyday bars: each month's groups add up to its total; one-offs are not in trends' groups",
      tm.months.every((m) => Object.values(m.groups).reduce((a, v) => a + v, 0) === m.total)
      && tm.months[11].total === trends.groups.reduce((a, x) => a + x.months[11], 0) && tm.months[11].groups.housing === undefined
      && tm.months[10].groups.housing === 450000);
const sp = C.sparkBars([0, 10, 20, 0], { width: 40, height: 20, gap: 0 });
check("sparkline: bars scaled to the row's own max, nothing drawn for a zero, the last one marked",
      same(sp.bars.map((x) => [x.x, x.h]), [[0, 0], [10, 10], [20, 20], [30, 0]]) && sp.bars[3].last && !sp.bars[2].last
      && C.sparkBars([0, 0], {}).max === 0 && C.sparkBars([1, 1000], { height: 20 }).bars[0].h === 1);
check("ticks: round steps covering the range, zero kept as a tick", same(C.niceTicks(-2666133, 3768616, 4), [-4000000, -2000000, 0, 2000000, 4000000])
      && same(C.niceTicks(5, 7, 4), [5, 5.5, 6, 6.5, 7]) && same(C.niceTicks(0, 0), [0, 25, 50, 75, 100]));
check("bars: a downward stack's end has rounded bottom corners, an upward one's top", /Q/.test(C.barPath({ x: 0, y: 10, w: 10, h: 20, bottom: true }))
      && C.barPath({ x: 0, y: 10, w: 10, h: 20, bottom: true }).startsWith("M0,10 V26") && C.barPath({ x: 0, y: 10, w: 10, h: 20 }) === "M0,30 V10 H10 V30 Z");

// --- cash flow --------------------------------------------------------------------------------------
const cg = C.cashflowGeometry(cf, { width: 640, height: 220 });
const nov11 = cg.bars[11];
check("cash flow: one signed scale — income up to a round ₪20k, spending down to ₪10k, zero between", cg.up === 2000000 && cg.down === 1000000
      && cg.zero === 135.3 && same(cg.ticks.map((t) => t.text), ["₪20k", "₪10k", "₪0", `${M}₪5k`, `${M}₪10k`]) && cg.ticks.find((t) => t.zero).y === cg.zero);
check("cash flow: income stands on the zero line, its top rounded", nov11.income.value === 1500000 && nov11.income.top && Math.abs(nov11.income.y + nov11.income.h + 1 - cg.zero) < 0.11
      && Math.abs((cg.zero - nov11.income.y) - 1500000 * cg.unit) < 0.11);
check("cash flow: fixed, yearly, everyday stacked down in that order with 2px gaps; the lowest has the rounded end",
      same(nov11.segments.map((x) => x.key), ["fixed", "yearly", "everyday"]) && nov11.segments[0].y === cg.zero + 1
      && nov11.segments.filter((x) => x.bottom).length === 1 && nov11.segments[2].bottom
      && nov11.segments.reduce((a, x) => a + x.value, 0) === cf.months[11].spent);
check("cash flow: the net is a point per month from the first one with money in or out, joined", nov11.net.v === cf.months[11].net
      && Math.abs(nov11.net.cy - (cg.zero - cf.months[11].net * cg.unit)) < 0.11 && pts(cg.netPath) === 12 && cg.bars[0].net.show
      && cg.bars[3].net.cy === cg.zero && C.cashflowGeometry({ months: [{ month: "2026-01", income: 0, spent: 0, net: 0 }, { ...cf.months[11] }] }, {}).bars[0].net.show === false);
check("cash flow: the month in progress is marked, the legend lists the series present (net last)", nov11.complete === false && cg.bars[10].complete === true
      && same(cg.legend.map((l) => l.key), ["income", "fixed", "yearly", "everyday", "net"]) && cg.legend[0].label === "Income");
const aside = C.cashflowGeometry({ months: [{ month: "2026-10", label: "Oct 2026", complete: true, income: 1000000, fixed: 200000, yearly: 0, everyday: 300000, one_off: 100000, spent: 600000, set_aside: 250000, net: 400000 }] }, {});
check("cash flow: money set aside is a bar of its own beside the spending, from zero down, not stacked", aside.bars[0].setAside.value === 250000
      && aside.bars[0].setAside.x > aside.bars[0].x + aside.bars[0].w && aside.bars[0].setAside.y === aside.zero + 1
      && same(aside.bars[0].segments.map((x) => x.key), ["fixed", "everyday", "one_off"]) && aside.legend.some((l) => l.key === "set_aside"));
check("cash flow: the totals line", C.cashflowTotalsText(cf) === "12 months: earned ₪60,519, spent ₪21,777, set aside ₪0, net ₪38,742");

// --- bank balance and forecast ------------------------------------------------------------------------
const bgeo = C.balanceGeometry(bal, { width: 640, height: 220 });
check("balance: history solid, forecast from today dashed, its range a band; zero in view (the history went below it)",
      bgeo.history.startsWith("M56,") && pts(bgeo.history) === bal.history.length && pts(bgeo.forecast) === bal.forecast.series.length && /Z$/.test(bgeo.band)
      && bgeo.lo === -4000000 && bgeo.hi === 4000000 && bgeo.zeroY !== null && bgeo.ticks.some((t) => t.zero));
check("balance: the time axis runs from the first history day to the forecast's end; today and the low point placed on it",
      bgeo.today.date === "2026-11-18" && bgeo.low.date === "2026-11-25" && bgeo.low.balance === 681867 && bgeo.low.x > bgeo.today.x
      && Math.abs(bgeo.x(bal.forecast.end) - (640 - 12)) < 0.11 && same(bgeo.xLabels.map((l) => l.text), ["Sep", "Oct", "Nov", "Dec", "Jan", "Feb"]));
check("balance: hover points — one a day, the forecast's range on forecast days, history kept where they meet",
      bgeo.points.length === new Set([...bal.history, ...bal.forecast.series].map((x) => x.date)).size
      && bgeo.points.find((x) => x.date === "2026-11-18").forecast === false && bgeo.points.find((x) => x.date === "2027-02-16").high === 3768616
      && bgeo.points[C.pointIndexAt(bgeo, bgeo.low.x)].date === "2026-11-25");
const b30 = C.balanceGeometry(bal, { width: 640, height: 220, historyDays: 30 });
check("balance: the past as long as the horizon (historyDays)", b30.points[0].date === "2026-10-19" && b30.points.length === 31 + 90);
const spark = C.balanceGeometry({ history: [], forecast: { ...bal.forecast, series: C.upcoming(bal.forecast, 45).series } }, { width: 300, height: 60, includeZero: false, low: bal.forecast.soon_low });
check("balance: the coming-up sparkline — 46 days of forecast, no ticks, scaled to itself, the 45-day low marked",
      pts(spark.forecast) === 46 && spark.ticks.length === 0 && spark.history === "" && spark.lo === 681867 && spark.low.date === bal.forecast.soon_low.date
      && C.balanceGeometry({ history: [], forecast: null }, {}) === null);
check("forecast note: what it leaves out and includes", C.forecastNote(bal.forecast) === "Forecast leaves out one-offs (none in the last 12 months), includes yearly charges and installment plans."
      && C.forecastNote({ left_out_total: -1234500 }).startsWith("Forecast leaves out one-offs (₪12,345 in the last 12 months)"));

// --- coming up ---------------------------------------------------------------------------------------
const up = C.upcoming(bal.forecast, 45);
check("coming up: the forecast's events in the next 45 days, in order", up.end === "2027-01-02" && up.events.length === 8
      && up.events.every((e, i) => e.date <= up.end && (i === 0 || e.date >= up.events[i - 1].date)) && !up.events.some((e) => e.date === "2027-01-10"));
const calBill = up.events.find((e) => e.kind === "card_bill" && e.estimated);
check("coming up: an estimated card bill — its parts in words (empty ones left out), its items, its range, the balance after the day",
      same(calBill.parts.map((x) => [x.key, x.amount]), [["known", 345081], ["fixed", 4990], ["yearly", 6000], ["everyday", 30530]])
      && calBill.parts[0].label === "Already on the card" && calBill.items.length === 2 && calBill.range === "₪3,561 – ₪4,906" && calBill.after === 1755266);
check("coming up: a known bill has no range; income and fixed rows have no parts", up.events[2].range === "" && same(up.events[2].parts.map((x) => x.key), ["known"])
      && up.events[0].kind === "income" && up.events[0].parts.length === 0 && up.events[0].after === 801867 && C.upcoming(null).events.length === 0);

// --- tiles and spending words ----------------------------------------------------------------------------
check("tile: net so far, the share of income kept, earned and spent", same(plain(C.netTile(ov, cf)), { label: "Net so far", value: 1022316, tone: "in",
      rate: "Kept 68% of income", sub: "Earned ₪15,000 · spent ₪4,777", typical: "" }) && C.netTile(past, cf).label === "Net");
check("tile: net — more out than in, no income, the typical month when there is one",
      C.netTile({ ...past, net: -50000, income_month: 100000 }, null).rate === "₪500 more out than in" && C.netTile({ ...ov, income_month: 0, net: -10 }, null).rate === "No income yet this month"
      && C.netTile(ov, { typical: { income: 1500000, net: 900000 } }).typical === "typical ₪9,000");
check("tile: everyday spending — on track while the month runs, typical when there is one, which way it leans",
      same(plain(C.everydayTile(ov.everyday, true)), { value: 401794, track: "On track for ₪4,018", typical: "", dir: "" })
      && C.everydayTile(past.everyday, false).track === "" && same(plain(C.everydayTile({ total: 100000, on_track: 300000, typical: 200000 }, true)),
        { value: 100000, track: "On track for ₪3,000", typical: "typical ₪2,000", dir: "up" }));
check("tile: balance ahead — now, the 45-day low and the forecast's end; above it, or none for a past month",
      same(plain(C.balanceTile(ov.forecast, "2026-11-18")), { value: 1234567, low: "Lowest in the next 45 days: ₪6,819 on 25 Nov", end: "₪66,346 by 17 May 2027", tone: "" })
      && C.balanceTile({ ...ov.forecast, soon_low: { balance: 1234567, date: "2026-11-18", days: 45 } }, "2026-11-18").low === "Stays above ₪12,346 for the next 45 days"
      && C.balanceTile({ ...ov.forecast, soon_low: { balance: -5000, date: "2026-12-01", days: 45 } }, "2026-11-18").tone === "bad" && C.balanceTile(past.forecast) === null);
check("tile: fixed costs a month, their share of income (typical first), the yearly part", same(plain(C.fixedTile(ov)), { value: 478623,
      share: "32% of this month's income", yearly: "incl. ₪236 a month for yearly charges" }) && C.fixedTile({ ...ov, income_typical: 2000000 }).share === "24% of a typical month's income");
const housing = trends.groups.find((x) => x.key === "housing");
check("vs typical: on track against typical while the month runs; usually nothing; about typical; below",
      same(plain(C.vsTypical(housing, true)), { dir: "up", text: "₪2,250 above typical" }) && C.vsTypical(trends.groups.find((x) => x.key === "food"), true).dir === "new"
      && C.vsTypical({ total: 10000, typical: 10200 }, false).dir === "same" && C.vsTypical({ total: 5000, typical: 10000 }, false).text === "₪50 below typical"
      && C.vsTypical(trends.groups.find((x) => x.key === "shopping"), true).dir === "none");
check("isolate Hebrew: each Hebrew run in a server sentence is isolated, the amounts beside them left alone",
      C.isolateHebrew(body("insights").items[0].detail).startsWith(`Next: ${C.FSI}קצבת ילדים${C.PDI} ₪173.00 on 20 Nov; ${C.FSI}משכנתא לאומי${C.PDI} ₪4,500.00`)
      && C.isolateHebrew("רמי לוי - יבנה ₪3") === `${C.FSI}רמי לוי - יבנה${C.PDI} ₪3` && C.isolateHebrew("NETFLIX.COM up") === "NETFLIX.COM up");

// --- recurring charges by their cadence -------------------------------------------------------------------
const ca = (x) => plain(C.cadenceAmount(x));
check("cadence: a monthly charge is '/ month' with nothing more", same(ca(rec.items.find((x) => x.merchant === "NETFLIX.COM")), { amount: "₪49.90", per: "/ month", monthly: "" }));
check("cadence: a bimonthly charge is '/ 2 months' with its monthly equivalent — never '/ month'",
      same(ca({ amount: 11730, monthly_cost: 5865, cadence: "bimonthly" }), { amount: "₪117.30", per: "/ 2 months", monthly: "≈ ₪58.65 a month" })
      && C.cadenceLabel("bimonthly") === "Every 2 months" && C.perText("bimonthly") === "/ 2 months");
check("cadence: weekly '/ week', yearly '/ year' (the fixture's yearly item), each with its monthly equivalent",
      same(ca({ amount: 5000, monthly_cost: 21700, cadence: "weekly" }), { amount: "₪50.00", per: "/ week", monthly: "≈ ₪217.00 a month" })
      && same(ca(rec.items.find((x) => x.cadence === "yearly")), { amount: "₪60.00", per: "/ year", monthly: "≈ ₪5.00 a month" })
      && same(ca({ ...rec.yearly[0], cadence: "yearly" }), { amount: "₪2,836.00", per: "/ year", monthly: "≈ ₪236.33 a month" }) && C.cadenceLabel("yearly") === "Yearly");
const [carPlan, kspPlan] = bal.forecast.plans;
check("installment plans: payments left, what's left to pay, the next date", same(plain(C.planText(carPlan, "2026-11-18")), { left: "3 payments left of 4", rest: "₪2,127.00 to go", next: "next 10 Jan 2027" })
      && same(plain(C.planText(kspPlan, "2026-11-18")), { left: "1 payment left of 3", rest: "₪400.00 to go", next: "next 10 Dec" }));



// --- filters ↔ query string / hash --------------------------------------------------------------------
const fq = params(F.transactions_filtered.request);
check("query: the filtered request of the fixture, built from the panel's filters",
      same(params("GET /api/money/transactions?" + C.filtersToQuery({ category: fq.category, account: fq.account, min: fq.min }, fq.period)), fq));
const nq = params(F.transactions_next.request);
check("query: the next page's cursor and limit", same(params("GET /x?" + C.filtersToQuery({}, nq.period, { limit: 12, cursor: nq.cursor })), nq));
check("query: a search over all time sends no period", C.filtersToQuery({ q: "wolt", all: true }, "2026-11") === "q=wolt"
      && same(params("GET /x?" + C.filtersToQuery({ q: "wolt", all: true }, "2026-11")), params(F.transactions_search.request)));
check("query: Hebrew and spaces encoded, blanks left out", C.filtersToQuery({ q: " קפה קפה ", min: "", max: "250" }, "2026-11")
      === `period=2026-11&q=${encodeURIComponent("קפה קפה")}&max=250`);
const st = { month: "2026-10", view: "sources", tab: "overview", f: { q: "קפה", category: "food", account: "a1", min: "100", max: "", all: true } };
const h = C.stateToHash(st, "2026-11");
check("hash: the page's state round-trips", h.startsWith("#money?month=2026-10&view=accounts&q=") && same(C.stateFromHash(h),
      { month: "2026-10", view: "sources", tab: "overview", f: { q: "קפה", category: "food", account: "a1", min: "100", max: "", all: true } }));
check("hash: the current month, the Overview and no filters is just #money", C.stateToHash({ month: "2026-11", view: "page", tab: "overview", f: {} }, "2026-11") === "#money"
      && same(C.stateFromHash("#money"), { month: null, view: "page", tab: "overview", f: plain(C.EMPTY_FILTERS) }));
check("hash: the view is the path — #money/cashflow?month=2026-10, #money/spending?category=food — and round-trips",
      C.stateToHash({ month: "2026-10", view: "page", tab: "cashflow", f: {} }, "2026-11") === "#money/cashflow?month=2026-10"
      && C.stateToHash({ month: "2026-11", view: "page", tab: "spending", f: { category: "food" } }, "2026-11") === "#money/spending?category=food"
      && same(C.stateFromHash("#money/transactions?q=wolt&all=1"), { month: null, view: "page", tab: "transactions", f: { ...plain(C.EMPTY_FILTERS), q: "wolt", all: true } })
      && C.TABS.every((t) => C.stateFromHash(C.stateToHash({ month: "2026-09", view: "page", tab: t, f: {} }, "2026-11")).tab === t));
check("hash: another tab's hash is not ours; a bad month, amount or view is dropped", C.stateFromHash("#inbox") === null && C.stateFromHash("#moneyx") === null
      && C.stateFromHash("#money?month=2026-13&min=abc").month === null && C.stateFromHash("#money?month=2026-13&min=abc").f.min === ""
      && C.stateFromHash("#money/nope?month=2026-10").tab === "overview" && C.stateFromHash("#money/nope?month=2026-10").month === "2026-10"
      && same(C.TABS, ["overview", "spending", "cashflow", "save", "investments", "meals", "transactions"]) && C.TAB_LABELS.cashflow === "Cash flow");
const chips = C.filterChips({ q: "wolt", category: "food", account: ov.cards[1].id, min: "100", max: "500" },
                            { categoryLabel: (k) => C.categoryLabel(taxonomy, k), accountLabel: (id) => C.accountName({ ...ov.cards.find((a) => a.id === id), kind: "card" }) });
check("chips: the active filters in words", same(chips.map((c) => [c.key, c.label]), [["q", "“wolt”"], ["category", "Food"], ["account", "Cal ••8763"], ["amount", "₪100 – ₪500"]]));
check("chips: one-sided amounts, uncategorized", C.filterChips({ min: "100" })[0].label === "₪100 or more" && C.filterChips({ max: "49.9" })[0].label === "up to ₪49.90"
      && C.categoryLabel(taxonomy, "uncategorized") === "Uncategorized" && C.hasFilters({ all: true }) === false && C.hasFilters({ min: "1" }));
check("taxonomy: labels and groups of categories", C.categoryLabel(taxonomy, "restaurants") === "Restaurants & cafés" && C.categoryLabel(taxonomy, "food") === "Food"
      && C.groupOf(taxonomy, "restaurants") === "food" && C.groupOf(taxonomy, "housing") === "housing" && C.groupOf(taxonomy, "nope") === null);

// --- the category picker ---------------------------------------------------------------------------
const pk = (q, o) => plain(C.pickerGroups(taxonomy, q, o)).map((gr) => [gr.key, gr.categories.map((c) => c.key)]);
check("picker: a Hebrew word finds its category", same(pk("מסעדות"), [["food", ["restaurants"]]]));
check("picker: a group's name keeps all of its categories; English any case", same(pk("FOOD"), [["food", ["groceries", "restaurants", "delivery"]]])
      && same(pk("fuel"), [["transport", ["fuel"]]]));
check("picker: money out lists income last, money in first; nothing matching is empty",
      pk("")[0][0] === "food" && pk("")[pk("").length - 1][0] === "income" && pk("", { income: true })[0][0] === "income" && pk("zzz").length === 0
      && pk("").length === taxonomy.groups.length);

// --- words ---------------------------------------------------------------------------------------------
const [okSrc, failSrc] = ov.sources;
check("sources: ok says when it synced; failing says why and the last good sync",
      same(plain(C.sourceStatus(okSrc, NOW)), { tone: "ok", label: "Synced 2 h ago", detail: "" })
      && C.sourceStatus(failSrc, NOW).tone === "warn" && C.sourceStatus(failSrc, NOW).label === "Last sync failed"
      && C.sourceStatus(failSrc, NOW).detail === "bank worker not reachable · last good sync 14 h ago");
check("sources: code, login problem, syncing, first sync", C.sourceStatus({ state: "needs_code" }, NOW).label === "Waiting for the SMS code"
      && C.sourceStatus({ state: "needs_attention", state_reason: "Cal wants a new password" }, NOW).tone === "bad"
      && C.sourceStatus({ state: "needs_attention", state_reason: "Cal wants a new password", can_sync: false }, NOW).label === "Login needs fixing"
      && C.sourceStatus({ state: "needs_attention", error_type: "LOGIN_UNKNOWN", can_sync: true }, NOW).label === "Stopped: sign in again yourself"
      && C.sourceStatus({ state: "syncing", last_ok_at: null }, NOW).tone === "busy" && C.sourceStatus({ state: "new" }, NOW).tone === "busy");
check("header: the last good sync, and how many need attention", same(plain(C.headerStatus(ov.sources, NOW)), { tone: "warn", text: "Synced 2 h ago · 1 needs attention" })
      && C.headerStatus([okSrc], NOW).text === "Synced 2 h ago" && C.headerStatus([{ ...okSrc, state: "syncing" }], NOW).tone === "busy"
      && C.headerStatus([], NOW).text === "");
check("busy: a running sync or a pending code request polls faster", C.busy({ state: "syncing" }) && C.busy({ state: "ok", code_request: { id: "r" } }) && !C.busy(okSrc));
const netflix = rec.items.find((x) => x.price_change);
check("recurring: the price change in words (the year only when it differs)", C.priceChangeText(netflix.price_change, "2026-11-18") === "₪39.90 → ₪49.90 since Nov"
      && C.priceChangeText(netflix.price_change, "2027-02-01") === "₪39.90 → ₪49.90 since Nov 2026");

// --- kinds, counts as, one-off / yearly ---------------------------------------------------------------
check("kinds: the list's rows carry one; badges for one-off, yearly and fixed, none for everyday or what isn't spending",
      monthList.every((t) => "kind" in t) && C.kindBadge(byMerchant("ביטוח רכב ישיר")[0]).text === "Yearly" && C.kindBadge(byMerchant("NETFLIX.COM")[0]).type === "fixed"
      && C.kindBadge(body("patch_one_off").transaction).text === "One-off" && C.kindBadge(byMerchant("סופר פארם")[0]) === null
      && C.kindBadge(byMerchant("משכורת אופק")[0]) === null && monthList.filter((t) => t.kind).every((t) => t.spending));
const fc = plain(C.flowChoices(body("transaction_foreign")));
check("counts as: money out offers spending, a transfer to a person, money set aside, my own money, a loan payment, a fee, cash",
      same(fc.map((x) => x.flow), ["purchase", "transfer_out", "investment", "own_transfer", "loan", "fee", "cash"]) && fc[2].label === "Money set aside"
      && fc[3].label === "My own money (not spending)" && C.flowLabel("transfer_in") === "money received");
check("counts as: money in offers income, money received, a refund, my own money; the row's own flow is always there",
      same(plain(C.flowChoices({ amount: 100, flow: "income" })).map((x) => x.flow), ["income", "transfer_in", "refund", "own_transfer"])
      && plain(C.flowChoices({ amount: -100, flow: "refund" })).some((x) => x.flow === "refund")
      && plain(C.flowChoices({ amount: -100, flow: "other" })).length === 7 && Object.keys(C.FLOW_LABELS).length === 10);
check("marks: true / false / null as Yes / No / Automatic; what Dana makes of it on its own",
      C.markState(true) === "yes" && C.markState(false) === "no" && C.markState(null) === "auto" && C.markState(undefined) === "auto"
      && body("transaction_foreign").one_off === null && C.markState(body("patch_one_off").transaction.one_off) === "yes"
      && C.kindText("everyday") === "Dana counts it as everyday spending." && C.kindText(null) === "");
check("toasts: counts as, one-off and yearly marks (with how many charges changed)", C.flowText(body("patch_flow")) === `${C.FSI}ONLINE COURSE CO${C.PDI} now counts as money set aside`
      && C.markText(body("patch_one_off"), "one_off", true) === `${C.FSI}ONLINE COURSE CO${C.PDI}: a one-off`
      && C.markText({ ...body("patch_one_off"), changed: 3 }, "yearly", false) === `${C.FSI}ONLINE COURSE CO${C.PDI}: not a yearly charge (3 charges)`
      && C.markText(body("patch_one_off"), "one_off", null) === `${C.FSI}ONLINE COURSE CO${C.PDI}: Dana decides if it's a one-off`
      && body("patch_flow").transaction.kind === null && body("patch_flow").undo_id && body("patch_one_off").undo_id);
const pc = body("patch_category");
check("undo text: the category for the merchant, now and from now on", C.undoText(pc, pc.transaction.category_label, true)
      === `${C.FSI}קפה קפה${C.PDI} → Restaurants & cafés, now and from now on`);
check("undo text: several rows changed, only this one, a note", /\(3 charges\)$/.test(C.undoText({ ...pc, changed: 3 }, "Food", true))
      && C.undoText(pc, "Groceries", false) === `${C.FSI}קפה קפה${C.PDI} → Groceries` && C.undoText(body("patch_note"), null) === "Note saved"
      && C.undoText({ transaction: { note: "" } }, null) === "Note removed");
check("errors: the server's message, not connected, too soon, offline", C.errorText(400, body("error_invalid")) === body("error_invalid").message
      && /turned off/.test(C.errorText(403, body("error_not_connected"))) && C.errorText(429, { error: "too_soon", retry_after: 420 }) === "Synced a moment ago. Try again in 7 min."
      && C.errorText(409, { error: "needs_attention", reason: "Cal wants a new password" }) === "Cal wants a new password" && /Can't reach Dana/.test(C.errorText(0, null))
      && /4 to 8 digits/.test(C.errorText(400, { error: "bad_code" })) && C.errorText(500, null) === "Something went wrong (HTTP 500).");

// ---------- Investments (tests/fixtures/money_portfolio_web.json) ----------
const PF = JSON.parse(fs.readFileSync(path.join(SERVER, "tests", "fixtures", "money_portfolio_web.json"), "utf8"));
const pf = PF.portfolio.body;
const pov = PF.overview.body;
const PNOW = Date.parse("2026-11-18T12:00:00Z");
check("investments: the tab only with an investment account (or when asked for in the hash)",
      same(C.visibleTabs({}, "overview"), ["overview", "spending", "cashflow", "save", "transactions"])
      && same(C.visibleTabs({}, "investments"), ["overview", "spending", "cashflow", "save", "investments", "transactions"])
      && same(C.visibleTabs({ investments: pov.investments.length > 0 }, "overview"), ["overview", "spending", "cashflow", "save", "investments", "transactions"])
      && C.stateToHash({ month: "2026-11", view: "page", tab: "investments", f: {} }, "2026-11") === "#money/investments"
      && C.stateFromHash("#money/investments").tab === "investments" && C.TAB_LABELS.investments === "Investments");
check("investments: kinds' icons and words; the account's name ends in its last three digits",
      C.kindIcon("investment") === "chart-line" && C.kindIcon("bank") === "landmark" && C.kindIcon("card") === "credit-card" && C.kindIcon("other") === "credit-card"
      && C.kindWord("investment") === "Investments" && C.accountName({ ...pf.accounts[0], kind: "investment" }) === "Psagot Trade ••456"
      && C.accountName({ ...PF.sources.body.sources[0].accounts[0], company_name: "Psagot Trade" }) === "Psagot Trade ••456");
const tiles = plain(C.portfolioTiles(pf, PNOW));
check("investments: tiles — value, today since the morning, profit (the site's %), cash share",
      tiles.value.value === pf.total.value && tiles.today.value === pf.total.day_change && tiles.today.tone === "in"
      && tiles.today.sub === "+1.05% since the morning" && tiles.profit.sub === "+6.98% over what you paid" && tiles.profit.tone === "in"
      && tiles.cash.value === pf.total.cash && tiles.cash.sub === "2.5% of the portfolio" && /^Updated /.test(tiles.value.sub), JSON.stringify(tiles));
check("investments: tiles without figures from the site", (() => {
  const t = plain(C.portfolioTiles({ total: { value: 0, cash: null, day_change: null, profit: null, cost: null, as_of: null }, accounts: [] }, PNOW));
  return t.today.value === null && t.today.tone === "" && /No change/.test(t.today.sub) && t.profit.value === null && t.cash.value === 0 && t.cash.sub === "" && t.value.sub === "";
})());
check("investments: percentages, units and unit prices",
      C.pctText(6.98) === "+6.98%" && C.pctText(-3.62) === `${C.MINUS}3.62%` && C.pctText(0) === "0%" && C.pctText(0.001) === "0%" && C.pctText(12.345) === "+12.3%"
      && C.pctText(2.5, { sign: "none" }) === "2.5%" && C.pctText(2) === "+2%" && C.pctText(null) === "" && C.pctText(NaN) === ""
      && C.quantityText(120) === "120" && C.quantityText(15.5) === "15.5" && C.quantityText(1234.25) === "1,234.25" && C.quantityText(null) === ""
      && C.unitPrice(250, "ILS") === "₪250.00" && C.unitPrice(150, "USD") === "$150.00" && C.unitPrice(0.5, "ILS") === "₪0.50"
      && C.unitPrice(0.12345, "NIS") === "₪0.1235" && C.unitPrice(12.5, "CHF") === "12.50 CHF" && C.unitPrice(null, "ILS") === "");
const vg = C.valueGeometry(pf.history, { width: 600, height: 220 });
check("investments: the value chart — one point a day, ticks around the range (not from zero), the change since the first day",
      vg && vg.points.length === pf.history.length && vg.points[0].x === vg.L && vg.points[vg.points.length - 1].x === vg.W - vg.R
      && vg.lo > 0 && vg.lo <= Math.min(...pf.history.map((h) => h.value)) && vg.hi >= Math.max(...pf.history.map((h) => h.value))
      && vg.points.every((p) => p.y >= vg.T && p.y <= vg.H - vg.B) && vg.line.startsWith("M") && vg.area.endsWith("Z")
      && vg.change.value === pf.history[pf.history.length - 1].value - pf.history[0].value
      && C.valueChangeText(vg, "2026-11-18") === "Up ₪2,000 (+5.33%) since 9 Oct", JSON.stringify(vg && vg.change));
check("investments: the range keeps the last days only; one point draws no area; nothing → null",
      C.valueGeometry(pf.history, { days: 7 }).points.every((p) => p.date >= "2026-11-11")
      && C.valueGeometry([{ date: "2026-11-18", value: 100 }]).area === "" && C.valueChangeText(C.valueGeometry([{ date: "2026-11-18", value: 100 }]), "2026-11-18") === ""
      && C.valueGeometry([]) === null && C.valueGeometry(null) === null
      && C.valueChangeText(C.valueGeometry([{ date: "2026-11-01", value: 500 }, { date: "2026-11-18", value: 400 }]), "2026-11-18") === `Down ₪1 (${C.MINUS}20%) since 1 Nov`);
const fg = C.valueGeometry(pf.history, { width: 600, flows: pf.flows });
check("investments: money put in is marked on the line and left out of the change",
      pf.flows.length === 1 && fg.marks.length === 1 && fg.marks[0].date === pf.flows[0].date && fg.marks[0].flow === pf.flows[0].amount
      && fg.change.moved === pf.flows[0].amount
      && C.valueChangeText(fg, "2026-11-18") === "Down ₪18,000 since 9 Oct, besides ₪20,000 put in", C.valueChangeText(fg, "2026-11-18"));
check("investments: a flow on a day without a value lands on the next one; outside the range or on its first day it is not counted",
      (() => {
        const h = [{ date: "2026-11-01", value: 1000 }, { date: "2026-11-03", value: 1600 }, { date: "2026-11-04", value: 1500 }];
        const g = C.valueGeometry(h, { flows: [{ date: "2026-11-02", amount: 500 }, { date: "2026-11-01", amount: 9 }, { date: "2026-12-01", amount: 9 }] });
        const out = C.valueGeometry(h, { flows: [{ date: "2026-11-03", amount: -300 }] });
        return g.marks.length === 1 && g.marks[0].date === "2026-11-03" && g.change.moved === 500
          && C.valueChangeText(g, "2026-11-18") === "No change since 1 Nov, besides ₪5 put in"
          && C.valueChangeText(out, "2026-11-18") === "Up ₪8 since 1 Nov, besides ₪3 taken out";
      })());
const slices = plain(C.allocationSlices(pf.allocation));
check("investments: allocation — holdings by size, cash last, widths add up to the bar",
      same(slices.map((a) => a.slot), ["a0", "a1", "cash"]) && slices[0].name === pf.allocation[0].name && slices[2].name === "Cash"
      && Math.abs(slices.reduce((s, a) => s + a.width, 0) - 100) < 0.5 && slices[2].text === "2.5%", JSON.stringify(slices));
check("investments: allocation folds holdings beyond the limit and the server's rest into Other holdings",
      same(plain(C.allocationSlices([{ name: "A", value: 50 }, { name: "B", value: 30 }, { name: "C", value: 10 }, { name: "Other holdings", value: 5 }, { name: "Cash", value: 5 }], 2))
        .map((a) => [a.slot, a.name, a.value]), [["a0", "A", 50], ["a1", "B", 30], ["rest", "Other holdings", 15], ["cash", "Cash", 5]])
      && same(plain(C.allocationSlices([])), []) && same(plain(C.allocationSlices([{ name: "Cash", value: 0 }])), []));
const tt = pf.trades.map((t) => plain(C.tradeText(t, "2026-11-18")));
const buy = tt.find((x) => x.title === "Bought" && x.meta.includes("20 units")), div = tt.find((x) => x.title === "Dividend"), other = tt[pf.trades.findIndex((t) => t.kind === "other")];
const dep = tt[pf.trades.findIndex((t) => t.kind === "deposit")];
check("investments: a deposit is money in, without units or a security name", dep && dep.title === "Money in" && dep.tone === "in"
      && dep.name === "" && !dep.meta.some((m) => /units/.test(m)) && dep.amount === 2000000, JSON.stringify(dep));
check("investments: activity — a buy (units, fee), a dividend (its tax and the site's words), an unknown action in the site's words",
      buy && buy.tone === "out" && buy.meta.includes("20 units") && buy.meta.includes("fee ₪7.50") && buy.name
      && div && div.tone === "in" && div.meta.includes("tax ₪3.33") && div.meta.includes("דיבידנד")
      && other && other.title === pf.trades.find((t) => t.kind === "other").action && other.tone === "" && pf.trades.every((t) => t.kind), JSON.stringify(tt));

// ---------- Save (tests/fixtures/money_savings_web.json) ----------
const SV = JSON.parse(fs.readFileSync(path.join(SERVER, "tests", "fixtures", "money_savings_web.json"), "utf8"));
const sv = SV.savings;
const DAY = sv.as_of;
const svi = (k) => sv.items.find((i) => i.key === k);
const BILL = "bill:סלולר הצפון", ARNONA = "arnona:עיריית גבעת אורנים:2027";
const CASH = sv.items.find((i) => i.type === "cash").key, FX = sv.items.find((i) => i.type === "fx").key;  // account ids: new on each regeneration
const SPREE = "spree:חנות הצפון אונליין:2026-09-14";
const parts = plain(C.saveSections(sv.items, DAY));
const secs = (g) => Object.fromEntries(parts.find((p) => p.group === g).sections.map((s) => [s.key, s.items.map((i) => i.key)]));
const order = (keys) => keys.every((k, i) => i === 0 || sv.items.findIndex((x) => x.key === keys[i - 1]) < sv.items.findIndex((x) => x.key === k));

check("save: the tab sits between Cash flow and Investments, #money/save, not tied to the month (nor Investments)",
      C.TABS.indexOf("save") === C.TABS.indexOf("cashflow") + 1 && C.TABS.indexOf("investments") === C.TABS.indexOf("save") + 1 && C.TAB_LABELS.save === "Save"
      && C.stateToHash({ month: "2026-11", view: "page", tab: "save", f: {} }, "2026-11") === "#money/save" && C.stateFromHash("#money/save").tab === "save"
      && !C.usesMonth("save") && !C.usesMonth("investments") && ["overview", "spending", "cashflow", "transactions"].every(C.usesMonth));

// ---- Meal card (tests/fixtures/money_meals_web.json, from test_money_il_meals.py) ----
const ML = JSON.parse(fs.readFileSync(path.join(SERVER, "tests", "fixtures", "money_meals_web.json"), "utf8"));
const ml = ML.meals.body;
const mcur = ml.accounts[0].current;
const MDAY = mcur.start;  // the fixture's cycle holds the day it was written; any day of that year reads the same
check("meals: the tab only with a meal card (or when asked for), after Investments, not tied to the month",
      same(C.visibleTabs({ meals: ML.overview_meals.body.length > 0 }, "overview"), ["overview", "spending", "cashflow", "save", "meals", "transactions"])
      && same(C.visibleTabs({ investments: true, meals: true }, "overview"), C.TABS.slice())
      && same(C.visibleTabs({}, "meals"), ["overview", "spending", "cashflow", "save", "meals", "transactions"])
      && C.TABS.indexOf("meals") === C.TABS.indexOf("investments") + 1 && C.TAB_LABELS.meals === "Meal card" && !C.usesMonth("meals")
      && C.stateFromHash("#money/meals").tab === "meals" && C.kindIcon("meal") === "utensils" && C.kindWord("meal") === "Meal card");
const mt = plain(C.mealTiles(ml, MDAY));
check("meals: tiles — what is left of the budget until the cycle's end, the pace, the card part, meals",
      mt.left.value === 31050 && mt.left.sub === `of ${C.moneyText(100000, { cents: false })} · until ${C.shortDate(mcur.end, MDAY)}`
      && mt.card.value === 0 && mt.card.sub === "nothing beyond the budget" && mt.count.value === 1
      && mt.count.sub === `${C.moneyText(4730, { cents: false })} from the budget`
      && (mcur.runs_out ? mt.pace.text.startsWith("Lasts to ") : mt.pace.text.endsWith(" a day")), JSON.stringify(mt));
check("meals: used up / runs out early / no card", (() => {
  const up = C.mealTiles({ accounts: [{ current: { ...mcur, left: 0, count: 3, days_left: 2, runs_out: mcur.start } }] }, MDAY);
  const early = C.mealTiles({ accounts: [{ current: { ...mcur, left: 5000, used: 95000, runs_out: mcur.end } }] }, MDAY);
  return up.pace.text === "Used up" && up.pace.tone === "neg" && /^2 days until it renews on /.test(up.pace.sub)
    && early.pace.text === `Lasts to ${C.shortDate(mcur.end, MDAY)}` && C.mealTiles({ accounts: [] }, MDAY) === null;
})());
const mb = C.mealCycleBars(ml.cycles, MDAY);
check("meals: cycle bars oldest first, scaled to the largest, the billed line linked",
      mb.length === 3 && mb[0].start < mb[2].start && Math.max(...mb.map((b) => b.budgetPct + b.cardPct)) === 100
      && mb[0].card === 11825 && mb[0].txn && mb[0].billed.startsWith("On your card ")
      && mb[1].card === 5000 && mb[1].billed === "Not on your card yet" && !mb[1].txn && mb[2].billed === ""
      && mb[2].label === C.cycleLabel(ml.cycles[0], MDAY) && / – /.test(mb[2].label));
check("meals: the card line's detail carries its cycle", ML.transaction.body.meal_cycle
      && ML.transaction.body.meal_cycle.card_paid === 11825 && ML.transaction.body.meal_cycle.meals.length === 1);
check("save: the fixture has every status (and inactive items)", ["open", "doing", "done", "dismissed", "snoozed", "accepted", "watching"].every((st) => sv.items.some((i) => i.status === st))
      && sv.items.some((i) => !i.active));
check("save: every item is listed once — the parts' sections add up to the list, each part to its group",
      parts.reduce((n, p) => n + p.sections.reduce((m, s) => m + s.items.length, 0), 0) === sv.items.length
      && new Set(parts.flatMap((p) => p.sections.flatMap((s) => s.items.map((i) => i.key)))).size === sv.items.length
      && same(parts.map((p) => [p.group, p.label, p.count]), [["save", "Ways to save", sv.items.filter((i) => i.group === "save").length], ["watch", "Spending to watch", sv.items.filter((i) => i.group === "watch").length]]));
check("save: Ways to save — Now (open, due or untimed), In progress, Done, Your choices; the server's order inside each",
      same(Object.keys(secs("save")), ["now", "doing", "done", "choices"])
      && same(secs("save").now, [ARNONA, "renewal:ביטוח הדרום חובה:2026-12", "renewal:ביטוח הדרום דירה:2026-11", "cushion", FX, "mortgage", "insurance_review", "subscriptions"])
      && same(secs("save").doing, [BILL]) && same(secs("save").done, ["electricity"]) && same(secs("save").choices, [CASH, "fees"])
      && Object.values(secs("save")).every(order), JSON.stringify(secs("save")));
check("save: Spending to watch — Now, Watching (with a target), Your choices",
      same(secs("watch"), { now: ["overlap:ai", "leak:ענן הצפון", "penalties"], watching: ["creep:eating_out"], choices: ["late_night", SPREE] }), JSON.stringify(secs("watch")));
check("save: dismissed, accepted and snoozed items — active or not — always land in Your choices",
      sv.items.filter((i) => ["dismissed", "accepted", "snoozed"].includes(i.status)).every((i) => C.saveSection(i, DAY) === "choices")
      && sv.items.filter((i) => !i.active).every((i) => parts.some((p) => p.sections.some((s) => s.items.some((x) => x.key === i.key)))));
check("save: an open item waits in Coming up while its timing starts later; an unknown status is still listed (as a choice)",
      C.saveSection({ status: "open", timing: { from: "2026-12-01" } }, DAY) === "coming" && C.saveSection({ status: "open", timing: { from: DAY } }, DAY) === "now"
      && C.saveSection({ status: "open", timing: null }, DAY) === "now" && C.saveSection({ status: "paused" }, DAY) === "choices"
      && same(plain(C.saveSections([{ ...svi(ARNONA), timing: { ...svi(ARNONA).timing, from: "2026-12-01" } }], DAY))[0].sections.map((s) => s.key), ["coming"])
      && same(plain(C.saveSections([], DAY)).map((p) => [p.count, p.sections.length]), [[0, 0], [0, 0]]));
const svt = plain(C.saveTiles(sv, DAY));
check("save: tiles — could save low–high a year with the count, saved so far ≈ a year, the rate's source line",
      svt.could.text === "₪2,281–₪2,548" && svt.could.sub === "a year · 4 ideas" && svt.saved.value === sv.totals.saved && svt.saved.sub === "≈ ₪359 a year"
      && svt.source === "Bank of Israel rate 4.25% (18 Nov) · amounts from your charges"
      && C.saveTiles({ totals: { low: 0, high: 0, count: 0, saved: 0 }, rate: null }, DAY).source === "Amounts from your charges"
      && C.saveTiles({ totals: { low: 50000, high: 50000, count: 1 } }, DAY).could.text === "₪500", JSON.stringify(svt));
check("save: the money chip — a saving a year (one amount or a range), else the money at stake, a spree's total spent",
      C.savingChip(svi(ARNONA)).text === "+₪14/yr" && C.savingChip(svi(ARNONA)).kind === "save" && C.savingChip(svi(BILL)).text === "+₪768–₪888/yr"
      && C.savingChip(svi("mortgage")).text === "₪60,000/yr at stake" && C.savingChip(svi("mortgage")).kind === "stake"
      && C.savingChip(svi(SPREE)).text === "₪615 spent" && C.savingChip({ saving: null, at_stake: null }) === null);
check("save: effort and basis chips", C.effortText(svi(BILL).effort) === "20-min call" && C.effortText(svi("cushion").effort) === "10 min in the app"
      && C.effortText(svi(ARNONA).effort) === "15 min online" && C.effortText(svi("mortgage").effort) === "1 h comparing" && C.effortText(svi("subscriptions").effort) === "10 min to review"
      && C.effortText({ minutes: 90, how: "call" }) === "1.5 h call" && C.effortText(null) === "" && C.effortText(svi("creep:eating_out").effort) === ""
      && C.basisChip(svi("cushion")).text === "measured" && C.basisChip(svi(BILL)).text === "market" && C.basisChip({ saving: { basis: "increase" } }).text === "price rise"
      && C.basisChip(svi("mortgage")) === null);
check("save: each choice says where it stands, with its date", C.statusLine(svi("fees"), DAY) === "Not for you · 18 Nov" && C.statusLine(svi(CASH), DAY) === "Snoozed until 18 Dec"
      && C.statusLine(svi("late_night"), DAY) === "Worth it to you · 18 Nov" && C.statusLine(svi("creep:eating_out"), DAY) === "Watching since 10 Sep"
      && C.statusLine(svi(BILL), DAY) === "On it since 18 Nov" && C.statusLine(svi("electricity"), DAY) === "Done 18 Nov" && C.statusLine(svi(ARNONA), DAY) === "");
const acts = (k) => plain(C.statusActions(typeof k === "string" ? svi(k) : k)).map((a) => [a.label, a.body, Boolean(a.input)]);
check("save: the actions of each status and their PATCH bodies",
      same(acts(ARNONA), [["I'm on it", { status: "doing" }, false], ["I did it", { status: "done" }, false], ["Snooze a month", { status: "snoozed", snooze_days: 30 }, false], ["Not for me", { status: "dismissed" }, false]])
      && same(acts("overlap:ai"), [["Cut back", null, true], ["Worth it", { status: "accepted" }, false]])
      && same(acts(BILL), [["I did it", { status: "done" }, false], ["Not doing it", { status: "open" }, false]])
      && same(acts("electricity"), [["Not done", { status: "open" }, false]])
      && same(acts("fees"), [["Flag again", { status: "open" }, false]]) && same(acts("late_night"), [["Flag again", { status: "open" }, false]])
      && same(acts(CASH), [["Unsnooze", { status: "open" }, false]])
      && same(acts("creep:eating_out"), [["Edit target", null, true], ["Stop tracking", { target: null }, false]])
      && same(acts({ status: "paused", group: "save" }), [["Dana decides again", { status: "open" }, false]]), JSON.stringify(acts(ARNONA)));
check("save: every choice the user made has a way back to Dana", sv.items.filter((i) => i.status !== "open").every((i) =>
      C.statusActions(i).some((a) => (a.body && (a.body.status === "open" || ("target" in a.body && a.body.target === null))))));
const H = (s) => C.isolateHebrew(s);
check("save: the Undo toast says what changed and to what", C.saveToast(SV.patch.item, { status: "doing" }) === `On it · ${H(SV.patch.item.title)}`
      && C.saveToast(svi(CASH), { status: "snoozed", snooze_days: 30 }) === `Snoozed until 18 Dec · ${svi(CASH).title}`
      && C.saveToast(svi("creep:eating_out"), { target: 65000 }) === "Target ₪650 a month · Eating out is up: ₪860 a month"
      && C.saveToast(svi("creep:eating_out"), { target: null }).startsWith("Stopped tracking · ")
      && C.saveToast(svi("subscriptions"), { subitem: { key: "ענן הצפון", choice: "cancel" } }) === `${C.isolate("ענן הצפון")}: marked to cancel`
      && C.saveToast(svi(BILL), { answers: { lines: 2 } }).startsWith("Answer saved · ") && C.saveToast(svi("fees"), { status: "open" }).startsWith("Dana decides again · "));
check("save: a typed monthly target in whole shekels → agorot", C.targetAgorot("650") === 65000 && C.targetAgorot("₪1,200") === 120000 && C.targetAgorot(" 70 ") === 7000
      && [" ", "", "6.5", "0", "-5", "abc", "99999999", null].every((v) => C.targetAgorot(v) === null));

const tel = plain(C.offerRows(svi(BILL).market, DAY));
check("save: telecom offers — provider, plan, the 12-month average, after the promo, conditions, the source",
      same(tel.columns, ["Provider", "Plan", "12-month average", "After the promo", "Conditions", "Source"]) && tel.hasPlan && tel.rows.length === 4
      && tel.rows[0].name === "סלולר המרכז" && tel.rows[0].plan === "גלישה 500"
      && same(tel.rows[0].cells.map((c) => [c.label, c.text, c.sub || ""]), [["12-month average", "₪39.90 a month", ""], ["After the promo", "₪49.90 a month after 6 months", "₪29.90 a month until then"], ["Conditions", "ללא התחייבות", ""]])
      && tel.rows[1].cells[1].text === "No promo" && tel.rows[1].cells[1].sub === "₪35.00 a month" && tel.rows[1].cells[1].muted
      && tel.rows[2].cells[1].text === "₪55.00 a month after 12 months"
      && tel.rows.every((r) => r.source && r.source.url === "https://example.com/offer") && same(tel.sources, [{ title: "השוואת מחירים לדוגמה", url: "https://example.com/prices" }]), JSON.stringify(tel.rows[0]));
const fxo = plain(C.offerRows(svi(FX).market, DAY));
check("save: card offers — a column empty in every row (conditions) is left out; fee and monthly fee in words",
      same(fxo.columns, ["Card or wallet", "Kind", "Conversion fee", "Monthly fee", "Source"]) && !fxo.hasPlan
      && same(fxo.rows.map((r) => [r.name, ...r.cells.map((c) => c.text)]), [["כרטיס הצפון", "Card", "0%", "None"], ["ארנק הדרום", "Wallet", "0.5%", "None"], ["בנק הגבעה", "Bank account", "1%", "₪9.90 a month"]]), JSON.stringify(fxo));
const elo = plain(C.offerRows(svi("electricity").market, DAY));
check("save: electricity offers — discount, hours and days (Hebrew isolated in an English line), meter",
      same(elo.columns, ["Supplier", "Plan", "Discount", "Hours", "Meter", "Source"])
      && same(elo.rows.map((r) => r.cells.map((c) => c.text)), [["7% off", "24 שעות · כל השבוע", "Any meter"], ["20% off", "23:00–07:00 · כל השבוע", "Smart meter needed"], ["15% off", "07:00–17:00 · א׳–ה׳", "Smart meter needed"]])
      && elo.rows[0].cells[1].iso === true, JSON.stringify(elo.rows.map((r) => r.cells)));
const aro = plain(C.offerRows(svi(ARNONA).market, DAY));
check("save: the arnona check — found, its discount, deadline and how", aro.rows.length === 1 && aro.rows[0].name === "A discount for paying the year at once"
      && same(aro.rows[0].cells.map((c) => c.text), ["2% off", "31 Jan 2027", "באתר העירייה"]) && same(aro.columns, ["Result", "Discount", "Deadline", "How", "Source"])
      && plain(C.offerRows({ topic: "arnona", items: [{ found: false, discount_pct: null, deadline: null, method: "", source: null }] }, DAY)).rows[0].name === "No discount found for paying at once");
check("save: only http(s) sources are links; a one-month promo says so", C.offerRows({ topic: "telecom", items: [{ ...svi(BILL).market.items[0], promo_months: 1, source: { title: "x", url: "javascript:alert(1)" } }], sources: [{ title: "y", url: "ftp://z" }] }, DAY).rows[0].source === null
      && C.offerRows({ topic: "telecom", items: [{ ...svi(BILL).market.items[0], promo_months: 1 }] }, DAY).rows[0].cells[1].text === "₪49.90 a month after 1 month"
      && C.offerRows({ topic: "telecom", items: [], sources: [{ title: "y", url: "ftp://z" }] }, DAY).sources.length === 0
      && C.hostOf("https://www.Example.com/a?b") === "example.com" && C.hostOf("javascript:alert(1)") === "" && C.offerRows(null) === null);
const mv = (m) => plain(C.marketView(m, DAY));
const okm = svi(BILL).market;
check("save: price check states — none (Check prices), a question first, checking, ok (Check again, checked date, stale), failed, unavailable",
      mv({ ...okm, state: "none", items: [], checked_at: null }).button === "Check prices"
      && mv({ ...okm, state: "none", items: [], needs_answer: "service" }).button === null && mv({ ...okm, state: "none", items: [], needs_answer: "service" }).question === "service"
      && mv({ ...okm, state: "checking" }).button === null && /Checking prices/.test(mv({ ...okm, state: "checking" }).text) && mv({ ...okm, state: "checking" }).offers
      && mv(okm).button === "Check again" && mv(okm).checked === "Checked 18 Nov with a web search · confirm on the provider's site" && mv(okm).stale === ""
      && mv({ ...okm, stale: true }).stale === "prices may have changed"
      && mv({ ...okm, state: "failed", error: "the model did not search" }).button === "Check again" && /the model did not search/.test(mv({ ...okm, state: "failed", error: "the model did not search" }).text)
      && mv({ ...okm, state: "failed", items: [] }).button === "Try again" && mv({ ...okm, state: "unavailable", items: [] }).button === null
      && /aren't available/.test(mv({ ...okm, state: "unavailable", items: [] }).text) && /municipality/.test(mv(svi(ARNONA).market).checked) && C.marketView(null) === null);
const cr = (st, b) => plain(C.checkReply(st, b));
check("save: Check prices answers — 202 / 409 poll, needs_answer shows the question, 429 waits in words, 503, no_check",
      cr(202, { state: "checking" }).kind === "checking" && cr(409, { error: "running" }).kind === "checking"
      && cr(400, { error: "needs_answer", question: svi(BILL).questions[0] }).kind === "question" && cr(400, { error: "needs_answer", question: svi(BILL).questions[0] }).question.id === "service"
      && /in 5 min\.$/.test(cr(429, { error: "too_soon", retry_after: 290 }).text) && /in 6 h\.$/.test(cr(429, { error: "too_soon", retry_after: 21000 }).text)
      && cr(429, {}).kind === "wait" && /aren't available/.test(cr(503, { error: "unavailable" }).text) && cr(400, { error: "no_check" }).text === "This idea has no price check."
      && cr(0, null).kind === "error");
check("save: outcomes — saved so far and a year, waiting, no change", (() => {
  const c = plain(C.outcomeText(svi("subscriptions").outcome)), w = plain(C.outcomeText(svi(BILL).outcome)), n = plain(C.outcomeText({ state: "no_change", detail: "Still charged" }));
  return c.tone === "good" && c.title === "Saved ₪60 so far" && c.sub === "≈ ₪359 a year" && c.detail === svi("subscriptions").outcome.detail
    && w.tone === "wait" && w.detail === "Waiting for the next charge" && n.tone === "warn" && n.detail === "Still charged" && C.outcomeText(null) === null;
})());
const tp = plain(C.targetProgress(svi("creep:eating_out").target));
check("save: a watched target — this month against it, on track, days left, past months under / over, saved",
      tp.bar === 43 && !tp.over && tp.text === "₪300 of ₪700 this month" && tp.track === "On track" && tp.tone === "good" && tp.left === "12 days left"
      && same(tp.months, [{ month: "2026-09", label: "Sep", text: "₪900 of ₪700", under: false }, { month: "2026-10", label: "Oct", text: "₪660 of ₪700", under: true }])
      && tp.saved === "₪200 saved so far"
      && (() => { const o = plain(C.targetProgress({ monthly: 70000, this_month: 80000, on_track: false, days_left: 1, months: [], saved: 0 })); return o.bar === 100 && o.over && o.track === "Over the target" && o.tone === "bad" && o.left === "1 day left" && o.saved === ""; })()
      && C.targetProgress({ monthly: 70000, this_month: 1000, on_track: null, days_left: 29 }).track === "" && C.targetProgress(null) === null, JSON.stringify(tp));
const subs = svi("subscriptions").subitems.map((x) => plain(C.subitemText(x, DAY)));
check("save: subscription lines — the charge at its cadence, a year, the next date; a cancelled one that stopped says so",
      same(subs[0], { amount: "₪74.20 / month", yearly: "₪890 a year", next: "next 12 Dec" }) && same(subs[3], { amount: "₪480.00 / year", yearly: "₪480 a year", next: "next 15 Mar 2027" })
      && subs[4].next === "No charge since you cancelled", JSON.stringify(subs));
check("save: evidence links — the merchant or category over all time in Transactions, or the named view",
      same(plain(C.savingLink(svi(BILL).evidence.link)), { tab: "transactions", f: { ...plain(C.EMPTY_FILTERS), q: "סלולר הצפון", all: true }, label: "See these charges" })
      && plain(C.savingLink(svi("subscriptions").evidence.link)).f.category === "subscriptions"
      && same(plain(C.savingLink(svi(CASH).evidence.link)), { tab: "investments", f: null, label: "Open Investments" }) && C.savingLink(svi("cushion").evidence.link).tab === "cashflow"
      && C.savingLink(svi(FX).evidence.link).label === "Open Transactions" && C.savingLink({ view: "nowhere" }) === null && C.savingLink(null) === null);
const spk = plain(C.seriesSpark(svi(BILL).evidence.series, { width: 180, height: 44 }, DAY));
check("save: the evidence sparkline — every point by date, from zero, the dates it covers; under 2 points none",
      spk && spk.count === 14 && spk.line.startsWith("M3,") && spk.last.x === 177 && spk.line.split(" ").length === 14 && spk.area.endsWith("Z")
      && spk.range === "10 Oct 2025 – 10 Nov" && spk.last.y < Number(spk.line.split(" ")[0].split(",")[1])
      && C.seriesSpark(svi("renewal:ביטוח הדרום חובה:2026-12").evidence.series) === null && C.seriesSpark([]) === null, JSON.stringify(spk));

console.log(`\n${pass}/${pass + failures.length} passed`);
if (failures.length) process.exit(1);
