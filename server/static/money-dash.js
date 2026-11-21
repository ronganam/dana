/* Money — the dashboard views of the Money tab: Overview, Spending, Cash flow, Save, Investments and Meal card (docs/money.md "Money tab").
 *
 * window.DanaMoneyDash(ctx) → {overview(el), spending(el), cashflow(el), save(el), investments(el), meals(el), redraw()}: static/money.js (the
 * controller: state, loading, events, the Transactions view, sheets) makes one with its state and helpers
 * and calls a view's function to fill its container; redraw() re-measures the charts on screen (resize).
 * Rendering only: no network and no state of its own — clicks land on data attributes that money.js
 * handles (data-go, data-link-*, data-focus, data-bill, data-horizon, data-spend-mode, data-oneoff-no,
 * data-txn-go, data-merchant, data-month-bar, data-inv-range, and the Save view's data-save-*). Geometry, words and money formatting are DanaMoneyCore's
 * (static/money-core.js, tested by test_web_money.mjs). Loaded before money.js.
 *
 *   Overview      overview?month (tiles, forecast), insights?month, balance?days= (coming up),
 *                 pace?month (everyday pace), cashflow?end= (the typical net)
 *   Spending      trends?month (where it went, movers, big purchases, the Everyday bars),
 *                 months?n=12 (the Everything bars), merchants?period=YYYY-MM
 *   Cash flow     cashflow?n=12&end=, balance?days=90|180|365, recurring, overview (fixed costs total)
 *   Save          savings (the tiles, ways to save and spending to watch in sections, each card's
 *                 evidence, math, questions, price check offers, subscription lines, steps, script,
 *                 target progress, outcome and the actions of its status); not tied to the month
 *   Investments   portfolio?days=365 (tiles, the value over 1 month / 3 months / 1 year, allocation,
 *                 holdings, activity); read-only: Dana never trades
 */
(() => {
  "use strict";

  const KIND_ICONS = { income: "arrow-down-left", fixed: "repeat", yearly: "calendar-clock", card_bill: "credit-card" };
  const KIND_WORDS = { income: "Income", fixed: "Fixed", yearly: "Yearly", card_bill: "Card bill" };
  const INSIGHT_ICONS = {
    low_point: "trending-down", mover: "trending-up", price_change: "tag", installments: "layers", unusual: "triangle-alert",
    net: "piggy-bank", first_time: "sparkle", duplicate: "copy",
  };
  const ITEM_KINDS = { fixed: "Fixed", yearly: "Yearly", installment: "Installment", everyday: "Everyday" };
  const HORIZONS = [[90, "3 months"], [180, "6 months"], [365, "12 months"]];
  const INV_RANGES = [[30, "1 month"], [90, "3 months"], [365, "1 year"]];
  const SAVE_ICONS = {
    bill: "receipt", electricity: "zap", cash: "coins", cushion: "landmark", fx: "globe", fees: "badge-percent", arnona: "building-2",
    renewal: "calendar-clock", insurance_review: "shield-check", subscriptions: "repeat", mortgage: "house", creep: "trending-up",
    spree: "shopping-cart", leak: "droplet", overlap: "copy", penalties: "gavel", late_night: "moon",
  };
  const EFFORT_ICONS = { call: "phone", app: "smartphone", online: "globe", compare: "scale", review: "list-checks" };

  window.DanaMoneyDash = (ctx) => {
    const { S, C, got, esc, icon, icons, today, isCurrent, seriesCls, acctOf, catLabel } = ctx;
    let svSeq = 0;
    const $ = (id) => document.getElementById(id);
    const amt = (v, o, cls) => C.moneyHtml(v, o, cls);
    const whole = (v, o = {}) => C.moneyHtml(v, { cents: false, ...o });
    const heb = (s) => esc(C.isolateHebrew(s));
    const skelCard = (title, body = `<div class="mny-chart-skel"></div>`) => `<section class="mny-card"><div class="mny-card-head"><span>${esc(title)}</span></div>${body}</section>`;
    const rowsSkel = (n) => `<div class="mny-row-skel"></div>`.repeat(n);
    const monthName = (m, o = {}) => C.monthLabel(m, { short: true, year: false, ...o });
    const gridY = (g, ticks) => ticks.map((t) => `<line class="mny-grid-l${t.zero ? " zero" : ""}" x1="${g.L}" x2="${g.W - g.R}" y1="${t.y}" y2="${t.y}"/>`
      + `<text class="mny-axis" x="${g.L - 6}" y="${t.y + 3.5}" text-anchor="end">${esc(t.text)}</text>`).join("");
    const width = (wrap, fallback = 600) => Math.max(260, Math.round(wrap.clientWidth || fallback));
    function placeTip(tip, svg, g, x) {
      const box = svg.getBoundingClientRect();
      const left = (x / g.W) * box.width;
      tip.classList.remove("hidden");
      tip.style.left = Math.min(Math.max(left, tip.offsetWidth / 2 + 2), box.width - tip.offsetWidth / 2 - 2) + "px";
    }
    const svgX = (svg, g, ev) => { const box = svg.getBoundingClientRect(); return ((ev.clientX - box.left) / box.width) * g.W; };
    function sparkSvg(values, cls, o = {}) {
      const sp = C.sparkBars(values, { width: o.width || 60, height: o.height || 18 });
      if (!sp.max) return `<span class="mny-spark empty" style="width:${sp.W}px"></span>`;
      return `<svg class="mny-spark ${cls}" viewBox="0 0 ${sp.W} ${sp.H}" width="${sp.W}" height="${sp.H}" aria-hidden="true">${
        sp.bars.map((b) => (b.h ? `<rect x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h}" rx="0.8"${b.last ? ` class="last${o.partial ? " partial" : ""}"` : ""}/>` : "")).join("")}</svg>`;
    }

    // ================================ Overview ================================

    function overview(el) {
      const ov = got("overview");
      if (!ov) {
        el.innerHTML = `<div class="mny-tiles">${`<div class="mny-tile mny-skel"></div>`.repeat(4)}</div>
          <div class="mny-ov-grid">${skelCard("Insights", rowsSkel(4))}${skelCard("Everyday pace")}</div>`;
        return;
      }
      const inProgress = Boolean(ov.period && ov.period.in_progress);
      const fc = inProgress ? ov.forecast : null;
      el.innerHTML = `<div class="mny-tiles">${tilesHtml(ov, inProgress, fc)}</div>
        <div class="mny-ov-grid${fc ? "" : " no-up"}">
          <section class="mny-card mny-insights" aria-label="Insights">${insightsHtml(ov)}</section>
          ${fc ? `<section class="mny-card mny-up" aria-label="Coming up">${upcomingHtml()}</section>` : ""}
          <section class="mny-card mny-pace" aria-label="Everyday pace">${paceHtml()}</section>
        </div>`;
      drawPace();
      drawUpSpark();
    }

    function tilesHtml(ov, inProgress, fc) {
      const out = [];
      const net = C.netTile(ov, got("cashflow"));
      out.push(`<button class="mny-tile" data-go="cashflow">
        <span class="mny-tl">${esc(net.label)}</span>
        <span class="mny-tv ${net.value < 0 ? "neg" : "in"}">${amt(net.value, { sign: "plus" })}</span>
        <span class="mny-ts strong">${esc(net.rate)}${net.typical ? ` · ${esc(net.typical)}` : ""}</span>
        <span class="mny-ts">${esc(net.sub)}</span></button>`);
      const ev = C.everydayTile(ov.everyday, inProgress);
      out.push(`<button class="mny-tile" data-go="spending">
        <span class="mny-tl">Everyday spending</span>
        <span class="mny-tv">${amt(ev.value, { sign: "none" })}</span>
        ${ev.track ? `<span class="mny-ts strong">${ev.dir ? icon(ev.dir === "up" ? "trending-up" : "trending-down", `mny-ts-i ${ev.dir}`) : ""}${esc(ev.track)}</span>` : ""}
        <span class="mny-ts">${esc(ev.typical || (inProgress ? "so far this month" : "in all"))}</span></button>`);
      const bt = C.balanceTile(fc, today());
      if (bt) {
        out.push(`<button class="mny-tile" data-go="cashflow" data-anchor="mny-bal">
          <span class="mny-tl">Balance ahead</span>
          <span class="mny-tv${bt.value < 0 ? " neg" : ""}">${amt(bt.value)}</span>
          ${bt.low ? `<span class="mny-ts strong${bt.tone === "bad" ? " bad" : ""}">${esc(bt.low)}</span>` : ""}
          ${bt.end ? `<span class="mny-ts">${esc(bt.end)}</span>` : ""}</button>`);
      } else {
        out.push(`<button class="mny-tile" data-go="spending" data-anchor="mny-big">
          <span class="mny-tl">One-offs</span>
          <span class="mny-tv">${amt(ov.one_off || 0, { sign: "none" })}</span>
          <span class="mny-ts">${ov.set_aside ? `Set aside ${C.moneyText(ov.set_aside, { cents: false, sign: "none" })}` : "left out of everyday spending"}</span></button>`);
      }
      const fx = C.fixedTile(ov);
      out.push(`<button class="mny-tile" data-go="cashflow" data-anchor="mny-fixed">
        <span class="mny-tl">Fixed costs</span>
        <span class="mny-tv">${amt(fx.value, { sign: "none" })}<em>/ month</em></span>
        ${fx.share ? `<span class="mny-ts strong">${esc(fx.share)}</span>` : ""}
        <span class="mny-ts">${esc(fx.yearly || "recurring and yearly charges")}</span></button>`);
      return out.join("");
    }

    function insightsHtml(ov) {
      const ins = got("insights");
      const un = ov.uncategorized || 0;
      const sort = un ? `<button class="mny-sort-chip" data-act="goto-uncat">${icon("circle-help")}<span>${un} to sort</span></button>` : "";
      const head = `<div class="mny-card-head"><span>Insights</span>${sort}</div>`;
      if (!ins) return head + rowsSkel(4);
      const items = ins.items || [];
      if (!items.length) return `${head}<div class="mny-empty">Nothing stands out in ${esc(C.monthLabel(S.month))}.</div>`;
      return head + `<div class="mny-ins-list">${items.map((it) => {
        const l = it.link || {};
        const attrs = l.view ? ` data-link-view="${esc(l.view)}"${l.category ? ` data-link-category="${esc(l.category)}"` : ""}${l.id ? ` data-link-id="${esc(l.id)}"` : ""}` : "";
        const tag = l.view ? "button" : "div";
        return `<${tag} class="mny-ins tone-${esc(it.tone || "info")}"${attrs}>
          <span class="mny-ins-icon">${icon(INSIGHT_ICONS[it.kind] || "info")}</span>
          <span class="mny-ins-main"><span class="mny-ins-title" dir="ltr">${heb(it.title)}</span>${it.detail ? `<span class="mny-ins-detail" dir="ltr">${heb(it.detail)}</span>` : ""}</span>
          ${l.view ? icon("chevron-right", "mny-chev") : ""}</${tag}>`;
      }).join("")}</div>`;
    }

    function upcomingHtml() {
      const b = got("balance");
      const head = `<div class="mny-card-head"><span>Coming up</span><span class="mny-head-hint">next 45 days</span></div>`;
      if (!b) return head + `<div class="mny-spark-skel mny-chart-skel"></div>` + rowsSkel(4);
      const fc = b.forecast;
      if (!fc) return `${head}<div class="mny-empty">Add a bank account to see what's coming.</div>`;
      const up = C.upcoming(fc, 45);
      const last = up.series[up.series.length - 1];
      const low = fc.soon_low;
      const caption = `${amt(fc.balance)} now${last ? ` → about ${whole(last.balance)} on ${esc(C.shortDate(last.date, today()))}` : ""}`
        + (low && low.balance < fc.balance ? `<span class="mny-up-low">lowest ${whole(low.balance)} on ${esc(C.shortDate(low.date, today()))}</span>` : "");
      const days = [];
      up.events.forEach((e, i) => {
        if (!days.length || days[days.length - 1].date !== e.date) days.push({ date: e.date, items: [] });
        days[days.length - 1].items.push([e, i]);
      });
      const rows = days.map((d) => `<div class="mny-up-day">${esc(C.dayLabel(d.date, today()))}</div>${d.items.map(([e, i]) => upRow(e, i)).join("")}`).join("");
      return `${head}<div class="mny-up-cap">${caption}</div><div class="mny-chart-wrap mny-up-spark" id="mny-up-spark"></div>
        <div class="mny-up-list">${rows || `<div class="mny-empty">Nothing expected in the next 45 days.</div>`}</div>`;
    }
    function upRow(e, i) {
      const key = `${e.date}|${e.account_id || ""}|${e.label}`;
      const bill = e.kind === "card_bill";
      const open = bill && S.openBills.has(key);
      const name = bill && acctOf(e.account_id) ? C.accountName(acctOf(e.account_id)) : e.label;
      const tone = e.amount > 0 ? "in" : "";
      const money = `${e.estimated ? `<span class="mny-about">about</span>` : ""}${amt(e.amount, { sign: e.amount > 0 ? "plus" : "minus", cents: e.estimated ? false : true })}`;
      const main = `<span class="mny-up-icon k-${esc(e.kind)}">${icon(KIND_ICONS[e.kind] || "circle")}</span>
        <span class="mny-up-label"><bdi dir="auto">${esc(name)}</bdi><span class="mny-up-kind">${esc(KIND_WORDS[e.kind] || e.kind)}${bill && e.range ? ` · ${esc(e.range)}` : ""}</span></span>
        <span class="mny-up-amt ${tone}">${money}</span>${bill ? icon(open ? "chevron-up" : "chevron-down", "mny-up-chev") : ""}`;
      if (!bill) return `<div class="mny-up-row"><div class="mny-up-main">${main}</div></div>`;
      const parts = open ? `<div class="mny-up-parts">
          ${e.parts.map((p) => `<span class="mny-pop-line"><span>${esc(p.label)}</span>${amt(p.amount, { sign: "none", cents: p.key !== "everyday" })}</span>`).join("")}
          ${e.items.length ? `<span class="mny-up-items-h">On this bill</span>${e.items.map((it) => `<span class="mny-pop-item"><bdi dir="auto">${esc(it.label)}</bdi>
            <span>${esc(ITEM_KINDS[it.kind] || "")} · ${amt(it.amount, { sign: "none" })}</span></span>`).join("")}` : ""}
          ${e.estimated ? `<span class="mny-up-note">The everyday part is an estimate from your usual card spending.</span>` : ""}</div>` : "";
      return `<div class="mny-up-row${open ? " open" : ""}"><button class="mny-up-main" data-bill="${esc(key)}" aria-expanded="${open}">${main}</button>${parts}</div>`;
    }

    function drawUpSpark() {
      const wrap = $("mny-up-spark");
      const b = got("balance");
      if (!wrap || !b || !b.forecast) return;
      const fc = b.forecast;
      const up = C.upcoming(fc, 45);
      if (up.series.length < 2) { wrap.innerHTML = ""; return; }
      const g = C.balanceGeometry({ history: [], forecast: { ...fc, series: up.series } },
                                  { width: width(wrap, 360), height: 64, left: 4, right: 4, top: 10, bottom: 8, includeZero: false, low: fc.soon_low });
      if (!g) return;
      wrap.innerHTML = `<svg viewBox="0 0 ${g.W} ${g.H}" height="${g.H}" role="img" aria-label="Projected bank balance for the next 45 days">
          ${g.band ? `<path class="mny-bal-band" d="${g.band}"/>` : ""}
          ${g.zeroY != null ? `<line class="mny-grid-l zero" x1="${g.L}" x2="${g.W - g.R}" y1="${g.zeroY}" y2="${g.zeroY}"/>` : ""}
          <path class="mny-bal-line fc" d="${g.forecast}"/>
          ${g.low ? `<circle class="mny-bal-low" cx="${g.low.x}" cy="${g.low.y}" r="4"/>` : ""}
          ${crossHtml(g)}
        </svg><div class="mny-tip hidden"></div>`;
      hoverPoints(wrap, g);
    }

    function paceHtml() {
      const p = got("pace");
      const head = (legend = "") => `<div class="mny-card-head"><span>Everyday pace</span>${legend}</div>`;
      if (!p) return head() + `<div class="mny-chart-skel"></div>`;
      const hasThis = (p.this_month || []).some((v) => v != null && v > 0);
      const legend = `<div class="mny-legend">
        <span><i class="mny-key this"></i>${isCurrent() ? "This month" : esc(monthName(p.month))}</span>
        ${p.typical ? `<span><i class="mny-key avg"></i>Typical</span><span><i class="mny-key band"></i>Usual range</span>` : ""}</div>`;
      const vals = (p.this_month || []).filter((v) => v != null);
      const so = vals.length ? vals[vals.length - 1] : 0;
      const at = p.today ? p.today - 1 : (p.typical || []).length - 1;
      const typ = p.typical && at >= 0 ? p.typical[Math.min(at, p.typical.length - 1)] : null;
      const ov = got("overview");
      const track = ov && ov.month === p.month && ov.everyday && ov.everyday.on_track != null && p.today ? ` · on track for ${whole(ov.everyday.on_track)}` : "";
      const foot = `${amt(so, { sign: "none" })} ${p.today ? "so far" : "in all"}${track}${typ != null ? ` · typical ${whole(typ)} ${p.today ? `by day ${p.today}` : "for the month"}` : ""}`;
      const none = !hasThis && !(p.high || []).some((v) => v > 0);
      return head(legend) + `<div class="mny-chart-wrap" id="mny-pace-wrap">${none ? `<div class="mny-empty">No everyday spending in ${esc(C.monthLabel(p.month))} yet.</div>` : ""}</div>
        <div class="mny-chart-foot">${foot}${p.typical ? "" : `<span class="mny-muted"> · A typical month needs a whole month of history.</span>`}</div>
        <div class="mny-chart-note">Everyday spending only: fixed costs, yearly charges and one-offs are left out. Typical = the median of the last ${p.months || 0} whole months.</div>`;
    }

    function drawPace() {
      const wrap = $("mny-pace-wrap");
      const p = got("pace");
      if (!wrap || !p || wrap.querySelector(".mny-empty")) return;
      const narrow = (wrap.clientWidth || 600) < 480;
      const g = C.paceGeometry(p, { width: width(wrap), height: narrow ? 180 : 210, left: narrow ? 42 : 52, right: narrow ? 8 : 14 });
      const xl = g.xLabels.map((l) => `<text class="mny-axis" x="${l.x}" y="${g.H - 6}" text-anchor="middle">${l.text}</text>`).join("");
      const todayM = g.today ? `<line class="mny-today" x1="${g.today.x}" x2="${g.today.x}" y1="${g.T}" y2="${g.base}"/><text class="mny-axis today" x="${g.today.x}" y="${g.T - 3}" text-anchor="middle">today</text>` : "";
      wrap.innerHTML = `<svg viewBox="0 0 ${g.W} ${g.H}" height="${g.H}" role="img" aria-label="Everyday spending by day, cumulative, against a typical month">
          ${gridY(g, g.ticks)}${xl}
          ${g.band ? `<path class="mny-band" d="${g.band}"/>` : ""}
          ${g.area ? `<path class="mny-area" d="${g.area}"/>` : ""}
          ${g.lines.typical ? `<path class="mny-line avg" d="${g.lines.typical}"/>` : ""}
          <path class="mny-line this" d="${g.lines.this}"/>
          ${todayM}${g.end ? `<circle class="mny-dot-this" cx="${g.end.x}" cy="${g.end.y}" r="4"/>` : ""}
          <g class="mny-cross hidden"><line class="mny-cross-l" y1="${g.T}" y2="${g.base}"/><circle class="c-this" r="4"/><circle class="c-avg" r="3.5"/></g>
          <rect class="mny-hit" x="${g.L}" y="0" width="${g.plotW}" height="${g.H}"/>
        </svg><div class="mny-tip hidden"></div>`;
      const svg = wrap.querySelector("svg"), tip = wrap.querySelector(".mny-tip"), cross = wrap.querySelector(".mny-cross");
      const name = monthName(p.month);
      const show = (ev) => {
        const i = C.paceIndexAt(g, svgX(svg, g, ev));
        const v = C.paceAt(p, i);
        const x = g.x(i);
        cross.classList.remove("hidden");
        const line = cross.querySelector("line");
        line.setAttribute("x1", x); line.setAttribute("x2", x);
        [["c-this", v.this], ["c-avg", v.typical]].forEach(([cls, val]) => {
          const c = cross.querySelector("." + cls);
          c.style.display = val == null ? "none" : "";
          if (val != null) { c.setAttribute("cx", x); c.setAttribute("cy", g.y(val)); }
        });
        tip.innerHTML = `<strong>${v.day} ${esc(name)}</strong>
          ${v.this != null ? `<span><i class="mny-key this"></i>${isCurrent() ? "This month" : esc(name)} ${amt(v.this, { sign: "none" })}</span>` : ""}
          ${v.typical != null ? `<span><i class="mny-key avg"></i>Typical ${amt(v.typical, { sign: "none" })}</span>` : ""}
          ${v.low != null && v.high != null && v.high > v.low ? `<span class="mny-muted">usually ${C.moneyText(v.low, { cents: false })} – ${C.moneyText(v.high, { cents: false })}</span>` : ""}`;
        placeTip(tip, svg, g, x);
      };
      svg.addEventListener("pointermove", show);
      svg.addEventListener("pointerdown", show);
      svg.addEventListener("pointerleave", () => { cross.classList.add("hidden"); tip.classList.add("hidden"); });
    }

    // A crosshair + dot for the balance charts, moved by hoverPoints.
    const crossHtml = (g) => `<g class="mny-cross hidden"><line class="mny-cross-l" y1="${g.T}" y2="${g.H - g.B}"/><circle class="c-bal" r="4"/></g>
      <rect class="mny-hit" x="${g.L}" y="0" width="${g.plotW}" height="${g.H}"/>`;
    const balanceTip = (p) => {
      const range = p.forecast && p.low != null && p.high != null && p.high !== p.low
        ? `<span class="mny-muted">${C.moneyText(p.low, { cents: false })} – ${C.moneyText(p.high, { cents: false })}</span>` : "";
      return `<strong>${esc(C.longDate(p.date))}</strong><span>${p.forecast ? "Forecast" : "Balance"} ${amt(p.balance, { cents: !p.forecast })}</span>${range}`;
    };
    function hoverPoints(wrap, g, tipHtml = balanceTip) {
      const svg = wrap.querySelector("svg"), tip = wrap.querySelector(".mny-tip"), cross = wrap.querySelector(".mny-cross");
      const show = (ev) => {
        const p = g.points[C.pointIndexAt(g, svgX(svg, g, ev))];
        if (!p) return;
        cross.classList.remove("hidden");
        const line = cross.querySelector("line");
        line.setAttribute("x1", p.x); line.setAttribute("x2", p.x);
        const dot = cross.querySelector("circle");
        dot.setAttribute("cx", p.x); dot.setAttribute("cy", p.y);
        dot.classList.toggle("fc", Boolean(p.forecast));
        tip.innerHTML = tipHtml(p);
        placeTip(tip, svg, g, p.x);
      };
      svg.addEventListener("pointermove", show);
      svg.addEventListener("pointerdown", show);
      svg.addEventListener("pointerleave", () => { cross.classList.add("hidden"); tip.classList.add("hidden"); });
    }

    // ================================ Spending ================================

    function focusGroup() {
      const k = S.f.category;
      if (!k) return null;
      if (k === "uncategorized") return "uncategorized";
      return C.groupOf(S.taxonomy, k) || k;
    }

    function spending(el) {
      const t = got("trends");
      const focus = S.f.category;
      const chip = focus ? `<div class="mny-focus">${icon("filter")}<span>Showing <strong>${esc(catLabel(focus))}</strong></span>
          <button class="mny-link" data-act="focus-tx">See its transactions</button>
          <button class="icon-btn mny-focus-x" data-focus="" aria-label="Show every category">${icon("x")}</button></div>` : "";
      el.innerHTML = `${chip}<div class="mny-cols">
        <div class="mny-col">
          <section class="mny-card mny-where o1" aria-label="Where it went">${whereHtml(t)}</section>
          <section class="mny-card mny-months o3" aria-label="12 months">${monthsHtml()}</section>
        </div>
        <div class="mny-col">
          <section class="mny-card mny-movers o2" aria-label="Movers">${moversHtml(t)}</section>
          <section class="mny-card mny-merchants o4" aria-label="Top merchants">${merchantsHtml()}</section>
          <section class="mny-card mny-big o5" id="mny-big" aria-label="Big purchases">${bigHtml(t)}</section>
        </div></div>`;
      drawMonths();
    }

    function whereHtml(t) {
      const head = (num = "") => `<div class="mny-card-head"><span>Where it went</span>${num}</div>`;
      if (!t) return head() + rowsSkel(6);
      const live = (x) => x.total > 0 || x.on_track > 0 || x.typical > 0;
      const groups = (t.groups || []).filter(live);
      const total = groups.reduce((a, g) => a + (g.total || 0), 0);
      const oneOffs = (t.one_offs || []).reduce((a, x) => a + Math.abs(x.amount || 0), 0);
      const on = S.f.category, fg = focusGroup();
      const row = (x, cls, sub, group) => {
        const vs = C.vsTypical(x, t.in_progress);
        const parts = [];
        if (t.in_progress && x.on_track != null && x.on_track !== x.total) parts.push(`on track ${C.moneyText(x.on_track, { cents: false })}`);
        if (x.typical > 0) parts.push(`typical ${C.moneyText(x.typical, { cents: false })}`);
        return `<button class="mny-wrow${sub ? " sub" : ""}${on === x.key ? " on" : ""}" data-focus="${esc(on === x.key ? "" : x.key)}"
            title="${on === x.key ? "Show every category" : `Focus on ${esc(x.label)}`}">
          ${sub ? "" : `<span class="mny-sw ${cls}"></span>`}
          <span class="mny-wrow-name"><span class="mny-wrow-t">${esc(x.label)}</span>${parts.length ? `<span class="mny-wrow-sub">${esc(parts.join(" · "))}</span>` : ""}</span>
          ${sparkSvg(x.months, cls, { partial: t.in_progress })}
          <span class="mny-wrow-amt">${amt(x.total || 0, { sign: "none" })}${vs.text ? `<span class="mny-delta ${vs.dir}">${esc(vs.text)}</span>` : ""}</span>
        </button>`;
      };
      const rows = groups.map((g) => {
        const cls = seriesCls(g.key === "uncategorized" ? null : g.key);
        const cats = (g.categories || []).filter(live);
        const multi = cats.length > 1 || (cats[0] && cats[0].key !== g.key);
        const open = S.expanded.has(g.key) || (fg === g.key && on !== g.key && multi);
        return `<div class="mny-cat-group${open ? " open" : ""}${fg && fg !== g.key ? " dim" : ""}">
          <div class="mny-cat-line">${row(g, cls, false)}
            ${multi ? `<button class="icon-btn mny-exp" data-expand="${esc(g.key)}" aria-expanded="${open}" aria-label="${open ? "Hide" : "Show"} ${esc(g.label)}'s categories">${icon(open ? "chevron-up" : "chevron-down")}</button>` : `<span class="mny-exp-pad"></span>`}
          </div>${open ? cats.map((c) => row(c, cls, true, g)).join("") : ""}</div>`;
      }).join("");
      return head(`<span class="mny-head-num">${amt(total, { sign: "none" })}</span>`)
        + `<div class="mny-head-sub">${t.in_progress ? `${t.elapsed} of ${t.days} days` : esc(t.label)} · one-offs left out${oneOffs ? ` (${C.moneyText(oneOffs, { cents: false })}, below)` : ""}</div>`
        + `<div class="mny-cats-list">${rows || `<div class="mny-empty">Nothing spent in ${esc(t.label)}.</div>`}</div>`;
    }

    function moversHtml(t) {
      const head = `<div class="mny-card-head"><span>Movers</span><span class="mny-head-hint">against a typical month</span></div>`;
      if (!t) return head + rowsSkel(3);
      const list = t.movers || [];
      if (!list.length) return `${head}<div class="mny-empty">Nothing far from usual${t.in_progress ? " so far" : ""}.</div>`;
      return head + `<div class="mny-mv-list">${list.map((m) => {
        const dir = m.direction;
        const d = dir === "new" ? "new" : `${dir === "up" ? "+" : C.MINUS}${C.money(Math.abs(m.delta), { cents: false, sign: "none" })}`;
        return `<button class="mny-mover" data-focus="${esc(m.key)}">
          <span class="mny-mv-icon ${dir}">${icon(dir === "down" ? "arrow-down-right" : dir === "new" ? "sparkle" : "arrow-up-right")}</span>
          <span class="mny-mv-main"><span class="mny-mv-t"><span class="mny-sw ${seriesCls(m.group)}"></span>${esc(m.label)}</span>
            <span class="mny-mv-sub">${t.in_progress ? "on track for " : ""}${C.moneyText(m.value, { cents: false })} · typical ${C.moneyText(m.typical, { cents: false })}</span></span>
          <span class="mny-delta ${dir}"><bdi dir="ltr">${esc(d)}</bdi></span></button>`;
      }).join("")}</div>`;
    }

    function monthsHtml() {
      const mode = S.spendMode;
      const seg = `<span class="mny-seg" role="radiogroup" aria-label="Which spending">
        <button role="radio" aria-checked="${mode === "everyday"}" class="${mode === "everyday" ? "on" : ""}" data-spend-mode="everyday" title="Without one-offs">Everyday</button>
        <button role="radio" aria-checked="${mode === "everything"}" class="${mode === "everything" ? "on" : ""}" data-spend-mode="everything" title="Every charge, one-offs too">Everything</button></span>`;
      const d = monthsData();
      const head = `<div class="mny-card-head"><span>12 months</span>${seg}</div>`;
      if (!d) return head + `<div class="mny-chart-skel short"></div>`;
      if (!d.months.some((m) => m.total > 0)) return `${head}<div class="mny-empty">No spending in these 12 months yet.</div>`;
      return head + `<div class="mny-chart-wrap" id="mny-months-wrap"></div><div class="mny-legend wrap" id="mny-months-legend"></div>
        <div class="mny-chart-note">${mode === "everyday" ? "Without one-offs" : "Every charge, one-offs too"} · click a month to open it</div>`;
    }
    const monthsData = () => (S.spendMode === "everything" ? got("months") : (got("trends") && C.trendsAsMonths(got("trends"))));

    function drawMonths() {
      const wrap = $("mny-months-wrap");
      const d = monthsData();
      if (!wrap || !d) return;
      const narrow = (wrap.clientWidth || 600) < 480;
      const g = C.monthsGeometry(d, { width: width(wrap), height: narrow ? 160 : 190, left: narrow ? 40 : 46 });
      const fs = focusGroup() ? C.seriesOf(focusGroup() === "uncategorized" ? null : focusGroup()) : null;
      const bars = g.bars.map((b, i) => {
        const sel = b.month === S.month;
        return `<g class="mny-bar${sel ? " sel" : ""}" data-i="${i}">
          ${b.segments.map((s) => `<path class="g-${s.key}${fs && fs !== s.key ? " dim" : ""}" d="${C.barPath(s)}"/>`).join("")}
          ${sel ? `<rect class="mny-bar-sel" x="${b.slotX + 1}" y="${g.T - 4}" width="${b.slotW - 2}" height="${g.plotH + 8}" rx="4"/>` : ""}</g>`;
      }).join("");
      const hits = g.bars.map((b, i) => `<rect class="mny-hit" data-month-bar="${b.month}" data-i="${i}" x="${b.slotX}" y="0" width="${b.slotW}" height="${g.H}"/>`).join("");
      const xl = g.xLabels.filter((l) => l.show).map((l) => `<text class="mny-axis${l.month === S.month ? " sel" : ""}" x="${l.x}" y="${g.H - 6}" text-anchor="middle">${esc(l.text)}</text>`).join("");
      wrap.innerHTML = `<svg viewBox="0 0 ${g.W} ${g.H}" height="${g.H}" role="img" aria-label="Spending per month for 12 months, by group">${gridY(g, g.ticks)}${bars}${xl}${hits}</svg><div class="mny-tip hidden"></div>`;
      $("mny-months-legend").innerHTML = g.legend.map((l) => `<span${fs && fs !== l.key ? ` class="dim"` : ""}><i class="mny-key sq g-${l.key}"></i>${esc(l.label)}</span>`).join("");
      const svg = wrap.querySelector("svg"), tip = wrap.querySelector(".mny-tip");
      const labels = Object.fromEntries((d.groups || []).map((x) => [x.key, x.label]));
      svg.addEventListener("pointermove", (ev) => {
        const hit = ev.target.closest("[data-month-bar]");
        svg.querySelectorAll(".mny-bar.hover").forEach((n) => n.classList.remove("hover"));
        if (!hit) { tip.classList.add("hidden"); return; }
        const i = +hit.dataset.i, b = g.bars[i], m = d.months[i];
        svg.querySelector(`.mny-bar[data-i="${i}"]`)?.classList.add("hover");
        const parts = Object.entries(m.groups || {}).filter(([, v]) => v > 0).sort((a, c) => c[1] - a[1]);
        tip.innerHTML = `<strong>${esc(b.label)} · ${amt(b.total, { sign: "none" })}</strong>
          ${parts.slice(0, 6).map(([k, v]) => `<span><i class="mny-key sq g-${C.seriesOf(k === "uncategorized" ? null : k)}"></i>${esc(labels[k] || k)} ${amt(v, { sign: "none" })}</span>`).join("")}
          ${parts.length > 6 ? `<span class="mny-muted">+ ${parts.length - 6} more</span>` : ""}`;
        placeTip(tip, svg, g, b.cx);
      });
      svg.addEventListener("pointerleave", () => { tip.classList.add("hidden"); svg.querySelectorAll(".mny-bar.hover").forEach((n) => n.classList.remove("hover")); });
    }

    function merchantsHtml() {
      const head = `<div class="mny-card-head"><span>Top merchants</span><span class="mny-head-hint">${esc(C.monthLabel(S.month, { short: true }))}</span></div>`;
      const m = got("merchants");
      if (!m) return head + rowsSkel(5);
      const fg = focusGroup(), on = S.f.category;
      const list = (m.merchants || []).filter((x) => !on || x.category === on || (!x.category && on === "uncategorized") || (fg && fg !== on && C.groupOf(S.taxonomy, x.category) === fg));
      if (!list.length) return `${head}<div class="mny-empty">${on ? `No ${esc(catLabel(on))} merchants` : "No merchants"} in ${esc(C.monthLabel(S.month))}.</div>`;
      return head + `<div class="mny-mer-list">${list.map((x) => {
        const cls = seriesCls(C.groupOf(S.taxonomy, x.category));
        const sub = [x.category_label || "Uncategorized", `${x.count}×`, x.count > 1 ? `avg ${C.money(x.average, { cents: false })}` : "", x.one_off ? "one-off" : ""].filter(Boolean).join(" · ");
        return `<button class="mny-mer" data-merchant="${esc(x.merchant)}" title="See every charge">
          <span class="mny-mer-main"><bdi dir="auto" class="mny-mer-name">${esc(x.merchant)}</bdi><span class="mny-mer-sub">${esc(sub)}</span></span>
          ${sparkSvg(x.months, cls)}
          <span class="mny-mer-amt">${amt(x.total, { sign: "none" })}</span></button>`;
      }).join("")}</div>`;
    }

    function bigHtml(t) {
      const list = (t && t.one_offs) || [];
      const total = list.reduce((a, x) => a + Math.abs(x.amount || 0), 0);
      const head = `<div class="mny-card-head"><span>Big purchases</span>${list.length ? `<span class="mny-head-num">${amt(total, { sign: "none" })}</span>` : ""}</div>`;
      if (!t) return head + rowsSkel(2);
      if (!list.length) return `${head}<div class="mny-empty">No one-offs in ${esc(t.label)}.</div>`;
      return head + `<div class="mny-head-sub">One-offs: left out of everyday spending and the forecast</div>
        <div class="mny-big-list">${list.map((x) => `<div class="mny-big-row">
          <button class="mny-big-main" data-txn-go="${esc(x.id)}">
            <bdi dir="auto" class="mny-mer-name">${esc(x.merchant || x.description || "—")}</bdi>
            <span class="mny-mer-sub">${esc(C.shortDate(x.purchase_date, today()))}${x.category_label ? ` · ${esc(x.category_label)}` : ""}</span></button>
          <span class="mny-big-amt">${amt(x.amount, { sign: "none" })}</span>
          <button class="chip mny-big-no" data-oneoff-no="${esc(x.id)}" title="Count it as everyday spending">Not a one-off</button>
        </div>`).join("")}</div>`;
    }

    // ================================ Cash flow ================================

    function cashflow(el) {
      el.innerHTML = `<section class="mny-card mny-cf" aria-label="Money in and out">${cfHtml()}</section>
        <div class="mny-cols cf">
          <div class="mny-col"><section class="mny-card mny-bal" id="mny-bal" aria-label="Bank balance">${balHtml()}</section></div>
          <div class="mny-col"><section class="mny-card mny-fixed" id="mny-fixed" aria-label="Fixed costs">${fixedHtml()}</section></div>
        </div>`;
      drawCf();
      drawBal();
    }

    function cfHtml() {
      const cf = got("cashflow");
      const head = `<div class="mny-card-head"><span>Money in and out</span><span class="mny-head-hint">12 months to ${esc(C.monthLabel(S.month, { short: true }))}</span></div>`;
      if (!cf) return head + `<div class="mny-chart-skel"></div>`;
      if (!cf.months.some((m) => m.income || m.spent || m.set_aside)) return `${head}<div class="mny-empty">Nothing in or out in these 12 months yet.</div>`;
      return head + `<div class="mny-chart-wrap" id="mny-cf-wrap"></div><div class="mny-legend wrap" id="mny-cf-legend"></div>
        <div class="mny-cf-totals">${esc(C.cashflowTotalsText(cf))}</div>
        <div class="mny-chart-note">Income above the line; spending below it — fixed, yearly, everyday and one-offs. Money set aside is the thin bar beside: not spending. Click a month to open it.</div>`;
    }

    function drawCf() {
      const wrap = $("mny-cf-wrap");
      const cf = got("cashflow");
      if (!wrap || !cf) return;
      const narrow = (wrap.clientWidth || 600) < 480;
      const g = C.cashflowGeometry(cf, { width: width(wrap), height: narrow ? 210 : 250, left: narrow ? 44 : 52 });
      const bars = g.bars.map((b, i) => {
        const sel = b.month === S.month;
        const segs = [b.income, ...b.segments, b.setAside].filter(Boolean).map((s) => `<path class="cf-${s.key}" d="${C.barPath(s)}"/>`).join("");
        return `<g class="mny-bar${sel ? " sel" : ""}${b.complete ? "" : " partial"}" data-i="${i}">${segs}
          ${sel ? `<rect class="mny-bar-sel" x="${b.slotX + 1}" y="${g.T - 4}" width="${b.slotW - 2}" height="${g.plotH + 8}" rx="4"/>` : ""}</g>`;
      }).join("");
      const net = g.netPath ? `<path class="mny-net-line" d="${g.netPath}"/>${g.bars.filter((b) => b.net.show)
        .map((b) => `<circle class="mny-net-dot${b.net.v < 0 ? " neg" : ""}" cx="${b.net.cx}" cy="${b.net.cy}" r="${b.net.v ? 4 : 2.5}"/>`).join("")}` : "";
      const hits = g.bars.map((b, i) => `<rect class="mny-hit" data-month-bar="${b.month}" data-i="${i}" x="${b.slotX}" y="0" width="${b.slotW}" height="${g.H}"/>`).join("");
      const xl = g.xLabels.filter((l) => l.show).map((l) => `<text class="mny-axis${l.month === S.month ? " sel" : ""}" x="${l.x}" y="${g.H - 6}" text-anchor="middle">${esc(l.text)}</text>`).join("");
      wrap.innerHTML = `<svg viewBox="0 0 ${g.W} ${g.H}" height="${g.H}" role="img" aria-label="Money in and out per month for 12 months">${gridY(g, g.ticks)}${bars}${net}${xl}${hits}</svg><div class="mny-tip hidden"></div>`;
      $("mny-cf-legend").innerHTML = g.legend.map((l) => `<span><i class="mny-key ${l.key === "net" ? "net" : `sq cf-${l.key}`}"></i>${esc(l.label)}</span>`).join("");
      const svg = wrap.querySelector("svg"), tip = wrap.querySelector(".mny-tip");
      const line = (cls, label, v, o = {}) => (v ? `<span><i class="mny-key sq cf-${cls}"></i>${esc(label)} ${amt(v, { sign: "none", ...o })}</span>` : "");
      svg.addEventListener("pointermove", (ev) => {
        const hit = ev.target.closest("[data-month-bar]");
        svg.querySelectorAll(".mny-bar.hover").forEach((n) => n.classList.remove("hover"));
        if (!hit) { tip.classList.add("hidden"); return; }
        const i = +hit.dataset.i, b = g.bars[i], m = cf.months[i];
        svg.querySelector(`.mny-bar[data-i="${i}"]`)?.classList.add("hover");
        tip.innerHTML = `<strong>${esc(m.label)}${m.complete ? "" : " · so far"}</strong>
          ${line("income", "Income", m.income)}${line("fixed", "Fixed", m.fixed)}${line("yearly", "Yearly", m.yearly)}
          ${line("everyday", "Everyday", m.everyday)}${line("one_off", "One-off", m.one_off)}${line("set_aside", "Set aside", m.set_aside)}
          <span><i class="mny-key net"></i>Net ${amt(m.net, { sign: "plus" })}</span>
          ${m.savings_rate != null ? `<span class="mny-muted">${m.savings_rate >= 0 ? `Kept ${Math.round(m.savings_rate * 100)}% of income` : "More out than in"}</span>` : ""}`;
        placeTip(tip, svg, g, b.cx);
      });
      svg.addEventListener("pointerleave", () => { tip.classList.add("hidden"); svg.querySelectorAll(".mny-bar.hover").forEach((n) => n.classList.remove("hover")); });
    }

    function balHtml() {
      const seg = `<span class="mny-seg" role="radiogroup" aria-label="Forecast horizon">${HORIZONS.map(([d, label]) =>
        `<button role="radio" aria-checked="${S.horizon === d}" class="${S.horizon === d ? "on" : ""}" data-horizon="${d}">${label}</button>`).join("")}</span>`;
      const head = `<div class="mny-card-head"><span>Bank balance</span>${seg}</div>`;
      const b = got("balance");
      if (!b) return head + `<div class="mny-chart-skel"></div>`;
      if (!b.history.length && !b.forecast) return `${head}<div class="mny-empty">Add a bank account to see its balance and where it's heading.</div>`;
      const fc = b.forecast;
      const now = fc ? fc.balance : b.history[b.history.length - 1].balance;
      const low = fc && fc.low_point;
      const last = fc && fc.series.length ? fc.series[fc.series.length - 1] : null;
      return head + `<div class="mny-bal-now">${amt(now)} now${last ? ` · about ${whole(last.balance)} on ${esc(C.shortDate(last.date, today()))}` : ""}
          ${low ? `<span class="mny-bal-lowtxt">lowest ${whole(low.balance)} on ${esc(C.shortDate(low.date, today()))}</span>` : ""}</div>
        <div class="mny-chart-wrap" id="mny-bal-wrap"></div>
        <div class="mny-legend wrap"><span><i class="mny-key bal"></i>Balance</span>${fc ? `<span><i class="mny-key bal fc"></i>Forecast</span><span><i class="mny-key band bal"></i>Likely range</span>` : ""}</div>
        ${fc ? `<div class="mny-chart-note">${esc(C.forecastNote(fc))}</div>` : ""}`;
    }

    function drawBal() {
      const wrap = $("mny-bal-wrap");
      const b = got("balance");
      if (!wrap || !b) return;
      const narrow = (wrap.clientWidth || 600) < 480;
      const g = C.balanceGeometry(b, { width: width(wrap), height: narrow ? 190 : 220, left: narrow ? 46 : 56, historyDays: S.horizon });
      if (!g) { wrap.innerHTML = ""; return; }
      const xl = g.xLabels.filter((l) => l.show).map((l) => `<text class="mny-axis" x="${l.x}" y="${g.H - 6}" text-anchor="middle">${esc(l.text)}</text>`).join("");
      const low = g.low ? `<circle class="mny-bal-low" cx="${g.low.x}" cy="${g.low.y}" r="4.5"/>
        <text class="mny-axis low" x="${g.low.x}" y="${Math.min(g.H - g.B - 4, g.low.y + 16)}" text-anchor="${g.low.x > g.W - 90 ? "end" : g.low.x < g.L + 60 ? "start" : "middle"}">lowest ${esc(C.compact(g.low.balance))}</text>` : "";
      wrap.innerHTML = `<svg viewBox="0 0 ${g.W} ${g.H}" height="${g.H}" role="img" aria-label="Bank balance: the past and the forecast">
          ${gridY(g, g.ticks)}${xl}
          ${g.band ? `<path class="mny-bal-band" d="${g.band}"/>` : ""}
          <line class="mny-today" x1="${g.today.x}" x2="${g.today.x}" y1="${g.T}" y2="${g.H - g.B}"/>
          <text class="mny-axis today" x="${g.today.x}" y="${g.T - 3}" text-anchor="middle">today</text>
          ${g.history ? `<path class="mny-bal-line" d="${g.history}"/>` : ""}
          ${g.forecast ? `<path class="mny-bal-line fc" d="${g.forecast}"/>` : ""}
          ${low}${crossHtml(g)}
        </svg><div class="mny-tip hidden"></div>`;
      hoverPoints(wrap, g);
    }

    function fixedHtml() {
      const ov = got("overview");
      const r = got("recurring");
      const b = got("balance");
      const total = ov ? `<span class="mny-head-num">${amt(ov.fixed_monthly || 0, { sign: "none" })}<em>/ month</em></span>` : "";
      const head = `<div class="mny-card-head"><span>Fixed costs</span>${total}</div>`;
      if (!r) return head + rowsSkel(5);
      const meta = (x, extra = []) => [C.cadenceLabel(x.cadence), x.next_date ? `next ${C.shortDate(x.next_date, today())}` : "", ...extra,
                                       C.accountName(acctOf(x.account_id)) || x.account_label].filter(Boolean).join(" · ");
      const item = (x) => {
        const ca = C.cadenceAmount(x);
        const up = x.price_change && x.price_change.to > x.price_change.from;
        return `<button class="mny-sub" data-merchant="${esc(x.merchant)}" title="See every charge">
          <span class="mny-sub-main"><bdi dir="auto" class="mny-sub-name">${esc(x.merchant)}</bdi>
            <span class="mny-sub-meta">${esc(meta(x))}</span>
            ${x.price_change ? `<span class="mny-flag ${up ? "up" : "down"}">${icon(up ? "arrow-up-right" : "arrow-down-right")}${esc(C.priceChangeText(x.price_change, today()))}</span>` : ""}</span>
          <span class="mny-sub-amt${x.direction === "in" ? " in" : ""}"><span><bdi dir="ltr" class="mny-amt">${x.direction === "in" ? "+" : ""}${esc(ca.amount)}</bdi><em>${esc(ca.per)}</em></span>
            ${ca.monthly ? `<span class="mny-sub-mo">${esc(ca.monthly)}</span>` : ""}</span></button>`;
      };
      const items = (r.items || []).filter((x) => x.direction !== "in");
      const keys = new Set(items.map((x) => x.merchant_key));
      const yearly = (r.yearly || []).filter((y) => !keys.has(y.merchant_key));
      const plans = (b && b.forecast && b.forecast.plans) || [];
      const yRow = (y) => {
        const ca = C.cadenceAmount({ ...y, cadence: "yearly" });
        const m = [y.next_date ? `next ${C.shortDate(y.next_date, today())}` : "", y.payments > 1 ? `in ${y.payments} payments` : "", y.category_label].filter(Boolean).join(" · ");
        return `<button class="mny-sub" data-merchant="${esc(y.merchant)}" title="See every charge">
          <span class="mny-sub-main"><bdi dir="auto" class="mny-sub-name">${esc(y.merchant)}</bdi><span class="mny-sub-meta">${esc(m)}</span></span>
          <span class="mny-sub-amt"><span><bdi dir="ltr" class="mny-amt">${esc(ca.amount)}</bdi><em>${esc(ca.per)}</em></span>
            ${ca.monthly ? `<span class="mny-sub-mo">${esc(ca.monthly)}</span>` : ""}</span></button>`;
      };
      const pRow = (p) => {
        const pt = C.planText(p, today());
        return `<button class="mny-sub" data-merchant="${esc(p.merchant)}" title="See every charge">
          <span class="mny-sub-main"><bdi dir="auto" class="mny-sub-name">${esc(p.merchant)}</bdi>
            <span class="mny-sub-meta">${esc([pt.left, pt.next, C.accountName(acctOf(p.account_id))].filter(Boolean).join(" · "))}</span></span>
          <span class="mny-sub-amt"><span>${amt(p.left, { sign: "none" })}<em>to go</em></span>
            <span class="mny-sub-mo">${esc(C.money(p.amount, { sign: "none" }))} a payment</span></span></button>`;
      };
      const section = (label, rows, empty) => `<div class="mny-sub-label">${label}</div>${rows.length ? `<div class="mny-subs-list">${rows.join("")}</div>` : `<div class="mny-empty slim">${empty}</div>`}`;
      return head
        + section("Recurring", items.map(item), "No subscriptions or standing orders found yet. They show up after three regular charges.")
        + (yearly.length ? section("Yearly charges", yearly.map(yRow), "") : "")
        + (plans.length ? section("Installment plans", plans.map(pRow), "") : "")
        + ((r.income || []).length ? section("Regular income", r.income.map(item), "") : "");
    }

    // ================================ Investments ================================

    function investments(el) {
      const p = got("portfolio");
      if (!p) {
        el.innerHTML = `<div class="mny-tiles">${`<div class="mny-tile mny-skel"></div>`.repeat(4)}</div>
          <div class="mny-cols inv"><div class="mny-col">${skelCard("Portfolio value")}</div><div class="mny-col">${skelCard("Allocation", rowsSkel(4))}</div></div>`;
        return;
      }
      if (!p.accounts.length) {
        el.innerHTML = `<section class="mny-card"><div class="mny-empty">No investment account yet. Add Psagot Trade under Banks &amp; cards:
          its holdings, their value and the account's activity show up here after the first sync.</div></section>`;
        return;
      }
      el.innerHTML = `<div class="mny-tiles">${invTilesHtml(C.portfolioTiles(p, Date.now()))}</div>
        <div class="mny-cols inv">
          <div class="mny-col">
            <section class="mny-card mny-inv-chart o1" aria-label="Portfolio value">${invChartHtml(p)}</section>
            <section class="mny-card mny-holdings o3" aria-label="Holdings">${holdingsHtml(p)}</section>
          </div>
          <div class="mny-col">
            <section class="mny-card mny-alloc o2" aria-label="Allocation">${allocHtml(p)}</section>
            <section class="mny-card mny-trades o4" aria-label="Activity">${tradesHtml(p)}</section>
          </div>
        </div>
        <div class="mny-chart-note mny-inv-note">${icon("eye", "mny-ts-i")}Read-only: Dana signs in as a view-only device and never places orders.</div>`;
      drawInv();
    }

    function invTilesHtml(t) {
      const signed = (v, o = {}) => (v == null ? `<span class="mny-muted">—</span>` : amt(v, { sign: "plus", cents: false, ...o }));
      return `<div class="mny-tile"><span class="mny-tl">Portfolio value</span>
          <span class="mny-tv">${whole(t.value.value)}</span><span class="mny-ts">${esc(t.value.sub)}</span></div>
        <div class="mny-tile"><span class="mny-tl">Today</span>
          <span class="mny-tv ${t.today.tone}">${signed(t.today.value, { cents: true })}</span><span class="mny-ts">${esc(t.today.sub)}</span></div>
        <div class="mny-tile"><span class="mny-tl">Profit</span>
          <span class="mny-tv ${t.profit.tone}">${signed(t.profit.value)}</span><span class="mny-ts">${esc(t.profit.sub)}</span></div>
        <div class="mny-tile"><span class="mny-tl">Cash</span>
          <span class="mny-tv">${whole(t.cash.value)}</span><span class="mny-ts">${esc(t.cash.sub || "not invested")}</span></div>`;
    }

    function invChartHtml(p) {
      const seg = `<span class="mny-seg" role="radiogroup" aria-label="Range">${INV_RANGES.map(([d, label]) =>
        `<button role="radio" aria-checked="${S.invDays === d}" class="${S.invDays === d ? "on" : ""}" data-inv-range="${d}">${label}</button>`).join("")}</span>`;
      const head = `<div class="mny-card-head"><span>Portfolio value</span>${seg}</div>`;
      const g = C.valueGeometry(p.history, { days: S.invDays, flows: p.flows });
      if (!g) return `${head}<div class="mny-empty">The value over time shows up after the next sync.</div>`;
      const change = C.valueChangeText(g, today());
      return `${head}${change ? `<div class="mny-bal-now">${esc(change)}</div>` : ""}<div class="mny-chart-wrap" id="mny-inv-wrap"></div>
        ${g.marks.length ? `<div class="mny-legend wrap"><span><i class="mny-key bal"></i>Value</span><span><i class="mny-key inv-flow"></i>Money put in or taken out</span></div>` : ""}
        <div class="mny-chart-note">The account's value each day, as the site reports it.</div>`;
    }

    function drawInv() {
      const wrap = $("mny-inv-wrap");
      const p = got("portfolio");
      if (!wrap || !p) return;
      const narrow = (wrap.clientWidth || 600) < 480;
      const g = C.valueGeometry(p.history, { width: width(wrap), height: narrow ? 190 : 230, left: narrow ? 46 : 56, days: S.invDays, flows: p.flows });
      if (!g) { wrap.innerHTML = ""; return; }
      const xl = g.xLabels.filter((l) => l.show).map((l) => `<text class="mny-axis" x="${l.x}" y="${g.H - 6}" text-anchor="middle">${esc(l.text)}</text>`).join("");
      const last = g.points[g.points.length - 1];
      wrap.innerHTML = `<svg viewBox="0 0 ${g.W} ${g.H}" height="${g.H}" role="img" aria-label="Portfolio value over time">
          ${gridY(g, g.ticks)}${xl}
          ${g.area ? `<path class="mny-inv-area" d="${g.area}"/>` : ""}
          <path class="mny-bal-line" d="${g.line}"/>
          <circle class="mny-inv-last" cx="${last.x}" cy="${last.y}" r="3.5"/>
          ${g.marks.map((m) => `<circle class="mny-inv-flow${m.flow < 0 ? " out" : ""}" cx="${m.x}" cy="${m.y}" r="4.5"/>`).join("")}
          ${crossHtml(g)}
        </svg><div class="mny-tip hidden"></div>`;
      hoverPoints(wrap, g, (pt) => `<strong>${esc(C.longDate(pt.date))}</strong><span>Value ${whole(pt.value)}</span>${
        pt.flow ? `<span class="mny-muted">${pt.flow > 0 ? "Put in" : "Taken out"} ${C.moneyText(Math.abs(pt.flow), { cents: false })}</span>` : ""}`);
    }

    function allocHtml(p) {
      const slices = C.allocationSlices(p.allocation, 8);
      const head = `<div class="mny-card-head"><span>Allocation</span></div>`;
      if (!slices.length) return `${head}<div class="mny-empty">Nothing held yet.</div>`;
      const bar = `<div class="mny-alloc-bar" role="img" aria-label="Allocation: ${esc(slices.map((a) => `${C.isolateHebrew(a.name)} ${a.text}`).join(", "))}">${
        slices.map((a) => `<span class="inv-${a.slot}" style="width:${a.width}%" title="${esc(a.name)} · ${esc(a.text)}"></span>`).join("")}</div>`;
      const rows = slices.map((a) => `<li><i class="mny-key sq inv-${a.slot}"></i><bdi dir="auto" class="mny-alloc-name">${esc(a.name)}</bdi>
          <span class="mny-alloc-pct">${esc(a.text)}</span><span class="mny-alloc-v">${whole(a.value)}</span></li>`).join("");
      return `${head}${bar}<ul class="mny-alloc-list">${rows}</ul>`;
    }

    function holdingsHtml(p) {
      const total = (p.total && p.total.value) || 0;
      const count = p.accounts.reduce((n, a) => n + a.holdings.length, 0);
      const head = `<div class="mny-card-head"><span>Holdings</span><span class="mny-head-hint">${count} ${count === 1 ? "security" : "securities"}</span></div>`;
      const row = (h) => {
        const meta = [h.quantity != null ? `${C.quantityText(h.quantity)} × ${C.unitPrice(h.price, h.currency)}` : "",
                      h.currency && !["ILS", "NIS"].includes(String(h.currency).toUpperCase()) ? h.currency : "",
                      total && h.value ? `${C.pctText((h.value / total) * 100, { sign: "none", digits: 1 })} of the portfolio` : "",
                      h.security ? `#${h.security}` : ""].filter(Boolean).join(" · ");
        const tone = h.profit == null || h.profit === 0 ? "" : h.profit < 0 ? "neg" : "in";
        return `<div class="mny-hold">
          <span class="mny-hold-main"><bdi dir="auto" class="mny-hold-name">${esc(h.name || `Security #${h.security}`)}</bdi><span class="mny-hold-meta">${esc(meta)}</span></span>
          <span class="mny-hold-num"><span>${h.value != null ? whole(h.value) : `<span class="mny-muted">—</span>`}</span>
            ${h.profit != null ? `<span class="mny-hold-pl ${tone}">${amt(h.profit, { sign: "plus", cents: false })}${h.profit_pct != null ? ` · ${esc(C.pctText(h.profit_pct))}` : ""}</span>` : ""}</span>
        </div>`;
      };
      const many = p.accounts.length > 1;
      const body = p.accounts.map((a) => `${many ? `<div class="mny-sub-label">${esc(C.accountName({ ...a, kind: "investment" }))} · ${C.moneyText(a.value, { cents: false })}</div>` : ""}${
        a.holdings.length ? a.holdings.map(row).join("") : `<div class="mny-empty slim">No holdings in this account.</div>`}`).join("");
      return head + body;
    }

    function tradesHtml(p) {
      const head = `<div class="mny-card-head"><span>Activity</span><span class="mny-head-hint">buys, sells, dividends, tax</span></div>`;
      if (!p.trades.length) return `${head}<div class="mny-empty">No activity in the last year.</div>`;
      return head + `<div class="mny-trade-list">${p.trades.map((t) => {
        const x = C.tradeText(t, today());
        return `<div class="mny-trade">
          <span class="mny-hold-main"><span class="mny-hold-name"><bdi dir="auto">${esc(x.title)}</bdi>${x.name ? ` <span class="mny-muted">·</span> <bdi dir="auto">${esc(x.name)}</bdi>` : ""}</span>
            <span class="mny-hold-meta">${x.meta.map((m) => `<bdi dir="auto">${esc(m)}</bdi>`).join(" · ")}</span></span>
          <span class="mny-trade-amt ${x.tone}">${x.amount ? amt(x.amount, { sign: "plus" }) : ""}</span>
        </div>`;
      }).join("")}</div>`;
    }

    // ================================ Meal card ================================

    function meals(el) {
      const m = got("meals");
      if (!m) {
        el.innerHTML = `<div class="mny-tiles">${`<div class="mny-tile mny-skel"></div>`.repeat(4)}</div>
          <div class="mny-cols inv"><div class="mny-col">${skelCard("Cycles", rowsSkel(6))}</div><div class="mny-col">${skelCard("Places", rowsSkel(5))}</div></div>`;
        return;
      }
      const t = C.mealTiles(m, today());
      if (!t) {
        el.innerHTML = `<section class="mny-card"><div class="mny-empty">No meal card yet. Add Pluxee under Banks &amp; cards:
          its budget, what is left and every meal show up here after the first sync.</div></section>`;
        return;
      }
      el.innerHTML = `<div class="mny-tiles">${mealTilesHtml(t)}</div>
        <div class="mny-cols inv">
          <div class="mny-col">
            <section class="mny-card o1" aria-label="Cycles">${mealCyclesHtml(m)}</section>
            <section class="mny-card o3" aria-label="Latest meals">${mealListHtml(m)}</section>
          </div>
          <div class="mny-col">
            <section class="mny-card o2" aria-label="Places">${mealPlacesHtml(m)}</section>
          </div>
        </div>
        <div class="mny-chart-note mny-inv-note">${icon("info", "mny-ts-i")}The budget is your employer's money and never counts as your spending; only the part beyond it, on your own card, does.</div>`;
    }

    function mealTilesHtml(t) {
      return `<div class="mny-tile"><span class="mny-tl">Left this cycle</span>
          <span class="mny-tv">${whole(t.left.value)}</span><span class="mny-ts">${esc(t.left.sub)}</span></div>
        <div class="mny-tile"><span class="mny-tl">Pace</span>
          <span class="mny-tv ${t.pace.tone}">${esc(t.pace.text)}</span><span class="mny-ts">${esc(t.pace.sub)}</span></div>
        <div class="mny-tile"><span class="mny-tl">On your card</span>
          <span class="mny-tv">${whole(t.card.value)}</span><span class="mny-ts">${esc(t.card.sub)}</span></div>
        <div class="mny-tile"><span class="mny-tl">Meals</span>
          <span class="mny-tv">${esc(String(t.count.value))}</span><span class="mny-ts">${esc(t.count.sub)}</span></div>`;
    }

    function mealCyclesHtml(m) {
      const bars = C.mealCycleBars(m.cycles, today());
      const head = `<div class="mny-card-head"><span>Cycles</span><span class="mny-head-hint">newest first</span></div>`;
      if (!bars.length) return `${head}<div class="mny-empty">No meals yet.</div>`;
      const rows = bars.slice().reverse().map((b) => `<div class="mny-ml-cycle">
          <div class="mny-ml-top"><span class="mny-ml-label">${esc(b.label)}</span>
            <span class="mny-ml-sum">${whole(b.budget)}${b.card ? ` <span class="mny-ml-over">+ ${amt(b.card, { sign: "none" })}</span>` : ""}</span></div>
          <div class="mny-ml-bar" role="img" aria-label="${esc(`${b.label}: ${C.moneyText(b.budget, { cents: false })} from the budget${b.card ? `, ${C.moneyText(b.card)} on your card` : ""}`)}">
            <span class="b" style="width:${b.budgetPct.toFixed(1)}%"></span>${b.card ? `<span class="c" style="width:${Math.max(1, b.cardPct).toFixed(1)}%"></span>` : ""}</div>
          <div class="mny-ml-meta">${b.count} ${b.count === 1 ? "meal" : "meals"}${b.billed ? ` · ${b.txn
            ? `<button class="mny-link" data-link-view="transactions" data-link-id="${esc(b.txn)}">${esc(b.billed)}</button>` : esc(b.billed)}` : ""}</div>
        </div>`).join("");
      return `${head}<div class="mny-legend"><span><i class="mny-key sq ml-b"></i>From the budget</span><span><i class="mny-key sq ml-c"></i>On your card</span></div>${rows}`;
    }

    function mealPlacesHtml(m) {
      const head = `<div class="mny-card-head"><span>Places</span><span class="mny-head-hint">most visits first</span></div>`;
      if (!m.places.length) return `${head}<div class="mny-empty">No meals yet.</div>`;
      const kinds = m.kinds.length > 1 ? `<div class="mny-ml-kinds">${m.kinds.map((k) => `<span>${esc(k.label)}: ${k.count} · ${whole(k.total)}</span>`).join("")}</div>` : "";
      return head + kinds + m.places.map((p) => `<div class="mny-hold">
          <span class="mny-hold-main"><bdi dir="auto" class="mny-hold-name">${esc(p.place)}</bdi><span class="mny-hold-meta">${p.count} ${p.count === 1 ? "visit" : "visits"}</span></span>
          <span class="mny-hold-num"><span>${whole(p.total)}</span></span></div>`).join("");
    }

    function mealListHtml(m) {
      const head = `<div class="mny-card-head"><span>Latest meals</span></div>`;
      if (!m.meals.length) return `${head}<div class="mny-empty">No meals yet.</div>`;
      return head + `<div class="mny-trade-list">${m.meals.map((x) => {
        const time = C.timeOf(x.occurred_at);
        const meta = [C.shortDate(x.date, today()) + (time ? `, ${time}` : ""), x.order_type === "delivery" ? "Delivery" : "",
                      x.card_paid ? `${C.moneyText(x.card_paid)} on your card` : "", x.active ? "" : "Cancelled"].filter(Boolean).join(" · ");
        return `<div class="mny-trade${x.active ? "" : " gone"}">
          <span class="mny-hold-main"><bdi dir="auto" class="mny-hold-name">${esc(x.place || "—")}</bdi><span class="mny-hold-meta">${esc(meta)}</span></span>
          <span class="mny-trade-amt">${amt(x.total, { sign: "none" })}</span></div>`;
      }).join("")}</div>`;
    }

    // ================================ Save ================================

    function save(el) {
      const sv = got("savings");
      if (!sv) {
        el.innerHTML = `<div class="mny-tiles sv">${`<div class="mny-tile mny-skel"></div>`.repeat(2)}</div>
          <div class="mny-cols sv"><div class="mny-col">${skelCard("Ways to save", rowsSkel(5))}</div><div class="mny-col">${skelCard("Spending to watch", rowsSkel(3))}</div></div>`;
        return;
      }
      const day = sv.as_of || today();
      const t = C.saveTiles(sv, day);
      const parts = C.saveSections(sv.items, day);
      el.innerHTML = `<div class="mny-tiles sv">
          <div class="mny-tile"><span class="mny-tl">${icon("piggy-bank")}Could save</span>
            <span class="mny-tv in">${esc(t.could.text)}</span><span class="mny-ts">${esc(t.could.sub)}</span></div>
          <div class="mny-tile"><span class="mny-tl">${icon("circle-check")}Saved so far</span>
            <span class="mny-tv">${whole(t.saved.value)}</span><span class="mny-ts">${esc(t.saved.sub)}</span></div>
        </div>
        <div class="mny-sv-source">${icon("info", "mny-ts-i")}<span>${esc(t.source)}</span></div>
        <div class="mny-cols sv">${parts.map((p, i) => `<div class="mny-col"><section class="mny-sv-part o${i + 1}" aria-label="${esc(p.label)}">${partHtml(p, day)}</section></div>`).join("")}</div>`;
    }

    function partHtml(p, day) {
      const head = `<h2 class="mny-sv-part-head"><span>${esc(p.label)}</span><span class="mny-head-hint">${p.count ? `${p.count}` : ""}</span></h2>`;
      if (!p.count) {
        return `${head}<div class="mny-card mny-empty">${p.group === "watch" ? "Nothing in your spending stands out right now." : "No ways to save found right now."} Dana looks again after every sync.</div>`;
      }
      return head + p.sections.map((s) => {
        const id = `${p.group}:${s.key}`;
        const shut = S.saveCollapsed.has(id);
        return `<div class="mny-sv-sec-wrap">
          <button class="mny-sv-sec-head" data-save-section="${esc(id)}" aria-expanded="${!shut}">${icon(shut ? "chevron-right" : "chevron-down")}<span>${esc(s.label)}</span><span class="mny-sv-count">${s.items.length}</span></button>
          ${shut ? "" : `<div class="mny-sv-list">${s.items.map((it) => cardHtml(it, s.key, day)).join("")}</div>`}</div>`;
      }).join("");
    }

    function cardHtml(it, section, day) {
      const open = S.saveOpen === it.key;
      const k = esc(it.key);
      const bid = `mny-svb-${++svSeq}`;
      const chip = C.savingChip(it), effort = C.effortText(it.effort), basis = C.basisChip(it), line = C.statusLine(it, day);
      const chips = [
        chip ? `<span class="mny-sv-chip ${chip.kind}" title="${esc(chip.title)}">${esc(chip.text)}</span>` : "",
        effort ? `<span class="mny-sv-chip">${icon(EFFORT_ICONS[it.effort.how] || "clock")}<span>${esc(effort)}</span></span>` : "",
        basis ? `<span class="mny-sv-chip basis" title="${esc(basis.title)}">${esc(basis.text)}</span>` : "",
        it.timing && it.timing.label ? `<span class="mny-sv-chip when">${icon("calendar")}<span dir="ltr">${heb(it.timing.label)}</span></span>` : "",
        it.new && it.status === "open" ? `<span class="mny-badge b-new">New</span>` : "",
      ].join("");
      const notes = [
        line ? `<span class="mny-sv-status st-${esc(it.status)}">${esc(line)}</span>` : "",
        it.active === false ? `<span class="mny-sv-gone">${icon("eye-off")}Dana no longer sees this</span>` : "",
      ].join("");
      // Watching shows the target's progress and Done / In progress the outcome without opening the card.
      const tp = !open && it.target ? C.targetProgress(it.target) : null;
      const oc = !open && (section === "done" || section === "doing") ? C.outcomeText(it.outcome) : null;
      const glance = tp ? `<span class="mny-sv-glance"><span class="mny-sv-bar sm${tp.over ? " over" : ""}"><span style="width:${tp.bar}%"></span></span>
          <span>${esc(tp.text)}${tp.track ? ` · <span class="mny-sv-track tone-${tp.tone}">${esc(tp.track)}</span>` : ""}${tp.left ? ` · ${esc(tp.left)}` : ""}</span></span>`
        : oc ? `<span class="mny-sv-glance oc tone-${oc.tone}">${icon(oc.tone === "good" ? "circle-check" : oc.tone === "warn" ? "circle-alert" : "hourglass")}<span>${esc(oc.title)}${oc.sub ? ` · ${esc(oc.sub)}` : ""}</span></span>` : "";
      // The user's choices keep their way back on the card itself, not only inside it.
      const quick = section === "choices" && !open ? `<div class="mny-sv-quick">${actionsHtml(it, true)}</div>` : "";
      return `<article class="mny-sv${open ? " open" : ""}${it.active === false ? " inactive" : ""}" data-sv="${k}">
        <div class="mny-sv-row">
          <button class="mny-sv-head" data-save-toggle="${k}" aria-expanded="${open}"${open ? ` aria-controls="${bid}"` : ""}>
            <span class="mny-sv-icon g-${it.group === "watch" ? "watch" : "save"}">${icon(SAVE_ICONS[it.type] || "lightbulb")}</span>
            <span class="mny-sv-main">
              <span class="mny-sv-title" dir="ltr">${heb(it.title)}</span>
              ${it.summary ? `<span class="mny-sv-sum" dir="ltr">${heb(it.summary)}</span>` : ""}
              ${chips ? `<span class="mny-sv-chips">${chips}</span>` : ""}
              ${glance}${notes ? `<span class="mny-sv-notes">${notes}</span>` : ""}
            </span>
            ${icon(open ? "chevron-up" : "chevron-down", "mny-chev")}
          </button>${quick}
        </div>
        ${open ? `<div class="mny-sv-body" id="${bid}">${bodyHtml(it, day)}</div>` : ""}
      </article>`;
    }

    function bodyHtml(it, day) {
      const out = [];
      const sec = (label, html, cls = "") => `<div class="mny-sv-sec${cls ? " " + cls : ""}"><div class="mny-d-label">${esc(label)}</div>${html}</div>`;
      const oc = C.outcomeText(it.outcome);
      if (oc) {
        out.push(`<div class="mny-sv-outcome tone-${oc.tone}">${icon(oc.tone === "good" ? "circle-check" : oc.tone === "warn" ? "circle-alert" : "hourglass")}
          <span><strong>${esc(oc.title)}</strong>${oc.sub ? ` · ${esc(oc.sub)}` : ""}${oc.detail ? `<span class="mny-sv-od" dir="ltr">${heb(oc.detail)}</span>` : ""}</span></div>`);
      }
      if (it.group === "watch" && (it.target || S.saveTarget === it.key)) out.push(sec("Monthly target", targetHtml(it)));
      const ev = it.evidence || {};
      const lines = ev.lines || [];
      const sp = C.seriesSpark(ev.series, { width: 180, height: 44 }, day);
      const link = C.savingLink(ev.link);
      if (lines.length || sp || link) {
        out.push(sec("Why", `<div class="mny-sv-ev">
          ${sp ? `<figure class="mny-sv-spark"><svg viewBox="0 0 ${sp.W} ${sp.H}" width="${sp.W}" height="${sp.H}" role="img" aria-label="${esc(`${sp.count} amounts, ${sp.range}`)}">
              <path class="a" d="${sp.area}"/><path class="l" d="${sp.line}"/><circle cx="${sp.last.x}" cy="${sp.last.y}" r="2.6"/></svg>
            <figcaption>${esc(sp.range)}</figcaption></figure>` : ""}
          <div class="mny-sv-evtext">${lines.length ? `<ul class="mny-sv-lines">${lines.map((l) => `<li dir="ltr">${heb(l)}</li>`).join("")}</ul>` : ""}
            ${link ? `<button class="mny-link" data-save-link="${esc(it.key)}">${esc(link.label)}</button>` : ""}</div></div>`));
      }
      if ((it.math || []).length) out.push(sec("The math", `<ul class="mny-sv-math">${it.math.map((l) => `<li dir="ltr">${heb(l)}</li>`).join("")}</ul>`));
      // A question the price check needs first is asked inside the Prices block, right above its button.
      const needs = Boolean(it.market && it.market.needs_answer);
      if ((it.questions || []).length && !needs) out.push(sec(it.questions.length > 1 ? "A few questions" : "A question", questionsHtml(it)));
      if (it.market) out.push(sec("Prices", pricesHtml(it, day), "prices"));
      if ((it.subitems || []).length) out.push(sec("Your subscriptions", subsHtml(it, day)));
      if ((it.steps || []).length) {
        out.push(sec("What to do", `<ol class="mny-sv-steps">${it.steps.map((s) => `<li><span dir="ltr">${heb(s.text)}</span>${
          s.url && C.hostOf(s.url) ? ` <a class="mny-sv-ext" href="${esc(s.url)}" target="_blank" rel="noopener">${esc(C.hostOf(s.url))}${icon("external-link")}</a>` : ""}</li>`).join("")}</ol>`));
      }
      if (it.script && it.script.text) {
        out.push(sec("What to say", `<div class="mny-sv-script"><p dir="rtl" lang="${esc(it.script.lang || "he")}">${esc(it.script.text)}</p>
          <button class="btn-secondary" data-save-copy="${esc(it.key)}">${icon("copy")}<span>Copy</span></button></div>`));
      }
      out.push(`<div class="mny-sv-actions">${actionsHtml(it, false)}</div>`);
      return out.join("");
    }

    function actionsHtml(it, quick) {
      const busy = Boolean(S.saveBusy[it.key]);
      const k = esc(it.key);
      const editing = S.saveTarget === it.key;
      return C.statusActions(it).map((a, i) => {
        if (a.input && (quick || editing)) return "";
        const cls = quick ? "btn-secondary sm" : a.primary ? "btn-primary" : "btn-secondary";
        const attr = a.input ? `data-save-target="${k}"` : `data-save-act="${k}" data-act-i="${i}"`;
        return `<button class="${cls}" ${attr} ${busy ? "disabled" : ""}>${esc(a.label)}</button>`;
      }).join("");
    }

    function targetHtml(it) {
      const k = esc(it.key);
      const editing = S.saveTarget === it.key;
      const tp = C.targetProgress(it.target);
      let html = "";
      if (tp) {
        html += `<div class="mny-sv-target">
          <div class="mny-sv-tline"><span>${esc(tp.text)}</span>${tp.track ? `<span class="mny-sv-track tone-${tp.tone}">${esc(tp.track)}</span>` : ""}${tp.left ? `<span class="mny-muted">${esc(tp.left)}</span>` : ""}</div>
          <div class="mny-sv-bar${tp.over ? " over" : ""}" role="img" aria-label="${esc(tp.text)}"><span style="width:${tp.bar}%"></span></div>
          ${tp.months.length ? `<div class="mny-sv-months">${tp.months.map((m) => `<span class="${m.under ? "under" : "over"}">${icon(m.under ? "check" : "x")}${esc(m.label)} ${esc(m.text)}</span>`).join("")}</div>` : ""}
          ${tp.saved ? `<div class="mny-sv-saved">${icon("piggy-bank")}${esc(tp.saved)}</div>` : ""}</div>`;
      }
      if (editing) {
        const pre = S.saveDraft[it.key] ?? String(Math.round(((it.target && it.target.monthly) || it.suggested_target || 0) / 100) || "");
        html += `<form class="mny-sv-tform" data-save-target-form="${k}">
          <label><span>Spend at most</span><span class="mny-sv-tin">₪<input name="amount" data-keep="sv-target" data-save-draft="${k}" inputmode="numeric" autocomplete="off"
            value="${esc(pre)}" aria-label="Monthly target in shekels"></span><span>a month</span></label>
          <button class="btn-primary" type="submit" ${S.saveBusy[it.key] ? "disabled" : ""}>${it.target ? "Save target" : "Set target"}</button>
          <button class="btn-secondary" type="button" data-save-target-cancel="${k}">Cancel</button>
          ${it.suggested_target ? `<span class="mny-sv-hint">Halfway back to before is ${whole(it.suggested_target)}.</span>` : ""}
          ${S.saveTargetError ? `<span class="mny-err" role="alert">${esc(S.saveTargetError)}</span>` : ""}</form>`;
      }
      return html;
    }

    function questionsHtml(it) {
      const k = esc(it.key);
      const needed = it.market && it.market.needs_answer;
      return it.questions.map((q) => `<div class="mny-sv-q${needed === q.id ? " needed" : ""}">
          <span class="mny-sv-qt" dir="ltr">${heb(q.text)}</span>
          <span class="mny-sv-qopts" role="group" aria-label="${esc(q.text)}">${(q.options || []).map((o, i) => {
            const on = q.answer != null && String(o.value) === String(q.answer);
            return `<button class="chip${on ? " active" : ""}" aria-pressed="${on}" data-save-answer="${k}" data-q="${esc(q.id)}" data-i="${i}" ${S.saveBusy[it.key] ? "disabled" : ""}>${esc(o.label)}</button>`;
          }).join("")}</span></div>`).join("");
    }

    function pricesHtml(it, day) {
      const v = C.marketView(it.market, day);
      const note = S.checkNote[it.key];
      const busy = Boolean(S.saveBusy[it.key]);
      const k = esc(it.key);
      const status = v.state === "checking"
        ? `<div class="mny-sv-mtext busy"><span class="mny-spin"></span><span>${esc(v.text)}</span></div>`
        : v.text ? `<div class="mny-sv-mtext ${esc(v.state)}">${esc(v.text)}</div>` : "";
      const o = v.offers ? C.offerRows(it.market, day) : null;
      const table = o ? `<div class="mny-sv-offers"><table>
          <thead><tr>${o.columns.map((c) => `<th scope="col">${esc(c)}</th>`).join("")}</tr></thead>
          <tbody>${o.rows.map((r) => `<tr>
            <td class="nm" data-l="${esc(o.columns[0])}"><bdi dir="auto">${esc(r.name)}</bdi></td>
            ${o.hasPlan ? `<td data-l="Plan"><bdi dir="auto">${esc(r.plan)}</bdi></td>` : ""}
            ${r.cells.map((c) => `<td data-l="${esc(c.label)}" class="${c.strong ? "strong" : ""}${c.muted ? " muted" : ""}${c.text ? "" : " blank"}"><bdi dir="${c.dir || "ltr"}">${c.iso ? heb(c.text) : esc(c.text)}</bdi>${c.sub ? `<span class="sub">${esc(c.sub)}</span>` : ""}</td>`).join("")}
            <td data-l="Source">${r.source ? `<a class="mny-sv-ext" href="${esc(r.source.url)}" target="_blank" rel="noopener"><bdi dir="auto">${esc(r.source.title)}</bdi>${icon("external-link")}</a>` : `<span class="mny-muted">—</span>`}</td>
          </tr>`).join("")}</tbody></table></div>` : "";
      const foot = o ? `<div class="mny-sv-checked">${esc(v.checked)}${v.stale ? ` · <span class="stale">${esc(v.stale)}</span>` : ""}${
        o.sources.length ? `<span class="mny-sv-srcs">Sources: ${o.sources.map((s) => `<a href="${esc(s.url)}" target="_blank" rel="noopener"><bdi dir="auto">${esc(s.title)}</bdi></a>`).join(", ")}</span>` : ""}</div>` : "";
      const btn = v.button && v.state !== "checking"
        ? `<button class="btn-secondary mny-sv-check" data-save-check="${k}" ${busy ? "disabled" : ""}>${icon(busy ? "loader-circle" : "search")}<span>${esc(v.button)}</span></button>` : "";
      const msg = note && note.text ? `<div class="mny-sv-mtext ${note.kind === "error" ? "failed" : "wait"}" role="status">${esc(note.text)}</div>` : "";
      const ask = v.question && (it.questions || []).length ? questionsHtml(it) : "";
      return `${status}${ask}${table}${foot}${msg}${btn ? `<div class="mny-sv-checkrow">${btn}</div>` : ""}`;
    }

    function subsHtml(it, day) {
      const k = esc(it.key);
      const busy = Boolean(S.saveBusy[it.key]);
      return `<div class="mny-sv-subs">${it.subitems.map((s, i) => {
        const x = C.subitemText(s, day);
        const seg = ["keep", "cancel"].map((c) => `<button class="${s.choice === c ? "on" : ""}" aria-pressed="${s.choice === c}" data-save-sub="${k}" data-sub-i="${i}" data-choice="${c}" ${busy ? "disabled" : ""}>${c === "keep" ? "Keep" : "Cancel"}</button>`).join("");
        return `<div class="mny-sv-sub${s.active === false ? " gone" : ""}">
          <span class="mny-hold-main"><bdi dir="auto" class="mny-hold-name">${esc(s.merchant)}</bdi>
            <span class="mny-hold-meta">${[x.amount, x.yearly, x.next].filter(Boolean).map(esc).join(" · ")}</span></span>
          <span class="mny-seg" role="group" aria-label="${esc(s.merchant)}">${seg}</span></div>`;
      }).join("")}</div>`;
    }

    function redraw() {
      if (S.tab === "overview") { drawPace(); drawUpSpark(); }
      else if (S.tab === "spending") drawMonths();
      else if (S.tab === "cashflow") { drawCf(); drawBal(); }
      else if (S.tab === "investments") drawInv();
      icons();
    }

    return { overview, spending, cashflow, save, investments, meals, redraw };
  };
})();
