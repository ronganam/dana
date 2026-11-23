/* Backups — the schedule, the destination folder, how many to keep, the last backup and the list, "Back up now" and
 * "Download backup" (server-wide, admins only), on the Settings screen.
 *
 *   GET  /api/backups              {schedule {every, at, day}, destination, default_destination, keep, next_at, running,
 *                                   last, last_ok, backups: [{name, size, created_at}], credentials_key, timezone}
 *   PUT  /api/backups              {schedule?, destination?, keep?} → the same; 400 {field, detail}
 *   POST /api/backups/run          → {backup, status}; 409 busy; 500 backup_failed {detail}
 *   GET  /api/backups/download     a fresh archive (the browser saves it)
 *   GET  /api/backups/files/{name} one archive of the destination
 *
 * Server side: core/backups.py, core/backups_api.py, server/docs/backups.md.
 *
 * Two parts: DanaBackupsCore (pure: sizes, the schedule in words, the last backup's line, the PUT body; tested by
 * test_web_backups.mjs) and the card, drawn for a user with manage_server.
 */
(() => {
  "use strict";

  const DAYS = { mon: "Monday", tue: "Tuesday", wed: "Wednesday", thu: "Thursday", fri: "Friday", sat: "Saturday", sun: "Sunday" };

  /** 1536 → "1.5 KB". */
  function sizeText(bytes) {
    const n = Number(bytes) || 0;
    if (n < 1024) return `${n} bytes`;
    let v = n;
    for (const unit of ["KB", "MB", "GB", "TB"]) {
      v /= 1024;
      if (v < 1024 || unit === "TB") return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${unit}`;
    }
    return `${n} bytes`;
  }

  /** The schedule in words: "Off", "Every day at 03:30", "Every Sunday at 03:30". */
  function scheduleText(schedule) {
    const s = schedule || {};
    if (s.every === "daily") return `Every day at ${s.at}`;
    if (s.every === "weekly") return `Every ${DAYS[s.day] || s.day} at ${s.at}`;
    return "Off";
  }

  /** The last backup's line: {tone: ok | bad | none, text}. */
  function lastLine(status) {
    const last = (status && status.last) || null;
    if (!last) return { tone: "none", text: "No backup yet." };
    if (!last.ok) return { tone: "bad", text: `Failed: ${last.error || "unknown reason"}` };
    return { tone: "ok", text: `${last.name} · ${sizeText(last.size)}` };
  }

  /** The PUT body from the form's values; {error: {field, detail}} for one the server would refuse. */
  function requestBody({ every = "off", at = "", day = "sun", destination = "", keep = "" } = {}) {
    if (!["off", "daily", "weekly"].includes(every)) return { error: { field: "schedule", detail: "Choose off, daily or weekly." } };
    const time = String(at || "").trim() || "03:30";
    if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(time)) return { error: { field: "schedule", detail: "The time is HH:MM, like 03:30." } };
    const folder = String(destination || "").trim();
    if (folder && !folder.startsWith("/")) return { error: { field: "destination", detail: "Give an absolute folder on the server, like /backups." } };
    const n = Number(String(keep).trim());
    if (!Number.isInteger(n) || n < 1 || n > 100) return { error: { field: "keep", detail: "Keep between 1 and 100 backups." } };
    return { body: { schedule: { every, at: time, day }, destination: folder, keep: n } };
  }

  const core = { sizeText, scheduleText, lastLine, requestBody };
  const G = typeof window !== "undefined" ? window : globalThis;
  G.DanaBackupsCore = core;
  if (typeof document === "undefined") return;

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const when = (iso) => {
    if (!iso) return "";
    const d = new Date(iso);
    return isNaN(d) ? iso : d.toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  };

  async function api(method, path, body) {
    const r = await fetch(path, { method, cache: "no-store", credentials: "same-origin",
                                  headers: body ? { "Content-Type": "application/json" } : {},
                                  body: body ? JSON.stringify(body) : undefined });
    let data = null;
    try { data = await r.json(); } catch (_) { /* empty */ }
    if (!r.ok) throw Object.assign(new Error((data && (data.detail || data.error)) || `HTTP ${r.status}`), { status: r.status, data });
    return data;
  }

  function mount(el) {
    let status = null;
    let every = "off";

    function draw(note = "", noteBad = false) {
      const s = status;
      const last = lastLine(s);
      const lastAt = s.last ? when(s.last.at) : "";
      every = s.schedule.every;
      const key = s.credentials_key === "env"
        ? "The credentials key comes from CREDENTIALS_KEY and is not in the backups: keep it in your secret store, a restore needs it."
        : "Backups hold the credentials key and everyone's data: keep them as safe as the server.";
      el.innerHTML = `
        <div class="bk-last">
          <span class="bk-pill ${last.tone}">${last.tone === "ok" ? "OK" : last.tone === "bad" ? "Failed" : "None"}</span>
          <span class="bk-last-text">${esc(last.text)}${lastAt ? ` <span class="bk-meta">${esc(lastAt)}</span>` : ""}</span>
        </div>
        ${s.last && !s.last.ok && s.last_ok ? `<span class="profile-hint">Last good backup: ${esc(s.last_ok.name)}, ${esc(when(s.last_ok.at))}.</span>` : ""}
        <span class="profile-hint">${esc(key)}</span>
        <form class="bk-form" novalidate>
          <span class="settings-label" id="bk-every-label">Schedule</span>
          <div class="segmented-control" role="radiogroup" aria-labelledby="bk-every-label">${["off", "daily", "weekly"].map((v) => `
            <button type="button" class="segmented-btn${v === every ? " active" : ""}" role="radio" aria-checked="${v === every}" data-every="${v}">${v === "off" ? "Off" : v === "daily" ? "Daily" : "Weekly"}</button>`).join("")}</div>
          <div class="bk-when${every === "off" ? " hidden" : ""}">
            <select class="settings-input bk-day${every === "weekly" ? "" : " hidden"}" aria-label="Day">${Object.entries(DAYS).map(([k, v]) => `
              <option value="${k}"${k === s.schedule.day ? " selected" : ""}>${v}</option>`).join("")}</select>
            <input class="settings-input bk-at" type="time" value="${esc(s.schedule.at)}" aria-label="Time">
            <span class="profile-hint">${esc(s.timezone)}${s.next_at ? ` · next ${esc(when(s.next_at))}` : ""}</span>
          </div>
          <label class="settings-label" for="bk-dest">Destination folder on the server</label>
          <input class="settings-input" id="bk-dest" value="${esc(s.destination === s.default_destination ? "" : s.destination)}"
                 placeholder="${esc(s.default_destination)}" autocomplete="off" spellcheck="false">
          <span class="profile-hint">Empty: ${esc(s.default_destination)}. Best on another disk than the data.</span>
          <label class="settings-label" for="bk-keep">Keep the newest</label>
          <input class="settings-input bk-keep" id="bk-keep" type="number" min="1" max="100" value="${esc(s.keep)}">
          <div class="setup-error bk-error" role="alert"></div>
          <button type="submit" class="btn-secondary">Save</button>
        </form>
        <div class="bk-btns">
          <button type="button" class="btn-primary bk-run"${s.running ? " disabled" : ""}><i data-lucide="archive"></i> Back up now</button>
          <a class="btn-secondary bk-download" href="/api/backups/download" download><i data-lucide="download"></i> Download backup</a>
        </div>
        <span class="profile-hint bk-status${noteBad ? " bad" : ""}" role="status" aria-live="polite">${esc(note)}</span>
        <div class="bk-list">${s.backups.length ? s.backups.map((b) => `
          <div class="bk-row">
            <div class="bk-text"><div class="bk-name">${esc(b.name)}</div><div class="bk-meta">${esc(sizeText(b.size))} · ${esc(when(b.created_at))}</div></div>
            <a class="icon-btn" href="/api/backups/files/${encodeURIComponent(b.name)}" download title="Download ${esc(b.name)}"><i data-lucide="download"></i></a>
          </div>`).join("") : `<span class="profile-hint">No backups in ${esc(s.destination)} yet.</span>`}</div>`;
      if (window.lucide) window.lucide.createIcons();

      el.querySelectorAll("[data-every]").forEach((b) => b.addEventListener("click", () => {
        every = b.dataset.every;
        el.querySelectorAll("[data-every]").forEach((x) => {
          x.classList.toggle("active", x === b);
          x.setAttribute("aria-checked", String(x === b));
        });
        el.querySelector(".bk-when").classList.toggle("hidden", every === "off");
        el.querySelector(".bk-day").classList.toggle("hidden", every !== "weekly");
      }));
      el.querySelector(".bk-form").addEventListener("submit", async (e) => {
        e.preventDefault();
        const error = el.querySelector(".bk-error");
        error.textContent = "";
        const out = requestBody({ every, at: el.querySelector(".bk-at").value, day: el.querySelector(".bk-day").value,
                                  destination: el.querySelector("#bk-dest").value, keep: el.querySelector(".bk-keep").value });
        if (out.error) { error.textContent = out.error.detail; return; }
        try {
          status = await api("PUT", "/api/backups", out.body);
          draw("Saved.");
        } catch (err) { error.textContent = err.message; }
      });
      el.querySelector(".bk-run").addEventListener("click", async (e) => {
        e.currentTarget.disabled = true;
        el.querySelector(".bk-status").textContent = "Backing up…";
        try {
          const done = await api("POST", "/api/backups/run");
          status = done.status;
          draw(`Written: ${done.backup.name} (${sizeText(done.backup.size)}).`);
        } catch (err) {
          try { status = await api("GET", "/api/backups"); } catch (_) { /* keep the last */ }
          draw(err.message, true);
        }
      });
      el.querySelector(".bk-download").addEventListener("click", () => {
        el.querySelector(".bk-status").textContent = "Making a backup for this browser; the download starts when it is ready.";
      });
    }

    api("GET", "/api/backups").then((s) => { status = s; draw(); }).catch((e) => {
      el.innerHTML = `<p class="profile-hint bad">${esc(e.status === 403 ? "Only an admin manages backups." : e.message)}</p>`;
    });
  }

  const card = document.getElementById("backups-card");
  if (!card) return;
  card.classList.add("hidden");
  fetch("/api/me", { cache: "no-store", credentials: "same-origin" }).then((r) => (r.ok ? r.json() : null)).then((me) => {
    if (!me || !(me.capabilities || []).includes("manage_server")) return;
    card.classList.remove("hidden");
    mount(document.getElementById("backups-box"));
  }).catch(() => {});
})();
