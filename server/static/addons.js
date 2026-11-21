/* Addons — turn addons on step by step; admins also run the server's addons and install new ones.
 *
 *   GET    /api/me                              who is signed in (name, access level, capabilities)
 *   GET    /api/users                           users an admin can pick (manage_users only)
 *   GET    /api/addons[?user=]                  every addon (state, what it adds, its setup words), welcome
 *   PUT    /api/addons/{addon}[?user=]          connect or change (credentials / settings), 400 names the field
 *   DELETE /api/addons/{addon}[?user=]          disconnect
 *   POST   /api/addons/{addon}/test             the connection check (run by itself right after connecting)
 *   GET    /api/addons/{addon}/options          setting suggestions from the user's own service
 *   POST   /api/addons/welcome                  {dismissed}: the first-run welcome, hidden or brought back
 *   manage_server only:
 *   POST   /api/addons/{addon}/disable|enable   for everyone on this server
 *   GET    /api/addons/catalog                  the catalog merged with what is installed
 *   POST   /api/addons/install                  {name} or {repo, path, ref}: fetch one commit (restart to load)
 *   POST   /api/addons/{addon}/remove           delete an installed addon's folder
 *   POST   /api/server/restart                  the server exits and its supervisor starts it again
 *
 * The page is an onboarding flow. The list groups the addons: On, Available, Not available (an admin's
 * choice, off on the server, did not load), and for admins the Catalog. Opening one shows what it adds (its
 * manifest's features, pages and needs) and, until it is on, numbered steps: what to do outside Dana (the
 * manifest's setup), the sign-in button or the fields with their help, link and example, then Connect, which
 * runs the connection check by itself and says in plain words whether it works and what to fix; then "Try
 * asking Dana" with the manifest's examples. Settings, Test again and Disconnect stay on the same view.
 * The page knows no addon by name: every word comes from the payload (core/addons_api.py).
 *
 * First run: while GET /api/addons says welcome.show (no optional addon on, not dismissed), the app shows a
 * welcome over the screen it opens on, suggesting addons to set up; closing it dismisses it for good on the
 * server, and the Addons page offers it again ("Show the welcome again"). The suggestions stay listed under
 * Available either way.
 *
 * Two parts: DanaAddonsCore (pure: view models, HTML, request bodies, error text; no DOM, exercised by
 * test_web_addons.mjs against payloads captured from the API) and the controller below it
 * (window.DanaAddons = {show, hide, openAddon}; app-nav.js calls show / hide on tab switches). Secret
 * credentials are never rendered into HTML: their inputs start empty, what is typed lives only in the input
 * and in memory until it is sent, and a successful save renders the form again (inputs empty).
 */
(() => {
  "use strict";

  // What came back from the Google sign-in (/?google=<result>#addons).
  const GOOGLE_NOTICES = {
    connected: { tone: "ok", text: "Google is connected." },
    denied: { tone: "bad", text: "Google was not connected: the sign-in was cancelled or no access was granted." },
    expired: { tone: "bad", text: "The Google sign-in took too long or was started elsewhere. Try again." },
    failed: { tone: "bad", text: "Google could not be connected. Try again; the server log says why." },
  };
  const SOURCES = { official: "Official", site: "Site", image: "Image", installed: "Installed" };
  const FULL_ACCESS = "An addon runs inside Dana with the server's full access: it can read and change every user's data and reach your network. Install only addons whose code you trust.";
  const FULL_COMMIT = /^[0-9a-f]{40}$/i;
  const TELEGRAM_TYPES = new Set(["telegram_chat"]);
  const LIST_SETTINGS = new Set(["selected_calendars"]);

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const words = (name) => String(name || "").replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
  /** An addon's name for people: its manifest title, else its name in words. */
  const displayName = (item) => (item && item.title) || words(item && item.name);
  const iconOf = (item) => (item && /^[a-z0-9-]+$/.test(item.icon || "") ? item.icon : "plug");
  const safeUrl = (url) => (typeof url === "string" && /^https:\/\/[^\s"'<>]+$/.test(url) ? url : null);
  const fieldsOf = (item) => [...(item.credentials || []).map((f) => ({ ...f, group: "credentials" })),
                              ...(item.settings || []).map((f) => ({ ...f, group: "settings" }))];

  /** A service you connect with an account / address (credentials, or an address setting). */
  function isService(item) {
    return (item.credentials || []).length > 0 || (item.settings || []).some((f) => f.type === "address");
  }

  /** Turning it on takes more than one press: steps outside Dana, a sign-in or details to type. */
  function needsSetup(item) {
    return Boolean((item.setup || []).length || item.connect_url || connectFields(item).some((f) => f.group === "credentials" ? f.required !== false : true));
  }

  /** "+972500000001" for a WhatsApp id; anything else as given. */
  function accountText(account) {
    if (!account) return "";
    const m = /^(\d{6,15})(?::\d+)?@(c\.us|s\.whatsapp\.net)$/.exec(String(account));
    return m ? "+" + m[1] : String(account);
  }

  function hostOf(address) {
    try { return new URL(address).host; } catch (_) { return String(address || ""); }
  }

  /** A field's name for people (the payload's label; older payloads: the name in words). */
  const fieldLabel = (item, name) => {
    const f = fieldsOf(item || {}).find((x) => x.name === name);
    return (f && f.label) || words(name);
  };

  /**
   * Where an addon stands for this user, from the server's answer (and the last check this page ran):
   * {key: on | error | setup | off | locked | server-off | failed, label, reason}.
   */
  function statusOf(item, ui) {
    if (item.state === "failed") {
      return { key: "failed", label: "Didn't load", reason: item.error ? `It failed to load: ${item.error}` : "It failed to load; an admin can see why on this page." };
    }
    if (item.state === "disabled-on-server") {
      return { key: "server-off", label: "Off on server",
               reason: `An admin turned it off for everyone on this server: it does nothing until it's turned on again.${item.connected ? " Your settings are kept." : ""}` };
    }
    if (item.connected) {
      if (item.everyone) return { key: "on", label: "Always on", reason: "" };
      if (item.account_conflict) {
        return { key: "error", label: "Error", reason: `This WhatsApp number (${accountText(item.account_conflict)}) is already connected by another user, so Dana doesn't use this connection. Use a different session, or ask an admin.` };
      }
      if (item.problem) return { key: "error", label: "Error", reason: `${fieldLabel(item, item.problem.field)}: ${item.problem.message} Set it again.` };
      const t = ui && ui.test;
      if (t && t.checked && t.ok === false) return { key: "error", label: "Error", reason: checkFailedText(item, t.reason) };
      return { key: "on", label: "On", reason: "" };
    }
    if (!item.can_change) return { key: "locked", label: "Admin only", reason: item.why_not || "Only an admin can turn it on." };
    return needsSetup(item) ? { key: "setup", label: "Needs setup", reason: "" } : { key: "off", label: "Off", reason: "" };
  }

  /** The list's groups: On, Available (you can turn it on), Not available (an admin's choice, off on the server, failed). */
  function groupItems(items) {
    const out = { on: [], available: [], unavailable: [] };
    for (const i of items || []) {
      if (i.state === "on") out.on.push(i);
      else if (i.state === "available") out.available.push(i);
      else out.unavailable.push(i);
    }
    return out;
  }

  /** The fields shown while turning it on: credentials not filled by a sign-in, and address settings; all once it's on. */
  function connectFields(item) {
    const all = fieldsOf(item);
    if (item.connected) return all;
    return all.filter((f) => (f.group === "credentials" ? !item.connect_url : f.type === "address"));
  }

  /**
   * The numbered steps to turn it on (empty once it's on or when the caller can't):
   * [{kind: "outside", lines} | {kind: "signin", label, url} | {kind: "fields", fields} | {kind: "connect", label}].
   */
  function stepsFor(item) {
    if (item.connected || !item.can_change || item.state !== "available") return [];
    const steps = (item.setup || []).length ? [{ kind: "outside", lines: [...item.setup] }] : [];
    if (item.connect_url) {
      steps.push({ kind: "signin", url: item.connect_url, label: item.connect_label || "Connect with Google" });
      return steps;  // the sign-in connects it
    }
    const fields = connectFields(item);
    if (fields.length) steps.push({ kind: "fields", fields });
    steps.push({ kind: "connect", label: isService(item) ? "Connect" : "Turn on" });
    return steps;
  }

  /** The source badge ("Site", "Installed"…; "" for an official addon). */
  function sourceLabel(item) {
    if (!item || item.source === "official") return "";
    return SOURCES[item.source] || String(item.source || "");
  }

  /** Admin buttons for an item: [{act, label}] (off / on for everyone, remove an installed one). */
  function serverActions(item, canManage) {
    if (!canManage || item.everyone) return [];
    const out = [];
    if (item.state === "disabled-on-server") out.push({ act: "enable", label: "Turn on for the server" });
    else if (item.state !== "failed") out.push({ act: "disable", label: "Turn off for everyone" });
    if (item.source === "installed") out.push({ act: "remove", label: "Remove" });
    return out;
  }

  /** What a catalog entry is here: {key, label, action: install | update | null}. */
  function catalogState(e) {
    if (e.official) return { key: "official", label: "Official: ships with Dana", action: null };
    if (e.present && e.present !== "installed") return { key: "present", label: `Already here (${SOURCES[e.present] || e.present} addon)`, action: null };
    if (!e.compatible) return { key: "incompatible", label: `Needs Dana ${e.min_dana} or later`, action: null };
    if (e.installed && e.update_available) return { key: "update", label: "Update available", action: "update" };
    if (e.installed) return { key: "installed", label: e.installed.loaded ? "Installed" : "Installed: restart to load", action: null };
    return { key: "available", label: "Not installed", action: "install" };
  }

  /** The POST /api/addons/install body from typed fields, or {error} saying what to fix. */
  function installBody(form) {
    const repo = String((form && form.repo) || "").trim(), path = String((form && form.path) || "").trim().replace(/^\/+|\/+$/g, "");
    const ref = String((form && form.ref) || "").trim().toLowerCase();
    if (!repo) return { error: "Give the repository's address (https://… or git@…)." };
    if (!FULL_COMMIT.test(ref)) return { error: "Give the full 40-character commit hash (a branch or tag can change under you)." };
    return { repo, path, ref };
  }

  /** The install / update confirmation: what is fetched, the full-access warning, and what comes next. */
  function installConfirm(source, name, update) {
    return {
      title: `${update ? "Update" : "Install"} ${name || "this addon"}?`,
      rows: [["Repository", source.repo], ["Folder", source.path || "(the repository's root)"], ["Commit", source.ref]],
      warning: FULL_ACCESS,
      after: "Dana fetches exactly this commit. It starts working after a restart, which this page offers next.",
      button: update ? "Update" : "Install",
    };
  }

  /** How one field is shown and edited. */
  function fieldModel(item, f) {
    const editable = Boolean(item.can_change && f.can_change);
    const secret = f.group === "credentials" && f.secret !== false;
    let input = "text";
    if (secret) input = "secret";
    else if (f.type === "address") input = "address";
    else if (TELEGRAM_TYPES.has(f.type)) input = "telegram";
    else if (LIST_SETTINGS.has(f.name) || Array.isArray(f.value) || Array.isArray(f.default)) input = "list";
    const value = secret ? null : f.value;
    const example = f.placeholder || "";
    return {
      name: f.name, group: f.group, label: f.label || words(f.name), what: f.description || "", help: f.help || "",
      url: safeUrl(f.url), type: f.type || "text", input,
      required: f.group === "credentials" ? f.required !== false : false,
      set: secret ? Boolean(f.set) : undefined,
      value, text: value == null ? "" : Array.isArray(value) ? value.join(", ") : String(value),
      placeholder: example ? `e.g. ${example}` : f.group === "settings" && f.default != null ? `Default: ${Array.isArray(f.default) ? f.default.join(", ") : f.default}` : "",
      editable, whyNot: editable ? null : (item.can_change ? f.why_not : item.why_not) || "Set by an admin",
      rule: f.rule || null,
    };
  }

  function sameValue(a, b) {
    const norm = (v) => (v == null ? "" : Array.isArray(v) ? v.map(String).join(",") : String(v)).trim();
    return norm(a) === norm(b);
  }

  /** A typed value as it is sent: numbers for Telegram chats, lists for list settings, null for empty. */
  function typedValue(model, raw) {
    const text = String(raw ?? "").trim();
    if (!text) return null;
    if (model.input === "telegram") return /^-?\d+$/.test(text) ? Number(text) : text;
    if (model.input === "list") return text.split(",").map((s) => s.trim()).filter(Boolean);
    return text;
  }

  /**
   * The PUT body for an addon from what was typed, or null when nothing changed.
   * draft = {values: {name: typed text}, remove: [names of optional secrets to remove]}.
   * Untouched fields, empty secret inputs and fields the caller may not change are never sent.
   */
  function buildChanges(item, draft) {
    const values = (draft && draft.values) || {};
    const remove = new Set((draft && draft.remove) || []);
    const body = {};
    const put = (group, name, value) => { (body[group] = body[group] || {})[name] = value; };
    for (const f of fieldsOf(item)) {
      const m = fieldModel(item, f);
      if (!m.editable) continue;
      if (m.input === "secret") {
        const typed = String(values[f.name] ?? "").trim();
        if (typed) put(f.group, f.name, typed);
        else if (remove.has(f.name) && !m.required && m.set) put(f.group, f.name, null);
        continue;
      }
      if (!(f.name in values)) continue;
      const next = typedValue(m, values[f.name]);
      if (!item.connected) { if (next != null) put(f.group, f.name, next); continue; }
      if (sameValue(next, m.value)) continue;
      // An emptied credential is sent as "" so the server can say it is required; an emptied setting goes back to its default.
      put(f.group, f.name, next == null ? (f.group === "credentials" && m.required ? "" : null) : next);
    }
    if (!item.connected) return body;  // connecting: {} is a valid body (an addon without fields)
    return Object.keys(body).length ? body : null;
  }

  /** Request path for an addon (and action), acting on another user when target is not the caller. */
  function path(addon, action, target, me) {
    const q = target && me && target !== me ? `?user=${encodeURIComponent(target)}` : "";
    return `/api/addons${addon ? "/" + encodeURIComponent(addon) : ""}${action ? "/" + action : ""}${q}`;
  }

  const KEY_TEXT = "The server can't read or store credentials right now: its CREDENTIALS_KEY is missing or not the right one. An admin has to fix the server's configuration; nothing was saved.";

  /** What went wrong, for the page: {field?, message}. */
  function errorText(status, body) {
    body = body || {};
    if (status === 0) return { message: "Couldn't reach the server. Check the connection and try again." };
    if (status === 503 && body.error === "credentials_key") return { message: KEY_TEXT };
    if (status === 400 && body.error === "invalid") return { field: body.field, message: body.message || "That value isn't accepted." };
    if (status === 403 && body.error === "forbidden") return { message: body.needs === "manage_users" ? "Only an admin can change this." : `This needs the '${body.needs}' permission.` };
    if (status === 403 && body.error === "not_connected") return { message: "Connect it first." };
    if (status === 404 && body.error === "user_not_found") return { message: "That user doesn't exist any more." };
    if (status === 409 && body.error === "addon_disabled") return { message: "An admin turned this addon off for everyone on this server." };
    if (status === 401) return { message: "You're signed out. Reload the page to sign in again." };
    if (status === 502) return { message: body.message || "The service couldn't be read." };
    return { message: body.message || body.error || `The server answered HTTP ${status}.` };
  }

  /** A failed check in plain words, with what to fix. */
  function checkFailedText(item, reason) {
    const why = String(reason || "It doesn't answer.").replace(/\s+$/, "");
    const fix = item.connect_url ? "Sign in again, or ask an admin if it keeps failing."
      : connectFields(item).some((f) => f.group === "credentials" || f.type === "address") ? "Check the details below, save them, and test again."
      : "Ask an admin if it keeps failing.";
    return `${why}${/[.!?]$/.test(why) ? "" : "."} ${fix}`;
  }

  /** The result line of a connection check: {ok: true | false | null, text}. */
  function testText(result, item) {
    if (!result) return null;
    if (result.checked === false) return { ok: null, text: result.reason || "This addon has no connection check." };
    return result.ok ? { ok: true, text: "It works: Dana reached it." } : { ok: false, text: checkFailedText(item || {}, result.reason) };
  }

  /** What the page says right after connecting: connected, then whether the check passed. */
  function connectedText(item, result) {
    const name = displayName(item);
    if (!result || result.checked === false) return { ok: true, text: `${name} is on.` };
    if (result.ok) return { ok: true, text: `${name} is connected, and it works.` };
    return { ok: false, text: `${name} is connected, but the check failed: ${checkFailedText(item, result.reason)}` };
  }

  /** The disconnect confirmation, in plain words (the manifest's disconnect_warning says what is deleted). */
  function disconnectText(item, whose) {
    const name = displayName(item);
    const forWhom = whose ? ` for ${whose}` : "";
    const service = isService(item);
    const kept = service ? "The saved address, keys and settings are removed from Dana, so reconnecting means entering them again."
      : (item.settings || []).length ? "Its settings go back to the defaults." : "";
    const deleted = item.disconnect_warning || "Nothing else is deleted.";
    return { title: `${service ? "Disconnect" : "Turn off"} ${name}${forWhom}?`,
             body: [`${name} stops working in Dana and the app${forWhom} until it's turned on again.`, kept, deleted].filter(Boolean).join(" "),
             button: service ? "Disconnect" : "Turn off" };
  }

  /** The welcome's view: {show, items: [addon]} from the list answer (suggested names, in order, that are listed). */
  function welcomeModel(body) {
    const w = (body && body.welcome) || {};
    const byName = new Map(((body && body.addons) || []).map((i) => [i.name, i]));
    const items = (w.suggested || []).map((n) => byName.get(n)).filter(Boolean);
    return { show: Boolean(w.show) && items.length > 0, dismissed: Boolean(w.dismissed), items };
  }

  function dlId(addon, name) { return `itg-dl-${addon}-${name}`; }

  // ---------- HTML ----------
  function renderHeader(s) {
    const me = s.me;
    if (!me) return "";
    const target = (s.users || []).find((u) => u.id === s.target);
    const other = s.target && s.target !== me.id;
    const picker = s.users && s.users.length > 1 ? `
      <label class="itg-picker"><span>Whose addons</span>
        <select class="settings-input" id="itg-user" aria-label="Whose addons">
          ${s.users.map((u) => `<option value="${esc(u.id)}"${u.id === s.target ? " selected" : ""}>${esc(u.name)}${u.id === me.id ? " (you)" : ""} · ${esc(u.access)}</option>`).join("")}
        </select></label>` : "";
    const banner = other ? `
      <div class="itg-banner" role="status"><i data-lucide="user-cog"></i>
        <span>You're editing <strong dir="auto">${esc(target ? target.name : s.target)}</strong>'s addons. Changes apply to them right away.</span>
        <button class="chip" data-mine>Back to mine</button></div>` : "";
    const notice = s.notice ? `<div class="itg-status ${esc(s.notice.tone)}" role="status">${esc(s.notice.text)}</div>` : "";
    return `${picker}${banner}${notice}`;
  }

  function statusChip(st) {
    return `<span class="itg-state ${esc(st.key)}"><span class="itg-dot"></span>${esc(st.label)}</span>`;
  }

  function renderRow(item, ui) {
    const st = statusOf(item, ui);
    const meta = item.account ? accountText(item.account) : "";
    const badge = sourceLabel(item) ? `<span class="itg-badge ${esc(item.source)}">${esc(sourceLabel(item))}</span>` : "";
    const reason = st.key === "error" || st.key === "failed" ? `<span class="itg-row-reason">${esc(st.reason)}</span>` : "";
    return `<button class="itg-row" data-open="${esc(item.name)}">
        <span class="itg-icon"><i data-lucide="${esc(iconOf(item))}"></i></span>
        <span class="itg-row-text">
          <span class="itg-row-name">${esc(displayName(item))}${badge}</span>
          <span class="itg-row-desc" dir="auto">${esc(item.description || "")}</span>
          ${meta ? `<span class="itg-row-meta" dir="auto">${esc(meta)}</span>` : ""}${reason}
        </span>
        ${statusChip(st)}<i data-lucide="chevron-right" class="itg-chev"></i></button>`;
  }

  function renderWelcome(model, where) {
    if (!model.show) return "";
    const rows = model.items.map((i) => `<button class="itg-suggest" data-open="${esc(i.name)}">
        <span class="itg-icon"><i data-lucide="${esc(iconOf(i))}"></i></span>
        <span class="itg-row-text"><span class="itg-row-name">${esc(displayName(i))}</span>
          <span class="itg-row-desc" dir="auto">${esc(i.description || "")}</span></span>
        <span class="itg-suggest-go">Set up<i data-lucide="chevron-right"></i></span></button>`).join("");
    const card = `<h3 id="itg-welcome-title">Welcome to Dana</h3>
      <p>Dana already keeps your tasks, shopping list, projects and the people you talk to. Connect the services you use and she can do more. Each one takes a minute; you can change it any time.</p>
      <div class="itg-suggests">${rows}</div>
      <div class="itg-actions end"><button class="btn-secondary" data-welcome-dismiss>${where === "modal" ? "Not now" : "Hide this"}</button></div>`;
    return where === "modal"
      ? `<div class="ibx-modal-card itg-welcome" role="dialog" aria-modal="true" aria-labelledby="itg-welcome-title">${card}</div>`
      : `<section class="itg-card itg-welcome">${card}</section>`;
  }

  function renderList(s) {
    if (s.error && !s.items) {
      return `<div class="itg-card itg-error"><strong>Couldn't load addons</strong><span>${esc(s.error)}</span>
        <button class="btn-secondary" data-retry><i data-lucide="refresh-cw"></i>Try again</button></div>`;
    }
    if (!s.items) return `<div class="itg-skel"></div><div class="itg-skel short"></div>`;
    const g = groupItems(s.items);
    const ui = s.ui || {};
    const section = (id, title, sub, items, extra = "") => (items.length || extra) ? `
      <section class="itg-group" data-group="${id}"><div class="itg-group-head"><span>${esc(title)}</span><em>${esc(sub)}</em></div>
        ${items.length ? `<div class="itg-card itg-rows">${items.map((i) => renderRow(i, ui[i.name])).join("")}</div>` : ""}${extra}</section>` : "";
    const welcome = s.welcome || { show: false };
    const again = welcome.dismissed && !welcome.show && s.target === (s.me && s.me.id)
      ? `<button class="itg-link-btn" data-welcome-again>Show the welcome again</button>` : "";
    return renderRestartBanner(s) + renderWelcome(welcome, "page") +
      section("on", "On", "Working for you now", g.on) +
      section("available", "Available", "Open one to see what it adds and how to set it up", g.available, again) +
      section("unavailable", "Not available", "An admin's choice, turned off on this server, or not working", g.unavailable) +
      (s.canManage ? renderCatalog(s) : "");
  }

  function renderRestartBanner(s) {
    if (!s.restartNeeded && !s.restarting) return "";
    if (s.restarting) {
      return `<div class="itg-banner itg-restart" role="status"><i data-lucide="loader"></i><span>Dana is restarting. This page reloads when it's back.</span></div>`;
    }
    const button = s.canManage ? `<button class="btn-primary" data-restart>Restart now</button>` : "";
    return `<div class="itg-banner itg-restart" role="status"><i data-lucide="rotate-cw"></i>
      <span><strong>Restart to finish.</strong> An addon was installed, updated or removed; ${s.canManage ? "restart Dana to load the change (everyone is disconnected for a few seconds)." : "an admin has to restart Dana."}</span>${button}</div>`;
  }

  function renderField(item, f, ui) {
    const m = fieldModel(item, f);
    const id = `itg-f-${item.name}-${m.name}`;
    const err = ui && ui.errors && ui.errors[m.name];
    const link = m.url ? ` <a class="itg-help-link" href="${esc(m.url)}" target="_blank" rel="noopener noreferrer">Where to find it<i data-lucide="external-link"></i></a>` : "";
    const help = m.help || link ? `<span class="itg-help">${esc(m.help)}${link}</span>` : "";
    // What the value is for, when the manifest gives no help on where to find it.
    const what = m.what && !m.help && m.what !== m.label ? `<span class="itg-what">${esc(m.what)}</span>` : "";
    const rule = m.rule && m.editable ? `<span class="itg-rule">${esc(m.rule)}</span>` : "";
    const errLine = `<span class="itg-ferr${err ? "" : " hidden"}" data-ferr="${esc(m.name)}" role="alert">${esc(err || "")}</span>`;
    const head = `<span class="itg-flabel">${esc(m.label)}${m.required && m.editable && !item.connected ? "" : !m.required && m.editable && m.group === "credentials" ? " <em>optional</em>" : ""}</span>`;
    if (!m.editable) {
      const shown = m.input === "secret" ? (m.set ? "Set" : "Not set") : (m.text || "Not set");
      return `<div class="itg-field locked" data-field="${esc(m.name)}">${head}
        <div class="itg-ro"><span class="itg-ro-val${m.text || m.set ? "" : " none"}" dir="auto">${esc(shown)}</span>
          <span class="itg-lock"><i data-lucide="lock"></i>${esc(m.whyNot)}</span></div>${help}</div>`;
    }
    if (m.input === "secret") {
      const status = item.connected ? `<span class="itg-set ${m.set ? "on" : ""}">${m.set ? "saved" : "not set"}</span>` : "";
      const removable = !m.required && m.set
        ? `<label class="itg-remove"><input type="checkbox" data-remove="${esc(m.name)}"> Remove the saved ${esc(m.label.toLowerCase())}</label>` : "";
      const ph = item.connected && m.set ? "Leave empty to keep the saved one" : m.placeholder;
      return `<div class="itg-field" data-field="${esc(m.name)}"><label class="itg-flabel-row" for="${id}">${head}${status}</label>
        ${help}<input class="settings-input" id="${id}" type="password" autocomplete="new-password" spellcheck="false" dir="ltr"
          data-addon="${esc(item.name)}" data-name="${esc(m.name)}" placeholder="${esc(ph)}">
        ${removable}${what}${errLine}</div>`;
    }
    const list = ui && ui.options && ui.options[m.name];
    const datalist = item.options && item.connected && m.group === "settings" ? ` list="${dlId(item.name, m.name)}"` : "";
    const dl = datalist ? `<datalist id="${dlId(item.name, m.name)}">${(list || []).map((o) => `<option value="${esc(o.value)}">${esc(o.label)}</option>`).join("")}</datalist>` : "";
    const type = m.input === "telegram" ? `type="text" inputmode="numeric"` : m.input === "address" ? `type="url" inputmode="url"` : `type="text"`;
    const dir = m.input === "address" || m.input === "telegram" ? "ltr" : "auto";
    return `<div class="itg-field" data-field="${esc(m.name)}"><label class="itg-flabel-row" for="${id}">${head}</label>
      ${help}<input class="settings-input" id="${id}" ${type} spellcheck="false" autocomplete="off" dir="${dir}"${datalist}
        data-addon="${esc(item.name)}" data-name="${esc(m.name)}" value="${esc(m.text)}" placeholder="${esc(m.placeholder)}">
      ${dl}${rule}${what}${errLine}</div>`;
  }

  function renderSteps(item, ui) {
    const steps = stepsFor(item);
    if (!steps.length) return "";
    const busy = (ui && ui.busy) || "";
    const body = (st) => {
      if (st.kind === "outside") {
        return st.lines.length === 1 ? `<p dir="auto">${esc(st.lines[0])}</p>`
          : `<ul class="itg-outside">${st.lines.map((l) => `<li dir="auto">${esc(l)}</li>`).join("")}</ul>`;
      }
      if (st.kind === "signin") {
        return `<p>Sign in at the service; Dana gets only what it needs.${item.connect_label ? "" : " One Google sign-in connects Calendar, Contacts and Gmail together; each can be turned off on its own."}</p>
          <div class="itg-actions"><a class="btn-primary" href="${esc(st.url)}">${esc(st.label)}</a></div>`;
      }
      if (st.kind === "fields") return `<div class="itg-fields">${st.fields.map((f) => renderField(item, f, ui)).join("")}</div>`;
      return `<p>${item.check ? "Dana saves it and checks that it answers." : "That's all it needs."}</p>
        <div class="itg-actions"><button class="btn-primary" data-act="save"${busy ? " disabled" : ""}>${busy === "saving" ? "Connecting…" : busy === "testing" ? "Checking…" : esc(st.label)}</button></div>`;
    };
    const title = (st) => ({ outside: "Before you start", signin: "Sign in", fields: "Enter the details", connect: st.label }[st.kind]);
    return `<section class="itg-section"><h4>Set it up</h4><ol class="itg-steps">${steps.map((st) =>
      `<li class="itg-step ${st.kind}"><span class="itg-step-title">${esc(title(st))}</span>${body(st)}</li>`).join("")}</ol></section>`;
  }

  function renderAdds(item) {
    const lines = (item.features || []).map((f) => `<li dir="auto">${esc(f)}</li>`);
    const pages = (item.pages || []).map((p) => `<li>A <strong>${esc(p)}</strong> page in the app</li>`);
    const needs = (item.needs || []).length ? `<p class="itg-needs">Works together with: ${(item.needs || []).map((n) => esc(words(n))).join(", ")} (turned on first).</p>` : "";
    if (!lines.length && !pages.length && !needs) return "";
    return `<section class="itg-section"><h4>What it adds</h4>${lines.length || pages.length ? `<ul class="itg-adds">${lines.join("")}${pages.join("")}</ul>` : ""}${needs}</section>`;
  }

  function renderExamples(item) {
    if (!item.connected || !(item.examples || []).length) return "";
    return `<section class="itg-section"><h4>Try asking Dana</h4><div class="itg-examples">${item.examples.map((e) =>
      `<button class="itg-example" data-ask="${esc(e)}" dir="auto">“${esc(e)}”</button>`).join("")}</div></section>`;
  }

  function renderServerActions(item, canManage, busy) {
    const acts = serverActions(item, canManage);
    if (!acts.length) return "";
    const record = item.installed && item.installed.commit
      ? `<span class="itg-installed" dir="ltr">${esc(item.installed.repo || "")} @ ${esc(String(item.installed.commit).slice(0, 12))}</span>` : "";
    return `<section class="itg-section itg-admin"><h4><i data-lucide="server"></i>On this server</h4>${record}
      <div class="itg-actions">${acts.map((a) => `<button class="chip${a.act === "remove" ? " itg-danger" : ""}" data-act="${a.act}" data-addon="${esc(item.name)}"${busy ? " disabled" : ""}>${esc(a.label)}</button>`).join("")}</div></section>`;
  }

  function renderDetail(item, ui, canManage) {
    ui = ui || {};
    const st = statusOf(item, ui);
    const busy = ui.busy || "";
    const reason = st.reason ? `<div class="itg-note ${st.key === "locked" ? "" : "warn"}"><i data-lucide="${st.key === "locked" ? "lock" : "triangle-alert"}"></i><span>${esc(st.reason)}</span></div>` : "";
    const status = ui.status ? `<div class="itg-status ${esc(ui.status.tone || "")}" role="status">${esc(ui.status.text)}</div>` : `<div class="itg-status hidden" role="status"></div>`;
    let settings = "";
    if (item.connected && !item.everyone) {
      const fields = fieldsOf(item);
      const editable = fields.some((f) => fieldModel(item, f).editable);
      let opts = "";
      if (item.options && item.can_change && fields.length) {
        const o = ui.optionsState;
        opts = `<div class="itg-opts" data-opts>${o === "loading" ? `Reading suggestions from ${esc(displayName(item))}…`
          : o === "ok" ? "Suggestions from your service appear as you type; any other value works too."
          : o ? `Couldn't read suggestions (${esc(o)}). Type the values instead.` : ""}</div>`;
      }
      const buttons = [];
      if (item.connect_url && item.can_change) buttons.push(`<a class="btn-secondary" href="${esc(item.connect_url)}">${esc(item.connect_label ? `${item.connect_label} again` : "Sign in with Google again")}</a>`);
      if (editable) buttons.push(`<button class="btn-primary" data-act="save"${busy ? " disabled" : ""}>${busy === "saving" ? "Saving…" : "Save"}</button>`);
      if (item.check && st.key !== "server-off") buttons.push(`<button class="btn-secondary" data-act="test"${busy ? " disabled" : ""}><i data-lucide="activity"></i>${busy === "testing" ? "Checking…" : "Test again"}</button>`);
      const off = disconnectText(item, "");
      if (item.can_change) buttons.push(`<button class="btn-destructive-outline itg-disconnect" data-act="disconnect"${busy ? " disabled" : ""}>${busy === "disconnecting" ? "…" : esc(off.button)}</button>`);
      const shown = fields.filter((f) => !(item.connect_url && f.group === "credentials"));
      settings = `<section class="itg-section"><h4>${shown.length ? "Settings" : "Connection"}</h4>
        ${shown.length ? `<div class="itg-fields">${shown.map((f) => renderField(item, f, ui)).join("")}</div>` : ""}${opts}
        <div class="itg-actions">${buttons.join("")}</div></section>`;
    }
    const meta = [item.account ? accountText(item.account) : "", item.version ? `v${item.version}` : "", sourceLabel(item)].filter(Boolean).join(" · ");
    return `<div class="itg-detail" data-detail="${esc(item.name)}">
      <button class="itg-link-btn itg-back" data-close><i data-lucide="arrow-left"></i>All addons</button>
      <header class="itg-hero">
        <span class="itg-icon big"><i data-lucide="${esc(iconOf(item))}"></i></span>
        <div class="itg-hero-text"><h3>${esc(displayName(item))}</h3><p dir="auto">${esc(item.description || "")}</p>
          ${meta ? `<span class="itg-row-meta" dir="auto">${esc(meta)}</span>` : ""}</div>
        ${statusChip(st)}
      </header>
      ${reason}${status}
      ${renderAdds(item)}
      ${renderSteps(item, ui)}
      ${settings}
      ${renderExamples(item)}
      ${renderServerActions(item, canManage, busy)}
    </div>`;
  }

  function renderCatalogEntry(e, ui) {
    const st = catalogState(e);
    const busy = ui && ui.busy;
    const meta = [e.author ? `by ${e.author}` : "", ...(e.tags || []), e.installed && e.installed.commit ? `installed @ ${String(e.installed.commit).slice(0, 12)}` : ""].filter(Boolean);
    const action = st.action ? `<button class="chip itg-connect" data-install="${esc(e.name)}"${busy ? " disabled" : ""}>${busy ? (st.action === "update" ? "Updating…" : "Installing…") : (st.action === "update" ? "Update" : "Install")}</button>` : "";
    const remove = e.installed ? `<button class="chip itg-danger" data-act="remove" data-addon="${esc(e.name)}"${busy ? " disabled" : ""}>Remove</button>` : "";
    const home = e.homepage && /^https?:\/\//.test(e.homepage) ? `<a class="itg-link" href="${esc(e.homepage)}" target="_blank" rel="noopener noreferrer">Homepage</a>` : "";
    const status = ui && ui.status ? `<div class="itg-status ${esc(ui.status.tone || "")} itg-row-status" role="status">${esc(ui.status.text)}</div>` : "";
    const icon = /^[a-z0-9-]+$/.test(e.icon || "") ? e.icon : "package";
    return `<div class="itg-entry" data-entry="${esc(e.name)}"><div class="itg-entry-head">
        <span class="itg-icon"><i data-lucide="${esc(icon)}"></i></span>
        <span class="itg-row-text"><span class="itg-row-name">${esc(e.title || e.name)}</span>
          <span class="itg-row-desc" dir="auto">${esc(e.description || "")}</span>
          ${meta.length || home ? `<span class="itg-row-meta">${meta.map((m) => `<span dir="auto">${esc(m)}</span>`).join("")}${home}</span>` : ""}</span>
        <span class="itg-state ${st.key === "installed" ? "on" : st.key === "update" ? "setup" : ""}"><span class="itg-dot"></span>${esc(st.label)}</span>
        <span class="itg-entry-actions">${action}${remove}</span></div>${status}</div>`;
  }

  function renderCatalog(s) {
    const head = `<div class="itg-group-head"><span>Catalog</span><em>More addons for this server (admins)</em></div>`;
    if (s.catalogError) {
      return `<section class="itg-group" data-group="catalog">${head}<div class="itg-card itg-error"><strong>Couldn't read the catalog</strong><span>${esc(s.catalogError)}</span>
        <button class="btn-secondary" data-catalog-retry><i data-lucide="refresh-cw"></i>Try again</button></div></section>`;
    }
    if (!s.catalog) return `<section class="itg-group" data-group="catalog">${head}<div class="itg-skel short"></div></section>`;
    const c = s.catalog, ui = s.catalogUi || {};
    const entries = c.entries.length ? `<div class="itg-card itg-rows">${c.entries.map((e) => renderCatalogEntry(e, ui[e.name])).join("")}</div>`
      : `<div class="itg-card itg-empty"><i data-lucide="package"></i><span>The catalog lists no addons yet.</span></div>`;
    const problems = (c.problems || []).length ? `<details class="itg-note itg-problems"><summary>${c.problems.length} catalog ${c.problems.length === 1 ? "entry was" : "entries were"} skipped</summary>
        <ul>${c.problems.map((p) => `<li>${esc(p)}</li>`).join("")}</ul></details>` : "";
    const form = s.gitForm || {};
    return `<section class="itg-group" data-group="catalog">${head}
      <div class="itg-note warn"><i data-lucide="shield-alert"></i><span>${esc(FULL_ACCESS)}</span></div>
      ${entries}${problems}
      <details class="itg-card itg-git" data-git${form.repo || form.ref ? " open" : ""}><summary>Install from a git repository</summary>
          <label class="itg-field"><span class="itg-flabel">Repository</span><input class="settings-input" data-git-field="repo" type="url" inputmode="url" spellcheck="false" autocomplete="off" dir="ltr" placeholder="https://github.com/owner/repo" value="${esc(form.repo || "")}"></label>
          <label class="itg-field"><span class="itg-flabel">Folder <em>optional</em></span><input class="settings-input" data-git-field="path" type="text" spellcheck="false" autocomplete="off" dir="ltr" placeholder="addons/weather" value="${esc(form.path || "")}"></label>
          <label class="itg-field"><span class="itg-flabel">Commit</span><input class="settings-input" data-git-field="ref" type="text" spellcheck="false" autocomplete="off" dir="ltr" placeholder="the full 40-character commit hash" value="${esc(form.ref || "")}"></label>
          <div class="itg-actions"><button class="btn-secondary" data-git-install${s.gitBusy ? " disabled" : ""}>${s.gitBusy ? "Installing…" : "Install…"}</button></div>
          <div class="itg-status ${s.gitStatus ? esc(s.gitStatus.tone || "") : "hidden"}" role="status">${esc(s.gitStatus ? s.gitStatus.text : "")}</div>
        </details></section>`;
  }

  /** Everything under the header: the list, or one addon's view. */
  function renderBody(s) {
    const item = s.open && (s.items || []).find((i) => i.name === s.open);
    if (item) return renderDetail(item, (s.ui || {})[item.name], s.canManage);
    return renderList(s);
  }

  /** The confirmation dialog's HTML (installs show the repository, folder, commit and the warning). */
  function renderConfirm(c) {
    const rows = (c.rows || []).length ? `<dl class="itg-facts">${c.rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd dir="ltr">${esc(v)}</dd>`).join("")}</dl>` : "";
    const warning = c.warning ? `<div class="itg-note warn"><i data-lucide="shield-alert"></i><span>${esc(c.warning)}</span></div>` : "";
    return `<div class="ibx-modal-card itg-confirm" role="dialog" aria-modal="true" aria-labelledby="itg-confirm-title">
      <h3 id="itg-confirm-title">${esc(c.title)}</h3>${c.body ? `<p>${esc(c.body)}</p>` : ""}${rows}${warning}${c.after ? `<p>${esc(c.after)}</p>` : ""}
      <div class="itg-actions end"><button class="btn-secondary" data-confirm="no">Cancel</button>
        <button class="${c.danger ? "btn-destructive-outline" : "btn-primary"}" data-confirm="yes">${esc(c.button || "OK")}</button></div></div>`;
  }

  const core = { FULL_ACCESS, KEY_TEXT, esc, displayName, iconOf, safeUrl, fieldLabel, isService, needsSetup, accountText,
                 statusOf, groupItems, connectFields, stepsFor, fieldModel, typedValue, buildChanges, path, errorText,
                 checkFailedText, testText, connectedText, disconnectText, welcomeModel, sourceLabel, serverActions,
                 catalogState, installBody, installConfirm, renderHeader, renderRow, renderWelcome, renderList,
                 renderField, renderSteps, renderAdds, renderExamples, renderDetail, renderBody, renderRestartBanner,
                 renderCatalog, renderCatalogEntry, renderConfirm };
  (typeof window !== "undefined" ? window : globalThis).DanaAddonsCore = core;
  if (typeof document === "undefined") return;

  // ---------- controller ----------
  const $ = (id) => document.getElementById(id);
  const icons = () => { try { window.lucide?.createIcons(); } catch (_) { /* ignore */ } };
  const S = { me: null, users: null, target: null, items: null, error: "", open: null, ui: {}, visible: false, confirm: null,
              canManage: false, restartNeeded: false, restarting: false, welcome: null,
              catalog: null, catalogError: "", catalogUi: {}, gitForm: {}, gitBusy: false, gitStatus: null };
  const drafts = {};  // addon -> {values, remove}: what was typed since the form was drawn (memory only)
  let seq = 0;

  async function request(method, url, body) {
    let r;
    try {
      r = await fetch(url, { method, headers: body !== undefined ? { "Content-Type": "application/json" } : {},
                             body: body !== undefined ? JSON.stringify(body) : undefined, cache: "no-store" });
    } catch (_) {
      return { status: 0, body: null };
    }
    let data = null;
    try { data = await r.json(); } catch (_) { /* empty */ }
    return { status: r.status, body: data };
  }

  const uiOf = (addon) => (S.ui[addon] = S.ui[addon] || {});
  const itemOf = (addon) => (S.items || []).find((i) => i.name === addon);
  const whose = () => (S.target && S.me && S.target !== S.me.id ? ((S.users || []).find((u) => u.id === S.target) || {}).name || "this user" : "");

  async function load() {
    const mine = ++seq;
    S.error = "";
    if (!S.me) {
      const me = await request("GET", "/api/me");
      if (mine !== seq) return;
      if (me.status !== 200) { S.error = errorText(me.status, me.body).message; S.items = null; render(); return; }
      S.me = me.body;
      S.target = S.target || S.me.id;
      if ((S.me.capabilities || []).includes("manage_users")) {
        const us = await request("GET", "/api/users");
        if (mine !== seq) return;
        S.users = us.status === 200 ? us.body.users : null;
      }
    }
    S.items = null;
    render();
    const r = await request("GET", path("", "", S.target, S.me.id));
    if (mine !== seq) return;
    if (r.status !== 200) { S.error = errorText(r.status, r.body).message; render(); return; }
    S.items = r.body.addons;
    S.welcome = welcomeModel(r.body);
    S.canManage = Boolean(r.body.can_manage);
    S.restartNeeded = Boolean(r.body.restart_needed);
    for (const k of Object.keys(drafts)) delete drafts[k];
    S.ui = {};
    render();
    if (S.open) loadOptions(S.open);
    if (S.canManage && !S.catalog) loadCatalog(false);
  }

  function render() {
    const kicker = $("itg-kicker");
    if (kicker) kicker.textContent = whose() ? `Editing ${whose()}` : "";
    $("itg-head").innerHTML = renderHeader(S);
    $("itg-list").innerHTML = renderBody(S);
    icons();
  }

  async function loadCatalog(fresh) {
    S.catalogError = "";
    if (fresh) S.catalog = null;
    if (!S.open) render();
    const r = await request("GET", `/api/addons/catalog${fresh ? "?fresh=1" : ""}`);
    if (r.status === 200) { S.catalog = r.body; S.restartNeeded = S.restartNeeded || Boolean(r.body.restart_needed); }
    else { S.catalog = null; S.catalogError = errorText(r.status, r.body).message; }
    if (!S.open) render();
  }

  function openDetail(addon) {
    if (S.open) { delete drafts[S.open]; }
    S.open = addon;
    if (addon && S.ui[addon]) { S.ui[addon].status = null; S.ui[addon].errors = {}; }
    render();
    $("panel-addons")?.querySelector(".itg-scroll")?.scrollTo(0, 0);
    if (addon) loadOptions(addon);
  }

  // Busy / status / field errors in place, without redrawing the inputs (typed values stay where they are).
  function patch(addon) {
    const ui = uiOf(addon);
    const root = document.querySelector(`[data-detail="${CSS.escape(addon)}"]`);
    if (!root) return;
    root.querySelectorAll("[data-act]").forEach((b) => { b.disabled = Boolean(ui.busy); });
    root.querySelectorAll("[data-ferr]").forEach((n) => {
      const msg = (ui.errors || {})[n.dataset.ferr];
      n.textContent = msg || "";
      n.classList.toggle("hidden", !msg);
    });
    const st = root.querySelector(".itg-status");
    if (st) {
      st.className = `itg-status ${ui.status ? ui.status.tone || "" : "hidden"}`;
      st.textContent = ui.status ? ui.status.text : "";
    }
  }

  async function loadOptions(addon) {
    const item = itemOf(addon);
    if (!item || !item.options || !item.connected || !item.can_change) return;
    const ui = uiOf(addon);
    ui.optionsState = "loading";
    const r = await request("GET", path(addon, "options", S.target, S.me.id));
    if (itemOf(addon) !== item) return;
    ui.options = r.status === 200 ? r.body.settings || {} : {};
    ui.optionsState = r.status === 200 ? "ok" : errorText(r.status, r.body).message.replace(/\.$/, "");
    for (const [name, list] of Object.entries(ui.options)) {
      const dl = document.getElementById(dlId(addon, name));
      if (dl) dl.innerHTML = list.map((o) => `<option value="${esc(o.value)}">${esc(o.label)}</option>`).join("");
    }
    const box = document.querySelector(`[data-detail="${CSS.escape(addon)}"] [data-opts]`);
    if (box) box.textContent = ui.optionsState === "ok" ? "Suggestions from your service appear as you type; any other value works too."
      : `Couldn't read suggestions (${ui.optionsState}). Type the values instead.`;
  }

  async function save(addon) {
    const item = itemOf(addon);
    const ui = uiOf(addon);
    const body = buildChanges(item, drafts[addon]);
    if (body === null) { ui.status = { tone: "", text: "Nothing changed." }; ui.errors = {}; patch(addon); return; }
    ui.busy = "saving"; ui.status = null; ui.errors = {};
    patch(addon);
    const r = await request("PUT", path(addon, "", S.target, S.me.id), body);
    if (r.status === 200 || r.status === 201) {
      S.items = S.items.map((i) => (i.name === addon ? r.body : i));
      delete drafts[addon];  // typed credentials are dropped with the form
      const connected = r.status === 201;
      if (connected) changed();
      if (r.body.check) {
        // Connecting (or changing the connection) runs the check by itself: success or what to fix.
        ui.busy = "testing";
        if (connected) render(); else patch(addon);
        const t = await request("POST", path(addon, "test", S.target, S.me.id));
        ui.test = t.status === 200 ? t.body : null;
      }
      ui.busy = "";
      const said = connected ? connectedText(r.body, ui.test) : ui.test ? testText(ui.test, r.body) : { ok: true, text: "Saved." };
      ui.status = { tone: said.ok === false ? "bad" : said.ok ? "ok" : "", text: connected || !ui.test ? said.text : `Saved. ${said.text}` };
      render();
      loadOptions(addon);
      return;
    }
    ui.busy = "";
    const e = errorText(r.status, r.body);
    if (e.field && fieldsOf(item).some((f) => f.name === e.field)) { ui.errors = { [e.field]: e.message }; ui.status = { tone: "bad", text: "Not saved: see the field above." }; }
    else ui.status = { tone: "bad", text: `Not saved. ${e.message}` };
    patch(addon);
  }

  async function test(addon) {
    const ui = uiOf(addon);
    ui.busy = "testing"; ui.status = null;
    patch(addon);
    const r = await request("POST", path(addon, "test", S.target, S.me.id));
    ui.busy = "";
    if (r.status === 200) {
      ui.test = r.body;
      const t = testText(r.body, itemOf(addon));
      ui.status = { tone: t.ok === true ? "ok" : t.ok === false ? "bad" : "", text: t.text };
      render();
      return;
    }
    const e = errorText(r.status, r.body);
    ui.status = { tone: "bad", text: e.field ? `${fieldLabel(itemOf(addon), e.field)}: ${e.message}` : e.message };
    patch(addon);
  }

  // S.confirm = {title, body, rows, warning, after, button, danger, run}: run() on yes.
  function ask(c) {
    S.confirm = c;
    $("itg-modal").innerHTML = renderConfirm(c);
    $("itg-modal").classList.remove("hidden");
    icons();
    $("itg-modal").querySelector('[data-confirm="no"]').focus();
  }

  function askDisconnect(addon) {
    const t = disconnectText(itemOf(addon), whose());
    ask({ ...t, danger: true, run: () => disconnect(addon) });
  }

  function askServer(act, addon) {
    const item = itemOf(addon) || { name: addon };
    const name = displayName(item);
    if (act === "disable") {
      ask({ title: `Turn ${name} off for everyone?`, button: "Turn off", danger: true, run: () => setServer(addon, "disable"),
            body: "It stops for every user: no tools, prompt lines, jobs or pages, and addons that need it stop too. Connections and data are kept; turn it on again any time." });
    } else if (act === "enable") {
      setServer(addon, "enable");
    } else if (act === "remove") {
      ask({ title: `Remove ${name}?`, button: "Remove", danger: true, run: () => removeAddon(addon),
            body: "Its folder is deleted from the server; its tables and rows stay in the database. It's unloaded at the next restart.",
            rows: item.installed ? [["Repository", item.installed.repo], ["Commit", item.installed.commit]] : [] });
    }
  }

  function askInstall(source, name, update) {
    ask({ ...installConfirm(source, name, update), run: () => doInstall(name && !source.fromForm ? { name } : { repo: source.repo, path: source.path, ref: source.ref }, name) });
  }

  function askRestart() {
    ask({ title: "Restart Dana?", button: "Restart", run: restart,
          body: "The server stops and its supervisor starts it again with the addons now on disk. Everyone is disconnected for a few seconds; running chats pick up where they were." });
  }

  function confirmed() { const c = S.confirm; closeConfirm(); if (c && c.run) c.run(); }

  function closeConfirm() { S.confirm = null; $("itg-modal").classList.add("hidden"); $("itg-modal").innerHTML = ""; }

  /** The shell builds its navigation again (static/app-panels.js): an addon's page comes or goes. */
  function changed() {
    document.dispatchEvent(new CustomEvent("dana:addons-changed"));
  }

  async function disconnect(addon) {
    const ui = uiOf(addon);
    ui.busy = "disconnecting"; ui.status = null;
    patch(addon);
    const r = await request("DELETE", path(addon, "", S.target, S.me.id));
    ui.busy = "";
    if (r.status === 200) {
      const { disconnected, ...item } = r.body;
      S.items = S.items.map((i) => (i.name === addon ? item : i));
      delete drafts[addon];
      ui.options = null; ui.optionsState = null; ui.test = null;
      ui.status = { tone: "", text: disconnected ? `${displayName(item)} is off.` : "It wasn't on." };
      render();
      changed();
      return;
    }
    ui.status = { tone: "bad", text: `Not turned off. ${errorText(r.status, r.body).message}` };
    patch(addon);
  }

  async function setServer(addon, act) {
    const ui = uiOf(addon);
    ui.busy = act; ui.status = null;
    render();
    const r = await request("POST", `/api/addons/${encodeURIComponent(addon)}/${act}`);
    ui.busy = "";
    if (r.status === 200) {
      await load();  // addons that need it change state too
      uiOf(addon).status = { tone: "ok", text: act === "disable" ? "Turned off for everyone on this server." : "Turned on again for the server." };
      render();
      changed();
      return;
    }
    ui.status = { tone: "bad", text: errorText(r.status, r.body).message };
    render();
  }

  async function removeAddon(addon) {
    const r = await request("POST", `/api/addons/${encodeURIComponent(addon)}/remove`);
    if (r.status === 200) {
      S.restartNeeded = true;
      S.open = null;
      if (S.catalog) loadCatalog(true);
      await load();
      return;
    }
    const where = S.open ? uiOf(addon) : (S.catalogUi[addon] = { status: null });
    where.status = { tone: "bad", text: `Not removed. ${errorText(r.status, r.body).message}` };
    render();
  }

  async function doInstall(body, name) {
    const fromForm = !body.name;
    if (fromForm) { S.gitBusy = true; S.gitStatus = null; }
    else S.catalogUi[name] = { busy: true };
    render();
    const r = await request("POST", "/api/addons/install", body);
    if (fromForm) S.gitBusy = false;
    const done = r.status === 201 || r.status === 200;
    const text = done ? `${r.body.replaced ? "Updated" : "Installed"} ${r.body.name} at ${String(r.body.commit).slice(0, 12)}. Restart Dana to finish.`
      : `Not installed. ${errorText(r.status, r.body).message}`;
    if (fromForm) { S.gitStatus = { tone: done ? "ok" : "bad", text }; if (done) S.gitForm = {}; }
    else S.catalogUi[name] = { status: { tone: done ? "ok" : "bad", text } };
    if (done) { S.restartNeeded = true; await loadCatalog(true); }
    render();
    if (done) $("panel-addons")?.querySelector(".itg-scroll")?.scrollTo(0, 0);  // the restart banner is at the top
  }

  async function restart() {
    S.restarting = true;
    render();
    const r = await request("POST", "/api/server/restart");
    if (r.status !== 202 && r.status !== 0) {
      S.restarting = false;
      S.error = `Couldn't restart. ${errorText(r.status, r.body).message}`;
      render();
      return;
    }
    // Wait for it to go away and come back, then load the page again (new scripts and panels).
    const started = Date.now();
    let wentDown = r.status === 0;
    const poll = async () => {
      const h = await request("GET", "/api/health");
      if (h.status !== 200) wentDown = true;
      if (h.status === 200 && (wentDown || Date.now() - started > 8000)) { location.reload(); return; }
      if (Date.now() - started > 120000) { S.restarting = false; S.error = "Dana hasn't come back after two minutes: check the server's logs."; render(); return; }
      setTimeout(poll, 1500);
    };
    setTimeout(poll, 1500);
  }

  async function setWelcome(dismissed) {
    const r = await request("POST", "/api/addons/welcome", { dismissed });
    if (r.status === 200 && S.items) {
      S.welcome = welcomeModel({ welcome: r.body, addons: S.items });
      if (S.visible && !S.open) render();
    }
    return r.status === 200 ? r.body : null;
  }

  /** "Try asking Dana": the example goes into the chat box, ready to send. */
  function tryExample(text) {
    window.danaSwitchTab?.("chat");
    const input = document.getElementById("chat-text-input");
    if (input) { input.value = text; input.dispatchEvent(new Event("input", { bubbles: true })); input.focus(); }
  }

  function wire() {
    const root = $("panel-addons");
    root.addEventListener("click", (ev) => {
      const t = ev.target;
      let n;
      if ((n = t.closest("[data-retry]"))) load();
      else if (t.closest("[data-restart]")) askRestart();
      else if (t.closest("[data-catalog-retry]")) loadCatalog(true);
      else if (t.closest("[data-welcome-dismiss]")) setWelcome(true);
      else if (t.closest("[data-welcome-again]")) setWelcome(false);
      else if ((n = t.closest("[data-install]"))) {
        const e = ((S.catalog || {}).entries || []).find((x) => x.name === n.dataset.install);
        if (e) askInstall(e.source, e.name, catalogState(e).action === "update");
      } else if (t.closest("[data-git-install]")) {
        const b = installBody(S.gitForm);
        if (b.error) { S.gitStatus = { tone: "bad", text: b.error }; render(); return; }
        askInstall({ ...b, fromForm: true }, "", false);
      }
      else if (t.closest("[data-mine]")) { S.target = S.me.id; S.open = null; load(); }
      else if ((n = t.closest("[data-open]"))) openDetail(n.dataset.open);
      else if (t.closest("[data-close]")) openDetail(null);
      else if ((n = t.closest("[data-ask]"))) tryExample(n.dataset.ask);
      else if ((n = t.closest("[data-act]"))) {
        const addon = n.dataset.addon || n.closest("[data-detail]")?.dataset.detail;
        if (!addon || uiOf(addon).busy) return;
        if (n.dataset.act === "save") save(addon);
        else if (n.dataset.act === "test") test(addon);
        else if (n.dataset.act === "disconnect") askDisconnect(addon);
        else if (["disable", "enable", "remove"].includes(n.dataset.act)) askServer(n.dataset.act, addon);
      }
    });
    root.addEventListener("input", (ev) => {
      const t = ev.target;
      if (t.dataset && t.dataset.gitField) { S.gitForm[t.dataset.gitField] = t.value; return; }
      if (t.dataset && t.dataset.name && t.dataset.addon) {
        const d = (drafts[t.dataset.addon] = drafts[t.dataset.addon] || { values: {}, remove: [] });
        d.values[t.dataset.name] = t.value;
      }
    });
    root.addEventListener("change", (ev) => {
      const t = ev.target;
      if (t.id === "itg-user") { S.target = t.value; S.open = null; load(); }
      else if (t.dataset && t.dataset.remove) {
        const addon = t.closest("[data-detail]").dataset.detail;
        const d = (drafts[addon] = drafts[addon] || { values: {}, remove: [] });
        d.remove = t.checked ? [...new Set([...d.remove, t.dataset.remove])] : d.remove.filter((x) => x !== t.dataset.remove);
      }
    });
    root.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && ev.target.matches("input.settings-input[data-addon]")) {
        ev.preventDefault();
        const addon = ev.target.dataset.addon;
        if (!uiOf(addon).busy) save(addon);
      }
    });
    $("itg-modal").addEventListener("click", (ev) => {
      const b = ev.target.closest("[data-confirm]");
      if (ev.target.id === "itg-modal" || (b && b.dataset.confirm === "no")) closeConfirm();
      else if (b && b.dataset.confirm === "yes" && S.confirm) confirmed();
    });
    document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && S.confirm) closeConfirm(); });
    $("itg-refresh")?.addEventListener("click", () => { load(); if (S.canManage) loadCatalog(true); });
    $("itg-back")?.addEventListener("click", () => { if (S.open) openDetail(null); else window.danaSwitchTab?.("settings"); });
  }

  // ---------- first run: the welcome over the screen the app opens on ----------
  function closeWelcome(dismiss) {
    const box = $("itg-welcome-modal");
    if (box) box.remove();
    document.removeEventListener("keydown", welcomeKeys);
    if (dismiss) setWelcome(true);
  }

  function welcomeKeys(ev) { if (ev.key === "Escape") closeWelcome(true); }

  async function firstRun() {
    if (location.hash === "#addons") return;  // the page itself shows the welcome
    const r = await request("GET", "/api/addons");
    if (r.status !== 200) return;
    const model = welcomeModel(r.body);
    if (!model.show || $("itg-welcome-modal")) return;
    const box = document.createElement("div");
    box.className = "ibx-modal";
    box.id = "itg-welcome-modal";
    box.innerHTML = renderWelcome(model, "modal");
    document.body.appendChild(box);
    icons();
    box.addEventListener("click", (ev) => {
      const open = ev.target.closest("[data-open]");
      // Choosing one opens its setup; the welcome comes back until something is on or it's dismissed.
      if (open) { closeWelcome(false); window.DanaAddons.openAddon(open.dataset.open); }
      else if (ev.target === box || ev.target.closest("[data-welcome-dismiss]")) closeWelcome(true);
    });
    document.addEventListener("keydown", welcomeKeys);
    box.querySelector("[data-open]")?.focus();
  }

  let wired = false;
  window.DanaAddons = {
    show() {
      if (!wired && $("panel-addons")) { wire(); wired = true; }
      S.visible = true;
      const result = new URLSearchParams(location.search).get("google");
      if (result) {
        S.notice = GOOGLE_NOTICES[result] || null;
        history.replaceState(null, "", location.pathname + location.hash);
      }
      load();
    },
    hide() {
      S.visible = false;
      S.notice = null;
      S.open = null;
      if (S.confirm) closeConfirm();
      // Nothing typed outlives the screen: drafts and any credential still in an input go.
      for (const k of Object.keys(drafts)) delete drafts[k];
      document.querySelectorAll("#panel-addons input[type=password]").forEach((i) => { i.value = ""; });
    },
    /** Open the Addons page on one addon's view (the welcome, other screens). */
    openAddon(name) {
      S.open = name || null;
      window.danaSwitchTab?.("addons");
    },
  };
  if (location.hash === "#addons") setTimeout(() => window.danaSwitchTab?.("addons"), 0);
  else setTimeout(firstRun, 0);
})();
