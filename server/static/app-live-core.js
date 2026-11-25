/* The web app — live activities' pure part (static/app.js says how the app's scripts fit together; the DOM is
 * static/app-live.js). Sets globalThis.DanaLive; no DOM here, tested by tests/test_web_live.mjs against
 * tests/fixtures/live_web.json.
 *
 * A live activity (server/docs/live.md) is drawn from its wire JSON only: what is shown (the top-level fields
 * overridden by the selected choice's), the timer ("Departs in 7:42", "Elapsed 28:14"), the progress bar's
 * geometry (segments, points, the value and its tracker), stepping between choices, where a tap goes, a reply
 * folded into the list, and the card's HTML. No add-on is named: an icon is a Lucide name, a tone a colour.
 */
(() => {
  "use strict";

  const TONES = ["neutral", "good", "warn", "bad", "muted"];
  // The display fields a choice may override (docs/live.md, "Wire JSON").
  const DISPLAY = ["title", "text", "context", "icon", "tone", "countdown", "since", "chip", "progress", "open"];
  const ICON_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const toneOf = (t) => (TONES.includes(t) ? t : "neutral");
  const iconOf = (name, fallback) => (typeof name === "string" && name.length <= 40 && ICON_RE.test(name) ? name : fallback);
  const list = (v) => (Array.isArray(v) ? v : []);

  // ── Choices ──────────────────────────────────────────────────────────

  /** The index of the choice shown: `key` (the user's pick on this client), else the activity's
   *  `selected`, else the first; -1 without choices. */
  function choiceIndex(activity, key) {
    const choices = list(activity && activity.choices);
    if (!choices.length) return -1;
    for (const k of [key, activity.selected]) {
      if (!k) continue;
      const i = choices.findIndex((c) => c && c.key === k);
      if (i >= 0) return i;
    }
    return 0;
  }

  /** The key `dir` steps away (+1 next, -1 previous) from the shown choice, or null at either end. */
  function stepChoice(activity, key, dir) {
    const choices = list(activity && activity.choices);
    const i = choiceIndex(activity, key);
    const j = i + (dir < 0 ? -1 : 1);
    return i >= 0 && j >= 0 && j < choices.length ? choices[j].key : null;
  }

  /** What the card shows: the activity's display fields overridden by the shown choice's (a field the choice
   *  leaves out keeps the top-level one; a choice's timer replaces the other kind too). An ended activity
   *  shows its final, muted unless the final says otherwise. */
  function view(activity, key) {
    const a = activity || {};
    if (a.ended) {
      const f = a.final || {};
      return { ended: true, title: f.title || a.title || "", text: f.text || "", context: "", icon: iconOf(a.icon, "check"),
               tone: f.tone ? toneOf(f.tone) : "muted", open: f.open || "", index: -1, count: 0, key: "" };
    }
    const out = { ended: false };
    for (const f of DISPLAY) if (a[f] !== undefined) out[f] = a[f];
    const choices = list(a.choices);
    const i = choiceIndex(a, key);
    const c = i >= 0 ? choices[i] : null;
    if (c) {
      for (const f of DISPLAY) if (c[f] !== undefined) out[f] = c[f];
      if (c.countdown && !c.since) delete out.since;
      if (c.since && !c.countdown) delete out.countdown;
    }
    out.title = out.title || "";
    out.tone = toneOf(out.tone);
    out.icon = iconOf(out.icon, "activity");
    out.key = c ? c.key : "";
    out.index = i;
    out.count = choices.length;
    return out;
  }

  // ── Time ─────────────────────────────────────────────────────────────

  const pad = (n) => String(n).padStart(2, "0");

  /** A length of time, short: "7:42" under an hour, "1 h 05" under a day, then "2 d 3 h". */
  function duration(seconds) {
    const s = Math.max(0, Math.floor(Number(seconds) || 0));
    if (s < 3600) return `${Math.floor(s / 60)}:${pad(s % 60)}`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ${pad(Math.floor((s % 3600) / 60))}`;
    const d = Math.floor(s / 86400);
    const h = Math.floor((s % 86400) / 3600);
    return h ? `${d} d ${h} h` : `${d} d`;
  }

  /** "17:46" from an ISO time, in the server's zone as written (the payload's own clock). */
  function clockOf(iso) {
    const m = /T(\d{2}):(\d{2})/.exec(String(iso || ""));
    return m ? `${m[1]}:${m[2]}` : "";
  }

  /** The timer of a view at `nowMs`: a countdown ({label: "Departs in", value: "7:42", at: "at 17:46"}; once
   *  it has passed, the label alone and "now") or a count-up for `since` ("Elapsed", "28:14"); null for none. */
  function timer(v, nowMs) {
    if (v && v.countdown && v.countdown.to) {
      const to = Date.parse(v.countdown.to);
      if (!Number.isFinite(to)) return null;
      const label = String(v.countdown.label || "").trim();
      const left = Math.ceil((to - nowMs) / 1000);
      const at = clockOf(v.countdown.to);
      if (left <= 0) return { kind: "countdown", label, value: "now", at: at ? `at ${at}` : "", past: true };
      return { kind: "countdown", label: label ? `${label} in` : "In", value: duration(left), at: at ? `at ${at}` : "", past: false };
    }
    if (v && v.since) {
      const since = Date.parse(v.since);
      if (!Number.isFinite(since)) return null;
      const at = clockOf(v.since);
      return { kind: "since", label: "Elapsed", value: duration((nowMs - since) / 1000), at: at ? `since ${at}` : "", past: false };
    }
    return null;
  }

  // ── Progress ─────────────────────────────────────────────────────────

  const round = (n) => Math.round(n * 100) / 100;

  /** The bar's geometry in percent of its width: each segment (start, width, tone, how much of it is filled),
   *  the points (where, tone, reached), the value (the tracker's place). The total is the sum of the segment
   *  lengths (100 without segments). Styled by progress (or without segments): filled up to the value; else
   *  every segment at full strength and the tracker alone marks the value. */
  function progress(p) {
    if (!p || typeof p !== "object") return null;
    const given = list(p.segments).filter((s) => s && Number(s.length) > 0);
    const segs = given.length ? given : [{ length: 100, tone: "" }];   // no segments: the card's own tone
    const total = segs.reduce((sum, s) => sum + Number(s.length), 0);
    const value = Math.min(total, Math.max(0, Number(p.value) || 0));
    const styled = !given.length || p.styled_by_progress !== false;
    let start = 0;
    const segments = segs.map((s) => {
      const len = Number(s.length);
      const fill = styled ? Math.min(1, Math.max(0, (value - start) / len)) : 1;
      const seg = { start: round(start / total * 100), width: round(len / total * 100), tone: s.tone === "" ? "" : toneOf(s.tone), fill: round(fill) };
      start += len;
      return seg;
    });
    const points = list(p.points).filter((pt) => pt && Number.isFinite(Number(pt.at))).map((pt) => {
      const at = Math.min(total, Math.max(0, Number(pt.at)));
      return { at: round(at / total * 100), tone: toneOf(pt.tone), reached: at <= value };
    });
    return { total, value, pct: round(value / total * 100), segments, points,
             tracker: iconOf(p.tracker, ""), indeterminate: p.indeterminate === true };
  }

  // ── Where a tap goes; replies ────────────────────────────────────────

  /** `open` as a target: a Dana path ("/rail" → the screen "rail"; "/rail/x" → with the argument "x") or an
   *  https address (a new tab); null for anything else. */
  function openTarget(open) {
    const s = String(open || "").trim();
    if (/^https:\/\/[^\s]+$/i.test(s)) return { type: "url", url: s };
    const m = /^\/([A-Za-z0-9_-]+)(?:\/([^\s]*))?$/.exec(s);
    if (m) return { type: "screen", id: m[1], arg: m[2] ? decodeURIComponent(m[2]) : null };
    return null;
  }

  /** The list after a reply ({activity: <wire> | null}): replaced in place, removed when null, added in front
   *  when it was not listed. */
  function applyReply(activities, id, activity) {
    const items = list(activities);
    if (!activity) return items.filter((a) => a.id !== id);
    const i = items.findIndex((a) => a.id === id);
    if (i < 0) return [activity, ...items];
    return items.map((a, j) => (j === i ? activity : a));
  }

  /** The activities a payload lists (bad entries out). */
  function parse(payload) {
    return list(payload && payload.activities).filter((a) => a && typeof a === "object" && typeof a.id === "string" && a.id);
  }

  /** Whether any shown activity has a timer running (the strip ticks only then). */
  const ticking = (activities, picks) => list(activities).some((a) => {
    const v = view(a, picks && picks[a.id]);
    return !v.ended && Boolean(v.countdown || v.since);
  });

  // ── The card ─────────────────────────────────────────────────────────

  const domId = (id) => "lv-" + String(id).replace(/[^a-zA-Z0-9_-]/g, "_");

  function timerHtml(v, nowMs) {
    if (v.ended) return "";
    const t = timer(v, nowMs);
    if (!t) return "";
    const data = t.kind === "countdown"
      ? ` data-to="${esc(v.countdown.to)}" data-label="${esc(v.countdown.label || "")}"`
      : ` data-since="${esc(v.since)}"`;
    return `<div class="lv-timer${t.past ? " past" : ""}"${data}>`
      + `<span class="lv-timer-label">${esc(t.label)}</span>`
      + `<span class="lv-timer-value" aria-live="off">${esc(t.value)}</span>`
      + (t.at ? `<span class="nav-sr-only lv-timer-at">${esc(t.at)}</span>` : "") + `</div>`;
  }

  function barHtml(p) {
    const g = progress(p);
    if (!g) return "";
    if (g.indeterminate) return `<div class="lv-bar indeterminate" aria-hidden="true"><div class="lv-track"><span class="lv-seg dn-shimmer" style="flex-grow:1"></span></div></div>`;
    const segs = g.segments.map((s) => `<span class="lv-seg${s.tone ? ` lv-tone-${s.tone}` : ""}" style="flex-grow:${s.width}">`
      + `<i style="width:${s.fill * 100}%"></i></span>`).join("");
    const points = g.points.map((pt) => `<b class="lv-point lv-tone-${pt.tone}${pt.reached ? " reached" : ""}" style="inset-inline-start:${pt.at}%"></b>`).join("");
    const tracker = g.tracker
      ? `<span class="lv-tracker" style="inset-inline-start:${g.pct}%"><i data-lucide="${esc(g.tracker)}"></i></span>`
      : `<span class="lv-tracker dot" style="inset-inline-start:${g.pct}%"></span>`;
    return `<div class="lv-bar" role="progressbar" aria-valuemin="0" aria-valuemax="${g.total}" aria-valuenow="${g.value}">`
      + `<div class="lv-track">${segs}</div>${points}${tracker}</div>`;
  }

  function actionsHtml(a, v, busy) {
    if (v.ended) {
      return `<div class="lv-acts"><button type="button" class="lv-stop icon" data-live-act="stop" aria-label="Dismiss ${esc(v.title)}" title="Dismiss">`
        + `<i data-lucide="x" aria-hidden="true"></i></button></div>`;
    }
    const acts = list(a.actions).filter((x) => x && x.id);
    if (!acts.length) return "";
    const nav = [];
    const rest = [];
    let stop = "";
    for (const x of acts) {
      const label = esc(x.label || x.id);
      if (x.kind === "next" || x.kind === "prev") {
        const dir = x.kind === "next" ? 1 : -1;
        const off = v.count < 2 || stepChoice(a, v.key, dir) === null;
        const icon = dir > 0 ? "chevron-right" : "chevron-left";
        nav.push(`<button type="button" class="lv-act ${x.kind}" data-live-act="${x.kind}"${off ? " disabled" : ""}>`
          + (dir < 0 ? `<i data-lucide="${icon}" aria-hidden="true"></i><span>${label}</span>` : `<span>${label}</span><i data-lucide="${icon}" aria-hidden="true"></i>`)
          + `</button>`);
      } else if (x.kind === "stop") {
        stop = `<button type="button" class="lv-stop" data-live-act="stop" aria-label="${label}: ${esc(v.title)}"><i data-lucide="x" aria-hidden="true"></i><span>${label}</span></button>`;
      } else {
        const on = busy && busy.has(x.id);
        rest.push(`<button type="button" class="dn-pill small" data-live-act="server" data-live-action="${esc(x.id)}"${on ? ' disabled aria-busy="true"' : ""}>${label}</button>`);
      }
    }
    const pos = nav.length && v.count > 1
      ? `<span class="lv-pos"><span aria-hidden="true">${v.index + 1}/${v.count}</span><span class="nav-sr-only">option ${v.index + 1} of ${v.count}</span></span>` : "";
    return `<div class="lv-acts">${nav.join("")}${pos}${rest.join("")}${stop}</div>`;
  }

  /** The card of one activity (`key`: the choice picked on this client; `busy`: server actions in flight). */
  function cardHtml(a, opts) {
    const o = opts || {};
    const v = view(a, o.key);
    const dom = domId(a.id);
    const target = openTarget(v.open);
    const title = esc(v.title);
    const titleEl = !target ? `<span>${title}</span>`
      : target.type === "url" ? `<a class="lv-open" href="${esc(target.url)}" target="_blank" rel="noopener">${title}</a>`
      : `<button type="button" class="lv-open" data-live-open>${title}</button>`;
    const chip = v.ended ? `<span class="lv-done">Done</span>` : v.chip ? `<span class="lv-chip">${esc(v.chip)}</span>` : "";
    const context = v.context || (v.ended ? a.source || "" : "");
    return `<article class="lv-card lv-tone-${v.tone}${v.ended ? " ended" : ""}${target ? " can-open" : ""}" id="${dom}" data-live-id="${esc(a.id)}" role="group" aria-labelledby="${dom}-t">`
      + `<span class="lv-icon" aria-hidden="true"><i data-lucide="${esc(v.icon)}"></i></span>`
      + `<div class="lv-main"><div class="lv-head"><h3 class="lv-title" id="${dom}-t" dir="auto">${titleEl}</h3>${chip}</div>`
      + (v.text ? `<p class="lv-text" dir="auto">${esc(v.text)}</p>` : "")
      + (context ? `<p class="lv-context" dir="auto">${esc(context)}</p>` : "")
      + `</div>`
      + timerHtml(v, o.now === undefined ? Date.now() : o.now)
      + (v.ended ? "" : barHtml(v.progress))
      + actionsHtml(a, v, o.busy)
      + `</article>`;
  }

  /** What makes a card's HTML change (its timer ticks on its own). */
  const signature = (a, key, busy) => `${a.rev}|${a.ended ? 1 : 0}|${key || ""}|${busy ? [...busy].join(",") : ""}`;

  globalThis.DanaLive = {
    TONES, DISPLAY, toneOf, choiceIndex, stepChoice, view, duration, clockOf, timer, progress, openTarget,
    applyReply, parse, ticking, domId, cardHtml, signature,
  };
})();
