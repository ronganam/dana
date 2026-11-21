/* Shopping — the pure part of the Shopping panel (shopping.js is the controller).
 *
 * DanaShoppingCore: no DOM, no network. It turns the server's screen payload (GET /api/shopping,
 * docs/shopping.md "API") into what the panel shows, applies the user's pending changes on top of it
 * (optimistic), says what each change sends, words results and errors, and renders HTML strings.
 * Exercised by test_web_shopping.mjs against payloads captured from the real API
 * (tests/fixtures/shopping_web.json, written by test_shopping_users.py).
 *
 * The model: `server` is the last screen the server answered (a GET or a write's answer); `ops` are the
 * user's changes still on their way. What is shown is always applyOps(server, ops): a failed request
 * just drops its op, so the screen falls back to the server's state by itself.
 */
(() => {
  "use strict";

  // Aisle icons from the app's icon set (lucide). Names come from the server (`categories`).
  const AISLE_ICONS = {
    produce: "apple", bakery: "croissant", dairy: "milk", meat: "beef", pantry: "wheat", snacks: "candy",
    drinks: "cup-soda", frozen: "snowflake", household: "spray-can", care: "pill", baby: "baby", pets: "paw-print",
    other: "package",
  };
  const MAX_ITEMS = 30;           // the server's limit per add (400 too_many above it)
  const LINGER_MS = 900;          // a ticked row stays in place this long before it moves to Bought
  const SNACK_MS = 6000;

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const firstName = (name) => String(name || "").trim().split(/\s+/)[0] || "Someone";
  function initials(name) {
    const words = String(name || "").trim().split(/\s+/).filter(Boolean);
    if (!words.length) return "?";
    const letters = (words.length > 1 ? [words[0], words[words.length - 1]] : [words[0]]).map((w) => Array.from(w)[0]);
    return letters.join("").toUpperCase();
  }
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + "s"}`;

  /** The server's name key (addons/shopping/names.py: NFKC, case folded, Hebrew marks out,
   *  dashes and slashes as spaces, other punctuation out, spaces collapsed). */
  function nameKey(name) {
    let out = "";
    for (const c of String(name || "").normalize("NFKC").toLowerCase()) {
      if (/\p{M}/u.test(c) && c >= "֑" && c <= "ׇ") continue;
      if (/\p{Pd}/u.test(c) || c === "/") out += " ";
      else if (!/\p{P}/u.test(c)) out += c;
    }
    return out.replace(/\s+/g, " ").trim();
  }

  /** Typed text as parts, the way the server splits it (lines and commas), with a leading or trailing
   *  count as the quantity. Only for showing the items at once; the server parses the text itself. */
  function splitTyped(text) {
    const parts = [];
    for (const raw of String(text || "").split(/[\n\r,،]+/)) {
      let t = raw.replace(/^\s*\d+[.)]\s+/, "").replace(/^[\s\-*•·▪›>]+/, "").trim();
      if (!t) continue;
      let quantity = "";
      let m = /^(\d+(?:[.,]\d+)?)\s*(?:[x×]\s*)?\s(\S.*)$/i.exec(t) || /^(\d+(?:[.,]\d+)?)\s*[x×]\s*(\S.*)$/i.exec(t);
      if (m) { quantity = m[1]; t = m[2]; }
      else if ((m = /^(.*?\S)\s*(?:\s[x×]|×)\s*(\d+(?:[.,]\d+)?)$/i.exec(t))) { t = m[1]; quantity = m[2]; }
      t = t.replace(/\s+/g, " ").slice(0, 200);
      if (nameKey(t)) parts.push({ name: t, quantity });
    }
    return parts;
  }

  // ---------- applying pending changes (optimistic) ----------

  const rankOf = (categories) => Object.fromEntries((categories || []).map((c, i) => [c.key, i]));

  /** Items in display order: to buy by aisle (then as they came), bought newest first. */
  function ordered(items, categories) {
    const rank = rankOf(categories);
    const other = rank.other ?? 99;
    const toBuy = items.map((it, i) => ({ it, i })).filter((x) => !x.it.checked)
      .sort((a, b) => ((rank[a.it.category] ?? other) - (rank[b.it.category] ?? other)) || (a.i - b.i)).map((x) => x.it);
    const bought = items.filter((it) => it.checked)
      .sort((a, b) => String(b.checked_at || "").localeCompare(String(a.checked_at || "")));
    return [...toBuy, ...bought];
  }

  /** The aisle a name is known under on this screen (an item or a usual with that name), else other. */
  function knownAisle(screen, key) {
    const hit = (screen.items || []).find((i) => nameKey(i.name) === key && i.category !== "other")
      || (screen.usuals || []).find((u) => nameKey(u.name) === key);
    return hit ? hit.category : "other";
  }

  /** One pending change applied to a screen (a new object; the input is never changed). */
  function applyOp(screen, op, me, now) {
    const s = { ...screen, items: (screen.items || []).map((i) => ({ ...i })) };
    const meRef = me ? { id: me.id, name: me.name } : null;
    const find = (id) => s.items.find((i) => i.id === id);
    if (op.type === "add") {
      op.parts.forEach((p, n) => {
        const key = nameKey(p.name);
        const open = s.items.find((i) => !i.checked && nameKey(i.name) === key);
        if (open) { if (p.quantity) open.quantity = p.quantity; return; }
        const done = s.items.find((i) => i.checked && nameKey(i.name) === key);
        if (done) {
          s.items = s.items.filter((i) => i !== done);
          s.items.push({ ...done, checked: false, checked_at: null, checked_by: null, quantity: p.quantity || "", note: "" });
          return;
        }
        s.items.push({ id: op.tempIds[n], name: p.name, quantity: p.quantity || "", note: "", category: knownAisle(screen, key),
                       checked: false, checked_at: null, checked_by: null, added_by: meRef, created_at: now, pending: true });
      });
    } else if (op.type === "check") {
      const it = find(op.id);
      if (it) Object.assign(it, op.checked ? { checked: true, checked_at: now, checked_by: meRef } : { checked: false, checked_at: null, checked_by: null });
    } else if (op.type === "edit") {
      const it = find(op.id);
      if (it) Object.assign(it, op.changes);
    } else if (op.type === "remove") {
      s.items = s.items.filter((i) => i.id !== op.id);
    } else if (op.type === "clear") {
      s.items = s.items.filter((i) => !i.checked);
    } else if (op.type === "forget") {
      s.usuals = (s.usuals || []).filter((u) => nameKey(u.name) !== nameKey(op.name));
    }
    return s;
  }

  /** The screen as shown: the server's last answer with every pending change on top. */
  function applyOps(screen, ops, me, now) {
    if (!screen) return null;
    const at = now || new Date().toISOString();
    let s = screen;
    for (const op of ops || []) s = applyOp(s, op, me, at);
    if (s === screen) return screen;
    s.items = ordered(s.items, s.categories);
    const toBuy = s.items.filter((i) => !i.checked);
    s.counts = { to_buy: toBuy.length, bought: s.items.length - toBuy.length };
    const keys = new Set(toBuy.map((i) => nameKey(i.name)));
    s.usuals = (s.usuals || []).filter((u) => !keys.has(nameKey(u.name)));
    return s;
  }

  // ---------- what each change sends ----------

  const q = (listId) => (listId ? `?list=${encodeURIComponent(listId)}` : "");
  const itemUrl = (id, listId) => `/api/shopping/items/${encodeURIComponent(id)}${q(listId)}`;

  /** {method, url, body} for a change on the list `listId` (writes always name the list on screen). */
  function requestFor(op, listId) {
    switch (op.type) {
      case "add": return { method: "POST", url: `/api/shopping/items${q(listId)}`,
                           body: op.text != null ? { text: op.text } : { items: op.parts.map((p) => ({ name: p.name })) } };
      case "check": return { method: "PATCH", url: itemUrl(op.id, listId), body: { checked: op.checked } };
      case "edit": return { method: "PATCH", url: itemUrl(op.id, listId), body: op.changes };
      case "remove": return { method: "DELETE", url: itemUrl(op.id, listId), body: undefined };
      case "clear": return { method: "POST", url: `/api/shopping/clear-bought${q(listId)}`, body: undefined };
      case "forget": return { method: "DELETE", url: `/api/shopping/suggestions${q(listId)}`, body: { name: op.name } };
      case "active": return { method: "PUT", url: "/api/shopping/active", body: { list_id: op.listId } };
      case "invite": return { method: "POST", url: `/api/shopping/invites/${encodeURIComponent(op.listId)}`,
                              body: op.action === "use" ? { action: "use", move_items: Boolean(op.moveItems) } : { action: "dismiss" } };
      case "share": return { method: "PUT", url: `/api/sharing/shopping_list/${encodeURIComponent(op.listId)}`,
                             body: { user_id: op.userId, level: op.level } };
      case "undo": return { method: "POST", url: `/api/undo/${encodeURIComponent(op.id)}`, body: undefined };
      default: throw new Error(`unknown change ${op.type}`);
    }
  }

  /** The add for typed text: null when there is nothing in it; {error} above the server's limit. */
  function addOp(text, makeId) {
    const parts = splitTyped(text);
    if (!parts.length) return null;
    if (parts.length > MAX_ITEMS) return { error: `That's ${parts.length} items — at most ${MAX_ITEMS} at a time.` };
    return { type: "add", text: String(text), parts, tempIds: parts.map(() => makeId()) };
  }

  /** The add for one chosen name (a usual or a suggestion): sent as a name, never parsed for a count. */
  function addNameOp(name, makeId) {
    return { type: "add", parts: [{ name: String(name), quantity: "" }], tempIds: [makeId()] };
  }

  // ---------- versions and answers ----------

  /** Whether a write's answer (or a GET) replaces the screen we hold. */
  function accepts(current, incoming) {
    if (!incoming || !incoming.list) return false;
    if (!current || !current.list) return true;
    if (incoming.list.id !== current.list.id) return true;
    return (incoming.list.version || 0) >= (current.list.version || 0);
  }

  /** What to do with a `changed` event: "now" (read the list), "later" (after our own writes land), "no". */
  function onChanged(event, current, pending) {
    if (!current || !current.list) return "now";
    if (event.list_id !== current.list.id) return "now";
    if ((event.version || 0) <= (current.list.version || 0)) return "no";
    return pending > 0 ? "later" : "now";
  }

  // ---------- words ----------

  /** The short line under the title. */
  function subtitle(screen, viewer) {
    if (!screen || !screen.list) return "";
    const { list, counts } = screen;
    const n = (counts && counts.to_buy) || 0;
    const count = n ? `${n} to buy` : "Nothing to buy";
    const owner = firstName(list.owner && list.owner.name);
    if (list.access === "read") return `View only · ${owner}'s list`;
    if (list.access === "edit") return `${owner}'s list · ${count}`;
    const others = (list.people || []).filter((p) => p.id !== (viewer && viewer.id) && p.access !== "owner").map((p) => firstName(p.name));
    if (!others.length) return count;
    const who = others.length === 1 ? others[0] : others.length === 2 ? `${others[0]} and ${others[1]}` : `${others[0]} and ${others.length - 1} others`;
    return `${count} · Shared with ${who}`;
  }

  /** What an add did, said only when it isn't the obvious one; with the plain case's short text. */
  function addResultText(results) {
    const by = (st) => (results || []).filter((r) => r.status === st).map((r) => r.name);
    const added = by("added"), back = by("restored"), already = by("already");
    const out = [];
    if (added.length) out.push(added.length === 1 ? `Added ${added[0]}` : `Added ${added.length} items`);
    if (back.length) out.push(back.length === 1 ? `${back[0]} is back on the list` : `${back.length} items are back on the list`);
    if (already.length) out.push(already.length === 1 ? `${already[0]} is already on the list` : `${already.length} were already on the list`);
    return { text: out.join(" · "), obvious: !back.length && !already.length };
  }

  /** {message} for a refused or failed request (status 0: no answer). */
  function errorText(status, body) {
    const b = body || {};
    if (status === 0) return "You're offline — nothing was changed.";
    if (b.error === "read_only") return "This list is view only.";
    if (b.error === "not_connected") return "Shopping isn't turned on for you. An admin can connect it in Settings → Addons.";
    if (b.error === "too_many") return `At most ${b.max || MAX_ITEMS} items at a time.`;
    if (b.error === "duplicate") return String(b.message || "That's already on the list.").replace(/^'([^']+)'/, "$1");
    if (status === 404) return "That's no longer on the list — someone may have changed it.";
    if (b.error === "owner_only") return "Only the list's owner can share it.";
    if (status === 400) return b.message || "That didn't look right.";
    if (status >= 500) return "Something went wrong on the server. Try again.";
    return b.message || (typeof b.error === "string" ? b.error : "") || `Couldn't do that (${status}).`;
  }

  /** What an undo answered. */
  function undoText(status, body) {
    if (status === 200) return { ok: true, text: "Undone" };
    if (status === 409) return { ok: false, text: `Couldn't undo: ${String((body && body.error) || "it changed since").replace(/\.$/, "")}.` };
    if (status === 404) return { ok: false, text: "This can no longer be undone." };
    return { ok: false, text: errorText(status, body) };
  }

  /** A change's snackbar: the server's undo label, shortened for the screen. */
  function undoLabel(label) {
    return String(label || "Done").replace(/ (to|from|on) the shopping list$/, "").replace(/'([^']+)'/g, "$1");
  }

  // ---------- view models ----------

  /** Everything the panel draws, from the shown screen. ui = {me, linger: Set of ids, boughtOpen}. */
  function view(screen, ui) {
    const u = ui || {};
    const me = u.me || null;
    const linger = u.linger || new Set();
    const list = screen.list;
    const canEdit = list.access !== "read";
    const shared = (list.people || []).length > 1;
    const names = Object.fromEntries((screen.categories || []).map((c) => [c.key, c.name]));
    const row = (it) => {
      const by = it.added_by && shared && (!me || it.added_by.id !== me.id) ? it.added_by : null;
      return { id: it.id, name: it.name, quantity: it.quantity || "", note: it.note || "", category: it.category,
               checked: Boolean(it.checked), pending: Boolean(it.pending), lingering: linger.has(it.id),
               by: by ? { name: by.name, initials: initials(by.name) } : null };
    };
    const showInPlace = (it) => !it.checked || linger.has(it.id);
    const groups = [];
    for (const c of screen.categories || []) {
      const rows = (screen.items || []).filter((it) => showInPlace(it) && it.category === c.key).map(row);
      if (rows.length) groups.push({ key: c.key, name: c.name, icon: AISLE_ICONS[c.key] || "package", rows });
    }
    const known = new Set((screen.categories || []).map((c) => c.key));
    const strays = (screen.items || []).filter((it) => showInPlace(it) && !known.has(it.category)).map(row);
    if (strays.length) {
      const other = groups.find((g) => g.key === "other");
      if (other) other.rows.push(...strays);
      else groups.push({ key: "other", name: names.other || "Other", icon: AISLE_ICONS.other, rows: strays });
    }
    const bought = (screen.items || []).filter((it) => it.checked && !linger.has(it.id)).map(row);
    const toBuy = (screen.counts && screen.counts.to_buy) || 0;
    return {
      listId: list.id, access: list.access, canEdit, isOwner: list.access === "owner", shared,
      subtitle: subtitle(screen, me),
      people: shared ? (list.people || []).map((p) => ({ id: p.id, name: p.name, initials: initials(p.name), access: p.access })) : [],
      groups, bought, toBuy, boughtCount: bought.length, boughtOpen: Boolean(u.boughtOpen),
      usuals: canEdit ? (screen.usuals || []).map((x) => ({ name: x.name, category: x.category })) : [],
      invites: screen.invites || [],
      empty: groups.length === 0,
      menu: { switchList: (screen.lists || []).length > 1, clearBought: canEdit && bought.length > 0 },
    };
  }

  /** The list menu's entries (only what it can offer). */
  function menuItems(screen, viewingId) {
    const out = [];
    const lists = screen.lists || [];
    if (lists.length > 1) {
      for (const l of lists) {
        const current = l.id === screen.list.id;
        const whose = l.access === "owner" ? "Your list" : `${firstName(l.owner && l.owner.name)}'s list`;
        out.push({ act: l.access === "read" ? "view" : "switch", id: l.id, label: whose,
                   note: l.access === "read" ? "View only" : "",
                   current });
      }
    }
    const bought = (screen.items || []).filter((i) => i.checked).length;
    if (screen.list.access !== "read" && bought) out.push({ act: "clear", label: `Clear ${plural(bought, "bought item")}` });
    return out;
  }

  /** One invitation banner: the words and what it can do. */
  function inviteModel(invite, screen) {
    const owner = firstName(invite.owner && invite.owner.name);
    const own = screen && screen.list && screen.list.access === "owner" ? screen.counts.to_buy || 0 : 0;
    return {
      listId: invite.list_id,
      text: `${owner} shared their shopping list with you`,
      canUse: invite.access === "edit",
      readOnly: invite.access !== "edit",
      askMove: own > 0,
      moveText: `Move your ${plural(own, "item")} over?`,
    };
  }

  /** The Share sheet: who has the list, who can be added. */
  function shareModel(payload) {
    const p = payload || {};
    const sharedIds = new Set((p.shared_with || []).map((s) => s.user_id));
    return {
      people: (p.shared_with || []).map((s) => ({ id: s.user_id, name: s.name, initials: initials(s.name), level: s.level })),
      others: (p.users || []).filter((u) => !sharedIds.has(u.id)).map((u) => ({ id: u.id, name: u.name, initials: initials(u.name) })),
    };
  }

  // ---------- the editor ----------

  const isCount = (q) => q === "" || /^\d+$/.test(String(q).trim());

  /** The editor's starting values for an item. */
  function editorDraft(item) {
    return { id: item.id, name: item.name, quantity: item.quantity || "", note: item.note || "", category: item.category };
  }

  /** − / + on the quantity: empty counts as 1; going below 2 empties it. Only for plain numbers. */
  function stepQuantity(quantity, delta) {
    if (!isCount(quantity)) return quantity;
    const n = (String(quantity).trim() === "" ? 1 : parseInt(quantity, 10)) + delta;
    return n <= 1 ? "" : String(Math.min(n, 999));
  }

  /** The PATCH body for what changed in the editor ({} when nothing), or {error}. */
  function editChanges(item, draft) {
    const name = String(draft.name ?? "").replace(/\s+/g, " ").trim();
    if (!name) return { error: "The name can't be empty." };
    const body = {};
    if (name !== item.name) body.name = name;
    for (const f of ["quantity", "note"]) {
      const v = String(draft[f] ?? "").replace(/\s+/g, " ").trim();
      if (v !== (item[f] || "")) body[f] = v;
    }
    if (draft.category && draft.category !== item.category) body.category = draft.category;
    return { changes: body };
  }

  // ---------- HTML ----------

  const icon = (name, cls = "") => `<i data-lucide="${esc(name)}"${cls ? ` class="${cls}"` : ""} aria-hidden="true"></i>`;

  function renderRow(r, canEdit) {
    const label = `${r.name}${r.quantity ? `, ${r.quantity}` : ""}${r.note ? `, ${r.note}` : ""}`;
    const by = r.by ? `<span class="shp-by" title="Added by ${esc(r.by.name)}" aria-label="Added by ${esc(r.by.name)}">${esc(r.by.initials)}</span>` : "";
    const text = `<span class="shp-text"><span class="shp-line"><span class="shp-name" dir="auto">${esc(r.name)}</span>`
      + (r.quantity ? `<span class="shp-qty" dir="auto">${esc(r.quantity)}</span>` : "") + `</span>`
      + (r.note ? `<span class="shp-note" dir="auto">${esc(r.note)}</span>` : "") + `</span>`;
    const cls = `shp-row${r.checked ? " is-checked" : ""}${r.pending ? " is-pending" : ""}`;
    if (!canEdit) {
      return `<div class="${cls} is-static" data-id="${esc(r.id)}"><span class="shp-check" aria-hidden="true">${icon("check")}</span>${text}${by}</div>`;
    }
    return `<div class="${cls}" data-id="${esc(r.id)}">`
      + `<button type="button" class="shp-main" role="checkbox" aria-checked="${r.checked}" data-act="tick" data-focus="${esc(r.id)}" `
      + `aria-label="${esc(label)}"><span class="shp-check" aria-hidden="true">${icon("check")}</span>${text}${by}</button>`
      + `<button type="button" class="shp-edit" data-act="edit" data-focus="edit:${esc(r.id)}" aria-label="Edit ${esc(r.name)}" title="Edit (e)">${icon("pencil")}</button>`
      + `</div>`;
  }

  /** The list area as keyed parts [{key, html}] (aisle headers and rows as siblings, so rows keep their nodes). */
  function listParts(v) {
    const parts = [];
    for (const g of v.groups) {
      parts.push({ key: `h:${g.key}`, html: `<div class="shp-aisle" role="heading" aria-level="3">${icon(g.icon)}<span>${esc(g.name)}</span><em>${g.rows.length}</em></div>` });
      for (const r of g.rows) parts.push({ key: `r:${r.id}`, html: renderRow(r, v.canEdit) });
    }
    if (v.boughtCount) {
      parts.push({ key: "bought", html: `<div class="shp-bought-head${v.boughtOpen ? " open" : ""}">`
        + `<button type="button" class="shp-bought-toggle" data-act="bought" aria-expanded="${v.boughtOpen}">${icon("chevron-right", "shp-chev")}<span>Bought</span><em>${v.boughtCount}</em></button>`
        + (v.canEdit ? `<button type="button" class="shp-clear" data-act="clear">Clear</button>` : "") + `</div>` });
      if (v.boughtOpen) for (const r of v.bought) parts.push({ key: `b:${r.id}`, html: renderRow(r, v.canEdit).replace('class="shp-row', 'class="shp-row dn-row is-bought') });
    }
    return parts;
  }

  function renderEmpty(v) {
    return `<div class="shp-empty">${icon("shopping-basket")}<strong>Nothing to buy</strong>`
      + `<span>${v.canEdit ? "Add what you need above, or tell Dana." : "Nothing on this list right now."}</span></div>`;
  }

  function renderUsuals(v) {
    if (!v.usuals.length) return "";
    return `<div class="shp-usuals-label">Usuals</div><div class="shp-usuals" role="list">`
      + v.usuals.map((u) => `<button type="button" class="shp-usual" role="listitem" data-act="usual" data-name="${esc(u.name)}" `
        + `title="Add ${esc(u.name)} · right-click or long-press for more">${icon("plus")}<span dir="auto">${esc(u.name)}</span></button>`).join("")
      + `</div>`;
  }

  function renderInvites(v, screen, ui) {
    return v.invites.map((inv) => {
      const m = inviteModel(inv, screen);
      const state = (ui && ui[inv.list_id]) || "";
      const busy = state === "busy" ? " disabled" : "";
      const actions = state === "ask"
        ? `<span class="shp-inv-q">${esc(m.moveText)}</span>`
          + `<button type="button" class="btn-primary" data-act="invite-move" data-list="${esc(m.listId)}"${busy}>Move them</button>`
          + `<button type="button" class="btn-secondary" data-act="invite-keep" data-list="${esc(m.listId)}"${busy}>Leave them</button>`
        : (m.canUse
          ? `<button type="button" class="btn-primary" data-act="invite-use" data-list="${esc(m.listId)}"${busy}>Use this list</button>`
          : `<button type="button" class="btn-primary" data-act="invite-view" data-list="${esc(m.listId)}"${busy}>Look at it</button>`)
          + `<button type="button" class="btn-secondary" data-act="invite-dismiss" data-list="${esc(m.listId)}"${busy}>Not now</button>`;
      return `<div class="shp-invite" data-invite="${esc(m.listId)}">${icon("users")}`
        + `<div class="shp-inv-body"><span class="shp-inv-text">${esc(m.text)}${m.readOnly ? " <em>(view only)</em>" : ""}</span>`
        + `<div class="shp-inv-actions">${actions}</div></div></div>`;
    }).join("");
  }

  function renderPeople(v) {
    if (!v.people.length) return "";
    return `<div class="shp-people" aria-label="People on this list: ${esc(v.people.map((p) => p.name).join(", "))}">`
      + v.people.slice(0, 4).map((p) => `<span class="shp-avatar" title="${esc(p.name)}${p.access === "owner" ? " (owner)" : p.access === "read" ? " (view only)" : ""}">${esc(p.initials)}</span>`).join("")
      + (v.people.length > 4 ? `<span class="shp-avatar more">+${v.people.length - 4}</span>` : "") + `</div>`;
  }

  function renderMenu(items) {
    if (!items.length) return "";
    const lists = items.filter((i) => i.act === "switch" || i.act === "view");
    const rest = items.filter((i) => i.act === "clear");
    return `<div class="shp-menu" role="menu">`
      + (lists.length ? `<div class="shp-menu-title">Lists</div>` + lists.map((i) => `<button type="button" role="menuitemradio" aria-checked="${i.current}" class="shp-menu-item${i.current ? " current" : ""}" data-act="menu-${i.act}" data-list="${esc(i.id)}">`
        + `${icon(i.current ? "check" : "list")}<span>${esc(i.label)}</span>${i.note ? `<em>${esc(i.note)}</em>` : ""}</button>`).join("") : "")
      + (lists.length && rest.length ? `<div class="shp-menu-sep"></div>` : "")
      + rest.map((i) => `<button type="button" role="menuitem" class="shp-menu-item" data-act="menu-clear">${icon("trash-2")}<span>${esc(i.label)}</span></button>`).join("")
      + `</div>`;
  }

  function renderSuggestions(list, index) {
    if (!list || !list.length) return "";
    return list.map((s, i) => `<li role="option" id="shp-sugg-${i}" class="shp-sugg${i === index ? " active" : ""}" aria-selected="${i === index}" data-act="suggest" data-name="${esc(s.name)}">`
      + `${icon("history")}<span dir="auto">${esc(s.name)}</span><em>${s.times > 1 ? `bought ${s.times}×` : ""}</em></li>`).join("");
  }

  function renderEditor(draft, categories, ui) {
    const u = ui || {};
    const stepper = isCount(draft.quantity);
    return `<div class="shp-sheet-head"><h3 id="shp-editor-title">Edit item</h3>`
      + `<button type="button" class="btn-primary" data-act="editor-close" title="Save (Enter)">Done</button></div>`
      + `<label class="shp-field"><span>Name</span><input class="settings-input" id="shp-ed-name" data-ed="name" dir="auto" maxlength="200" autocomplete="off" value="${esc(draft.name)}"></label>`
      + (u.error ? `<div class="shp-ed-error" role="alert">${esc(u.error)}</div>` : "")
      + `<div class="shp-ed-row"><label class="shp-field shp-qty-field"><span>Quantity</span><div class="shp-stepper">`
      + `<button type="button" class="shp-step" data-act="qty-down" aria-label="Less"${stepper && draft.quantity !== "" ? "" : " disabled"}>${icon("minus")}</button>`
      + `<input class="settings-input" data-ed="quantity" dir="auto" maxlength="40" placeholder="1" autocomplete="off" value="${esc(draft.quantity)}" aria-label="Quantity">`
      + `<button type="button" class="shp-step" data-act="qty-up" aria-label="More"${stepper ? "" : " disabled"}>${icon("plus")}</button></div></label>`
      + `<label class="shp-field shp-note-field"><span>Note</span><input class="settings-input" data-ed="note" dir="auto" maxlength="200" placeholder="Brand, size…" autocomplete="off" value="${esc(draft.note)}"></label></div>`
      + `<div class="shp-field"><span>Aisle</span><div class="shp-aisles" role="radiogroup" aria-label="Aisle">`
      + (categories || []).map((c) => `<button type="button" role="radio" aria-checked="${c.key === draft.category}" class="shp-aisle-chip${c.key === draft.category ? " on" : ""}" data-act="aisle" data-key="${esc(c.key)}">${icon(AISLE_ICONS[c.key] || "package")}<span>${esc(c.name)}</span></button>`).join("")
      + `</div></div>`
      + `<div class="shp-sheet-foot"><button type="button" class="btn-destructive-outline" data-act="editor-remove">${icon("trash-2")}<span>Remove</span></button>`
      + `<span class="shp-hint">Enter saves · Esc cancels</span></div>`;
  }

  function renderShare(model, ui) {
    const u = ui || {};
    const busy = u.busy ? " disabled" : "";
    const person = (p) => `<li class="shp-share-row"><span class="shp-avatar">${esc(p.initials)}</span><span class="shp-share-name">${esc(p.name)}</span>`
      + `<div class="shp-seg" role="radiogroup" aria-label="${esc(p.name)}'s access">`
      + `<button type="button" role="radio" aria-checked="${p.level === "edit"}" class="${p.level === "edit" ? "on" : ""}" data-act="share-level" data-user="${esc(p.id)}" data-level="edit"${busy}>Can edit</button>`
      + `<button type="button" role="radio" aria-checked="${p.level === "read"}" class="${p.level === "read" ? "on" : ""}" data-act="share-level" data-user="${esc(p.id)}" data-level="read"${busy}>View only</button></div>`
      + `<button type="button" class="icon-btn" data-act="share-level" data-user="${esc(p.id)}" data-level="none" aria-label="Stop sharing with ${esc(p.name)}" title="Remove"${busy}>${icon("x")}</button></li>`;
    const other = (p) => `<li class="shp-share-row"><span class="shp-avatar dim">${esc(p.initials)}</span><span class="shp-share-name">${esc(p.name)}</span>`
      + `<button type="button" class="chip" data-act="share-level" data-user="${esc(p.id)}" data-level="edit"${busy}>${icon("plus")}<span>Add</span></button></li>`;
    return `<div class="shp-sheet-head"><h3 id="shp-share-title">Share your list</h3>`
      + `<button type="button" class="icon-btn" data-act="sheet-close" aria-label="Close">${icon("x")}</button></div>`
      + (model === null ? `<div class="shp-sheet-note">Loading…</div>` : "")
      + (u.error ? `<div class="shp-ed-error" role="alert">${esc(u.error)}</div>` : "")
      + (model ? (model.people.length ? `<div class="shp-share-label">On the list</div><ul class="shp-share-list">${model.people.map(person).join("")}</ul>`
                                       : `<div class="shp-sheet-note">Only you can see this list. People you add see changes live and get an invitation.</div>`)
        + (model.others.length ? `<div class="shp-share-label">Add someone</div><ul class="shp-share-list">${model.others.map(other).join("")}</ul>`
                               : (model.people.length ? "" : `<div class="shp-sheet-note">No one else uses Dana on this server yet.</div>`)) : "");
  }

  const core = {
    AISLE_ICONS, MAX_ITEMS, LINGER_MS, SNACK_MS, esc, firstName, initials, nameKey, splitTyped, ordered, applyOp, applyOps,
    requestFor, addOp, addNameOp, accepts, onChanged, subtitle, addResultText, errorText, undoText, undoLabel, view,
    menuItems, inviteModel, shareModel, isCount, editorDraft, stepQuantity, editChanges, renderRow, listParts, renderEmpty,
    renderUsuals, renderInvites, renderPeople, renderMenu, renderSuggestions, renderEditor, renderShare,
  };
  (typeof window !== "undefined" ? window : globalThis).DanaShoppingCore = core;
})();
