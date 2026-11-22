/* Money — the pure part of the Money panel (money.js is the controller, money-dash.js draws
 * the dashboard views).
 *
 * DanaMoneyCore: no DOM, no network. Amounts arrive from the server in hundredths of the user's currency
 * (signed: money out is negative, docs/money.md "Page API and insights") and are formatted only here, in
 * that currency and the browser's locale (setCurrency, from GET /api/me); dates are the server's
 * local ISO dates. It formats money (bidi-safe next to Hebrew merchant names), dates and times, works out
 * months, the charts' geometry (everyday pace, 12-month bars, cash flow in / out, bank balance and
 * forecast, row sparklines), groups transactions by day, maps the page's view and filters to the API's
 * query string and the #money hash and back, and words the tiles, coming up, recurring charges by their
 * cadence, a source's state, badges, the "counts as" choices and errors; for the Save view it sorts the
 * ways to save and spending to watch into sections and words their chips, actions, price checks,
 * targets and outcomes (only formatting: every number is the server's). Tested by test_web_money.mjs
 * against the payloads captured from the real API (tests/fixtures/money_web.json, written by
 * test_money_page.py; money_portfolio_web.json; money_savings_web.json).
 */
(() => {
  "use strict";

  const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  const MON = MONTHS.map((m) => m.slice(0, 3));
  const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  const MINUS = "−";
  // Bidi isolates: FSI…PDI around text of either direction (a Hebrew merchant in an English sentence),
  // LRI…PDI around an amount so "−₪25.00" keeps its order inside right-to-left text.
  const FSI = "⁨", LRI = "⁦", PDI = "⁩";
  const INCOME_FLOWS = ["income", "transfer_in"];
  // The 12-month chart's series, bottom to top: eight groups keep a colour of their own (money.css
  // .mny-g-<key>), the rest fold into "other"; uncategorized stays apart. Colour follows the group.
  const SERIES = ["food", "housing", "transport", "bills", "shopping", "health", "leisure", "family", "people", "other", "uncategorized"];
  const SERIES_LABELS = { other: "Other", uncategorized: "Uncategorized" };
  const CATEGORY_SOURCES = {
    user: "set by you", rule: "your rule for this merchant", memory: "learned from earlier charges",
    model: "Dana's guess", bank: "from the card company",
  };
  const FLOW_CHIPS = { card_bill: "Card bill", own_transfer: "Between your accounts" };
  const EMPTY_FILTERS = Object.freeze({ q: "", category: "", account: "", min: "", max: "", all: false });
  // The page's views, in the header's order; the first is the default (no path in the hash).
  // Investments shows only for a user with an investment account, Meal card only with a meal card
  // (visibleTabs). Save, Investments and Meal card are not tied to a month: the header's month switcher
  // hides on them (usesMonth).
  const TABS = ["overview", "spending", "cashflow", "save", "investments", "meals", "transactions"];
  const TAB_LABELS = { overview: "Overview", spending: "Spending", cashflow: "Cash flow", save: "Save", investments: "Investments", meals: "Meal card", transactions: "Transactions" };
  const MONTHLESS_TABS = ["save", "investments", "meals"];
  // Tabs shown only when the user has that kind of account (visibleTabs' `has`).
  const OPTIONAL_TABS = ["investments", "meals"];
  // An account's kind (sources, the account picker, the add-a-login list): its icon and word.
  const ACCOUNT_KINDS = { bank: { icon: "landmark", word: "Bank" }, card: { icon: "credit-card", word: "Card" }, investment: { icon: "chart-line", word: "Investments" }, meal: { icon: "utensils", word: "Meal card" } };
  // What a transaction counts as (PATCH {flow}), in the words the page uses; money out lists the first
  // group, money in the second (own_transfer in both), the row's own flow always.
  const FLOW_LABELS = {
    purchase: "spending", transfer_out: "a transfer to a person", investment: "money set aside",
    own_transfer: "my own money (not spending)", income: "income", transfer_in: "money received", refund: "a refund",
    loan: "a loan payment", fee: "a fee", cash: "cash",
  };
  const OUT_FLOWS = ["purchase", "transfer_out", "investment", "own_transfer", "loan", "fee", "cash"];
  const IN_FLOWS = ["income", "transfer_in", "refund", "own_transfer"];
  const KIND_LABELS = { everyday: "everyday spending", fixed: "a fixed cost", yearly: "a yearly charge", one_off: "a one-off" };

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isolate = (s) => FSI + String(s ?? "") + PDI;
  const group3 = (n) => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const pad2 = (n) => String(n).padStart(2, "0");

  // ---------- money ----------

  // The user's currency (ISO 4217) and the locale amounts are written in: setCurrency.
  let CUR = "USD", LOCALE = "en-US";

  /** Use the user's `currency` (GET /api/me) and the browser's `locale` (navigator.language) from now on. */
  function setCurrency(currency, locale) {
    CUR = /^[A-Za-z]{3}$/.test(String(currency || "")) ? String(currency).toUpperCase() : "USD";
    if (locale) { try { new Intl.NumberFormat(locale); LOCALE = locale; } catch (_) { LOCALE = "en-US"; } }
  }
  /** The user's currency code ("EUR"). */
  const currency = () => CUR;
  /** A currency code as ISO 4217, upper case; "NIS" (some sites' name for the shekel) → "ILS"; "" → the user's. */
  function currencyCode(code) {
    const c = String(code || "").trim().toUpperCase();
    return c === "NIS" ? "ILS" : c || CUR;
  }

  /** `units` (a positive decimal) in `code` and the locale: "₪1,234.50", "1.234,50 €", "CHF 80.00";
   *  `digits` fraction digits (null: the currency's own). A code Intl does not know: "80.00 XYZ". */
  function formatUnits(units, code, digits) {
    const opts = { style: "currency", currency: code, currencyDisplay: "narrowSymbol" };
    if (digits != null) { opts.minimumFractionDigits = digits; opts.maximumFractionDigits = digits; }
    try { return new Intl.NumberFormat(LOCALE, opts).format(units); } catch (_) {
      return `${digits == null ? units : units.toFixed(digits)} ${code}`;
    }
  }
  /** The currency's narrow symbol in the locale ("€", "₪", "CHF"). */
  function currencySymbol(code = CUR) {
    try {
      const part = new Intl.NumberFormat(LOCALE, { style: "currency", currency: code, currencyDisplay: "narrowSymbol" })
        .formatToParts(0).find((p) => p.type === "currency");
      return part ? part.value : code;
    } catch (_) { return code; }
  }

  /** Hundredths → "₪1,234.50" in the user's currency. sign: "minus" (default; "−" on money out), "plus" (also "+"
   *  on money in), "none" (the absolute amount). cents: true (default), false (whole units, rounded), "auto" (drop ".00"). */
  function money(agorot, o = {}) {
    const v = Math.round(Number(agorot) || 0);
    const abs = Math.abs(v);
    const s = o.cents === false ? formatUnits(Math.round(abs / 100), CUR, 0)
      : formatUnits(abs / 100, CUR, o.cents === "auto" && abs % 100 === 0 ? 0 : 2);
    const sign = o.sign || "minus";
    if (sign === "none" || v === 0 || (o.cents === false && Math.round(abs / 100) === 0)) return s;
    if (v < 0) return MINUS + s;
    return sign === "plus" ? "+" + s : s;
  }
  /** The same, isolated for plain text (toasts, titles, aria labels) beside text of either direction. */
  const moneyText = (agorot, o) => LRI + money(agorot, o) + PDI;
  /** The same as HTML: a left-to-right isolate with tabular figures (money.css .mny-amt). */
  const moneyHtml = (agorot, o = {}, cls = "") => `<bdi dir="ltr" class="mny-amt${cls ? " " + cls : ""}">${esc(money(agorot, o))}</bdi>`;

  /** Short axis / chart values: ₪850, ₪4.8k, ₪12k, ₪1.2M (the symbol first, whatever the locale). */
  function compact(agorot) {
    const sh = Math.abs(Number(agorot) || 0) / 100;
    const neg = (Number(agorot) || 0) < 0 ? MINUS : "";
    const sym = currencySymbol();
    const trim = (x) => x.toFixed(1).replace(/\.0$/, "");
    if (sh >= 1e6) return `${neg}${sym}${trim(sh / 1e6)}M`;
    if (sh >= 1e4) return `${neg}${sym}${Math.round(sh / 1e3)}k`;
    if (sh >= 1e3) return `${neg}${sym}${trim(sh / 1e3)}k`;
    return `${neg}${sym}${Math.round(sh)}`;
  }

  /** A foreign amount in its currency's hundredths → "$5.00", "€35.00", "CHF 5.00" (absolute). */
  function foreignText(amount, currency) {
    const abs = Math.abs(Math.round(Number(amount) || 0));
    return formatUnits(abs / 100, currencyCode(currency), 2);
  }

  // ---------- dates ----------

  const isDay = (s) => /^\d{4}-\d{2}-\d{2}$/.test(String(s || ""));
  function parseDay(iso) {
    const [y, m, d] = String(iso).slice(0, 10).split("-").map(Number);
    return new Date(y, m - 1, d, 12);
  }
  const dayIso = (d) => `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
  const todayIso = (now = new Date()) => dayIso(now instanceof Date ? now : new Date(now));
  function addDays(iso, n) { const d = parseDay(iso); d.setDate(d.getDate() + n); return dayIso(d); }

  /** "15 Dec" (+ " 2025" when not in `today`'s year). */
  function shortDate(iso, today) {
    if (!iso) return "";
    const d = parseDay(iso);
    const year = today && String(today).slice(0, 4) !== String(d.getFullYear()) ? ` ${d.getFullYear()}` : "";
    return `${d.getDate()} ${MON[d.getMonth()]}${year}`;
  }
  /** "Tue 17 Nov 2026". */
  function longDate(iso) {
    if (!iso) return "";
    const d = parseDay(iso);
    return `${WEEKDAYS[d.getDay()]} ${d.getDate()} ${MON[d.getMonth()]} ${d.getFullYear()}`;
  }
  /** A day header: "Today", "Yesterday", "Mon 16 Nov" (+ year when not this year). */
  function dayLabel(iso, today) {
    if (today && iso === today) return "Today";
    if (today && iso === addDays(today, -1)) return "Yesterday";
    const d = parseDay(iso);
    const year = today && String(today).slice(0, 4) !== String(d.getFullYear()) ? ` ${d.getFullYear()}` : "";
    return `${WEEKDAYS[d.getDay()]} ${d.getDate()} ${MON[d.getMonth()]}${year}`;
  }
  /** The local time an aware ISO time was written in ("2026-11-18T08:10:00+02:00" → "08:10"), so the
   *  browser's own zone never shifts it. */
  function timeOf(iso) {
    const m = /T(\d{2}):(\d{2})/.exec(String(iso || ""));
    return m ? `${m[1]}:${m[2]}` : "";
  }
  const ms = (t) => (typeof t === "number" ? t : t instanceof Date ? t.getTime() : Date.now());
  /** "just now", "5 min ago", "2 h ago", "yesterday", "3 days ago", then a date. */
  function ago(iso, now) {
    if (!iso) return "";
    const s = Math.max(0, (ms(now) - Date.parse(iso)) / 1000);
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.floor(s / 60)} min ago`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
    if (s < 2 * 86400) return "yesterday";
    if (s < 30 * 86400) return `${Math.floor(s / 86400)} days ago`;
    return shortDate(dayIso(new Date(Date.parse(iso))), todayIso(new Date(ms(now))));
  }
  /** "in 5 min", "in 3 h", "in 2 days"; "now" once it has come. */
  function until(iso, now) {
    if (!iso) return "";
    const s = (Date.parse(iso) - ms(now)) / 1000;
    if (s <= 30) return "now";
    if (s < 3600) return `in ${Math.max(1, Math.round(s / 60))} min`;
    if (s < 86400) return `in ${Math.round(s / 3600)} h`;
    return `in ${Math.round(s / 86400)} days`;
  }
  /** "3:42" to an expiry (never negative). */
  function countdown(iso, now) {
    const s = Math.max(0, Math.floor((Date.parse(iso) - ms(now)) / 1000));
    return `${Math.floor(s / 60)}:${pad2(s % 60)}`;
  }
  /** A server instant in the viewer's clock: "07:01" today, "yesterday 19:00", "17 Nov 19:00". */
  function when(iso, now) {
    if (!iso) return "";
    const d = new Date(Date.parse(iso));
    const t = `${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
    const today = todayIso(new Date(ms(now))), day = dayIso(d);
    if (day === today) return t;
    if (day === addDays(today, -1)) return `yesterday ${t}`;
    if (day === addDays(today, 1)) return `tomorrow ${t}`;
    return `${shortDate(day, today)} ${t}`;
  }
  /** Seconds to wait → "45 s" / "7 min". */
  const waitText = (sec) => (sec < 60 ? `${Math.max(1, Math.round(sec))} s` : `${Math.ceil(sec / 60)} min`);

  // ---------- months ----------

  const isMonth = (s) => /^\d{4}-(0[1-9]|1[0-2])$/.test(String(s || ""));
  const monthOf = (now = new Date()) => todayIso(now).slice(0, 7);
  function shiftMonth(key, n) {
    const [y, m] = key.split("-").map(Number);
    const t = y * 12 + (m - 1) + n;
    return `${Math.floor(t / 12)}-${pad2((t % 12) + 1)}`;
  }
  /** "November 2026" · short: "Nov 2026" · the month alone with year:false. */
  function monthLabel(key, o = {}) {
    const [y, m] = key.split("-").map(Number);
    const name = o.short ? MON[m - 1] : MONTHS[m - 1];
    return o.year === false ? name : `${name} ${y}`;
  }
  function monthBounds(key) {
    const [y, m] = key.split("-").map(Number);
    return { start: `${key}-01`, end: `${key}-${pad2(new Date(y, m, 0).getDate())}` };
  }

  // ---------- chart geometry ----------

  function niceMax(v) {
    if (!(v > 0)) return 100;
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    const n = v / p;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
  }
  const r1 = (n) => Math.round(n * 10) / 10;

  /** The everyday pace chart (GET /api/money/pace): this month's everyday spending, cumulative by day,
   *  against the typical month (the median of the whole months before) and its usual range (the
   *  quartiles, a band). One y scale; day 1 at the left; this month stops at today. */
  function paceGeometry(p, size = {}) {
    const W = size.width || 640, H = size.height || 200;
    const L = size.left ?? 52, R = size.right ?? 14, T = size.top ?? 14, B = size.bottom ?? 24;
    const s = { this: p.this_month || [], typical: p.typical || [], low: p.low || [], high: p.high || [] };
    const n = Math.max(p.days || 0, s.this.length, 2);
    const values = [...s.this, ...s.typical, ...s.high].filter((v) => v != null);
    const max = niceMax(Math.max(0, ...values));
    const plotW = W - L - R, plotH = H - T - B;
    const x = (i) => r1(L + (i / (n - 1)) * plotW);
    const y = (v) => r1(T + plotH - (Math.max(0, v) / max) * plotH);
    const known = (arr) => { const out = []; for (let i = 0; i < arr.length && i < n && arr[i] != null; i++) out.push([x(i), y(arr[i])]); return out; };
    const line = (pts) => pts.map((pt, i) => `${i ? "L" : "M"}${pt[0]},${pt[1]}`).join(" ");
    const pts = { this: known(s.this), typical: known(s.typical), low: known(s.low), high: known(s.high) };
    const base = r1(T + plotH);
    const area = pts.this.length > 1 ? `${line(pts.this)} L${pts.this[pts.this.length - 1][0]},${base} L${pts.this[0][0]},${base} Z` : "";
    const band = pts.high.length > 1 && pts.low.length === pts.high.length
      ? `${line(pts.high)} ${pts.low.slice().reverse().map((pt) => `L${pt[0]},${pt[1]}`).join(" ")} Z` : "";
    const i = pts.this.length - 1;
    const ticks = [0, 0.5, 1].map((f) => ({ v: max * f, y: y(max * f), text: compact(max * f) }));
    const labelDays = [1, 5, 10, 15, 20, 25, n].filter((d, k, a) => d <= n && a.indexOf(d) === k && !(d !== n && n - d < 3));
    return {
      W, H, L, R, T, B, n, max, plotW, plotH, base, ticks,
      lines: { this: line(pts.this), typical: line(pts.typical) }, band, area,
      end: i < 0 ? null : { i, x: pts.this[i][0], y: pts.this[i][1], v: s.this[i] },
      today: p.today ? { day: p.today, x: x(p.today - 1) } : null,
      xLabels: labelDays.map((d) => ({ day: d, x: x(d - 1), text: String(d) })),
      x, y,
    };
  }
  /** The day index (0-based) under an x position in the pace chart. */
  function paceIndexAt(geo, px) {
    return Math.max(0, Math.min(geo.n - 1, Math.round(((px - geo.L) / geo.plotW) * (geo.n - 1))));
  }
  /** The pace chart's values on a day (index 0-based): null where a series has no value. */
  function paceAt(p, i) {
    const at = (arr) => (arr && i < arr.length && arr[i] != null ? arr[i] : null);
    return { day: i + 1, this: at(p.this_month), typical: at(p.typical), low: at(p.low), high: at(p.high) };
  }

  const seriesOf = (group) => (!group ? "uncategorized" : SERIES.includes(group) ? group : "other");

  /** The 12-month stacked bars (GET /api/money/months): one bar per month, its groups folded into the
   *  chart's series and stacked bottom-up in SERIES order with a 2px gap between segments. */
  function monthsGeometry(payload, size = {}) {
    const W = size.width || 640, H = size.height || 180;
    const L = size.left ?? 46, R = size.right ?? 6, T = size.top ?? 8, B = size.bottom ?? 22, GAP = size.gap ?? 2;
    const months = payload.months || [];
    const max = niceMax(Math.max(0, ...months.map((m) => m.total)));
    const plotW = W - L - R, plotH = H - T - B;
    const slot = plotW / Math.max(1, months.length);
    const bw = Math.max(4, Math.min(34, slot * 0.62));
    const base = T + plotH;
    const present = new Set();
    const bars = months.map((m, i) => {
      const sums = {};
      Object.entries(m.groups || {}).forEach(([g, v]) => { if (v > 0) { const k = seriesOf(g === "uncategorized" ? null : g); sums[k] = (sums[k] || 0) + v; } });
      const x = r1(L + i * slot + (slot - bw) / 2);
      let top = base;
      const segments = [];
      SERIES.forEach((k) => {
        const v = sums[k];
        if (!v) return;
        present.add(k);
        const h = (v / max) * plotH;
        const gap = segments.length ? GAP : 0;
        const y = top - h;
        segments.push({ key: k, value: v, x, w: r1(bw), y: r1(y), h: r1(Math.max(0, h - gap)) });
        top = y;
      });
      // The rounded top goes on the highest segment tall enough to see (a sliver above it is only a hairline).
      const visible = segments.filter((s) => s.h >= 1);
      if (visible.length) visible[visible.length - 1].top = true;
      return { month: m.month, label: m.label, total: m.total, count: m.count, x, w: r1(bw), slotX: r1(L + i * slot), slotW: r1(slot), cx: r1(x + bw / 2), segments };
    });
    const ticks = [0, 0.5, 1].map((f) => ({ v: max * f, y: r1(base - f * plotH), text: compact(max * f) }));
    const every = months.length > 8 ? 2 : 1;
    const xLabels = bars.map((b, i) => ({ month: b.month, x: b.cx, text: MON[Number(b.month.slice(5)) - 1], show: (months.length - 1 - i) % every === 0 }));
    const legend = SERIES.filter((k) => present.has(k)).map((k) => ({ key: k, label: SERIES_LABELS[k] || labelOfGroup(payload.groups, k) }));
    return { W, H, L, R, T, B, max, base: r1(base), plotH, bars, ticks, xLabels, legend };
  }
  const labelOfGroup = (groups, key) => ((groups || []).find((g) => g.key === key) || {}).label || key;

  /** An SVG path for a bar segment: its top corners rounded when it is the top of an upward stack
   *  (`top`), its bottom corners when it is the end of a downward one (`bottom`). */
  function barPath(s, radius = 4) {
    const r = s.top || s.bottom ? Math.min(radius, s.w / 2, s.h) : 0;
    const x = s.x, y = s.y, w = s.w, h = s.h, b = r1(y + h), xr = r1(x + w);
    if (!r) return `M${x},${b} V${y} H${xr} V${b} Z`;
    if (s.bottom) return `M${x},${y} V${r1(b - r)} Q${x},${b} ${r1(x + r)},${b} H${r1(xr - r)} Q${xr},${b} ${xr},${r1(b - r)} V${y} Z`;
    return `M${x},${b} V${r1(y + r)} Q${x},${y} ${r1(x + r)},${y} H${r1(xr - r)} Q${xr},${y} ${xr},${r1(y + r)} V${b} Z`;
  }

  /** Trends (GET /api/money/trends) as a months payload for monthsGeometry: the groups' 12 monthly
   *  totals (one-offs left out), oldest first, the trend's month last. */
  function trendsAsMonths(t) {
    const labels = (t && t.months) || [];
    const groups = (t && t.groups) || [];
    return {
      groups: groups.map((g) => ({ key: g.key, label: g.label })),
      months: labels.map((label, i) => {
        const sums = {};
        let total = 0;
        groups.forEach((g) => { const v = (g.months || [])[i] || 0; if (v > 0) { sums[g.key] = v; total += v; } });
        return { month: shiftMonth(t.month, i - (labels.length - 1)), label, total, count: null, groups: sums };
      }),
    };
  }

  /** A row's tiny bar chart of monthly totals (oldest first): [{x, y, w, h, last}] in a W × H box. */
  function sparkBars(values, o = {}) {
    const W = o.width || 64, H = o.height || 20, GAP = o.gap ?? 1.5;
    const vs = (values || []).map((v) => Math.max(0, Number(v) || 0));
    const max = Math.max(0, ...vs);
    const n = Math.max(1, vs.length);
    const w = (W - GAP * (n - 1)) / n;
    return {
      W, H, max,
      bars: vs.map((v, i) => {
        const h = max > 0 && v > 0 ? Math.max(1, (v / max) * H) : 0;
        return { x: r1(i * (w + GAP)), y: r1(H - h), w: r1(w), h: r1(h), v, last: i === n - 1 };
      }),
    };
  }

  /** Round ticks covering [lo, hi]: about `count` steps of 1, 2, 2.5 or 5 × a power of ten. */
  function niceTicks(lo, hi, count = 4) {
    if (!(hi > lo)) { hi = lo + (Math.abs(lo) || 100); }
    const raw = (hi - lo) / count;
    const p = Math.pow(10, Math.floor(Math.log10(raw)));
    const n = raw / p;
    const step = (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
    const out = [];
    for (let k = Math.floor(lo / step); k <= Math.ceil(hi / step); k++) out.push(Number((k * step).toPrecision(12)));
    return out;
  }

  /** The cash flow chart (GET /api/money/cashflow): per month, income as a bar up from zero; fixed,
   *  yearly, everyday and one-off spending stacked down from it (2px gaps); money set aside as a thin
   *  bar of its own beside them; the net as a point (joined by a line). One signed y scale. */
  const FLOW_SERIES = ["fixed", "yearly", "everyday", "one_off"];
  const FLOW_SERIES_LABELS = { income: "Income", fixed: "Fixed", yearly: "Yearly", everyday: "Everyday", one_off: "One-off", set_aside: "Set aside", net: "Net" };
  function cashflowGeometry(cf, size = {}) {
    const W = size.width || 640, H = size.height || 220;
    const L = size.left ?? 52, R = size.right ?? 8, T = size.top ?? 10, B = size.bottom ?? 22, GAP = size.gap ?? 2;
    const months = (cf && cf.months) || [];
    const outOf = (m) => FLOW_SERIES.reduce((a, k) => a + Math.max(0, m[k] || 0), 0);
    const maxUp = Math.max(0, ...months.map((m) => Math.max(m.income || 0, m.net || 0)));
    const maxDown = Math.max(0, ...months.map((m) => Math.max(outOf(m), m.set_aside || 0, -(m.net || 0))));
    let up = maxUp > 0 ? niceMax(maxUp) : 0, down = maxDown > 0 ? niceMax(maxDown) : 0;
    if (!up && !down) up = 100;
    const plotW = W - L - R, plotH = H - T - B;
    const unit = plotH / (up + down);
    const zero = r1(T + up * unit);
    const slot = plotW / Math.max(1, months.length);
    const anyAside = months.some((m) => m.set_aside > 0);
    const bw = Math.max(4, Math.min(24, slot * (anyAside ? 0.42 : 0.56)));
    const sw = anyAside ? Math.max(3, Math.round(bw * 0.4)) : 0;
    const groupW = bw + (sw ? GAP + sw : 0);
    const present = new Set();
    const bars = months.map((m, i) => {
      const x = r1(L + i * slot + (slot - groupW) / 2);
      const income = m.income > 0 ? (() => { present.add("income"); const h = m.income * unit; return { key: "income", value: m.income, x, w: r1(bw), y: r1(zero - h), h: r1(Math.max(0, h - 1)), top: true }; })() : null;
      let edge = zero + 1;
      const segments = [];
      FLOW_SERIES.forEach((k) => {
        const v = m[k] || 0;
        if (v <= 0) return;
        present.add(k);
        const h = v * unit;
        const gap = segments.length ? GAP : 0;
        segments.push({ key: k, value: v, x, w: r1(bw), y: r1(edge + gap), h: r1(Math.max(0, h - gap)) });
        edge += h;
      });
      const visible = segments.filter((s) => s.h >= 1);
      if (visible.length) visible[visible.length - 1].bottom = true;
      let setAside = null;
      if (m.set_aside > 0) { present.add("set_aside"); setAside = { key: "set_aside", value: m.set_aside, x: r1(x + bw + GAP), w: sw, y: r1(zero + 1), h: r1(Math.max(0, m.set_aside * unit - 1)), bottom: true }; }
      const cx = r1(x + bw / 2);
      return { month: m.month, label: m.label, complete: m.complete !== false, x, w: r1(bw), cx, slotX: r1(L + i * slot), slotW: r1(slot),
               income, segments, setAside, net: { cx, cy: r1(zero - (m.net || 0) * unit), v: m.net || 0 } };
    });
    // The net from the first month with anything in or out: an empty month after it is a real zero.
    const first = months.findIndex((m) => m.income || m.spent || m.set_aside);
    bars.forEach((b, i) => { b.net.show = first >= 0 && i >= first; });
    const netPath = bars.filter((b) => b.net.show).map((b, i) => `${i ? "L" : "M"}${b.net.cx},${b.net.cy}`).join(" ");
    if (first >= 0) present.add("net");
    const ticks = [];
    if (up) { ticks.push({ v: up, y: r1(zero - up * unit), text: compact(up) }); if (up * unit > 40) ticks.push({ v: up / 2, y: r1(zero - (up / 2) * unit), text: compact(up / 2) }); }
    ticks.push({ v: 0, y: zero, text: compact(0), zero: true });
    if (down) { if (down * unit > 40) ticks.push({ v: -down / 2, y: r1(zero + (down / 2) * unit), text: compact(-down / 2) }); ticks.push({ v: -down, y: r1(zero + down * unit), text: compact(-down) }); }
    const every = months.length > 8 ? 2 : 1;
    const xLabels = bars.map((b, i) => ({ month: b.month, x: b.cx, text: MON[Number(b.month.slice(5)) - 1], show: (months.length - 1 - i) % every === 0 }));
    const legend = ["income", ...FLOW_SERIES, "set_aside", "net"].filter((k) => present.has(k)).map((k) => ({ key: k, label: FLOW_SERIES_LABELS[k] }));
    return { W, H, L, R, T, B, zero, up, down, unit, plotH, bars, netPath, ticks, xLabels, legend };
  }

  /** Days since 1970 for an ISO date (UTC, so no daylight-saving hour ever shifts a day). */
  function dayNum(iso) {
    const [y, m, d] = String(iso).slice(0, 10).split("-").map(Number);
    return Math.round(Date.UTC(y, m - 1, d) / 86400000);
  }

  /** The bank balance chart (GET /api/money/balance): the history as a solid line, the forecast as a
   *  dashed one from its first day with its low–high range as a band, the low point marked, a time x
   *  axis (month starts labelled) and round y ticks (zero kept in view unless `includeZero: false`);
   *  `historyDays` keeps only that many days of the history (the past as long as the horizon ahead).
   *  `points` are what hover reads: one per day, the forecast's range on forecast days. */
  function balanceGeometry(b, size = {}) {
    let hist = (b && b.history) || [];
    if (size.historyDays && hist.length) { const from = addDays(hist[hist.length - 1].date, -size.historyDays); hist = hist.filter((h) => h.date >= from); }
    const fc = (b && b.forecast) || null;
    const series = (fc && fc.series) || [];
    if (!hist.length && !series.length) return null;
    const W = size.width || 640, H = size.height || 220;
    const L = size.left ?? 56, R = size.right ?? 12, T = size.top ?? 12, B = size.bottom ?? 24;
    const start = (hist[0] || series[0]).date;
    const end = (series[series.length - 1] || hist[hist.length - 1]).date;
    const d0 = dayNum(start), span = Math.max(1, dayNum(end) - d0);
    const values = [...hist.map((h) => h.balance), ...series.flatMap((s) => [s.balance, s.low ?? s.balance, s.high ?? s.balance])];
    let lo = Math.min(...values), hi = Math.max(...values);
    if (size.includeZero !== false) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
    const ticks = size.includeZero === false ? [lo, hi] : niceTicks(lo, hi, size.tickCount || 4);
    const dLo = ticks[0], dHi = ticks[ticks.length - 1] > dLo ? ticks[ticks.length - 1] : dLo + 100;
    const plotW = W - L - R, plotH = H - T - B;
    const x = (iso) => r1(L + ((dayNum(iso) - d0) / span) * plotW);
    const y = (v) => r1(T + plotH - ((v - dLo) / (dHi - dLo)) * plotH);
    const line = (rows, key = "balance") => rows.map((r, i) => `${i ? "L" : "M"}${x(r.date)},${y(r[key])}`).join(" ");
    const band = series.length > 1 && series.some((s) => s.low !== s.high)
      ? `${line(series, "high")} ${series.slice().reverse().map((s) => `L${x(s.date)},${y(s.low)}`).join(" ")} Z` : "";
    const lowSrc = size.low !== undefined ? size.low : fc && fc.low_point;
    const low = lowSrc && lowSrc.date >= start && lowSrc.date <= end ? { date: lowSrc.date, balance: lowSrc.balance, x: x(lowSrc.date), y: y(lowSrc.balance) } : null;
    const todayIso = fc ? fc.as_of : hist[hist.length - 1].date;
    const byDate = new Map();
    hist.forEach((h) => byDate.set(h.date, { date: h.date, balance: h.balance, forecast: false }));
    series.forEach((s) => { if (!byDate.has(s.date)) byDate.set(s.date, { date: s.date, balance: s.balance, low: s.low, high: s.high, forecast: true }); });
    const points = [...byDate.values()].sort((a, c) => (a.date < c.date ? -1 : 1)).map((p) => ({ ...p, x: x(p.date), y: y(p.balance) }));
    const xLabels = monthLabels(start, end, plotW, x);
    return {
      W, H, L, R, T, B, plotW, plotH, lo: dLo, hi: dHi,
      ticks: size.includeZero === false ? [] : ticks.map((v) => ({ v, y: y(v), text: compact(v), zero: v === 0 })),
      history: line(hist), forecast: line(series), band, low,
      today: { date: todayIso, x: x(todayIso) }, zeroY: dLo <= 0 && dHi >= 0 ? y(0) : null,
      points, xLabels, x, y,
    };
  }
  /** The point (index into geo.points) nearest an x position. */
  function pointIndexAt(geo, px) {
    let best = 0, dist = Infinity;
    geo.points.forEach((p, i) => { const d = Math.abs(p.x - px); if (d < dist) { dist = d; best = i; } });
    return best;
  }

  /** A time axis's labels: each month start after `start` up to `end` ("Nov"), every nth shown so they fit. */
  function monthLabels(start, end, plotW, x) {
    const months = [];
    const [sy, sm] = start.split("-").map(Number);
    for (let k = 1; ; k++) {
      const t = (sy * 12 + sm - 1) + k;
      const iso = `${Math.floor(t / 12)}-${pad2((t % 12) + 1)}-01`;
      if (iso > end) break;
      months.push(iso);
    }
    const every = Math.max(1, Math.ceil(months.length / Math.max(2, Math.floor(plotW / 56))));
    return months.map((iso, i) => ({ date: iso, x: x(iso), text: MON[Number(iso.slice(5, 7)) - 1], show: (months.length - 1 - i) % every === 0 }));
  }

  // ---------- investments ----------

  /** The tabs to show: Investments only with an investment account, Meal card only with a meal card
   *  (`has`: {investments, meals}), either also when it is the view asked for. */
  const visibleTabs = (has, current) => TABS.filter((t) => !OPTIONAL_TABS.includes(t) || (has && has[t]) || current === t);
  /** Whether a view follows the header's month switcher (Save and Investments don't, so it hides there). */
  const usesMonth = (tab) => !MONTHLESS_TABS.includes(tab);
  const kindIcon = (kind) => (ACCOUNT_KINDS[kind] || ACCOUNT_KINDS.card).icon;
  const kindWord = (kind) => (ACCOUNT_KINDS[kind] || ACCOUNT_KINDS.card).word;
  /** A percentage: "+6.98%", "−3.62%", "0%" (two decimals under 10, one above; `digits` for fewer);
   *  `sign: "none"` drops the plus; "" without a number. */
  function pctText(p, o = {}) {
    if (p == null || !isFinite(p)) return "";
    const abs = Math.abs(p);
    const digits = Math.min(o.digits ?? 2, abs >= 10 ? 1 : 2);
    if (Number(abs.toFixed(digits)) === 0) return "0%";
    const body = abs.toFixed(digits).replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "") + "%";
    return (p < 0 ? MINUS : o.sign === "none" ? "" : "+") + body;
  }
  /** Units held or traded: "120", "15.5", "1,234.25". */
  function quantityText(q) {
    if (q == null || !isFinite(q)) return "";
    const abs = Math.abs(q), whole = Math.floor(abs + 1e-9);
    const frac = String(Math.round((abs - whole) * 10000) / 10000).replace(/^0/, "");
    return (q < 0 ? MINUS : "") + group3(whole) + (frac === "0" ? "" : frac);
  }
  /** A unit price in its own currency (a decimal number, not minor units; none: the user's): "₪250.00",
   *  "$150.00", "CHF 12.50"; four decimals below 1, trailing zeros after two dropped. */
  function unitPrice(price, currency) {
    if (price == null || !isFinite(price)) return "";
    const abs = Math.abs(price);
    const f = abs.toFixed(abs >= 1 ? 2 : 4).split(".")[1].replace(/(\d\d\d*?)0+$/, "$1");
    return formatUnits(abs, currencyCode(currency), f.length);
  }
  /** The four tiles of GET /api/money/portfolio (its total; the site's own profit % with one account):
   *  value, today's change, profit over cost, cash. */
  function portfolioTiles(p, now) {
    const t = (p && p.total) || {};
    const accounts = (p && p.accounts) || [];
    const value = t.value || 0;
    const morning = t.day_change != null ? value - t.day_change : null;
    const dayPct = t.day_change != null && morning > 0 ? (t.day_change / morning) * 100 : null;
    const profitPct = accounts.length === 1 && accounts[0].profit_pct != null ? accounts[0].profit_pct
      : t.profit != null && t.cost > 0 ? (t.profit / t.cost) * 100 : null;
    const tone = (v) => (v == null || v === 0 ? "" : v < 0 ? "neg" : "in");
    return {
      value: { value, sub: t.as_of ? `Updated ${when(t.as_of, now)}` : "" },
      today: { value: t.day_change, tone: tone(t.day_change), sub: t.day_change == null ? "No change from the site yet" : pctText(dayPct) ? `${pctText(dayPct)} since the morning` : "since the morning" },
      profit: { value: t.profit, tone: tone(t.profit), sub: t.profit == null ? "No profit figure from the site yet" : pctText(profitPct) ? `${pctText(profitPct)} over what you paid` : "over what you paid" },
      cash: { value: t.cash || 0, sub: value > 0 ? `${pctText(((t.cash || 0) / value) * 100, { sign: "none", digits: 1 })} of the portfolio` : "" },
    };
  }
  // ---------- meal card ----------

  /** "22 Oct – 21 Nov" (with the year when it is not `today`'s). */
  function cycleLabel(c, today) {
    if (!c || !isDay(c.start) || !isDay(c.end)) return "";
    return `${shortDate(c.start, today)} – ${shortDate(c.end, today)}`;
  }
  /** The four tiles of GET /api/money/meals (its first card's current cycle): what is left, the pace,
   *  what went on the user's own card this cycle, meals this cycle. Null without a card. */
  function mealTiles(m, today) {
    const a = ((m && m.accounts) || [])[0];
    if (!a || !a.current) return null;
    const c = a.current;
    const left = c.left;
    const days = c.days_left;
    let pace;
    if (left === 0 && c.count) pace = { text: "Used up", tone: "neg", sub: days ? `${days} ${days === 1 ? "day" : "days"} until it renews on ${shortDate(addDays(c.end, 1), today)}` : "It renews tomorrow" };
    else if (c.runs_out) pace = { text: `Lasts to ${shortDate(c.runs_out, today)}`, tone: "neg", sub: `at this cycle's pace; it renews on ${shortDate(addDays(c.end, 1), today)}` };
    else if (left) pace = { text: `${moneyText(c.per_day_left, { cents: false })} a day`, tone: "", sub: c.used ? "on track to last the cycle" : "nothing used yet this cycle" };
    else pace = { text: "—", tone: "", sub: "" };
    return {
      left: { value: left, sub: c.budget ? `of ${moneyText(c.budget, { cents: false })} · until ${shortDate(c.end, today)}` : `until ${shortDate(c.end, today)}` },
      pace,
      card: { value: c.card_paid || 0, sub: c.card_paid ? "beyond the budget, on your own card" : "nothing beyond the budget" },
      count: { value: c.count, sub: c.used ? `${moneyText(c.used, { cents: false })} from the budget` : "this cycle" },
    };
  }
  /** Bars for the cycles (oldest first, at most `n`): each cycle's budget part and own-card part as % of
   *  the largest cycle, with its label and what its card line says. */
  function mealCycleBars(cycles, today, n = 12) {
    const list = (cycles || []).slice(0, n).reverse();
    const top = Math.max(1, ...list.map((c) => c.total || 0));
    return list.map((c) => ({
      start: c.start, label: cycleLabel(c, today), count: c.count, total: c.total, budget: c.budget_paid, card: c.card_paid,
      budgetPct: (100 * (c.budget_paid || 0)) / top, cardPct: (100 * (c.card_paid || 0)) / top,
      billed: c.card_paid ? (c.billed ? `On your card ${shortDate(c.billed.date, today)}` : "Not on your card yet") : "",
      txn: c.billed ? c.billed.txn_id : null,
    }));
  }

  /** The portfolio value chart (GET /api/money/portfolio history: [{date, value}] by day): a line with
   *  a soft area under it, y ticks around the range (not from zero: the change is the story), the last
   *  `days` days only, month starts on the x axis. `change`: the value's move over the range and `moved`,
   *  the money put in (+) or taken out (−) in it (`size.flows`: the payload's flows); `marks`: where that
   *  money moved, on the line (each point's `flow` too, for hover; a weekend's lands on the next day). */
  function valueGeometry(history, size = {}) {
    let pts = (history || []).filter((h) => h && isDay(h.date) && h.value != null);
    if (size.days && pts.length) { const from = addDays(pts[pts.length - 1].date, -size.days); pts = pts.filter((h) => h.date >= from); }
    if (!pts.length) return null;
    const W = size.width || 640, H = size.height || 220;
    const L = size.left ?? 56, R = size.right ?? 12, T = size.top ?? 12, B = size.bottom ?? 24;
    const start = pts[0].date, end = pts[pts.length - 1].date;
    const d0 = dayNum(start), span = Math.max(1, dayNum(end) - d0);
    const values = pts.map((p) => p.value);
    const lo = Math.min(...values), hi = Math.max(...values);
    const pad = Math.max((hi - lo) * 0.1, Math.abs(hi) * 0.005, 100);
    const ticks = niceTicks(Math.max(lo >= 0 ? 0 : -Infinity, lo - pad), hi + pad, size.tickCount || 4);
    const dLo = ticks[0], dHi = ticks[ticks.length - 1];
    const plotW = W - L - R, plotH = H - T - B;
    const x = (iso) => r1(L + (pts.length > 1 ? ((dayNum(iso) - d0) / span) * plotW : plotW));
    const y = (v) => r1(T + plotH - ((v - dLo) / (dHi - dLo)) * plotH);
    const points = pts.map((p) => ({ date: p.date, value: p.value, x: x(p.date), y: y(p.value) }));
    const line = points.map((p, i) => `${i ? "L" : "M"}${p.x},${p.y}`).join(" ");
    const area = points.length > 1 ? `${line} L${points[points.length - 1].x},${T + plotH} L${points[0].x},${T + plotH} Z` : "";
    const first = values[0], last = values[values.length - 1];
    let moved = 0;
    (size.flows || []).forEach((f) => {
      if (!f || !f.amount || !(f.date > start) || f.date > end) return;
      const p = points.find((q) => q.date >= f.date);
      if (!p) return;
      p.flow = (p.flow || 0) + f.amount;
      moved += f.amount;
    });
    return {
      W, H, L, R, T, B, plotW, plotH, lo: dLo, hi: dHi, line, area, points, x, y,
      ticks: ticks.map((v) => ({ v, y: y(v), text: compact(v), zero: v === 0 })),
      xLabels: monthLabels(start, end, plotW, x),
      change: { from: start, to: end, value: last - first, pct: first > 0 ? ((last - first) / first) * 100 : null, moved },
      marks: points.filter((p) => p.flow),
    };
  }
  /** The value's move over the chart's range in words: "Up ₪2,000 (+5.33%) since 9 Oct"; with money put
   *  in or taken out in the range, the move without it: "Down ₪18,000 since 9 Oct, besides ₪20,000 put in". */
  function valueChangeText(g, today) {
    if (!g || g.points.length < 2) return "";
    const c = g.change;
    const since = shortDate(c.from, today);
    if (c.moved) {
      const net = c.value - c.moved;
      const besides = `besides ${money(Math.abs(c.moved), { cents: false })} ${c.moved > 0 ? "put in" : "taken out"}`;
      return net ? `${net > 0 ? "Up" : "Down"} ${money(Math.abs(net), { cents: false })} since ${since}, ${besides}` : `No change since ${since}, ${besides}`;
    }
    if (!c.value) return `No change since ${since}`;
    return `${c.value > 0 ? "Up" : "Down"} ${money(Math.abs(c.value), { cents: false })}${c.pct != null ? ` (${pctText(c.pct)})` : ""} since ${since}`;
  }
  /** The allocation bar (GET /api/money/portfolio allocation: [{name, value, share}], the largest
   *  holdings, then "Other holdings" and "Cash"): each slice's width in percent of the bar and its colour
   *  slot (`a0`… by rank, `rest`, `cash`); holdings beyond `max` fold into the rest. */
  function allocationSlices(allocation, max = 7) {
    const rows = (allocation || []).filter((a) => a && a.value > 0);
    const cash = rows.filter((a) => a.name === "Cash");
    const restIn = rows.filter((a) => a.name === "Other holdings");
    const held = rows.filter((a) => !cash.includes(a) && !restIn.includes(a)).sort((a, b) => b.value - a.value);
    const total = rows.reduce((s, a) => s + a.value, 0);
    if (!total) return [];
    const top = held.slice(0, max), rest = [...held.slice(max), ...restIn];
    const out = top.map((a, i) => ({ name: a.name, value: a.value, slot: `a${i}` }));
    if (rest.length) out.push({ name: "Other holdings", value: rest.reduce((s, a) => s + a.value, 0), slot: "rest" });
    cash.forEach((a) => out.push({ name: "Cash", value: a.value, slot: "cash" }));
    return out.map((a) => ({ ...a, share: a.value / total, width: r1((a.value / total) * 100), text: pctText((a.value / total) * 100, { sign: "none", digits: 1 }) }));
  }
  const TRADE_WORDS = {
    buy: "Bought", sell: "Sold", dividend: "Dividend", interest: "Interest", deposit: "Money in", withdrawal: "Money out",
    tax: "Tax", split: "Split", redemption: "Redemption", benefit: "Benefit", expiry: "Expired",
  };
  /** One row of the account activity: {title, name, meta, amount, tone}. The site's own words (Hebrew)
   *  when the kind is unknown; the amount in shekels (in "in", out "out"). */
  function tradeText(t, today) {
    const word = TRADE_WORDS[t.kind];
    const title = word || t.action || "Activity";
    // Units only for a security's rows: the cash account's (deposits, interest) count shekels as units.
    const units = t.quantity && t.security && t.kind !== "deposit" && t.kind !== "withdrawal";
    const meta = [shortDate(t.date, today), units ? `${quantityText(Math.abs(t.quantity))} units` : "",
                  t.commission ? `fee ${money(Math.abs(t.commission), { sign: "none" })}` : "",
                  t.tax && t.kind !== "tax" ? `tax ${money(Math.abs(t.tax), { sign: "none" })}` : "",
                  word && t.action && t.kind !== "buy" && t.kind !== "sell" ? t.action : ""].filter(Boolean);
    return { title, name: t.name || "", meta, amount: t.amount || 0, tone: !t.amount ? "" : t.amount > 0 ? "in" : "out" };
  }

  // ---------- savings (GET /api/money/savings, docs/money.md "Savings") ----------

  const SAVE_PARTS = [["save", "Ways to save"], ["watch", "Spending to watch"]];
  const SAVE_SECTIONS = [["now", "Now"], ["coming", "Coming up"], ["doing", "In progress"], ["watching", "Watching"], ["done", "Done"], ["choices", "Your choices"]];
  // at_stake is money a year, except a spree's: the run's total.
  const AT_STAKE_ONCE = ["spree"];
  const BASIS = {
    measured: { text: "measured", title: "Measured from your own charges" },
    increase: { text: "price rise", title: "What the price rise costs a year" },
    market: { text: "market", title: "Against the offers a price check found" },
  };
  const FX_KINDS = { card: "Card", wallet: "Wallet", bank: "Bank account" };
  // Whose site to confirm a price check's offers on, by topic.
  const CONFIRM_ON = { telecom: "the provider's site", electricity: "the supplier's site", fx_card: "the issuer's site", arnona: "the municipality's site" };
  const whole = (v) => money(v, { cents: false });
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
  /** The viewer's local day of an aware ISO time ("2026-11-18T19:41:45+00:00" → "2026-11-18"). */
  const localDay = (iso) => (iso && isFinite(Date.parse(iso)) ? dayIso(new Date(Date.parse(iso))) : "");
  /** "example.com" from a link (no "www."), "" when it isn't one. */
  function hostOf(url) {
    const m = /^https?:\/\/([^/?#:]+)/i.exec(String(url || ""));
    return m ? m[1].replace(/^www\./i, "").toLowerCase() : "";
  }
  /** A source link the page may open: {title, url} for http(s) only, else null. */
  const safeSource = (s) => (s && hostOf(s.url) ? { title: String(s.title || "") || hostOf(s.url), url: String(s.url) } : null);

  /** The section an item is listed in: an open one by its timing (Coming up while timing.from is ahead,
   *  else Now), the rest by status. Dismissed, accepted and snoozed items — and a status the page doesn't
   *  know — are the user's choices, so no item is ever left off the page. */
  function saveSection(item, today) {
    switch (item.status) {
      case "open": return item.timing && item.timing.from && today && item.timing.from > today ? "coming" : "now";
      case "doing": return "doing";
      case "watching": return "watching";
      case "done": return "done";
      default: return "choices";
    }
  }
  /** The Save view's two parts, Ways to save and Spending to watch, each with its non-empty sections in
   *  order and the server's order inside a section: [{group, label, count, sections: [{key, label, items}]}]. */
  function saveSections(items, today) {
    return SAVE_PARTS.map(([group, label]) => {
      const mine = (items || []).filter((it) => (it.group === "watch" ? "watch" : "save") === group);
      const sections = SAVE_SECTIONS.map(([key, l]) => ({ key, label: l, items: mine.filter((it) => saveSection(it, today) === key) }))
        .filter((s) => s.items.length);
      return { group, label, count: mine.length, sections };
    });
  }

  /** The two tiles and the source line: {could: {text, sub}, saved: {value, sub}, source}. */
  function saveTiles(sv, today) {
    const t = (sv && sv.totals) || {};
    const lo = Math.min(t.low || 0, t.high || 0), hi = Math.max(t.low || 0, t.high || 0);
    const n = t.count || 0;
    const r = sv && sv.rate;
    return {
      could: { text: whole(lo) === whole(hi) ? whole(hi) : `${whole(lo)}–${whole(hi)}`, sub: n ? `a year · ${plural(n, "idea")}` : "No open idea with a saving right now" },
      saved: { value: t.saved || 0, sub: t.saved_yearly ? `≈ ${whole(t.saved_yearly)} a year` : t.saved ? "" : "Counted once something you did shows in your charges" },
      source: r && r.policy != null
        ? `Bank of Israel rate ${pctText(r.policy, { sign: "none" })}${r.date ? ` (${shortDate(r.date, today)})` : ""} · amounts from your charges`
        : "Amounts from your charges",
    };
  }

  /** The collapsed card's money chip: {kind: "save", text: "+₪476/yr"} (a range "+₪768–₪888/yr"), or
   *  without a saving {kind: "stake", text: "₪3,520/yr at stake"} (a spree: "₪615 spent"); null without either. */
  function savingChip(item) {
    const s = item.saving;
    if (s && (s.low || s.high)) {
      const lo = Math.min(s.low || 0, s.high || 0), hi = Math.max(s.low || 0, s.high || 0);
      return { kind: "save", text: whole(lo) === whole(hi) ? `+${whole(hi)}/yr` : `+${whole(lo)}–${whole(hi)}/yr`, title: "What it could save a year" };
    }
    if (item.at_stake) {
      if (AT_STAKE_ONCE.includes(item.type)) return { kind: "stake", text: `${whole(item.at_stake)} spent`, title: "What it came to" };
      return { kind: "stake", text: `${whole(item.at_stake)}/yr at stake`, title: "The money a year this is about" };
    }
    return null;
  }
  /** "10 min", "1 h", "1.5 h". */
  const minutesText = (m) => (m < 60 ? `${m} min` : `${Number.isInteger(m / 60) ? m / 60 : (m / 60).toFixed(1)} h`);
  /** The effort chip: "10-min call", "2 min in the app", "15 min online", "30 min comparing", "10 min to review"; "" without one. */
  function effortText(effort) {
    if (!effort || !(effort.minutes > 0)) return "";
    const m = Math.round(effort.minutes);
    const t = minutesText(m);
    switch (effort.how) {
      case "call": return m < 60 ? `${m}-min call` : `${t} call`;
      case "app": return `${t} in the app`;
      case "online": return `${t} online`;
      case "compare": return `${t} comparing`;
      case "review": return `${t} to review`;
      default: return t;
    }
  }
  /** A saving's basis as a chip: {text: measured | price rise | market, title}; null without a saving. */
  const basisChip = (item) => (item.saving && BASIS[item.saving.basis]) || null;

  /** Where the user's choice stands, with its date: "On it since 18 Nov", "Done 18 Nov", "Not for you · 18 Nov",
   *  "Worth it to you · 18 Nov", "Snoozed until 18 Dec", "Watching since 10 Sep"; "" for an open item. */
  function statusLine(item, today) {
    const d = localDay(item.status_at);
    const on = d ? shortDate(d, today) : "";
    switch (item.status) {
      case "doing": return on ? `On it since ${on}` : "On it";
      case "done": return on ? `Done ${on}` : "Done";
      case "dismissed": return on ? `Not for you · ${on}` : "Not for you";
      case "accepted": return on ? `Worth it to you · ${on}` : "Worth it to you";
      case "snoozed": return item.snooze_until ? `Snoozed until ${shortDate(item.snooze_until, today)}` : "Snoozed";
      case "watching": return on ? `Watching since ${on}` : "Watching";
      case "open": return "";
      default: return item.status ? String(item.status) : "";
    }
  }
  /** The buttons an item offers in its status: [{key, label, body (the PATCH), primary?, input?}]. An
   *  `input` action opens the monthly target field (its PATCH is {target} in agorot). Every status has a
   *  way back: {status: "open"} gives the item back to Dana. */
  function statusActions(item) {
    const A = (key, label, body, o = {}) => ({ key, label, body, ...o });
    const watch = item.group === "watch";
    switch (item.status) {
      case "open": return watch
        ? [A("target", "Cut back", null, { input: true, primary: true }), A("accept", "Worth it", { status: "accepted" })]
        : [A("doing", "I'm on it", { status: "doing" }, { primary: true }), A("done", "I did it", { status: "done" }),
           A("snooze", "Snooze a month", { status: "snoozed", snooze_days: 30 }), A("dismiss", "Not for me", { status: "dismissed" })];
      case "doing": return [A("done", "I did it", { status: "done" }, { primary: true }), A("open", "Not doing it", { status: "open" })];
      case "done": return [A("open", "Not done", { status: "open" })];
      case "dismissed": case "accepted": return [A("open", "Flag again", { status: "open" })];
      case "snoozed": return [A("open", "Unsnooze", { status: "open" })];
      case "watching": return [A("target", "Edit target", null, { input: true }), A("stop", "Stop tracking", { target: null })];
      default: return [A("open", "Dana decides again", { status: "open" })];
    }
  }
  /** The Undo toast after a PATCH: what changed, then the item. */
  function saveToast(item, body) {
    const b = body || {}, it = item || {};
    const name = isolateHebrew(it.title || "");
    if (b.subitem) {
      const sub = (it.subitems || []).find((s) => s.key === b.subitem.key);
      const said = b.subitem.choice === "keep" ? "keeping it" : b.subitem.choice === "cancel" ? "marked to cancel" : "no choice";
      return `${isolate(sub ? sub.merchant : b.subitem.key)}: ${said}`;
    }
    if (b.answers) return `Answer saved · ${name}`;
    if ("target" in b) return b.target == null ? `Stopped tracking · ${name}` : `Target ${whole(b.target)} a month · ${name}`;
    const words = { doing: "On it", done: "Done", dismissed: "Not for you", accepted: "Worth it", open: "Dana decides again",
                    snoozed: it.snooze_until ? `Snoozed until ${shortDate(it.snooze_until)}` : "Snoozed" };
    return `${words[b.status] || "Saved"} · ${name}`;
  }
  /** Whole units typed for a monthly target ("650", "₪1,200", "1,200 €") → hundredths; null when
   *  it isn't a whole number from 1 to 1,000,000. */
  function targetAgorot(text) {
    const s = String(text ?? "").replace(/[,\s]/g, "").replace(/^\p{Sc}|\p{Sc}$/u, "");
    if (!/^\d{1,7}$/.test(s)) return null;
    const n = Number(s);
    return n >= 1 && n <= 1000000 ? n * 100 : null;
  }

  /** What the evidence link opens: {tab, f (the Transactions filters, over all time) | null, label}. */
  function savingLink(link) {
    if (!link || !link.view) return null;
    if (link.view === "transactions") {
      const f = cleanFilters({ q: link.q || "", category: link.category || "", all: true });
      return { tab: "transactions", f, label: f.q || f.category ? "See these charges" : "Open Transactions" };
    }
    return TABS.includes(link.view) ? { tab: link.view, f: null, label: `Open ${TAB_LABELS[link.view]}` } : null;
  }
  /** The evidence sparkline (series [{date, amount}], oldest first) in a W × H box: a line over a soft
   *  area from zero, x by date; {W, H, line, area, last: {x, y}, range: "10 Oct 2025 – 10 Nov"}; null under 2 points. */
  function seriesSpark(series, o = {}, today) {
    const pts = (series || []).filter((p) => p && isDay(p.date) && p.amount != null && isFinite(p.amount));
    if (pts.length < 2) return null;
    const W = o.width || 160, H = o.height || 40, P = o.pad ?? 3;
    const d0 = dayNum(pts[0].date), span = Math.max(1, dayNum(pts[pts.length - 1].date) - d0);
    const vals = pts.map((p) => p.amount);
    const lo = Math.min(0, ...vals), hi = Math.max(...vals);
    const top = hi > lo ? hi : lo + 1;
    const x = (iso) => r1(P + ((dayNum(iso) - d0) / span) * (W - 2 * P));
    const y = (v) => r1(H - P - ((v - lo) / (top - lo)) * (H - 2 * P));
    const xy = pts.map((p) => [x(p.date), y(p.amount)]);
    const line = xy.map(([a, b], i) => `${i ? "L" : "M"}${a},${b}`).join(" ");
    const base = y(Math.max(lo, 0));
    const last = xy[xy.length - 1];
    return { W, H, line, area: `${line} L${last[0]},${base} L${xy[0][0]},${base} Z`, last: { x: last[0], y: last[1] },
             range: `${shortDate(pts[0].date, today)} – ${shortDate(pts[pts.length - 1].date, today)}`, count: pts.length };
  }

  /** The Prices block's state in words (an item's `market`): {state, text, button: "Check prices" |
   *  "Check again" | "Try again" | null, offers (there are offers to show: the last good check's, also
   *  while a new one runs or after one fails), checked: "Checked 18 Nov with a web search · confirm on the
   *  provider's site", stale, question (the id to answer first)}; null without a price check. */
  function marketView(m, today) {
    if (!m) return null;
    const offers = (m.items || []).length > 0;
    const out = {
      state: m.state || "none", text: "", button: null, offers, question: m.needs_answer || null,
      checked: m.checked_at ? `Checked ${shortDate(localDay(m.checked_at), today)} with a web search · confirm on ${CONFIRM_ON[m.topic] || "the provider's site"}` : "",
      stale: offers && m.stale ? "prices may have changed" : "",
    };
    switch (out.state) {
      case "checking": out.text = "Checking prices… this can take a minute or two."; break;
      case "ok": out.button = "Check again"; break;
      case "failed": out.text = m.error ? `The last check didn't work: ${m.error}.` : "The last check didn't work."; out.button = offers ? "Check again" : "Try again"; break;
      case "unavailable": out.text = "Price checks aren't available right now."; break;
      default:
        if (out.question) out.text = "To check prices, answer this first:";
        else { out.text = "See what others charge today. Dana searches the web only when you press the button."; out.button = "Check prices"; }
    }
    return out;
  }
  /** POST …/check's answer: {kind: "checking" (202, or 409: one already runs) | "question" (answer it
   *  first) | "wait" (429) | "error", text, question}. */
  function checkReply(status, body) {
    const b = body || {};
    if (status === 202 || status === 409) return { kind: "checking", text: "" };
    if (status === 400 && b.error === "needs_answer") return { kind: "question", text: "Answer this first, then check prices.", question: b.question || null };
    if (status === 429) {
      const s = Math.max(60, Number(b.retry_after) || 0);
      return { kind: "wait", text: `Prices were checked a short while ago. Try again in ${s < 3600 ? `${Math.ceil(s / 60)} min` : `${Math.ceil(s / 3600)} h`}.` };
    }
    if (status === 503) return { kind: "error", text: "Price checks aren't available right now." };
    if (status === 400 && b.error === "no_check") return { kind: "error", text: "This idea has no price check." };
    return { kind: "error", text: errorText(status, b) };
  }
  /** A price check's offers as a table: {topic, columns, hasPlan, rows: [{name, plan, cells: [{label,
   *  text, sub?, strong?, muted?, dir?, iso? (an English line with Hebrew runs: isolate them)}], source:
   *  {title, url} | null}], sources}; a column empty in every row is left out. Every number is the
   *  check's own; telecom's "after the promo" is the regular price once the promotion ends. */
  function offerRows(m, today) {
    const t = offerTable(m, today);
    if (!t || !t.rows.length) return t;
    const keep = t.rows[0].cells.map((_, i) => t.rows.some((r) => r.cells[i] && r.cells[i].text));
    const first = t.hasPlan ? 2 : 1;
    return { ...t, columns: t.columns.filter((_, i) => i < first || i >= t.columns.length - 1 || keep[i - first]),
             rows: t.rows.map((r) => ({ ...r, cells: r.cells.filter((_, i) => keep[i]) })) };
  }
  function offerTable(m, today) {
    if (!m) return null;
    const items = m.items || [];
    const sources = (m.sources || []).map(safeSource).filter(Boolean);
    const mo = (v) => `${money(v)} a month`;
    const pctOff = (p) => `${pctText(p, { sign: "none", digits: 1 })} off`;
    const base = (name, plan, cells, it) => ({ name: String(name || ""), plan: String(plan || ""), cells, source: safeSource(it.source) });
    switch (m.topic) {
      case "telecom": return {
        topic: m.topic, hasPlan: true, sources,
        columns: ["Provider", "Plan", "12-month average", "After the promo", "Conditions", "Source"],
        rows: items.map((it) => base(it.provider, it.plan, [
          { label: "12-month average", text: mo(it.avg_12), strong: true },
          it.promo_price != null && it.promo_months
            ? { label: "After the promo", text: `${mo(it.price)} after ${plural(it.promo_months, "month")}`, sub: `${mo(it.promo_price)} until then` }
            : { label: "After the promo", text: "No promo", sub: mo(it.price), muted: true },
          { label: "Conditions", text: it.conditions || "", dir: "auto" }], it)),
      };
      case "electricity": return {
        topic: m.topic, hasPlan: true, sources,
        columns: ["Supplier", "Plan", "Discount", "Hours", "Meter", "Source"],
        rows: items.map((it) => base(it.supplier, it.plan, [
          { label: "Discount", text: pctOff(it.discount_pct), strong: true },
          { label: "Hours", text: [it.hours, it.days].filter(Boolean).join(" · "), iso: true },
          { label: "Meter", text: it.smart_meter ? "Smart meter needed" : "Any meter" }], it)),
      };
      case "fx_card": return {
        topic: m.topic, hasPlan: false, sources,
        columns: ["Card or wallet", "Kind", "Conversion fee", "Monthly fee", "Conditions", "Source"],
        rows: items.map((it) => base(it.name, "", [
          { label: "Kind", text: FX_KINDS[it.kind] || "" },
          { label: "Conversion fee", text: pctText(it.fx_fee_pct, { sign: "none" }), strong: true },
          { label: "Monthly fee", text: it.monthly_fee ? mo(it.monthly_fee) : "None" },
          { label: "Conditions", text: it.conditions || "", dir: "auto" }], it)),
      };
      case "arnona": return {
        topic: m.topic, hasPlan: false, sources,
        columns: ["Result", "Discount", "Deadline", "How", "Source"],
        rows: items.map((it) => base(it.found ? "A discount for paying the year at once" : "No discount found for paying at once", "", [
          { label: "Discount", text: it.found && it.discount_pct != null ? pctOff(it.discount_pct) : "—", strong: Boolean(it.found) },
          { label: "Deadline", text: it.deadline ? shortDate(it.deadline, today) : "—" },
          { label: "How", text: it.method || "", dir: "auto" }], it)),
      };
      default: return {
        topic: m.topic, hasPlan: false, sources, columns: ["Offer", "Source"],
        rows: items.map((it) => base(it.name || it.provider || it.supplier, "", [], it)),
      };
    }
  }
  /** An outcome in words: {tone: good | warn | wait, title, sub, detail}; null without one. */
  function outcomeText(o) {
    if (!o) return null;
    const detail = o.detail || "";
    if (o.state === "confirmed") {
      return { tone: "good", title: o.saved ? `Saved ${whole(o.saved)} so far` : "It worked", sub: o.saved_yearly ? `≈ ${whole(o.saved_yearly)} a year` : "", detail };
    }
    if (o.state === "no_change") return { tone: "warn", title: "No change in your charges yet", sub: "", detail };
    return { tone: "wait", title: "Waiting to see it in your charges", sub: "", detail };
  }
  /** A watched target's progress: {bar (0–100, this month against the target), over, text: "₪300 of
   *  ₪700 this month", track: "On track" | "Off track" | "Over the target" | "", tone, left: "12 days left",
   *  months: [{month, label, text, under}], saved: "₪200 saved so far" | ""}; null without a target. */
  function targetProgress(t) {
    if (!t) return null;
    const monthly = t.monthly || 0, spent = t.this_month || 0;
    const over = monthly > 0 && spent > monthly;
    return {
      bar: monthly > 0 ? Math.min(100, Math.round((spent / monthly) * 100)) : 0, over,
      text: `${whole(spent)} of ${whole(monthly)} this month`,
      track: over ? "Over the target" : t.on_track === true ? "On track" : t.on_track === false ? "Off track" : "",
      tone: over || t.on_track === false ? "bad" : t.on_track === true ? "good" : "",
      left: t.days_left != null ? `${plural(t.days_left, "day")} left` : "",
      months: (t.months || []).map((m) => ({ month: m.month, label: monthLabel(m.month, { short: true, year: false }),
                                             text: `${whole(m.spent)} of ${whole(m.target)}`, under: Boolean(m.under) })),
      saved: t.saved ? `${whole(t.saved)} saved so far` : "",
    };
  }
  /** A subscription line: {amount: "₪74.20 / month", yearly: "₪890 a year", next: "next 12 Dec" | the
   *  reason it isn't charged any more}. */
  function subitemText(s, today) {
    return {
      amount: `${money(s.amount || 0, { sign: "none" })} ${perText(s.cadence)}`.trim(),
      yearly: s.yearly ? `${whole(s.yearly)} a year` : "",
      next: s.active === false ? (s.choice === "cancel" ? "No charge since you cancelled" : "Not charged lately")
        : s.next_date ? `next ${shortDate(s.next_date, today)}` : "",
    };
  }

  // ---------- accounts ----------

  /** "Cal ••8763","Shared card ••4580" (a label of the user's own), "Hapoalim ••901". */
  function accountName(a) {
    if (!a) return "";
    const digits = String(a.number || "").replace(/\D/g, "");
    const short = a.kind === "bank" || a.kind === "investment";
    const last = digits.slice(short ? -3 : -4);
    const company = a.company_name || "";
    const custom = a.label && !(company && a.label.startsWith(company)) ? a.label : "";
    const name = custom || (a.kind === "bank" ? company.replace(/^Bank\s+/, "") : company) || kindWord(a.kind);
    return last ? `${name} ••${last}` : name;
  }

  // ---------- transactions ----------

  const counts = (t) => t.spending && t.status !== "unmatched";
  const isIncome = (t) => INCOME_FLOWS.includes(t.flow) && t.status !== "unmatched";

  /** The list grouped by purchase date, in the server's order (newest first): [{date, items, spent,
   *  income}] — spent = what the day's counted rows add to spending (refunds reduce it), income likewise. */
  function groupByDay(txns) {
    const out = [];
    let cur = null;
    (txns || []).forEach((t) => {
      if (!cur || cur.date !== t.purchase_date) { cur = { date: t.purchase_date, items: [], spent: 0, income: 0 }; out.push(cur); }
      cur.items.push(t);
      if (counts(t)) cur.spent -= t.amount;
      else if (isIncome(t)) cur.income += t.amount;
    });
    return out;
  }
  /** Appends a page, dropping rows already there (a refetch may overlap). */
  function mergeTxns(have, page) {
    const seen = new Set((have || []).map((t) => t.id));
    return (have || []).concat((page || []).filter((t) => !seen.has(t.id)));
  }
  /** Rows the "uncategorized" filter shows: a card bill and an own transfer take no category, so they
   *  are not waiting for one (the server's filter is category IS NULL). */
  const needsCategory = (t) => !t.category && !FLOW_CHIPS[t.flow];

  /** How the amount reads: "out" (spending), "in" (money in), "muted" (not counted: unmatched, card
   *  bills, own transfers, a bank line with no flow). */
  function amountTone(t) {
    if (t.status === "unmatched") return "muted";
    if (t.amount > 0) return t.spending || isIncome(t) ? "in" : "muted";
    return t.spending ? "out" : "muted";
  }

  /** The row's badges: [{type, text, title}]. `company` names the card company ("Cal"). */
  function badges(t, company) {
    const who = company || "the card company";
    const out = [];
    (t.badges || []).forEach((b) => {
      if (b.type === "provisional") out.push({ type: "provisional", text: `Waiting for ${who}`, title: `From your phone's payment notification. It counts now; ${who} hasn't listed it yet.` });
      else if (b.type === "pending") out.push({ type: "pending", text: "Pending", title: `${who} lists it as pending: the amount or date may still change.` });
      else if (b.type === "unmatched") out.push({ type: "unmatched", text: "Not charged?", title: "Your phone saw this payment but it never appeared on the card: probably declined or reversed. Not counted." });
      else if (b.type === "installment") out.push({ type: "installment", text: `${b.number}/${b.total}`, title: `Payment ${b.number} of ${b.total}` });
      else if (b.type === "foreign") out.push({ type: "foreign", text: foreignText(b.amount, b.currency), title: `Paid in ${b.currency}` });
    });
    if (t.amount > 0 && t.flow === "refund") out.push({ type: "refund", text: "Refund", title: "Money back: it reduces spending" });
    return out;
  }
  const flowChip = (t) => (!t.category && FLOW_CHIPS[t.flow]) || null;
  const categorySourceLabel = (src) => CATEGORY_SOURCES[src] || "";

  /** Why a row is shown the way it is (the detail's explanation), or "". */
  function statusExplain(t, company) {
    const who = company || "the card company";
    if (t.status === "provisional") return `Your phone saw this payment${t.occurred_at ? ` at ${timeOf(t.occurred_at)}` : ""}. It counts now; when ${who} lists it, the two become one row.`;
    if (t.status === "unmatched") return `Your phone saw this payment, but ${who} never listed it within 5 days: probably declined or reversed. It isn't counted.`;
    if (t.status === "pending") return `${who} lists it as pending: the amount or date may still change.`;
    if (t.flow === "card_bill") return "The bank's line for a card bill. The card's own transactions are the spending, so this isn't counted twice.";
    if (t.flow === "own_transfer") return "Money moved between your own accounts: not spending.";
    if (!t.spending && t.amount < 0 && !t.category) return "Not counted as spending until it has a category.";
    return "";
  }

  // ---------- filters ↔ query string / hash ----------

  const num = (v) => (v === "" || v == null || !isFinite(Number(v)) ? "" : String(Number(v)));
  function cleanFilters(f = {}) {
    return {
      q: String(f.q || "").trim(), category: String(f.category || ""), account: String(f.account || ""),
      min: num(f.min), max: num(f.max), all: Boolean(f.all),
    };
  }
  const hasFilters = (f) => { const c = cleanFilters(f); return Boolean(c.q || c.category || c.account || c.min || c.max); };
  function qs(pairs) {
    return pairs.filter(([, v]) => v !== "" && v != null && v !== false).map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&");
  }
  /** GET /api/money/transactions's query: the page's month unless "all time", then the filters. */
  function filtersToQuery(f, month, extra = {}) {
    const c = cleanFilters(f);
    return qs([["period", c.all ? "" : month], ["q", c.q], ["category", c.category], ["account", c.account],
               ["min", c.min], ["max", c.max], ["limit", extra.limit || ""], ["cursor", extra.cursor || ""]]);
  }
  /** The page's state as a hash: "#money", "#money/cashflow?month=2026-10", "#money/transactions?category=food&q=wolt&all=1",
   *  "#money?view=accounts" (the view in the path unless it is the Overview; the month only when it
   *  is not the current one; the filters whatever the view, so a category chosen on Spending stays). */
  function stateToHash(state, currentMonth) {
    const c = cleanFilters(state.f);
    const query = qs([["month", state.month && state.month !== currentMonth ? state.month : ""],
                      ["view", state.view === "sources" ? "accounts" : ""], ["q", c.q], ["category", c.category],
                      ["account", c.account], ["min", c.min], ["max", c.max], ["all", c.all ? "1" : ""]]);
    const tab = TABS.includes(state.tab) && state.tab !== TABS[0] ? "/" + state.tab : "";
    return "#money" + tab + (query ? "?" + query : "");
  }
  function stateFromHash(hash) {
    const h = String(hash || "");
    const m = /^#money(?:\/([a-z_]*))?(?:\?|$)/.exec(h);
    if (!m) return null;
    const params = {};
    h.slice(h.indexOf("?") + 1 || h.length).split("&").filter(Boolean).forEach((kv) => {
      const i = kv.indexOf("=");
      const k = decodeURIComponent(i < 0 ? kv : kv.slice(0, i)), v = i < 0 ? "" : decodeURIComponent(kv.slice(i + 1).replace(/\+/g, " "));
      params[k] = v;
    });
    return {
      month: isMonth(params.month) ? params.month : null,
      view: params.view === "accounts" ? "sources" : "page",
      tab: TABS.includes(m[1]) ? m[1] : TABS[0],
      f: cleanFilters({ q: params.q, category: params.category, account: params.account, min: params.min, max: params.max, all: params.all === "1" }),
    };
  }
  /** The active filters as removable chips: [{key, label}]. */
  function filterChips(f, ctx = {}) {
    const c = cleanFilters(f);
    const out = [];
    if (c.q) out.push({ key: "q", label: `“${c.q}”` });
    if (c.category) out.push({ key: "category", label: (ctx.categoryLabel && ctx.categoryLabel(c.category)) || c.category });
    if (c.account) out.push({ key: "account", label: (ctx.accountLabel && ctx.accountLabel(c.account)) || "One account" });
    if (c.min || c.max) {
      const a = c.min && money(Number(c.min) * 100, { cents: "auto" }), b = c.max && money(Number(c.max) * 100, { cents: "auto" });
      out.push({ key: "amount", label: a && b ? `${a} – ${b}` : a ? `${a} or more` : `up to ${b}` });
    }
    return out;
  }
  /** A group, a category or "uncategorized" → its label (the taxonomy of GET /api/money/categories). */
  function categoryLabel(taxonomy, key) {
    if (!key) return "";
    if (key === "uncategorized") return "Uncategorized";
    for (const g of (taxonomy && taxonomy.groups) || []) {
      if (g.key === key) return g.label;
      const c = g.categories.find((x) => x.key === key);
      if (c) return c.label;
    }
    return key;
  }
  /** The group a category key belongs to (or the key when it is a group). */
  function groupOf(taxonomy, key) {
    for (const g of (taxonomy && taxonomy.groups) || []) {
      if (g.key === key || g.categories.some((c) => c.key === key)) return g.key;
    }
    return null;
  }

  /** The category picker's list: groups with the categories matching `q` (English or Hebrew label, key;
   *  a matching group keeps all of its categories); money-in rows get the income group first. */
  function pickerGroups(taxonomy, q, o = {}) {
    const needle = String(q || "").trim().toLowerCase();
    const hit = (x) => !needle || [x.key, x.label, x.label_he].some((s) => String(s || "").toLowerCase().includes(needle));
    const groups = ((taxonomy && taxonomy.groups) || []).map((g) => ({ ...g, categories: hit(g) ? g.categories : g.categories.filter(hit) }))
      .filter((g) => g.categories.length);
    return groups.filter((g) => Boolean(g.income) === Boolean(o.income)).concat(groups.filter((g) => Boolean(g.income) !== Boolean(o.income)));
  }

  // ---------- words ----------

  /** A source's state for people: {tone: ok | busy | warn | bad, label, detail}. */
  function sourceStatus(s, now) {
    const lastOk = s.last_ok_at ? `last good sync ${ago(s.last_ok_at, now)}` : "";
    switch (s.state) {
      case "new": return { tone: "busy", label: "Starting the first sync…", detail: "The first sync reads a year back and takes a few minutes." };
      case "syncing": return { tone: "busy", label: "Syncing…", detail: lastOk ? `Last good sync ${ago(s.last_ok_at, now)}` : "The first sync reads a year back and takes a few minutes." };
      case "needs_code": return { tone: "warn", label: "Waiting for the SMS code", detail: s.state_reason || "" };
      case "needs_attention": return { tone: "bad", label: s.can_sync ? "Stopped: sign in again yourself" : "Login needs fixing", detail: s.state_reason || "" };
      case "failing": return { tone: "warn", label: "Last sync failed", detail: [s.state_reason, lastOk].filter(Boolean).join(" · ") };
      default: return { tone: "ok", label: s.last_ok_at ? `Synced ${ago(s.last_ok_at, now)}` : "Synced", detail: "" };
    }
  }
  const busy = (s) => s.state === "syncing" || s.state === "new" || s.state === "needs_code" || Boolean(s.code_request);
  /** The header's one line from the sources: {tone, text}. */
  function headerStatus(sources, now) {
    const list = sources || [];
    if (!list.length) return { tone: "", text: "" };
    if (list.some((s) => s.state === "syncing" || s.state === "new")) return { tone: "busy", text: "Syncing…" };
    const last = list.map((s) => s.last_ok_at).filter(Boolean).sort().pop();
    const bad = list.filter((s) => ["needs_attention", "needs_code", "failing"].includes(s.state)).length;
    const synced = last ? `Synced ${ago(last, now)}` : "Not synced yet";
    return { tone: bad ? "warn" : "ok", text: bad ? `${synced} · ${bad === 1 ? "1 needs" : `${bad} need`} attention` : synced };
  }

  const CADENCES = { weekly: "Weekly", monthly: "Monthly", bimonthly: "Every 2 months", yearly: "Yearly" };
  const PER = { weekly: "/ week", monthly: "/ month", bimonthly: "/ 2 months", yearly: "/ year" };
  const cadenceLabel = (c) => CADENCES[c] || c || "";
  /** "/ week", "/ month", "/ 2 months", "/ year": what one charge covers. */
  const perText = (c) => PER[c] || "";
  /** A recurring charge as its cadence bills it (GET /api/money/recurring: `amount` is one charge,
   *  `monthly_cost` its monthly equivalent): {amount: "₪117.30", per: "/ 2 months", monthly: "≈ ₪58.65 a
   *  month"} — `monthly` only when one charge is not a month's worth, so a bimonthly, weekly or yearly
   *  amount is never shown as a monthly one. */
  function cadenceAmount(item) {
    const c = item.cadence || "monthly";
    const amount = money(Math.abs(item.amount || 0), { sign: "none" });
    const monthly = c !== "monthly" && item.monthly_cost != null && Math.abs(item.monthly_cost) !== Math.abs(item.amount)
      ? `≈ ${money(Math.abs(item.monthly_cost), { sign: "none" })} a month` : "";
    return { amount, per: perText(c), monthly };
  }
  /** An installment plan (forecast.plans): {left: "3 payments left", rest: "₪2,127.00 to go", next: "next 10 Jan"}. */
  function planText(plan, today) {
    const n = (plan.dates || []).length || Math.max(0, (plan.of || 0) - (plan.number || 0));
    return {
      left: `${n} payment${n === 1 ? "" : "s"} left of ${plan.of}`,
      rest: `${money(Math.abs(plan.left || 0), { sign: "none" })} to go`,
      next: plan.dates && plan.dates[0] ? `next ${shortDate(plan.dates[0], today)}` : "",
    };
  }
  /** "₪39.90 → ₪49.90 since Nov" (+ the year when not `today`'s). */
  function priceChangeText(pc, today) {
    if (!pc) return "";
    const d = parseDay(pc.since);
    const year = today && String(today).slice(0, 4) !== String(d.getFullYear()) ? ` ${d.getFullYear()}` : "";
    return `${money(pc.from)} → ${money(pc.to)} since ${MON[d.getMonth()]}${year}`;
  }

  // ---------- dashboard words ----------

  /** Hebrew runs (a merchant, a name) isolated (FSI … PDI) inside a server-written English sentence,
   *  so an amount or date beside them keeps its place. */
  const HEB = /[\u0590-\u05FF\uFB1D-\uFB4F](?:[\u0590-\u05FF\uFB1D-\uFB4F\d\s'"׳״.,&\-–]*[\u0590-\u05FF\uFB1D-\uFB4F])?/g;
  const isolateHebrew = (text) => String(text ?? "").replace(HEB, (m) => FSI + m + PDI);

  const pct = (part, whole) => Math.round((part / whole) * 100);

  /** The Net tile (overview, and cashflow for the typical month): {label, value, tone, rate, sub, typical}. */
  function netTile(ov, cf) {
    const inProgress = Boolean(ov.period && ov.period.in_progress);
    const income = ov.income_month || 0, net = ov.net || 0;
    const rate = income > 0
      ? (net >= 0 ? `Kept ${pct(net, income)}% of income` : `${money(-net, { cents: false })} more out than in`)
      : inProgress ? "No income yet this month" : "No income that month";
    const typ = cf && cf.typical && cf.typical.income > 0 ? `typical ${money(cf.typical.net, { cents: false })}` : "";
    return { label: inProgress ? "Net so far" : "Net", value: net, tone: net < 0 ? "out" : "in", rate,
             sub: `Earned ${money(income, { cents: false })} · spent ${money(income - net, { cents: false })}`, typical: typ };
  }
  /** The Everyday spending tile: {value, track: "On track for ₪4,018" | "", typical: "typical ₪3,500" | "", dir: up|down|""}. */
  function everydayTile(ev, inProgress) {
    const e = ev || {};
    const track = inProgress && e.on_track != null ? `On track for ${money(e.on_track, { cents: false })}` : "";
    const typical = e.typical > 0 ? `typical ${money(e.typical, { cents: false })}` : "";
    const ref = inProgress ? e.on_track : e.total;
    const dir = e.typical > 0 && ref != null ? (ref > e.typical * 1.05 ? "up" : ref < e.typical * 0.95 ? "down" : "") : "";
    return { value: e.total || 0, track, typical, dir };
  }
  /** The Balance ahead tile from overview.forecast: {value, low, end, tone}; null without a forecast. */
  function balanceTile(fc, today) {
    if (!fc) return null;
    const sl = fc.soon_low;
    const days = (sl && sl.days) || 45;
    let low = "", tone = "";
    if (sl && sl.balance < fc.balance) {
      low = `Lowest in the next ${days} days: ${money(sl.balance, { cents: false })} on ${shortDate(sl.date, today)}`;
      tone = sl.balance < 0 ? "bad" : "";
    } else if (sl) low = `Stays above ${money(sl.balance, { cents: false })} for the next ${days} days`;
    const end = fc.end_balance != null && fc.end ? `${money(fc.end_balance, { cents: false })} by ${shortDate(fc.end, today)}` : "";
    return { value: fc.balance, low, end, tone };
  }
  /** The Fixed costs tile: {value (a month), share: "32% of a typical month's income" | "", yearly: "incl. ₪236 a month for yearly charges" | ""}. */
  function fixedTile(ov) {
    const v = ov.fixed_monthly || 0;
    const share = ov.income_typical > 0 ? `${pct(v, ov.income_typical)}% of a typical month's income`
      : ov.income_month > 0 ? `${pct(v, ov.income_month)}% of this month's income` : "";
    const yearly = ov.yearly_monthly > 0 ? `incl. ${money(ov.yearly_monthly, { cents: false })} a month for yearly charges` : "";
    return { value: v, share, yearly };
  }

  /** A trends group / category against its typical month: {dir: up|down|same|new|none, text}. While the
   *  month runs the comparison is what it is on track for. */
  function vsTypical(item, inProgress) {
    const value = inProgress && item.on_track != null ? item.on_track : item.total || 0;
    const typical = item.typical || 0;
    if (!value && !typical) return { dir: "none", text: "" };
    if (!typical) return { dir: "new", text: "usually nothing" };
    const d = value - typical;
    if (Math.abs(d) < Math.max(100, typical * 0.05)) return { dir: "same", text: "about typical" };
    return { dir: d > 0 ? "up" : "down", text: `${money(Math.abs(d), { cents: false })} ${d > 0 ? "above" : "below"} typical` };
  }

  /** A card bill's parts (forecast event `parts`) as lines, the empty ones left out. */
  const BILL_PARTS = [["known", "Already on the card"], ["installments", "Installments"], ["fixed", "Fixed charges"],
                      ["yearly", "Yearly charges"], ["everyday", "Everyday spending, estimated"]];
  const cardBillParts = (e) => BILL_PARTS.filter(([k]) => e.parts && e.parts[k] > 0).map(([key, label]) => ({ key, label, amount: e.parts[key] }));

  /** Coming up: the forecast's events in its first `days` days (default 45), each with its card bill
   *  parts, its range in words and the projected balance at the end of that day. */
  function upcoming(fc, days = 45) {
    if (!fc) return { events: [], end: null };
    const end = addDays(fc.as_of, days);
    const after = new Map((fc.series || []).map((s) => [s.date, s.balance]));
    const events = (fc.events || []).filter((e) => e.date <= end).map((e) => {
      const r = e.range && e.range[0] !== e.range[1] ? e.range.map((v) => Math.abs(v)).sort((a, b) => a - b) : null;
      return { ...e, parts: cardBillParts(e), items: e.items || [], range: r ? `${money(r[0], { cents: false })} – ${money(r[1], { cents: false })}` : "",
               after: after.has(e.date) ? after.get(e.date) : null };
    });
    return { events, end, series: (fc.series || []).filter((s) => s.date <= end) };
  }

  /** "12 months: earned ₪60,519, spent ₪21,777, set aside ₪0, net ₪38,742". */
  function cashflowTotalsText(cf) {
    const t = (cf && cf.totals) || {};
    const n = ((cf && cf.months) || []).length;
    return `${n} months: earned ${money(t.income || 0, { cents: false })}, spent ${money(t.spent || 0, { cents: false })}, `
      + `set aside ${money(t.set_aside || 0, { cents: false })}, net ${money(t.net || 0, { cents: false })}`;
  }
  /** What the forecast counts and leaves out, in one sentence. */
  function forecastNote(fc) {
    if (!fc) return "";
    const out = fc.left_out_total ? `${money(Math.abs(fc.left_out_total), { cents: false })} in the last 12 months` : "none in the last 12 months";
    return `Forecast leaves out one-offs (${out}), includes yearly charges and installment plans.`;
  }

  /** A row's kind as a small badge: one-off, yearly, fixed; null for everyday spending and what isn't spending. */
  function kindBadge(t) {
    if (t.kind === "one_off") return { type: "oneoff", text: "One-off", title: "A one-off: left out of everyday spending and the forecast" };
    if (t.kind === "yearly") return { type: "yearly", text: "Yearly", title: "A yearly charge: counted a twelfth a month in fixed costs" };
    if (t.kind === "fixed") return { type: "fixed", text: "Fixed", title: "A regular charge: counted in fixed costs" };
    return null;
  }
  const flowLabel = (flow) => FLOW_LABELS[flow] || "";
  /** The "Counts as" choices for a row: [{flow, label}] (capitalised), by the sign of its amount, its own flow kept. */
  function flowChoices(t) {
    const list = (t.amount > 0 ? IN_FLOWS : OUT_FLOWS).slice();
    if (FLOW_LABELS[t.flow] && !list.includes(t.flow)) list.push(t.flow);
    return list.map((flow) => ({ flow, label: FLOW_LABELS[flow].charAt(0).toUpperCase() + FLOW_LABELS[flow].slice(1) }));
  }
  /** A three-way mark (one_off / yearly: true, false, null = Dana decides) as "yes" | "no" | "auto". */
  const markState = (v) => (v === true ? "yes" : v === false ? "no" : "auto");
  /** What Dana makes of a row on its own: "Dana counts it as a one-off" (kind of GET transactions/{id}). */
  const kindText = (kind) => (KIND_LABELS[kind] ? `Dana counts it as ${KIND_LABELS[kind]}.` : "");
  /** The toast after PATCH {one_off} / {yearly}. */
  function markText(body, mark, value) {
    const t = (body && body.transaction) || {};
    const what = mark === "yearly" ? "a yearly charge" : "a one-off";
    const said = value === true ? what : value === false ? `not ${what}` : `Dana decides if it's ${mark === "yearly" ? "yearly" : "a one-off"}`;
    const more = body && body.changed > 1 ? ` (${body.changed} charges)` : "";
    return `${isolate(t.merchant)}: ${said}${more}`;
  }
  /** The toast after PATCH {flow}. */
  const flowText = (body) => `${isolate(((body && body.transaction) || {}).merchant)} now counts as ${flowLabel(((body && body.transaction) || {}).flow)}`;

  /** A PATCH's answer as a toast line. */
  function undoText(body, label, forMerchant) {
    const t = body && body.transaction;
    if (!t) return "Saved";
    if (label === null) return t.note ? "Note saved" : "Note removed";
    const more = body.changed > 1 ? ` (${body.changed} charges)` : "";
    return `${isolate(t.merchant)} → ${label}${forMerchant ? `, now and from now on${more}` : ""}`;
  }

  /** An error answer in words. */
  function errorText(status, body) {
    const b = body || {};
    if (status === 0) return "Can't reach Dana. Check the connection and try again.";
    if (b.error === "too_soon") return `Synced a moment ago. Try again in ${waitText(b.retry_after || 60)}.`;
    if (b.error === "needs_attention") return b.reason || "This login needs fixing first.";
    if (b.error === "bad_code") return "That doesn't look like the code: 4 to 8 digits.";
    if (b.error === "not_pending") return "That code request is over. Dana will ask again when it signs in next.";
    if (b.error === "not_connected") return "Money is turned off for you. Turn it on in Addons.";
    if (b.error === "not_found") return "It isn't there any more.";
    if (b.message) return b.message;
    return `Something went wrong (HTTP ${status}).`;
  }

  const core = {
    MONTHS, MON, SERIES, EMPTY_FILTERS, MINUS, FSI, LRI, PDI, TABS, TAB_LABELS, FLOW_LABELS, FLOW_SERIES, ACCOUNT_KINDS,
    esc, isolate, setCurrency, currency, currencyCode, currencySymbol, money, moneyText, moneyHtml, compact, foreignText,
    isDay, parseDay, dayIso, todayIso, addDays, shortDate, longDate, dayLabel, timeOf, ago, until, countdown, when, waitText, accountName,
    isMonth, monthOf, shiftMonth, monthLabel, monthBounds,
    niceMax, niceTicks, paceGeometry, paceIndexAt, paceAt, seriesOf, monthsGeometry, barPath, trendsAsMonths, sparkBars,
    cashflowGeometry, dayNum, balanceGeometry, pointIndexAt, monthLabels,
    visibleTabs, usesMonth, kindIcon, kindWord, cycleLabel, mealTiles, mealCycleBars, pctText, quantityText, unitPrice, portfolioTiles, valueGeometry, valueChangeText, allocationSlices, tradeText,
    saveSection, saveSections, saveTiles, savingChip, effortText, basisChip, statusLine, statusActions, saveToast, targetAgorot,
    savingLink, seriesSpark, marketView, checkReply, offerRows, outcomeText, targetProgress, subitemText, hostOf, localDay,
    groupByDay, mergeTxns, needsCategory, amountTone, badges, flowChip, categorySourceLabel, statusExplain, isIncome,
    cleanFilters, hasFilters, filtersToQuery, stateToHash, stateFromHash, filterChips, categoryLabel, groupOf, pickerGroups,
    sourceStatus, busy, headerStatus, cadenceLabel, perText, cadenceAmount,
    planText, priceChangeText,
    isolateHebrew, netTile, everydayTile, balanceTile, fixedTile, vsTypical, cardBillParts, upcoming, cashflowTotalsText, forecastNote,
    kindBadge, flowLabel, flowChoices, markState, kindText, markText, flowText,
    undoText, errorText,
  };
  (typeof window !== "undefined" ? window : globalThis).DanaMoneyCore = core;
})();
