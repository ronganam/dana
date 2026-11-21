/* Addons — connect and configure a user's services from Settings.
 *
 *   GET    /api/me                              who is signed in (name, access level, capabilities)
 *   GET    /api/users                           users an admin can pick (manage_users only)
 *   GET    /api/addons[?user=]            every addon the user may connect, with can_change / why_not
 *   PUT    /api/addons/{addon}[?user=]     connect or change (credentials / settings), 400 names the field
 *   DELETE /api/addons/{addon}[?user=]     disconnect
 *   POST   /api/addons/{addon}/test        the connection check
 *   GET    /api/addons/{addon}/options     setting suggestions from the user's own service
 *
 * Two parts: DanaAddonsCore (pure: view models, HTML, request bodies, error text; no DOM, exercised
 * by test_web_addons.mjs against payloads captured from the API) and the controller below it
 * (window.DanaAddons = {show, hide}; app-nav.js calls them on tab switches). Secret credentials are
 * never rendered into HTML: their inputs start empty, what is typed lives only in the input and in
 * memory until it is sent, and a successful save renders the form again (inputs empty).
 * Server side: core/addons_api.py, docs/access.md ("Managing users").
 */
(() => {
  "use strict";

  // ---------- what the page says about each addon (display only; the server decides the rest) ----------
  const ADDONS = {
    homeassistant: { name: "Home Assistant", icon: "house", blurb: "Home controls, location and car reminders, the home map",
                     stops: "home controls, the home map in chat, location and car reminders, the wake confirmation and the dashboard's home tiles" },
    whatsapp: { name: "WhatsApp", icon: "message-circle", blurb: "Messages and voice-note transcripts, through WAHA",
                stops: "reading and sending WhatsApp messages and voice-note transcripts" },
    calendar: { name: "Google Calendar", icon: "calendar", blurb: "Your events in chat, Now, the briefing and the review",
                stops: "calendar events in chat, Now, the briefing and the weekly review" },
    contacts: { name: "Google Contacts", icon: "contact", blurb: "Names, numbers and birthdays for the people Dana knows",
                stops: "looking up your Google contacts and filling in people from them" },
    gmail: { name: "Gmail", icon: "mail", blurb: "Search, read and write your mail in chat",
             stops: "reading, searching and writing your mail" },
    money: { name: "Money", icon: "wallet", blurb: "Bank and card transactions",
             stops: "your bank and card transactions, balances and syncs",
             deletes: "Your saved bank and card logins, accounts and transactions are deleted from Dana." },
    cameras: { name: "Cameras", icon: "cctv", blurb: "Frigate cameras on the dashboard", stops: "the camera views and events on the dashboard" },
    tasks: { name: "Tasks", icon: "check-square", blurb: "Task lists, tasks and reminders", stops: "tasks, lists and reminders" },
    shopping: { name: "Shopping", icon: "shopping-cart", blurb: "Your grocery list, shared live", stops: "the shopping list" },
    knowledge: { name: "Projects", icon: "folder", blurb: "Projects and saved entries", stops: "projects and saved entries" },
    about: { name: "About you", icon: "star", blurb: "What Dana remembers about you", stops: "the facts Dana keeps about you" },
    people: { name: "People", icon: "users", blurb: "People you talk to and their briefs", stops: "people and their briefs" },
    phone: { name: "Phone", icon: "smartphone", blurb: "Notifications Inbox, calls and suggestions, push",
             stops: "the phone's notifications Inbox, calls, suggestions and push" },
    ops: { name: "Ops", icon: "terminal", blurb: "Read-only kubectl and Python on the server, asks first (admins; not a sandbox)", stops: "kubectl and Python on the server" },
  };
  const FIELDS = {
    host: "Address", url: "Address", token: "Access token", api_key: "API key", session: "Session name",
    refresh_token: "Refresh token", calendar_id: "Calendar for new events", selected_calendars: "Calendars shown",
    person: "Person", car_sensor: "Car Bluetooth sensor", car_device: "Car Bluetooth name",
    phone_interactive_sensor: "Screen-on sensor", phone_battery_sensor: "Battery sensor",
    phone_notify_service: "Notify service", weather_entity: "Weather",
    voice_telegram_chat_id: "Voice-note transcripts chat", active_list: "Active list",
  };
  // Settings whose value is a list of ids (typed comma-separated).
  // What came back from the Google sign-in (/?google=<result>#addons).
  const GOOGLE_NOTICES = {
    connected: { tone: "ok", text: "Google is connected." },
    denied: { tone: "bad", text: "Google was not connected: the sign-in was cancelled or no access was granted." },
    expired: { tone: "bad", text: "The Google sign-in took too long or was started elsewhere. Try again." },
    failed: { tone: "bad", text: "Google could not be connected. Try again; the server log says why." },
  };
  const LIST_SETTINGS = new Set(["selected_calendars"]);
  const TELEGRAM_TYPES = new Set(["telegram_chat"]);

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const addonName = (addon) => (ADDONS[addon] && ADDONS[addon].name) || String(addon || "").replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
  const fieldLabel = (name) => FIELDS[name] || String(name || "").replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
  const fieldsOf = (item) => [...(item.credentials || []).map((f) => ({ ...f, group: "credentials" })),
                              ...(item.settings || []).map((f) => ({ ...f, group: "settings" }))];

  /** A service you connect with an account / address (listed first); everything else is built in. */
  function isService(item) {
    return (item.credentials || []).length > 0 || (item.settings || []).some((f) => f.type === "address");
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

  /** {key: connected | off | attention, label, notes: [..]} — only from the server's answer. */
  function rowState(item) {
    if (!item.connected) return { key: "off", label: "Not connected", notes: [] };
    const notes = [];
    if (item.account_conflict) {
      notes.push(`This WhatsApp number (${accountText(item.account_conflict)}) is already connected by another user, so Dana doesn't use this connection. Use a different session, or ask an admin.`);
    }
    if (item.problem) notes.push(`${fieldLabel(item.problem.field)}: ${item.problem.message} Set it again.`);
    return notes.length ? { key: "attention", label: "Needs attention", notes } : { key: "connected", label: "Connected", notes };
  }

  /** The short line under a row's name: the account, else the service's address. */
  function rowMeta(item) {
    if (item.account) return accountText(item.account);
    const addr = fieldsOf(item).find((f) => f.type === "address" && f.value);
    return addr && item.connected ? hostOf(addr.value) : "";
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
    return {
      name: f.name, group: f.group, label: fieldLabel(f.name), help: f.description || "", type: f.type || "text", input,
      required: f.group === "credentials" ? f.required !== false : false,
      set: secret ? Boolean(f.set) : undefined,
      value, text: value == null ? "" : Array.isArray(value) ? value.join(", ") : String(value),
      placeholder: f.group === "settings" && f.default != null ? `Default: ${Array.isArray(f.default) ? f.default.join(", ") : f.default}` : "",
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
    if (!item.connected) return body;  // connecting: {} is a valid body (a official addon)
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
    if (status === 401) return { message: "You're signed out. Reload the page to sign in again." };
    if (status === 502) return { message: body.message || "The service couldn't be read." };
    return { message: body.message || body.error || `The server answered HTTP ${status}.` };
  }

  /** The result line of a connection test. */
  function testText(result) {
    if (!result) return null;
    if (result.checked === false) return { ok: null, text: result.reason || "This addon has no connection check." };
    return result.ok ? { ok: true, text: "It answers: the connection works." } : { ok: false, text: result.reason || "It doesn't answer." };
  }

  /** The disconnect confirmation, in plain words. */
  function disconnectText(item, whose) {
    const name = addonName(item.name);
    const stops = (ADDONS[item.name] && ADDONS[item.name].stops) || "what it does in Dana";
    // An addon whose data goes with the connection (money) says so instead of "Nothing is deleted."
    const deletes = (ADDONS[item.name] && ADDONS[item.name].deletes) || "Nothing is deleted.";
    const forWhom = whose ? ` for ${whose}` : "";
    if (isService(item)) {
      return { title: `Disconnect ${name}${forWhom}?`,
               body: `Dana stops using ${whose ? whose + "'s" : "your"} ${name}: ${stops}. The saved address, keys and settings are removed from Dana, so reconnecting means entering them again. Everything else in Dana is kept.` };
    }
    const settings = (item.settings || []).length ? " Its settings go back to the defaults." : "";
    return { title: `Disconnect ${name}${forWhom}?`,
             body: `${name} will disappear from Dana and the app${forWhom} until it's connected again (${stops}). ${deletes}${settings}` };
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
    return `
      <div class="itg-who">
        <div class="itg-avatar" aria-hidden="true">${esc((me.name || "?").trim().charAt(0).toUpperCase())}</div>
        <div class="itg-who-text"><span class="itg-who-name" dir="auto">${esc(me.name)}</span>
          <span class="itg-who-sub">Signed in · <span class="itg-level">${esc(me.access)}</span></span></div>
      </div>${picker}${banner}${notice}`;
  }

  function renderField(item, f, ui) {
    const m = fieldModel(item, f);
    const id = `itg-f-${item.name}-${m.name}`;
    const err = ui && ui.errors && ui.errors[m.name];
    const help = m.help ? `<span class="itg-help">${esc(m.help)}</span>` : "";
    const rule = m.rule && m.editable ? `<span class="itg-rule">${esc(m.rule)}</span>` : "";
    const errLine = `<span class="itg-ferr${err ? "" : " hidden"}" data-ferr="${esc(m.name)}" role="alert">${esc(err || "")}</span>`;
    const head = `<span class="itg-flabel">${esc(m.label)}${m.required && m.editable && !item.connected ? ' <em>required</em>' : ""}</span>`;
    if (!m.editable) {
      const shown = m.input === "secret" ? (m.set ? "Set" : "Not set") : (m.text || "Not set");
      return `<div class="itg-field locked" data-field="${esc(m.name)}">${head}
        <div class="itg-ro"><span class="itg-ro-val${m.text || m.set ? "" : " none"}" dir="auto">${esc(shown)}</span>
          <span class="itg-lock"><i data-lucide="lock"></i>${esc(m.whyNot)}</span></div>${help}</div>`;
    }
    if (m.input === "secret") {
      const status = item.connected ? `<span class="itg-set ${m.set ? "on" : ""}">${m.set ? "set" : "not set"}</span>` : "";
      const removable = !m.required && m.set
        ? `<label class="itg-remove"><input type="checkbox" data-remove="${esc(m.name)}"> Remove the saved ${esc(m.label.toLowerCase())}</label>` : "";
      const ph = item.connected && m.set ? "Leave empty to keep the saved one" : (m.required ? "Required" : "Optional");
      return `<div class="itg-field" data-field="${esc(m.name)}"><label class="itg-flabel-row" for="${id}">${head}${status}</label>
        <input class="settings-input" id="${id}" type="password" autocomplete="new-password" spellcheck="false"
          data-addon="${esc(item.name)}" data-name="${esc(m.name)}" placeholder="${esc(ph)}">
        ${removable}${help}${errLine}</div>`;
    }
    const list = ui && ui.options && ui.options[m.name];
    const datalist = item.options && item.connected && m.group === "settings" ? ` list="${dlId(item.name, m.name)}"` : "";
    const dl = datalist ? `<datalist id="${dlId(item.name, m.name)}">${(list || []).map((o) => `<option value="${esc(o.value)}">${esc(o.label)}</option>`).join("")}</datalist>` : "";
    const type = m.input === "telegram" ? `type="number" step="1"` : m.input === "address" ? `type="url" inputmode="url"` : `type="text"`;
    return `<div class="itg-field" data-field="${esc(m.name)}"><label class="itg-flabel-row" for="${id}">${head}</label>
      <input class="settings-input" id="${id}" ${type} spellcheck="false" autocomplete="off" dir="auto"${datalist}
        data-addon="${esc(item.name)}" data-name="${esc(m.name)}" value="${esc(m.text)}" placeholder="${esc(m.placeholder)}">
      ${dl}${rule}${help}${errLine}</div>`;
  }

  function renderDetail(item, ui) {
    ui = ui || {};
    const fields = fieldsOf(item);
    const st = rowState(item);
    const notes = st.notes.map((n) => `<div class="itg-note warn"><i data-lucide="triangle-alert"></i><span>${esc(n)}</span></div>`).join("");
    const locked = !item.can_change
      ? `<div class="itg-note"><i data-lucide="lock"></i><span>${esc(item.why_not || "Set by an admin")}: only an admin can connect, change or disconnect this.</span></div>` : "";
    let opts = "";
    if (item.options && item.connected && item.can_change) {
      const o = ui.optionsState;
      opts = `<div class="itg-opts" data-opts>${o === "loading" ? `Reading suggestions from ${esc(addonName(item.name))}…`
        : o === "ok" ? "Suggestions from your service are offered as you type; any other value works too."
        : o ? `Couldn't read suggestions (${esc(o)}). Type the values instead.` : ""}</div>`;
    }
    const editable = fields.some((f) => fieldModel(item, f).editable);
    const busy = ui.busy || "";
    const buttons = [];
    // A sign-in at the service (the server offers it only for your own account): Google (one consent for every
    // Google addon) or a addon's own sign-in page with its connect_label (Telegram's QR code).
    const signIn = item.connect_url && item.can_change;
    const google = signIn && !item.connect_label;
    const signInNote = google ? `<div class="itg-note"><i data-lucide="log-in"></i><span>One Google sign-in connects Calendar, Contacts and Gmail together. Each can be disconnected on its own.</span></div>` : "";
    const signInLabel = item.connect_label ? (item.connected ? `${item.connect_label} again` : item.connect_label)
      : (item.connected ? "Sign in with Google again" : "Connect with Google");
    if (signIn) buttons.push(`<a class="btn-primary" href="${esc(item.connect_url)}">${esc(signInLabel)}</a>`);
    if (item.can_change) {
      if (!item.connected) buttons.push(`<button class="${signIn ? "btn-secondary" : "btn-primary"}" data-act="save"${busy ? " disabled" : ""}>${busy === "saving" ? "Connecting…" : "Connect"}</button>`);
      else if (editable) buttons.push(`<button class="btn-primary" data-act="save"${busy ? " disabled" : ""}>${busy === "saving" ? "Saving…" : "Save"}</button>`);
    }
    if (item.connected && item.check) buttons.push(`<button class="btn-secondary" data-act="test"${busy ? " disabled" : ""}><i data-lucide="activity"></i>${busy === "testing" ? "Testing…" : "Test connection"}</button>`);
    if (item.connected && item.can_change) buttons.push(`<button class="btn-destructive-outline itg-disconnect" data-act="disconnect"${busy ? " disabled" : ""}>${busy === "disconnecting" ? "Disconnecting…" : "Disconnect"}</button>`);
    const status = ui.status ? `<div class="itg-status ${esc(ui.status.tone || "")}" role="status">${esc(ui.status.text)}</div>` : `<div class="itg-status hidden" role="status"></div>`;
    return `<div class="itg-detail" data-detail="${esc(item.name)}">
      ${notes}${locked}${signInNote}
      ${fields.length ? `<div class="itg-fields">${fields.map((f) => renderField(item, f, ui)).join("")}</div>` : ""}
      ${opts}
      <div class="itg-actions">${buttons.join("")}</div>
      ${status}</div>`;
  }

  function renderRow(item, ui, open) {
    const info = ADDONS[item.name] || {};
    const st = rowState(item);
    const meta = rowMeta(item);
    const hasFields = fieldsOf(item).length > 0;
    const chip = `<span class="itg-state ${st.key}"><span class="itg-dot"></span>${esc(st.label)}</span>`;
    const conflict = item.account_conflict ? `<span class="itg-conflict">Number used by another user</span>` : "";
    // Built-in addons without fields: the connect / disconnect control sits on the row itself.
    let control = "";
    if (!hasFields) {
      const busy = ui && ui.busy;
      control = !item.can_change ? `<span class="itg-lock"><i data-lucide="lock"></i>${esc(item.why_not || "Set by an admin")}</span>`
        : item.connected ? `<button class="chip" data-act="disconnect" data-addon="${esc(item.name)}"${busy ? " disabled" : ""}>${busy === "disconnecting" ? "Disconnecting…" : "Disconnect"}</button>`
        : `<button class="chip itg-connect" data-act="save" data-addon="${esc(item.name)}"${busy ? " disabled" : ""}>${busy === "saving" ? "Connecting…" : "Connect"}</button>`;
    }
    const head = `
        <span class="itg-icon"><i data-lucide="${esc(info.icon || "plug")}"></i></span>
        <span class="itg-row-text">
          <span class="itg-row-name">${esc(addonName(item.name))}</span>
          <span class="itg-row-desc">${esc(info.blurb || item.description || "")}</span>
          ${meta || conflict ? `<span class="itg-row-meta">${meta ? `<span dir="auto">${esc(meta)}</span>` : ""}${conflict}</span>` : ""}
        </span>
        ${chip}`;
    const status = !hasFields && ui && ui.status ? `<div class="itg-status ${esc(ui.status.tone || "")} itg-row-status" role="status">${esc(ui.status.text)}</div>` : "";
    if (!hasFields) {
      return `<div class="itg-row flat" data-row="${esc(item.name)}"><div class="itg-row-head">${head}${control}</div>${status}</div>`;
    }
    return `<div class="itg-row${open ? " open" : ""}" data-row="${esc(item.name)}">
      <button class="itg-row-head" data-toggle="${esc(item.name)}" aria-expanded="${open ? "true" : "false"}">${head}
        <i data-lucide="chevron-down" class="itg-chev"></i></button>
      ${open ? renderDetail(item, ui) : ""}</div>`;
  }

  function renderList(s) {
    if (s.error && !s.items) {
      return `<div class="itg-card itg-error"><strong>Couldn't load addons</strong><span>${esc(s.error)}</span>
        <button class="btn-secondary" data-retry><i data-lucide="refresh-cw"></i>Try again</button></div>`;
    }
    if (!s.items) return `<div class="itg-skel"></div><div class="itg-skel short"></div>`;
    const services = s.items.filter(isService), builtin = s.items.filter((i) => !isService(i));
    const section = (title, sub, items) => items.length ? `
      <section class="itg-group"><div class="itg-group-head"><span>${esc(title)}</span><em>${esc(sub)}</em></div>
        <div class="itg-card itg-rows">${items.map((i) => renderRow(i, (s.ui || {})[i.name], s.open === i.name)).join("")}</div></section>` : "";
    return section("Services", "Your own accounts: connect each with its address and key", services) +
           section("Built in", "Parts of Dana you can turn on or off", builtin);
  }

  const core = { ADDONS, FIELDS, esc, addonName, fieldLabel, isService, accountText, rowState, rowMeta, fieldModel, typedValue,
                 buildChanges, path, errorText, testText, disconnectText, renderHeader, renderField, renderDetail, renderRow,
                 renderList, KEY_TEXT };
  (typeof window !== "undefined" ? window : globalThis).DanaAddonsCore = core;
  if (typeof document === "undefined") return;

  // ---------- controller ----------
  const $ = (id) => document.getElementById(id);
  const icons = () => { try { window.lucide?.createIcons(); } catch (_) { /* ignore */ } };
  const S = { me: null, users: null, target: null, items: null, error: "", open: null, ui: {}, visible: false, confirm: null };
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
    for (const k of Object.keys(drafts)) delete drafts[k];
    S.ui = {};
    render();
    if (S.open) loadOptions(S.open);
  }

  function render() {
    const kicker = $("itg-kicker");
    if (kicker) kicker.textContent = whose() ? `Editing ${whose()}` : "";
    $("itg-head").innerHTML = renderHeader(S);
    $("itg-list").innerHTML = renderList(S);
    icons();
  }

  // Redraw one row (after a server answer). Its form starts from the new payload: typed values are gone.
  function renderOne(addon) {
    const el = document.querySelector(`[data-row="${CSS.escape(addon)}"]`);
    const item = itemOf(addon);
    if (!el || !item) return render();
    el.outerHTML = renderRow(item, S.ui[addon], S.open === addon);
    icons();
  }

  // Busy / status / field errors in place, without redrawing the inputs (typed values stay where they are).
  function patch(addon) {
    const ui = uiOf(addon);
    const row = document.querySelector(`[data-row="${CSS.escape(addon)}"]`);
    if (!row) return;
    const item = itemOf(addon);
    if (!fieldsOf(item).length) { renderOne(addon); return; }
    row.querySelectorAll("[data-act]").forEach((b) => {
      b.disabled = Boolean(ui.busy);
      const act = b.dataset.act;
      const label = act === "save" ? (ui.busy === "saving" ? (item.connected ? "Saving…" : "Connecting…") : (item.connected ? "Save" : "Connect"))
        : act === "test" ? (ui.busy === "testing" ? "Testing…" : "Test connection")
        : (ui.busy === "disconnecting" ? "Disconnecting…" : "Disconnect");
      const text = [...b.childNodes].find((n) => n.nodeType === 3 && n.textContent.trim());
      if (text) text.textContent = label; else b.append(label);
    });
    row.querySelectorAll("[data-ferr]").forEach((n) => {
      const msg = (ui.errors || {})[n.dataset.ferr];
      n.textContent = msg || "";
      n.classList.toggle("hidden", !msg);
    });
    const st = row.querySelector(".itg-status");
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
    const el = document.querySelector(`[data-row="${CSS.escape(addon)}"] [data-opts]`);
    if (el) el.textContent = `Reading suggestions from ${addonName(addon)}…`;
    const r = await request("GET", path(addon, "options", S.target, S.me.id));
    if (itemOf(addon) !== item) return;
    ui.options = r.status === 200 ? r.body.settings || {} : {};
    ui.optionsState = r.status === 200 ? "ok" : errorText(r.status, r.body).message.replace(/\.$/, "");
    for (const [name, list] of Object.entries(ui.options)) {
      const dl = document.getElementById(dlId(addon, name));
      if (dl) dl.innerHTML = list.map((o) => `<option value="${esc(o.value)}">${esc(o.label)}</option>`).join("");
    }
    const box = document.querySelector(`[data-row="${CSS.escape(addon)}"] [data-opts]`);
    if (box) box.textContent = ui.optionsState === "ok" ? "Suggestions from your service are offered as you type; any other value works too."
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
    ui.busy = "";
    if (r.status === 200 || r.status === 201) {
      S.items = S.items.map((i) => (i.name === addon ? r.body : i));
      delete drafts[addon];  // typed credentials are dropped with the form
      ui.status = { tone: "ok", text: r.status === 201 ? "Connected." : "Saved." };
      renderOne(addon);
      loadOptions(addon);
      return;
    }
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
      const t = testText(r.body);
      ui.status = { tone: t.ok === true ? "ok" : t.ok === false ? "bad" : "", text: t.text };
    } else {
      const e = errorText(r.status, r.body);
      ui.status = { tone: "bad", text: e.field ? `${fieldLabel(e.field)}: ${e.message}` : e.message };
    }
    patch(addon);
  }

  function askDisconnect(addon) {
    const item = itemOf(addon);
    const t = disconnectText(item, whose());
    S.confirm = addon;
    $("itg-modal").innerHTML = `<div class="ibx-modal-card itg-confirm" role="dialog" aria-modal="true" aria-labelledby="itg-confirm-title">
      <h3 id="itg-confirm-title">${esc(t.title)}</h3><p>${esc(t.body)}</p>
      <div class="itg-actions end"><button class="btn-secondary" data-confirm="no">Cancel</button>
        <button class="btn-destructive-outline" data-confirm="yes">Disconnect</button></div></div>`;
    $("itg-modal").classList.remove("hidden");
    $("itg-modal").querySelector('[data-confirm="no"]').focus();
  }

  function closeConfirm() { S.confirm = null; $("itg-modal").classList.add("hidden"); $("itg-modal").innerHTML = ""; }

  async function disconnect(addon) {
    closeConfirm();
    const ui = uiOf(addon);
    ui.busy = "disconnecting"; ui.status = null;
    patch(addon);
    const r = await request("DELETE", path(addon, "", S.target, S.me.id));
    ui.busy = "";
    if (r.status === 200) {
      const { disconnected, ...item } = r.body;
      S.items = S.items.map((i) => (i.name === addon ? item : i));
      delete drafts[addon];
      ui.options = null; ui.optionsState = null;
      ui.status = { tone: "", text: disconnected ? "Disconnected. Nothing else was deleted." : "It wasn't connected." };
      renderOne(addon);
      return;
    }
    ui.status = { tone: "bad", text: `Not disconnected. ${errorText(r.status, r.body).message}` };
    patch(addon);
  }

  function wire() {
    const root = $("panel-addons");
    root.addEventListener("click", (ev) => {
      const t = ev.target;
      let n;
      if ((n = t.closest("[data-retry]"))) load();
      else if (t.closest("[data-mine]")) { S.target = S.me.id; S.open = null; load(); }
      else if ((n = t.closest("[data-toggle]"))) {
        const addon = n.dataset.toggle;
        const was = S.open;
        S.open = S.open === addon ? null : addon;
        if (was) { delete drafts[was]; if (S.ui[was]) { S.ui[was].status = null; S.ui[was].errors = {}; } renderOne(was); }
        if (S.open) { renderOne(S.open); loadOptions(S.open); }
      } else if ((n = t.closest("[data-act]"))) {
        const addon = n.dataset.addon || n.closest("[data-row]")?.dataset.row;
        if (!addon || uiOf(addon).busy) return;
        if (n.dataset.act === "save") save(addon);
        else if (n.dataset.act === "test") test(addon);
        else if (n.dataset.act === "disconnect") askDisconnect(addon);
      }
    });
    root.addEventListener("input", (ev) => {
      const t = ev.target;
      if (t.dataset && t.dataset.name && t.dataset.addon) {
        const d = (drafts[t.dataset.addon] = drafts[t.dataset.addon] || { values: {}, remove: [] });
        d.values[t.dataset.name] = t.value;
      }
    });
    root.addEventListener("change", (ev) => {
      const t = ev.target;
      if (t.id === "itg-user") { S.target = t.value; S.open = null; load(); }
      else if (t.dataset && t.dataset.remove) {
        const addon = t.closest("[data-row]").dataset.row;
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
      else if (b && b.dataset.confirm === "yes" && S.confirm) disconnect(S.confirm);
    });
    document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && S.confirm) closeConfirm(); });
    $("itg-refresh")?.addEventListener("click", () => load());
    $("itg-back")?.addEventListener("click", () => window.danaSwitchTab?.("settings"));
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
      if (S.confirm) closeConfirm();
      // Nothing typed outlives the screen: drafts and any credential still in an input go.
      for (const k of Object.keys(drafts)) delete drafts[k];
      document.querySelectorAll("#panel-addons input[type=password]").forEach((i) => { i.value = ""; });
    },
  };
  if (location.hash === "#addons") setTimeout(() => window.danaSwitchTab?.("addons"), 0);
})();
