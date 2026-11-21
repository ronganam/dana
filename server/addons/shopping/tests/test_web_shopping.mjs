/**
 * Contract test for the web Shopping panel's pure part (addons/shopping/web/shopping-core.js, DanaShoppingCore).
 *
 * Loads it without a browser and feeds it the *actual payloads* the API answered, captured in
 * tests/fixtures/shopping_web.json by `WRITE_WEB_FIXTURE=1 bin/test test_shopping_users`: the owner alone
 * (aisles, quantities, notes, Hebrew, bought items, usuals), the owner of a shared list, a member with an
 * invitation and then on the shared list, a read-only viewer, an empty list, every write's answer and each
 * error. Checks what is shown (grouping, row state, words), what each change sends, the optimistic
 * apply / revert, version handling and what the editor sends.
 *
 * Run: bin/test test_web_shopping [path-to-fixture]
 */

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const SERVER = path.join(here, "..", "..", "..");  // the server folder
const FIXTURE = process.argv[2] || path.join(SERVER, "tests", "fixtures", "shopping_web.json");
const F = JSON.parse(fs.readFileSync(FIXTURE, "utf8"));
const src = fs.readFileSync(path.join(SERVER, "addons/shopping/web/shopping-core.js"), "utf8");

const sandbox = { console, URL, Set, Map, JSON, Object, Array, String, Number, Boolean, RegExp, Math, Date, Error };
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "addons/shopping/web/shopping-core.js" });
const C = sandbox.DanaShoppingCore;

let pass = 0;
const failures = [];
function check(name, cond, detail = "") {
    if (cond) { pass++; console.log(`[PASS] ${name}`); }
    else { failures.push(name); console.log(`[FAIL] ${name}${detail ? " — " + String(detail).slice(0, 500) : ""}`); }
}
const plain = (o) => JSON.parse(JSON.stringify(o));  // values made in the sandbox, compared here
const body = (k) => F[k].body;
const names = (items) => items.map((i) => i.name);

const alone = body("owner_alone");
const noa = { id: alone.list.owner.id, name: alone.list.owner.name };
const shared = body("owner_shared");
const tomScreen = body("member_shared");
const tom = { id: shared.list.people.find((p) => p.name.startsWith("Tom")).id, name: "Tom Hadad" };
const viewerScreen = body("viewer");
const galInvite = body("viewer_invite");
const gal = { id: galInvite.list.owner.id, name: galInvite.list.owner.name };

check("loaded the panel's pure part without a DOM", C && typeof C.view === "function" && typeof C.applyOps === "function");
check("fixture: the core payloads are there", F.owner_alone.status === 200 && F.member_invite.status === 200 && F.viewer.status === 200);

// --- grouping by aisle ---------------------------------------------------------------------------
const v = C.view(alone, { me: noa });
const order = alone.categories.map((c) => c.key);
check("groups: only aisles with items, in the server's aisle order",
      JSON.stringify(plain(v.groups.map((g) => g.key))) === JSON.stringify(order.filter((k) => alone.items.some((i) => !i.checked && i.category === k))),
      JSON.stringify(plain(v.groups.map((g) => g.key))));
check("groups: the aisle names come from the server, with an icon from the app's set",
      v.groups.every((g) => g.name === alone.categories.find((c) => c.key === g.key).name && C.AISLE_ICONS[g.key] === g.icon));
check("groups: every aisle key the server knows has an icon", order.every((k) => typeof C.AISLE_ICONS[k] === "string"));
check("groups: rows keep the server's order inside an aisle",
      JSON.stringify(plain(v.groups.flatMap((g) => g.rows.map((r) => r.name)))) === JSON.stringify(names(alone.items.filter((i) => !i.checked))));
check("groups: a name no aisle knows is under Other, last", v.groups[v.groups.length - 1].key === "other"
      && v.groups[v.groups.length - 1].rows[0].name === "Birthday candles");
check("bought: its own section, newest first, with the count", v.boughtCount === 2 && JSON.stringify(plain(v.bought.map((r) => r.name)))
      === JSON.stringify(names(alone.items.filter((i) => i.checked))) && v.bought.every((r) => r.checked));
check("rows: quantity and note carried, empty strings when none",
      (() => { const y = v.groups.flatMap((g) => g.rows).find((r) => r.name === "יוגורט"); return y.quantity === "4" && y.note === "בלי סוכר"; })());
check("rows: no added-by initial when the list isn't shared", v.groups.flatMap((g) => g.rows).every((r) => r.by === null));
check("header: alone → the count only, no people, no share-related words", v.subtitle === `${alone.counts.to_buy} to buy` && v.people.length === 0);
check("menu: one list → no switching; bought items → Clear bought",
      v.menu.switchList === false && v.menu.clearBought === true
      && JSON.stringify(plain(C.menuItems(alone).map((m) => m.act))) === '["clear"]' && C.menuItems(alone)[0].label === "Clear 2 bought items");
check("usuals: from the server, offered on a list the user can change", JSON.stringify(plain(v.usuals.map((u) => u.name).sort())) === '["Oat milk","Pita"]');

// --- HTML: rows, direction, escaping ---------------------------------------------------------------
const parts = C.listParts({ ...v, boughtOpen: true });
const keys = parts.map((p) => p.key);
check("list parts: aisle headers and rows as keyed siblings, bought last",
      keys[0] === `h:${v.groups[0].key}` && keys.includes(`r:${alone.items[0].id}`) && keys.indexOf("bought") > keys.indexOf("h:other")
      && keys.filter((k) => k.startsWith("b:")).length === 2);
check("list parts: Bought closed shows only its header", C.listParts(v).filter((p) => p.key.startsWith("b:")).length === 0
      && /Bought<\/span><em>2<\/em>[\s\S]*data-act="clear"/.test(C.listParts(v).find((p) => p.key === "bought").html));
const heb = C.renderRow(v.groups.flatMap((g) => g.rows).find((r) => r.name === "יוגורט"), true);
check("rows: name, quantity and note each read in their own direction (dir=auto)",
      /class="shp-name" dir="auto">יוגורט</.test(heb) && /class="shp-qty" dir="auto">4</.test(heb) && /class="shp-note" dir="auto">בלי סוכר</.test(heb), heb);
check("rows: the check target first and the edit button last, whatever the language",
      heb.indexOf("shp-check") < heb.indexOf("shp-name") && heb.lastIndexOf('data-act="edit"') > heb.indexOf("shp-note"));
check("rows: the whole row is one checkbox button with a spoken label", /<button type="button" class="shp-main" role="checkbox" aria-checked="false" data-act="tick"[^>]*aria-label="יוגורט, 4, בלי סוכר"/.test(heb), heb);
check("rows: the edit button is labelled", /aria-label="Edit יוגורט"/.test(heb));
const evil = C.renderRow({ id: 'x"><img src=x>', name: "<script>alert(1)</script>", quantity: "<b>", note: "&", checked: false, by: null }, true);
check("rows: user text is escaped", !evil.includes("<script>") && !evil.includes("<img") && evil.includes("&lt;script&gt;") && evil.includes("&amp;"));

// --- a shared list: the owner, the member ----------------------------------------------------------
const vs = C.view(shared, { me: noa });
check("shared (owner): who it is shared with, in the header line",
      vs.subtitle === `${shared.counts.to_buy} to buy · Shared with Tom and Gal` && vs.shared, vs.subtitle);
check("shared (owner): the people as initials, owner first", JSON.stringify(plain(vs.people.map((p) => p.initials))) === '["NP","TH","G"]');
const byTom = vs.groups.flatMap((g) => g.rows).filter((r) => r.by);
check("shared (owner): an initial on rows someone else added, never on their own",
      byTom.length === 3 && byTom.every((r) => r.by.initials === "TH" && r.by.name === "Tom Hadad")
      && JSON.stringify(plain(byTom.map((r) => r.name).sort())) === '["Olive oil","Pasta","Toilet paper"]');
check("shared (owner): the initial is in the row's HTML with who it was", /class="shp-by" title="Added by Tom Hadad"[^>]*>TH</.test(C.renderRow(byTom[0], true)));
const vt = C.view(tomScreen, { me: tom });
check("shared (member): whose list it is, then the count", vt.subtitle === `Noa's list · ${tomScreen.counts.to_buy} to buy` && vt.canEdit, vt.subtitle);
check("shared (member): Noa's rows carry her initial, his own don't",
      vt.groups.flatMap((g) => g.rows).filter((r) => r.by).every((r) => r.by.initials === "NP")
      && vt.groups.flatMap((g) => g.rows).filter((r) => ["Pasta", "Olive oil", "Toilet paper"].includes(r.name)).every((r) => r.by === null));
check("shared (member): not the owner → no Share", vt.isOwner === false && vs.isOwner === true);
const tomMenu = C.menuItems(tomScreen);
check("shared (member): the menu offers his lists, the current one marked",
      tomMenu.filter((m) => m.act === "switch").length === 2 && tomMenu.find((m) => m.current).label === "Noa's list"
      && tomMenu.find((m) => !m.current && m.act === "switch").label === "Your list", JSON.stringify(plain(tomMenu)));
check("menu HTML: lists as radio items", /role="menuitemradio" aria-checked="true"[^>]*data-act="menu-switch"/.test(C.renderMenu(tomMenu)));

// --- the invitation --------------------------------------------------------------------------------
const inviteScreen = body("member_invite");
const inv = C.inviteModel(inviteScreen.invites[0], inviteScreen);
check("invite: the words, with the owner's first name", inv.text === "Noa shared their shopping list with you" && inv.canUse);
check("invite: his own list has items → asks to move them first", inv.askMove && inv.moveText === `Move your ${inviteScreen.counts.to_buy} items over?`, inv.moveText);
const banner = C.renderInvites(C.view(inviteScreen, { me: tom }), inviteScreen, {});
check("invite: the banner has Use this list and Not now", /Noa shared their shopping list with you[\s\S]*data-act="invite-use"[^>]*>Use this list[\s\S]*data-act="invite-dismiss"[^>]*>Not now/.test(banner));
const asking = C.renderInvites(C.view(inviteScreen, { me: tom }), inviteScreen, { [inv.listId]: "ask" });
check("invite: the move step is inline in the banner (Move them / Leave them)",
      /Move your 3 items over\?[\s\S]*data-act="invite-move"[^>]*>Move them[\s\S]*data-act="invite-keep"[^>]*>Leave them/.test(asking) && !asking.includes("Use this list"));
check("invite: busy disables its buttons", (C.renderInvites(C.view(inviteScreen, { me: tom }), inviteScreen, { [inv.listId]: "busy" }).match(/ disabled/g) || []).length === 2);
const empty = { ...inviteScreen, items: [], counts: { to_buy: 0, bought: 0 } };
check("invite: nothing of his own to buy → no question", C.inviteModel(inviteScreen.invites[0], empty).askMove === false);
check("invite: use sends move_items as chosen", JSON.stringify(plain(C.requestFor({ type: "invite", listId: inv.listId, action: "use", moveItems: true })))
      === JSON.stringify({ method: "POST", url: `/api/shopping/invites/${inv.listId}`, body: { action: "use", move_items: true } })
      && C.requestFor({ type: "invite", listId: inv.listId, action: "use", moveItems: false }).body.move_items === false
      && JSON.stringify(plain(C.requestFor({ type: "invite", listId: inv.listId, action: "dismiss" }).body)) === '{"action":"dismiss"}');
check("invite: the answer to use is the shared list's screen, with one undo for the whole step",
      body("invite_use").list.id === inv.listId && /^Switched to .*'s shopping list and moved 3 items$/.test(body("invite_use").undo.label)
      && C.undoLabel(body("invite_use").undo.label) === "Switched to Noa Peretz's shopping list and moved 3 items", body("invite_use").undo.label);
const readInv = C.inviteModel(galInvite.invites[0], galInvite);
check("invite (view only): Look at it instead of Use, and no move question",
      readInv.readOnly && !readInv.canUse && !readInv.askMove
      && /data-act="invite-view"[^>]*>Look at it[\s\S]*\(view only\)|\(view only\)[\s\S]*data-act="invite-view"/.test(C.renderInvites(C.view(galInvite, { me: gal }), galInvite, {})));
check("invite: dismissed → the answer has no invitation", body("invite_dismiss").invites.length === 0);

// --- an empty list ---------------------------------------------------------------------------------
const ve = C.view(galInvite, { me: gal });
check("empty: no groups, the calm empty state, the add bar stays (can edit)", ve.empty && ve.canEdit && ve.groups.length === 0
      && /Nothing to buy[\s\S]*Add what you need above, or tell Dana\./.test(C.renderEmpty(ve)) && ve.subtitle === "Nothing to buy");
check("empty: no usuals → nothing rendered for them", C.renderUsuals(ve) === "");

// --- read only -------------------------------------------------------------------------------------
const vr = C.view(viewerScreen, { me: gal });
check("read only: can't edit, the header says so", !vr.canEdit && vr.subtitle.startsWith("View only · Noa's list"), vr.subtitle);
const roHtml = C.listParts(vr).map((p) => p.html).join("");
check("read only: rows are static — no tick, no edit button", !roHtml.includes('data-act="tick"') && !roHtml.includes('data-act="edit"') && roHtml.includes("is-static"));
check("read only: no usuals, no Clear", vr.usuals.length === 0 && !roHtml.includes('data-act="clear"') && C.menuItems(viewerScreen).every((m) => m.act !== "clear"));
check("read only: a refused write is said plainly", C.errorText(F.error_read_only.status, F.error_read_only.body) === "This list is view only.");
check("read only: the menu views a read-only list with ?list= (never makes it active)",
      C.menuItems(galInvite).find((m) => m.id === viewerScreen.list.id).act === "view");

// --- optimistic changes: apply, revert ---------------------------------------------------------
const milk = alone.items.find((i) => i.name === "חלב");
const now = "2030-01-01T00:00:00+00:00";
const ticked = C.applyOps(alone, [{ type: "check", id: milk.id, checked: true }], noa, now);
check("tick: shown as bought at once (counts, Bought, by me)", ticked.counts.to_buy === alone.counts.to_buy - 1
      && ticked.items.find((i) => i.id === milk.id).checked && ticked.items.find((i) => i.id === milk.id).checked_by.id === noa.id);
check("tick: the newest bought comes first", ticked.items.filter((i) => i.checked)[0].id === milk.id);
const lingering = C.view(ticked, { me: noa, linger: new Set([milk.id]) });
check("tick: while it lingers it stays in its aisle, shown ticked, and isn't in Bought yet",
      lingering.groups.find((g) => g.key === "dairy").rows.some((r) => r.id === milk.id && r.checked && r.lingering)
      && !lingering.bought.some((r) => r.id === milk.id));
check("tick: after the moment it moves to Bought", C.view(ticked, { me: noa }).bought[0].id === milk.id
      && !C.view(ticked, { me: noa }).groups.flatMap((g) => g.rows).some((r) => r.id === milk.id));
check("revert: no pending change → exactly the server's screen", C.applyOps(alone, [], noa) === alone);
check("revert: the input screen is never changed", !alone.items.find((i) => i.id === milk.id).checked);
const add = C.addOp("2 avocados, Bread, Eggs", (() => { let n = 0; return () => `tmp-${++n}`; })());
check("add: typed text split like the server (commas, lines, a leading count)",
      JSON.stringify(plain(add.parts)) === JSON.stringify([{ name: "avocados", quantity: "2" }, { name: "Bread", quantity: "" }, { name: "Eggs", quantity: "" }]));
const added = C.applyOps(alone, [add], noa, now);
check("add: a new name shows at once as pending, under its aisle when the screen knows one, else Other",
      added.items.find((i) => i.id === "tmp-1" && i.pending && i.quantity === "2" && i.category === "other"));
check("add: a name already to buy isn't added twice", added.items.filter((i) => C.nameKey(i.name) === "bread").length === 1);
check("add: a bought name comes back to buy", added.items.find((i) => i.name === "Eggs" && !i.checked)
      && !added.items.some((i) => i.name === "Eggs" && i.checked) && added.counts.to_buy === alone.counts.to_buy + 2);
check("add: a usual that was added leaves the usuals at once",
      JSON.stringify(plain(C.applyOps(alone, [C.addNameOp("Pita", () => "tmp-p")], noa, now).usuals.map((u) => u.name))) === '["Oat milk"]');
check("add: sends the typed text as is (the server parses it)",
      JSON.stringify(plain(C.requestFor(add, alone.list.id))) === JSON.stringify({ method: "POST", url: `/api/shopping/items?list=${alone.list.id}`, body: { text: "2 avocados, Bread, Eggs" } }));
const usual = C.addNameOp("2% milk", () => "tmp-u");
check("add: a chosen name (usual / suggestion) is sent as a name, never parsed for a count",
      JSON.stringify(plain(C.requestFor(usual, "L1").body)) === '{"items":[{"name":"2% milk"}]}' && usual.parts[0].quantity === "");
check("add: nothing typed → no change", C.addOp("  , \n ", () => "x") === null);
check("add: more than 30 → said before sending (and nothing sent)", /at most 30 at a time/.test(C.addOp(Array.from({ length: 31 }, (_, i) => `x${i}`).join(","), () => "x").error));
const removed = C.applyOps(alone, [{ type: "remove", id: milk.id }], noa);
check("remove: gone at once", !removed.items.some((i) => i.id === milk.id) && removed.counts.to_buy === alone.counts.to_buy - 1);
const cleared = C.applyOps(alone, [{ type: "clear" }], noa);
check("clear bought: bought rows gone at once, to buy untouched", cleared.counts.bought === 0 && cleared.counts.to_buy === alone.counts.to_buy);
const forgot = C.applyOps(alone, [{ type: "forget", name: "pita" }], noa);
check("forget: the usual leaves at once (by the name key)", JSON.stringify(plain(forgot.usuals.map((u) => u.name))) === '["Oat milk"]');
const edited = C.applyOps(alone, [{ type: "edit", id: milk.id, changes: { category: "pantry", quantity: "3" } }], noa);
check("edit: applied at once and re-sorted into its new aisle", edited.items.find((i) => i.id === milk.id).quantity === "3"
      && C.view(edited, { me: noa }).groups.find((g) => g.key === "pantry").rows.some((r) => r.id === milk.id));
const stacked = C.applyOps(alone, [{ type: "check", id: milk.id, checked: true }, { type: "remove", id: alone.items[0].id }], noa);
check("several pending changes stack; dropping one shows the rest", stacked.counts.to_buy === alone.counts.to_buy - 2
      && C.applyOps(alone, [{ type: "remove", id: alone.items[0].id }], noa).counts.to_buy === alone.counts.to_buy - 1);

// --- what each change sends ------------------------------------------------------------------------
const L = alone.list.id;
const req = (op) => JSON.stringify(plain(C.requestFor(op, L)));
check("sends: tick / untick", req({ type: "check", id: "a b", checked: true }) === JSON.stringify({ method: "PATCH", url: `/api/shopping/items/a%20b?list=${L}`, body: { checked: true } }));
check("sends: edit only the changed fields", req({ type: "edit", id: "i1", changes: { note: "ripe" } }) === JSON.stringify({ method: "PATCH", url: `/api/shopping/items/i1?list=${L}`, body: { note: "ripe" } }));
check("sends: remove", req({ type: "remove", id: "i1" }) === JSON.stringify({ method: "DELETE", url: `/api/shopping/items/i1?list=${L}` }));
check("sends: clear bought", req({ type: "clear" }) === JSON.stringify({ method: "POST", url: `/api/shopping/clear-bought?list=${L}` }));
check("sends: don't suggest again", req({ type: "forget", name: "Hummus" }) === JSON.stringify({ method: "DELETE", url: `/api/shopping/suggestions?list=${L}`, body: { name: "Hummus" } }));
check("sends: switch list", req({ type: "active", listId: "L2" }) === JSON.stringify({ method: "PUT", url: "/api/shopping/active", body: { list_id: "L2" } }));
check("sends: share", req({ type: "share", listId: L, userId: "u1", level: "read" }) === JSON.stringify({ method: "PUT", url: `/api/sharing/shopping_list/${L}`, body: { user_id: "u1", level: "read" } }));
check("sends: undo", req({ type: "undo", id: "u-1" }) === JSON.stringify({ method: "POST", url: "/api/undo/u-1" }));

// --- versions --------------------------------------------------------------------------------------
const ver = alone.list.version;
check("versions: our own write's answer (newer) replaces the screen", C.accepts(alone, body("edit")) && body("edit").list.version > ver);
check("versions: an older answer arriving late is ignored", !C.accepts(body("edit"), alone));
check("versions: another list's screen (a switch) is taken", C.accepts(alone, body("member_shared")));
check("versions: changed with the version we hold → no reload", C.onChanged({ list_id: L, version: ver }, alone, 0) === "no");
check("versions: changed with a newer version → reload now", C.onChanged({ list_id: L, version: ver + 1 }, alone, 0) === "now");
check("versions: newer while our own writes are on their way → after they land", C.onChanged({ list_id: L, version: ver + 1 }, alone, 1) === "later");
check("versions: a changed for another list (the active list switched) → reload", C.onChanged({ list_id: "other", version: 1 }, alone, 0) === "now");
check("versions: every write's answer carries the screen and a newer version",
      ["add", "add_mixed", "check", "edit", "remove", "clear_bought", "forget"].every((k) => F[k].status === 200 && body(k).list && body(k).items && "undo" in body(k)));

// --- words -----------------------------------------------------------------------------------------
const mixed = C.addResultText(body("add_mixed").results);
check("add result: the plain case is short", C.addResultText(body("add").results).text === "Added 6 items" && C.addResultText(body("add").results).obvious
      && C.addResultText([{ name: "Milk", status: "added" }]).text === "Added Milk");
check("add result: already / back said plainly", mixed.text === "Added Avocado · Eggs is back on the list · חלב is already on the list" && !mixed.obvious, mixed.text);
check("add result: several of a kind", C.addResultText([{ name: "a", status: "already" }, { name: "b", status: "already" }]).text === "2 were already on the list");
check("undo label: the server's label without 'the shopping list' and quotes",
      C.undoLabel(body("check").undo.label) === "Bought Eggs" && C.undoLabel(body("remove").undo.label) === "Removed Avocado"
      && C.undoLabel(body("clear_bought").undo.label) === body("clear_bought").undo.label, C.undoLabel(body("remove").undo.label));
const err = (k) => C.errorText(F[k].status, F[k].body);
check("errors: duplicate said with the name", err("error_duplicate") === "bread is already on the list.", err("error_duplicate"));
check("errors: too many with the server's max", err("error_too_many") === `At most ${F.error_too_many.body.max} items at a time.`);
check("errors: not found", err("error_not_found") === "That's no longer on the list — someone may have changed it.");
check("errors: invalid → the server's message", err("error_invalid") === F.error_invalid.body.message);
check("errors: not connected", /isn't turned on/.test(err("error_not_connected")));
check("errors: offline", C.errorText(0, null) === "You're offline — nothing was changed.");
check("errors: server failure", /went wrong/.test(C.errorText(500, null)));
check("undo: ok", plain(C.undoText(F.undo_ok.status, F.undo_ok.body)).ok === true);
check("undo: refused (409) says why", C.undoText(F.undo_conflict.status, F.undo_conflict.body).text
      === "Couldn't undo: 'Avocado' was changed again since, so it was left as it is.", C.undoText(F.undo_conflict.status, F.undo_conflict.body).text);
check("undo: expired / used (404)", C.undoText(F.undo_gone.status, F.undo_gone.body).text === "This can no longer be undone.");

// --- the editor ------------------------------------------------------------------------------------
const yog = alone.items.find((i) => i.name === "יוגורט");
const draft = C.editorDraft(yog);
check("editor: starts from the item", draft.name === "יוגורט" && draft.quantity === "4" && draft.note === "בלי סוכר" && draft.category === "dairy");
check("editor: nothing changed → nothing sent", JSON.stringify(plain(C.editChanges(yog, draft))) === '{"changes":{}}');
check("editor: only what changed, trimmed", JSON.stringify(plain(C.editChanges(yog, { ...draft, note: "  שתיים  ", category: "pantry" })))
      === JSON.stringify({ changes: { note: "שתיים", category: "pantry" } }));
check("editor: clearing the quantity sends an empty string", JSON.stringify(plain(C.editChanges(yog, { ...draft, quantity: "" }))) === '{"changes":{"quantity":""}}');
check("editor: an empty name is refused before sending", C.editChanges(yog, { ...draft, name: "  " }).error === "The name can't be empty.");
check("stepper: + / − on a count; empty counts as 1; below 2 empties it",
      C.stepQuantity("", 1) === "2" && C.stepQuantity("2", 1) === "3" && C.stepQuantity("2", -1) === "" && C.stepQuantity("", -1) === "");
check("stepper: free text (1 kg) isn't stepped", C.stepQuantity("1 kg", 1) === "1 kg" && !C.isCount("1 kg") && C.isCount("") && C.isCount("12"));
const edHtml = C.renderEditor(draft, alone.categories, {});
check("editor HTML: name, quantity stepper, note, every aisle as a chip with the current one on, Remove",
      /data-ed="name" dir="auto"[^>]*value="יוגורט"/.test(edHtml) && /data-act="qty-down"/.test(edHtml) && /data-ed="quantity"[^>]*value="4"/.test(edHtml)
      && (edHtml.match(/data-act="aisle"/g) || []).length === alone.categories.length
      && /aria-checked="true" class="shp-aisle-chip on" data-act="aisle" data-key="dairy"/.test(edHtml) && /data-act="editor-remove"/.test(edHtml));
check("editor HTML: a 409 is said inline", C.renderEditor(draft, alone.categories, { error: err("error_duplicate") }).includes('role="alert">bread is already on the list.'));
check("editor HTML: free-text quantity disables the stepper", (C.renderEditor({ ...draft, quantity: "1 kg" }, alone.categories, {}).match(/shp-step"[^>]* disabled/g) || []).length === 2);

// --- the Share sheet -------------------------------------------------------------------------------
const share = C.shareModel(body("share_get"));
check("share: the people on the list with their access", JSON.stringify(plain(share.people.map((p) => [p.name, p.level]))) === '[["Tom Hadad","edit"],["Gal","read"]]');
check("share: the others to add, without the people already on it",
      share.others.length === body("share_get").users.length - 2 && !share.others.some((o) => ["Tom Hadad", "Gal"].includes(o.name)));
const shHtml = C.renderShare(share, {});
check("share HTML: Can edit / View only per person, Remove, Add for the others",
      /Tom Hadad[\s\S]*aria-checked="true" class="on"[^>]*data-level="edit"[^>]*>Can edit/.test(shHtml) && /data-level="none"/.test(shHtml)
      && (shHtml.match(/>Add</g) || []).length === share.others.length);
check("share: an unshared list says so", /Only you can see this list/.test(C.renderShare(C.shareModel({ shared_with: [], users: [{ id: "u", name: "Maya" }] }), {})));
check("share: nobody else on the server", /No one else uses Dana/.test(C.renderShare(C.shareModel({ shared_with: [], users: [] }), {})));

// --- names and typed text ------------------------------------------------------------------------
check("name key: case, Hebrew vowel marks, dashes and punctuation like the server",
      C.nameKey("חָלָב") === "חלב" && C.nameKey("Coca-Cola") === "coca cola" && C.nameKey("  Eggs!! ") === "eggs" && C.nameKey("1/2 kg") === "1 2 kg");
check("typed text: lines and commas, bullets and numbering dropped, trailing x2",
      JSON.stringify(plain(C.splitTyped("- milk\n2. bread, eggs x2\n\n"))) === JSON.stringify([{ name: "milk", quantity: "" }, { name: "bread", quantity: "" }, { name: "eggs", quantity: "2" }]));
check("initials: two words, one word, Hebrew", C.initials("Noa Peretz") === "NP" && C.initials("Gal") === "G" && C.initials("רובין לוי") === "רל");
check("suggestions HTML: options with the chosen one marked", /role="option" id="shp-sugg-0" class="shp-sugg active" aria-selected="true"[^>]*data-name="Hummus"/.test(C.renderSuggestions(body("suggestions").suggestions, 0)));

console.log(`\n${pass}/${pass + failures.length} passed`);
if (failures.length) process.exit(1);
