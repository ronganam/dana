/* Usage — what Dana costs, from the web.
 *
 *   GET /api/usage?range=&kind=&purpose=&source=&model=     totals, per day, breakdowns, previous period
 *   GET /api/usage/events?range=&sort=&limit=&before=&…     individual calls (kept USAGE_RAW_DAYS)
 * Clicking a breakdown row filters the whole screen by it. Exposes window.DanaUsage = {show, hide};
 * app-nav.js calls show/hide on tab switches. Server side: core/usage.py, docs/usage.md.
 */
(() => {
  "use strict";

  const RANGES = [
    { id: "today", label: "Today", prev: "yesterday" },
    { id: "7d", label: "7 days", prev: "the 7 days before" },
    { id: "30d", label: "30 days", prev: "the 30 days before" },
    { id: "90d", label: "90 days", prev: "the 90 days before" },
    { id: "1y", label: "1 year", prev: "the year before" },
    { id: "all", label: "All time", prev: "" },
  ];
  const TABS = [
    { id: "purpose", label: "What for" },
    { id: "source", label: "Source" },
    { id: "model", label: "Model" },
    { id: "kind", label: "Type" },
  ];
  const SOURCE_NAMES = { app: "App / web", phone: "Phone", telegram: "Telegram", whatsapp: "WhatsApp", background: "Background",
                         watch: "Watch", tablet: "Tablet", tv: "TV", mac: "Mac", web: "Web" };
  const PAGE = 30;
  const store = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (_) { return null; } };

  const S = { range: store("usg.range") || "30d", tab: store("usg.tab") || "purpose", sort: "recent", filters: {},
              data: null, events: [], next: null, visible: false, loading: false, error: "" };
  let timer = null;
  let seq = 0;

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const icons = () => { try { window.lucide?.createIcons(); } catch (_) { /* ignore */ } };
  const $ = (id) => document.getElementById(id);

  function money(v, hero = false) {
    v = Number(v) || 0;
    if (v === 0) return "$0.00";
    if (v < 0.01) return "$" + v.toFixed(4);
    if (v < 100 || hero) return "$" + v.toFixed(2);
    return "$" + Math.round(v).toLocaleString();
  }
  function compact(n) {
    n = Number(n) || 0;
    if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + "M";
    if (n >= 1e3) return (n / 1e3).toFixed(n >= 1e4 ? 0 : 1) + "k";
    return String(n);
  }
  const sourceName = (k) => SOURCE_NAMES[k] || k || "—";
  const labelOf = (dim, item) => (dim === "source" ? sourceName(item.key) : item.label);
  const fmtDay = (iso) => new Date(iso + "T12:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  function fmtWhen(ts) {
    const d = new Date(ts * 1000);
    const today = new Date();
    const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    return d.toDateString() === today.toDateString() ? time : d.toLocaleDateString(undefined, { day: "numeric", month: "short" }) + " " + time;
  }
  const ms = (v) => (v >= 1000 ? (v / 1000).toFixed(1) + " s" : v + " ms");

  async function api(path) {
    const r = await fetch(path);
    let data = null;
    try { data = await r.json(); } catch (_) { /* empty */ }
    if (!r.ok) throw new Error((data && data.error) || `HTTP ${r.status}`);
    return data;
  }
  const query = (extra = {}) => new URLSearchParams({ range: S.range, ...S.filters, ...extra });

  // ---------- data ----------
  async function load({ more = false } = {}) {
    const mine = ++seq;
    S.loading = !more;
    if (!more) { S.error = ""; renderHero(); }
    try {
      if (more) {
        const page = await api(`/api/usage/events?${query({ sort: S.sort, limit: PAGE, before: S.next })}`);
        S.events = S.events.concat(page.events);
        S.next = page.next;
      } else {
        const [data, page] = await Promise.all([
          api(`/api/usage?${query()}`),
          api(`/api/usage/events?${query({ sort: S.sort, limit: PAGE })}`),
        ]);
        if (mine !== seq) return;
        S.data = data; S.events = page.events; S.next = page.next;
      }
    } catch (e) {
      if (mine !== seq) return;
      S.error = e.message;
    }
    S.loading = false;
    render();
  }

  function setFilter(dim, key) {
    if (S.filters[dim] === key) delete S.filters[dim]; else S.filters[dim] = key;
    load();
  }

  // ---------- render ----------
  function render() {
    renderRanges();
    renderHero();
    renderChart();
    renderBreakdown();
    renderEvents();
    icons();
  }

  function renderRanges() {
    $("usg-ranges").innerHTML = RANGES.map((r) =>
      `<button class="chip${r.id === S.range ? " active" : ""}" data-range="${r.id}">${r.label}</button>`).join("");
  }

  function renderHero() {
    const el = $("usg-hero");
    if (S.error && !S.data) { el.innerHTML = `<div class="usg-error"><strong>Couldn't load usage</strong><span>${esc(S.error)}</span></div>`; return; }
    const d = S.data;
    if (!d) { el.innerHTML = `<div class="usg-skel"></div>`; return; }
    const t = d.totals;
    const range = RANGES.find((r) => r.id === S.range) || RANGES[2];
    let delta = "";
    if (d.previous && d.previous.cost > 0) {
      const pct = Math.round(((t.cost - d.previous.cost) / d.previous.cost) * 100);
      delta = `<span class="usg-delta">${pct === 0 ? "same as" : (pct > 0 ? "▲ " : "▼ ") + Math.abs(pct) + "% vs"} ${esc(range.prev)} (${money(d.previous.cost)})</span>`;
    }
    const filters = Object.entries(S.filters).map(([dim, key]) => {
      const item = (d.breakdown[dim] || []).find((i) => i.key === key);
      const name = dim === "source" ? sourceName(key) : (item ? item.label : key || "—");
      return `<button class="chip active usg-filter" data-clear="${esc(dim)}" title="Remove filter">${esc(name)} <i data-lucide="x"></i></button>`;
    }).join("");
    const unpriced = t.unpriced
      ? `<div class="usg-note"><i data-lucide="circle-help"></i><span>${t.unpriced.toLocaleString()} call${t.unpriced === 1 ? "" : "s"} ha${t.unpriced === 1 ? "s" : "ve"} no known price, so ${t.unpriced === 1 ? "it isn't" : "they aren't"} in the total (LiteLLM has no price for that model).</span></div>` : "";
    el.innerHTML = `
      <div class="usg-hero-main">
        <div class="usg-total">${money(t.cost, true)}</div>
        <div class="usg-sub">${esc(range.label.toLowerCase())}${Object.keys(S.filters).length ? " · filtered" : ""} · ${t.calls.toLocaleString()} calls${t.failures ? ` · ${t.failures.toLocaleString()} failed` : ""}</div>
        ${delta}
      </div>
      <div class="usg-stats">
        <div class="usg-stat"><span class="v">${compact(t.input_tokens)}</span><span class="l">tokens in</span></div>
        <div class="usg-stat"><span class="v">${compact(t.output_tokens)}</span><span class="l">tokens out</span></div>
        <div class="usg-stat"><span class="v">${t.input_tokens ? Math.round((t.cached_tokens / t.input_tokens) * 100) : 0}%</span><span class="l">from cache</span></div>
        <div class="usg-stat"><span class="v">${t.calls ? ms(t.avg_ms) : "—"}</span><span class="l">avg call</span></div>
      </div>
      ${filters ? `<div class="usg-filters">${filters}</div>` : ""}
      ${unpriced}`;
  }

  // Per-day bars; long ranges are summed per week so bars stay readable.
  function barsFor(days) {
    if (days.length <= 100) return days.map((d) => ({ from: d.day, to: d.day, cost: d.cost, calls: d.calls }));
    const out = [];
    for (let i = 0; i < days.length; i += 7) {
      const chunk = days.slice(i, i + 7);
      out.push({ from: chunk[0].day, to: chunk[chunk.length - 1].day, cost: chunk.reduce((a, c) => a + c.cost, 0), calls: chunk.reduce((a, c) => a + c.calls, 0) });
    }
    return out;
  }

  function niceMax(v) {
    if (v <= 0) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    const n = v / p;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
  }

  function renderChart() {
    const el = $("usg-chart");
    const d = S.data;
    if (!d) { el.innerHTML = ""; return; }
    const bars = barsFor(d.by_day);
    const weekly = d.by_day.length > 100;
    if (!bars.length || !bars.some((b) => b.cost > 0 || b.calls > 0)) {
      el.innerHTML = `<div class="usg-empty">Nothing recorded in this range yet.</div>`;
      return;
    }
    const W = Math.max(280, Math.round((el.clientWidth || 720) - 34)), H = 180, L = 44, R = 6, T = 8, B = 22;
    const max = niceMax(Math.max(...bars.map((b) => b.cost)));
    const plotW = W - L - R, plotH = H - T - B;
    const slot = plotW / bars.length;
    const bw = Math.max(2, Math.min(28, slot - 2));
    const y = (v) => T + plotH - (v / max) * plotH;
    const grid = [0, 0.5, 1].map((f) => {
      const v = max * f;
      return `<line class="usg-grid" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/>` +
             `<text class="usg-axis" x="${L - 6}" y="${y(v) + 3.5}" text-anchor="end">${money(v)}</text>`;
    }).join("");
    const every = Math.max(1, Math.ceil(bars.length / 7));
    const rects = bars.map((b, i) => {
      const x = L + i * slot + (slot - bw) / 2;
      const h = Math.max(b.cost > 0 ? 2 : 0, (b.cost / max) * plotH);
      const hit = `<rect class="usg-hit" x="${L + i * slot}" y="${T}" width="${slot}" height="${plotH + B}" data-i="${i}"/>`;
      const r = Math.min(3, bw / 2, h), base = T + plotH, top = base - h;
      const bar = h > 0 ? `<path class="usg-bar" data-bar="${i}" d="M${x},${base} V${top + r} Q${x},${top} ${x + r},${top} H${x + bw - r} Q${x + bw},${top} ${x + bw},${top + r} V${base} Z"/>` : "";
      const label = i % every === 0
        ? `<text class="usg-axis" x="${x + bw / 2}" y="${H - 6}" text-anchor="middle">${new Date(b.from + "T12:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" })}</text>` : "";
      return bar + hit + label;
    }).join("");
    el.innerHTML = `
      <div class="usg-card-head"><span>Cost per ${weekly ? "week" : "day"}</span></div>
      <div class="usg-chart-wrap">
        <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Cost per ${weekly ? "week" : "day"}">${grid}${rects}</svg>
        <div class="usg-tip hidden" id="usg-tip"></div>
      </div>`;
    const wrap = el.querySelector(".usg-chart-wrap");
    const tip = $("usg-tip");
    wrap.addEventListener("pointermove", (ev) => {
      const hit = ev.target.closest(".usg-hit");
      el.querySelectorAll(".usg-bar.on").forEach((n) => n.classList.remove("on"));
      if (!hit) { tip.classList.add("hidden"); return; }
      const i = +hit.dataset.i, b = bars[i];
      el.querySelector(`[data-bar="${i}"]`)?.classList.add("on");
      tip.innerHTML = `<strong>${weekly ? `${fmtDay(b.from)} – ${fmtDay(b.to)}` : fmtDay(b.from)}</strong><span>${money(b.cost)} · ${b.calls.toLocaleString()} calls</span>`;
      tip.classList.remove("hidden");
      const box = wrap.getBoundingClientRect();
      const px = ev.clientX - box.left;
      tip.style.left = Math.min(Math.max(px, tip.offsetWidth / 2), box.width - tip.offsetWidth / 2) + "px";
    });
    wrap.addEventListener("pointerleave", () => {
      tip.classList.add("hidden");
      el.querySelectorAll(".usg-bar.on").forEach((n) => n.classList.remove("on"));
    });
  }

  function renderBreakdown() {
    const el = $("usg-breakdown");
    const d = S.data;
    if (!d) { el.innerHTML = ""; return; }
    const tabs = TABS.map((t) => `<button class="chip${t.id === S.tab ? " active" : ""}" data-tab="${t.id}">${t.label}</button>`).join("");
    const items = d.breakdown[S.tab] || [];
    const max = Math.max(...items.map((i) => i.cost), 0.0000001);
    const total = d.totals.cost || 0;
    const rows = items.map((i) => {
      const on = S.filters[S.tab] === i.key;
      const share = total > 0 ? Math.round((i.cost / total) * 100) : 0;
      const bits = [`${i.calls.toLocaleString()} call${i.calls === 1 ? "" : "s"}`, `${compact(i.input_tokens)} in · ${compact(i.output_tokens)} out`];
      if (i.failures) bits.push(`${i.failures} failed`);
      if (i.unpriced) bits.push(`${i.unpriced} unpriced`);
      return `<button class="usg-row${on ? " on" : ""}" data-pick="${esc(i.key)}" title="${on ? "Remove filter" : "Filter the screen by this"}">
        <span class="usg-row-top"><span class="usg-row-name">${esc(labelOf(S.tab, i))}</span>
          <span class="usg-row-cost">${money(i.cost)}${total > 0 ? `<em>${share}%</em>` : ""}</span></span>
        <span class="usg-meter"><span style="width:${Math.max(i.cost > 0 ? 1.5 : 0, (i.cost / max) * 100)}%"></span></span>
        <span class="usg-row-meta">${esc(bits.join(" · "))}</span></button>`;
    }).join("");
    el.innerHTML = `
      <div class="usg-card-head"><span>Where it goes</span><div class="usg-tabs">${tabs}</div></div>
      <div class="usg-rows">${rows || `<div class="usg-empty">Nothing here.</div>`}</div>`;
  }

  function refLink(ref) {
    if (!ref) return "";
    const [kind, id] = [ref.slice(0, ref.indexOf(":")), ref.slice(ref.indexOf(":") + 1)];
    const label = kind === "session" ? (id === "main" ? "main chat" : "chat " + id.slice(0, 12))
      : kind === "call" ? "call" : kind === "notification" ? "notification" : kind === "person" ? "person" : ref;
    const opens = ["session", "call", "person"].includes(kind);
    return opens ? `<button class="usg-ref" data-ref="${esc(ref)}">${esc(label)}</button>` : `<span class="usg-ref plain">${esc(label)}</span>`;
  }

  function renderEvents() {
    const el = $("usg-events");
    if (!S.data) { el.innerHTML = ""; return; }
    const raw = S.data.raw_days;
    const rows = S.events.map((e) => {
      const tokens = e.kind === "decision" ? `~${compact(e.input_tokens)} in` : e.kind === "transcribe" && !e.input_tokens ? "" : `${compact(e.input_tokens)} in · ${compact(e.output_tokens)} out`;
      const cost = !e.ok ? `<span class="usg-bad">failed</span>` : e.cost == null ? `<span class="usg-nop" title="No known price for this model">no price</span>` : money(e.cost);
      const meta = [fmtWhen(e.ts), sourceName(e.source), e.model, tokens, ms(e.ms)].filter(Boolean).map(esc).join(" · ");
      return `<div class="usg-event${e.ok ? "" : " fail"}">
        <div class="usg-event-main"><span class="usg-event-name">${esc(e.purpose_label)}</span>${refLink(e.ref)}</div>
        <div class="usg-event-cost">${cost}</div>
        <div class="usg-event-meta">${meta}</div>
        ${!e.ok && e.error ? `<div class="usg-event-err">${esc(e.error)}</div>` : ""}</div>`;
    }).join("");
    el.innerHTML = `
      <div class="usg-card-head"><span>Calls</span>
        <div class="usg-tabs">
          <button class="chip${S.sort === "recent" ? " active" : ""}" data-sort="recent">Recent</button>
          <button class="chip${S.sort === "cost" ? " active" : ""}" data-sort="cost">Most expensive</button>
        </div></div>
      <div class="usg-events">${rows || `<div class="usg-empty">No calls in this range.</div>`}</div>
      ${S.next ? `<button class="chip usg-more" data-more>Show more</button>` : ""}
      <p class="usg-foot">Individual calls are kept for ${raw} days; daily totals are kept for good.</p>`;
  }

  // ---------- events ----------
  function openRef(ref) {
    const i = ref.indexOf(":");
    const kind = ref.slice(0, i), id = ref.slice(i + 1);
    if (kind === "session") window.danaOpenSession?.(id);
    else if (kind === "call") window.danaSwitchTab?.("calls", id);
    else if (kind === "person") window.danaSwitchTab?.("people", id);
  }

  function wire() {
    const root = $("panel-usage");
    root.addEventListener("click", (ev) => {
      const t = ev.target;
      let n;
      if ((n = t.closest("[data-range]"))) { S.range = n.dataset.range; store("usg.range", S.range); load(); }
      else if ((n = t.closest("[data-tab]"))) { S.tab = n.dataset.tab; store("usg.tab", S.tab); renderBreakdown(); }
      else if ((n = t.closest("[data-pick]"))) setFilter(S.tab, n.dataset.pick);
      else if ((n = t.closest("[data-clear]"))) { delete S.filters[n.dataset.clear]; load(); }
      else if ((n = t.closest("[data-sort]"))) { S.sort = n.dataset.sort; load(); }
      else if (t.closest("[data-more]")) load({ more: true });
      else if ((n = t.closest("[data-ref]"))) openRef(n.dataset.ref);
    });
    $("usg-refresh")?.addEventListener("click", () => load());
    let resizeTimer = null;
    window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { if (S.visible) renderChart(); }, 150); });
  }

  let wired = false;
  window.DanaUsage = {
    show() {
      if (!wired && $("panel-usage")) { wire(); wired = true; }
      S.visible = true;
      load();
      clearInterval(timer);
      timer = setInterval(() => { if (S.visible && !document.hidden) load(); }, 60000);
    },
    hide() { S.visible = false; clearInterval(timer); },
  };
  if (location.hash === "#usage") setTimeout(() => window.danaSwitchTab?.("usage"), 0);
})();
