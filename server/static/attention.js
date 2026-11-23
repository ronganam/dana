/* Needs attention — Settings → Needs attention: everything that needs the user, each with the button that fixes it.
 *
 *   GET  /api/checks                       every check the user sees; the failing ones are listed here, the
 *                                          connections to reconnect first (core/checks.py, server/docs/now.md)
 *   POST /api/checks/{key}/fix {fix}       a check's server fix: its addon carries it out for the user
 *   POST /api/checks/{key}/ignore {days}   hide its Now card (a reconnect: a day at most); 0 = watch it again
 *
 * A fix (`fix` of a check) is a server action (the request above) or a client one: `connect` opens the addon's
 * connect flow on the Addons page, `screen` opens that screen, `url` opens an https link in a new tab. Each row
 * shows the check's title, why, what it concerns, since when, and whether its card is ignored. Nothing failing:
 * "All clear." Opened with a check's key (from its Now card), that row is brought into view and marked.
 * The navigation place `attention` carries the count of failing checks not ignored, read at start, when the
 * window gets focus, after every fix and every five minutes while the tab is visible.
 *
 * Two parts: DanaAttentionCore (pure: view model, HTML; tested by tests/test_web_attention.mjs against
 * tests/fixtures/attention_web.json) and the controller (window.DanaAttention = {show, hide, refresh};
 * app-nav.js calls show(arg) / hide on tab switches).
 */
(() => {
  "use strict";

  const RECONNECT = "reconnect:";
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  /** "since 5 min ago" / "since 3 h ago" / "since 2 Nov" from an ISO time and now (ms); "" without a time. */
  function sinceText(iso, nowMs) {
    const t = Date.parse(iso || "");
    if (!Number.isFinite(t)) return "";
    const minutes = Math.max(0, Math.round((nowMs - t) / 60000));
    if (minutes < 1) return "since just now";
    if (minutes < 60) return `since ${minutes} min ago`;
    if (minutes < 48 * 60) return `since ${Math.round(minutes / 60)} h ago`;
    const d = new Date(t);
    return `since ${d.getDate()} ${MONTHS[d.getMonth()]}`;
  }

  /** What a fix does on the web: {type: server, action} | {type: connect, addon} | {type: screen, id} |
   *  {type: url, url} (https only), or null for a fix the web cannot carry out. */
  function fixTarget(fix) {
    if (!fix || !fix.kind) return null;
    if (fix.kind === "server" && fix.action) return { type: "server", action: String(fix.action) };
    if (fix.kind === "connect" && fix.addon) return { type: "connect", addon: String(fix.addon) };
    if (fix.kind === "screen" && fix.screen) return { type: "screen", id: String(fix.screen) };
    if (fix.kind === "url" && /^https:\/\//.test(String(fix.url || ""))) return { type: "url", url: String(fix.url) };
    return null;
  }

  /** The rows: the failing checks in server order (reconnects come first from the server), each
   *  {key, title, why, label, since, ignoredUntil, reconnect, server, fix, target}. */
  function itemsOf(payload, nowMs) {
    return ((payload && payload.checks) || []).filter((c) => c && c.key && c.ok === false).map((c) => ({
      key: String(c.key),
      title: c.title || `${c.label || c.key}: ${c.detail || "failing"}`,
      why: c.why || c.detail || "",
      label: c.label || c.key,
      since: sinceText(c.since, nowMs),
      ignoredUntil: c.ignored_until || null,
      reconnect: String(c.key).startsWith(RECONNECT),
      server: Boolean(c.server),
      fix: c.fix || null,
      target: fixTarget(c.fix),
    }));
  }

  /** The navigation badge: failing checks whose card is not ignored. */
  const countOf = (payload, nowMs) => itemsOf(payload, nowMs).filter((i) => !i.ignoredUntil).length;

  /** "until Tue 14:00"-style words for an ignored card's end (local time). */
  function untilText(iso) {
    const t = Date.parse(iso || "");
    if (!Number.isFinite(t)) return "";
    const d = new Date(t);
    return `${d.getDate()} ${MONTHS[d.getMonth()]} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  }

  function renderItem(item, ui) {
    const u = ui || {};
    const busy = u.busy === item.key;
    const meta = [item.reconnect ? "Reconnect" : item.server ? "This server" : item.label, item.since,
                  item.ignoredUntil ? `card hidden until ${untilText(item.ignoredUntil)}` : ""].filter(Boolean);
    const fix = item.target
      ? `<button class="btn-primary" data-fix="${esc(item.key)}"${busy ? " disabled" : ""}>${busy ? "Working…" : esc(item.fix.label)}</button>` : "";
    const ignore = item.ignoredUntil
      ? `<button class="btn-secondary" data-ignore="${esc(item.key)}" data-days="0">Show on Now again</button>`
      : `<button class="btn-secondary" data-ignore="${esc(item.key)}" data-days="${item.reconnect ? 1 : 7}">${item.reconnect ? "Not today" : "Ignore for a week"}</button>`;
    const status = u.status && u.status.key === item.key
      ? `<p class="att-status ${esc(u.status.tone)}" role="status" dir="auto">${esc(u.status.text)}</p>` : "";
    return `<li class="att-row${item.reconnect ? " reconnect" : ""}${u.focus === item.key ? " focus" : ""}${item.ignoredUntil ? " ignored" : ""}" data-key="${esc(item.key)}">
        <span class="att-bar" aria-hidden="true"></span>
        <div class="att-text">
          <span class="att-meta">${meta.map((m) => `<span dir="auto">${esc(m)}</span>`).join(" · ")}</span>
          <h3 class="att-title" dir="auto">${esc(item.title)}</h3>
          ${item.why ? `<p class="att-why" dir="auto">${esc(item.why)}</p>` : ""}
          ${status}
          <div class="att-actions">${fix}${ignore}</div>
        </div>
      </li>`;
  }

  /** The screen's body: the rows, or "All clear." when nothing fails. */
  function renderList(items, ui) {
    if (!items.length) {
      return `<div class="att-clear"><span class="now-mark" aria-hidden="true"></span><p class="now-allclear">All clear.</p>
        <p class="att-muted">Every connection and signal Dana depends on works. Anything that stops working shows up here, with the button that fixes it.</p></div>`;
    }
    const open = items.filter((i) => !i.ignoredUntil).length;
    return `<h2 class="now-section-h">Needs attention <span class="now-count">${open}</span></h2>
      <ul class="att-list">${items.map((i) => renderItem(i, ui)).join("")}</ul>`;
  }

  const core = { sinceText, fixTarget, itemsOf, countOf, untilText, renderItem, renderList };
  (typeof window !== "undefined" ? window : globalThis).DanaAttentionCore = core;
  if (typeof document === "undefined") return;

  // ---------- controller ----------
  const S = { visible: false, payload: null, loading: false, error: "", busy: null, status: null, focus: null };
  const $ = (id) => document.getElementById(id);
  const icons = () => { try { window.lucide?.createIcons(); } catch (_) { /* ignore */ } };

  async function request(method, url, body) {
    try {
      const r = await fetch(url, { method, headers: body !== undefined ? { "Content-Type": "application/json" } : {},
                                   body: body !== undefined ? JSON.stringify(body) : undefined, cache: "no-store" });
      let data = null;
      try { data = await r.json(); } catch (_) { /* empty */ }
      return { status: r.status, body: data };
    } catch (_) {
      return { status: 0, body: null };
    }
  }

  function badge() {
    if (S.payload && typeof setPanelBadge === "function") setPanelBadge("attention", countOf(S.payload, Date.now()) || "", "");
  }

  function render() {
    const el = $("att-list");
    if (!el) return;
    if (!S.payload) {
      el.innerHTML = S.error ? `<p class="att-muted">${esc(S.error)}</p>` : `<div class="dev-skel"></div>`;
      return;
    }
    el.innerHTML = renderList(itemsOf(S.payload, Date.now()), { busy: S.busy, status: S.status, focus: S.focus });
    icons();
    if (S.focus) el.querySelector(`[data-key="${CSS.escape(S.focus)}"]`)?.scrollIntoView({ block: "center" });
  }

  async function load() {
    if (S.loading) return;
    S.loading = true;
    const r = await request("GET", "/api/checks");
    S.loading = false;
    if (r.status === 200 && r.body) { S.payload = r.body; S.error = ""; }
    else if (!S.payload) S.error = "Couldn't load what needs attention. Try again in a moment.";
    badge();
    if (S.visible) render();
  }

  async function runFix(key) {
    const item = itemsOf(S.payload, Date.now()).find((i) => i.key === key);
    const t = item && item.target;
    if (!t) return;
    if (t.type === "connect") { window.DanaAddons?.openAddon(t.addon); return; }
    if (t.type === "screen") { window.openScreen?.(t.id); return; }
    if (t.type === "url") { window.open(t.url, "_blank", "noopener"); return; }
    S.busy = key;
    render();
    const r = await request("POST", `/api/checks/${encodeURIComponent(key)}/fix`, { fix: t.action });
    S.busy = null;
    S.status = r.status === 200
      ? { key, tone: "ok", text: (r.body && r.body.message) || "Done" }
      : { key, tone: "bad", text: (r.body && (r.body.error || r.body.message)) || "Couldn't fix it. Try again in a moment." };
    await load();
  }

  async function ignore(key, days) {
    const r = await request("POST", `/api/checks/${encodeURIComponent(key)}/ignore`, { days });
    S.status = { key, tone: r.status === 200 ? "ok" : "bad",
                 text: (r.body && (r.body.message || r.body.error)) || (r.status === 200 ? "Done" : "Couldn't change it") };
    await load();
  }

  function wire() {
    $("att-list")?.addEventListener("click", (ev) => {
      const fix = ev.target.closest("[data-fix]");
      if (fix && !S.busy) { runFix(fix.dataset.fix); return; }
      const ign = ev.target.closest("[data-ignore]");
      if (ign) ignore(ign.dataset.ignore, Number(ign.dataset.days));
    });
    $("att-refresh")?.addEventListener("click", () => load());
    $("att-back")?.addEventListener("click", () => window.danaSwitchTab?.("settings"));
  }

  let wired = false;
  window.DanaAttention = {
    show(arg) {
      if (!wired && $("panel-attention")) { wire(); wired = true; }
      S.visible = true;
      S.focus = arg || null;
      S.status = null;
      render();
      load();
    },
    hide() {
      S.visible = false;
      S.focus = null;
    },
    refresh: load,
  };
  window.addEventListener("focus", () => load());
  setInterval(() => { if (document.visibilityState === "visible") load(); }, 5 * 60 * 1000);
  setTimeout(load, 0);
})();
