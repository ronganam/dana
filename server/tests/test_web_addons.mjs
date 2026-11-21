/**
 * Contract test for the web Addons page (static/addons.js).
 *
 * Loads the page's pure part (DanaAddonsCore: view models, HTML, request bodies, error text) without
 * a browser and feeds it the *actual payloads* the API answered, captured in
 * tests/fixtures/addons_web.json by `WRITE_WEB_FIXTURE=1 bin/test test_addons_api` (the first
 * admin, a member, an admin acting for the member, a WhatsApp account conflict, addons not connected, a
 * field-level 400, a 403, a connection test). Checks what would be rendered and what would be sent:
 * untouched credentials are never sent, read-only fields have no input, a secret never reaches the HTML.
 *
 * Run: bin/test test_web_addons [path-to-fixture]
 */

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const SERVER = path.join(here, "..");  // the server folder
const FIXTURE = process.argv[2] || path.join(SERVER, "tests", "fixtures", "addons_web.json");
const F = JSON.parse(fs.readFileSync(FIXTURE, "utf8"));
const src = fs.readFileSync(path.join(SERVER, "static/addons.js"), "utf8");

const sandbox = { console, URL, Set, Map, JSON, Object, Array, String, Number, Boolean, RegExp, Math };
vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: "static/addons.js" });
const C = sandbox.DanaAddonsCore;

let pass = 0;
const failures = [];
function check(name, cond, detail = "") {
    if (cond) { pass++; console.log(`[PASS] ${name}`); }
    else { failures.push(name); console.log(`[FAIL] ${name}${detail ? " — " + String(detail).slice(0, 400) : ""}`); }
}

const byName = (list) => Object.fromEntries(list.body.addons.map((i) => [i.name, i]));
const admin = byName(F.list_admin), member = byName(F.list_member);
const forMember = byName(F.list_admin_for_member), carl = byName(F.list_conflict);
const meAdmin = F.me_admin.body, meMember = F.me_member.body, users = F.users.body.users;
const inputOf = (html, name) => (html.match(new RegExp(`<input[^>]*data-name="${name}"[^>]*>`)) || [""])[0];
const plain = (o) => JSON.parse(JSON.stringify(o));  // values made in the sandbox, compared here

check("loaded the page's pure part without a DOM", C && typeof C.renderList === "function");
check("fixture: the core shapes are there", F.list_admin.status === 200 && F.list_member.status === 200 && meAdmin.access === "admin");

// --- the list --------------------------------------------------------------------------------------
const listHtml = C.renderList({ items: F.list_admin.body.addons, ui: {}, open: null });
check("list: services first, built in after",
      listHtml.indexOf("Services") >= 0 && listHtml.indexOf("Services") < listHtml.indexOf("Built in")
      && listHtml.indexOf('data-row="homeassistant"') < listHtml.indexOf('data-row="tasks"'));
check("list: services are the addons with an account or address",
      ["homeassistant", "whatsapp", "calendar", "cameras"].every((k) => !admin[k] || C.isService(admin[k]))
      && ["tasks", "knowledge", "about", "people", "phone"].every((k) => !C.isService(admin[k])));
check("list: a row has the name, the description and the state from the server",
      /data-row="homeassistant"[\s\S]*Home Assistant[\s\S]*itg-state connected/.test(listHtml));
check("list: a service row shows the address's host", C.rowMeta(admin.homeassistant) === new URL(admin.homeassistant.credentials[0].value).host,
      C.rowMeta(admin.homeassistant));
check("list: loading and failed states",
      C.renderList({ items: null }).includes("itg-skel")
      && /Couldn't load addons[\s\S]*boom[\s\S]*data-retry/.test(C.renderList({ items: null, error: "boom" })));
const notConnected = Object.values(carl).find((i) => !i.connected && C.isService(i));
check("list: an addon not connected says so", notConnected && C.rowState(notConnected).key === "off"
      && C.renderRow(notConnected, {}, false).includes("Not connected"));

// --- the conflict ----------------------------------------------------------------------------------
const wa = carl.whatsapp;
const waState = C.rowState(wa);
check("conflict: needs attention, with the number and what to do",
      wa.account_conflict && waState.key === "attention" && waState.notes[0].includes(C.accountText(wa.account_conflict))
      && waState.notes[0].includes("another user"), JSON.stringify(waState));
check("conflict: the row says so before opening", C.renderRow(wa, {}, false).includes("Number used by another user"));
check("account: a WhatsApp id reads as a number", C.accountText("972500000001@c.us") === "+972500000001");

// --- one addon: the admin's Home Assistant ---------------------------------------------------
const ha = C.renderDetail(admin.homeassistant, {});
const host = inputOf(ha, "host"), token = inputOf(ha, "token");
check("address: the admin sees and edits the current address", host.includes('type="url"')
      && host.includes(`value="${admin.homeassistant.credentials[0].value}"`), host);
check("secret: an empty password input, no value, with set beside it",
      token.includes('type="password"') && !/\svalue=/.test(token) && /itg-set on">set</.test(ha), token);
check("secret: 'leave empty to keep' for a stored one", token.includes("Leave empty to keep the saved one"));
check("rule: the address rule is shown", ha.includes(admin.homeassistant.credentials[0].rule));
check("buttons: Save, Test connection, Disconnect for a connected service",
      ha.includes('data-act="save"') && ha.includes('data-act="test"') && ha.includes('data-act="disconnect"'));
check("options: a datalist per setting for an addon with options",
      ha.includes('list="itg-dl-homeassistant-person"') && ha.includes('<datalist id="itg-dl-homeassistant-person">'));
const opts = F.options_member.body.settings;
const haBob = C.renderDetail(member.homeassistant, { options: opts, optionsState: "ok" });
check("options: the member's own candidates offered (value + label), free text still an input",
      opts.person.every((o) => haBob.includes(`<option value="${o.value}">${o.label}</option>`))
      && inputOf(haBob, "person").includes('type="text"'));

// --- what the caller may not change ----------------------------------------------------------------
const cams = C.renderDetail(member.cameras, {});
check("read-only addon: the member's cameras have no input and no Save / Disconnect / Connect",
      member.cameras.can_change === false && !cams.includes("<input") && !cams.includes('data-act="save"')
      && !cams.includes('data-act="disconnect"'), cams);
check("read-only addon: the reason is shown", cams.includes("Set by an admin"));
const waRo = F.member_readonly_field.body;
const waRoHtml = C.renderDetail(waRo, {});
const waRoUrl = waRo.credentials.find((c) => c.name === "url");
check("read-only field: the member's WAHA address (no origin allowed) has no input, with the reason",
      !inputOf(waRoHtml, "url") && /data-field="url"[\s\S]*Set by an admin/.test(waRoHtml), waRoHtml);
check("read-only field: the model says not editable", C.fieldModel(waRo, { ...waRoUrl, group: "credentials" }).editable === false);
const voice = C.renderDetail(member.whatsapp, {});
const voiceIn = inputOf(voice, "voice_telegram_chat_id");
check("telegram chat: a number input with the rule in one line for the member",
      voiceIn.includes('type="number"') && voice.includes("Only your own Telegram id"), voiceIn);
check("address: the member sees their own WAHA address; key and session stay secret",
      inputOf(voice, "url").includes(`value="${member.whatsapp.credentials.find((c) => c.name === "url").value}"`)
      && !/\svalue=/.test(inputOf(voice, "api_key")) && !/\svalue=/.test(inputOf(voice, "session")));
check("admin for the member: cameras and the Telegram chat are editable",
      C.renderDetail(forMember.cameras, {}).includes('data-name="url"')
      && inputOf(C.renderDetail(forMember.whatsapp, {}), "voice_telegram_chat_id").includes('type="number"'));

// --- what is sent ----------------------------------------------------------------------------------
check("send: nothing typed → nothing to save", C.buildChanges(admin.homeassistant, { values: {}, remove: [] }) === null);
check("send: an untouched credential is not sent",
      JSON.stringify(plain(C.buildChanges(admin.homeassistant, { values: { person: "person.x" } }))) === JSON.stringify({ settings: { person: "person.x" } }));
check("send: an empty secret input is not sent", C.buildChanges(admin.homeassistant, { values: { token: "  " } }) === null);
check("send: a typed secret is sent, nothing else",
      JSON.stringify(plain(C.buildChanges(admin.homeassistant, { values: { token: "new-token" } }))) === JSON.stringify({ credentials: { token: "new-token" } }));
check("send: the address unchanged (retyped) is not sent",
      C.buildChanges(admin.homeassistant, { values: { host: admin.homeassistant.credentials[0].value } }) === null);
check("send: a new address is sent",
      plain(C.buildChanges(admin.homeassistant, { values: { host: "http://other:8123" } })).credentials.host === "http://other:8123");
check("send: an emptied required address is sent as '' (the server names it)",
      plain(C.buildChanges(admin.homeassistant, { values: { host: "" } })).credentials.host === "");
check("send: a read-only field is never sent (member: cameras, the WAHA address without an allowed origin)",
      C.buildChanges(member.cameras, { values: { url: "http://x:1" } }) === null
      && C.buildChanges(waRo, { values: { url: "http://x:1" } }) === null);
check("send: removing an optional saved secret sends null",
      member.whatsapp.credentials.find((c) => c.name === "api_key").set
      && JSON.stringify(plain(C.buildChanges(member.whatsapp, { values: {}, remove: ["api_key"] }))) === JSON.stringify({ credentials: { api_key: null } }));
check("send: a required secret is never removed", C.buildChanges(admin.homeassistant, { values: {}, remove: ["token"] }) === null);
check("send: a Telegram chat goes as a number",
      plain(C.buildChanges(forMember.whatsapp, { values: { voice_telegram_chat_id: "-100123" } })).settings.voice_telegram_chat_id === -100123);
const weather = admin.homeassistant.settings.find((s) => s.name === "weather_entity");
check("send: a setting emptied goes back to its default (null)", weather.value
      && plain(C.buildChanges(admin.homeassistant, { values: { weather_entity: "" } })).settings.weather_entity === null);
const calendarItem = admin.calendar || member.calendar;
check("send: a list setting goes as a list",
      JSON.stringify(plain(C.buildChanges(calendarItem, { values: { selected_calendars: "a@x, b@y" } })).settings.selected_calendars) === JSON.stringify(["a@x", "b@y"]));
const offHa = carl.homeassistant;
check("send: connecting sends what was typed",
      !offHa.connected && JSON.stringify(plain(C.buildChanges(offHa, { values: { host: "http://h:1", token: "t", person: "" } })))
      === JSON.stringify({ credentials: { host: "http://h:1", token: "t" } }));
const offBuiltin = Object.values(carl).find((i) => !i.connected && !C.isService(i) && !(i.settings || []).length);
check("send: connecting a built-in sends {}", offBuiltin && JSON.stringify(plain(C.buildChanges(offBuiltin, {}))) === "{}");
check("send: a not-connected service shows Connect, no Test / Disconnect",
      C.renderDetail(offHa, {}).includes(">Connect<") && !C.renderDetail(offHa, {}).includes('data-act="test"'));
check("path: own addons without ?user=, another user's with it",
      C.path("homeassistant", "", meAdmin.id, meAdmin.id) === "/api/addons/homeassistant"
      && C.path("homeassistant", "test", meMember.id, meAdmin.id) === `/api/addons/homeassistant/test?user=${meMember.id}`
      && C.path("", "", meMember.id, meAdmin.id) === `/api/addons?user=${meMember.id}`);

// --- answers -----------------------------------------------------------------------------------------
const fe = C.errorText(F.error_field.status, F.error_field.body);
check("400: the message goes next to its field", fe.field === "host" && fe.message === F.error_field.body.message, JSON.stringify(fe));
const withErr = C.renderDetail(carl.homeassistant, { errors: { host: fe.message } });
check("400: rendered under the field, visible", new RegExp(`itg-ferr" data-ferr="host" role="alert">[^<]*${fe.message.slice(0, 20).replace(/[.*+?^${}()|[\]\\/]/g, "\\$&")}`).test(withErr));
check("403: an admin's change in plain words", C.errorText(F.error_forbidden.status, F.error_forbidden.body).message === "Only an admin can change this.");
check("503 credentials_key: explained in a sentence",
      C.errorText(503, { error: "credentials_key", message: "CREDENTIALS_KEY is not set" }).message === C.KEY_TEXT && C.KEY_TEXT.includes("nothing was saved"));
check("network failure: said plainly", C.errorText(0, null).message.startsWith("Couldn't reach the server"));
check("test ok: it works", C.testText(F.test_ok.body).ok === true);
check("test failed: the short reason", C.testText(F.test_failed.body).ok === false && C.testText(F.test_failed.body).text === F.test_failed.body.reason);
check("test: an addon without a check", C.testText({ checked: false, ok: null, reason: "none" }).ok === null);
check("disconnect: the answer shape the page reads", F.disconnect.status === 200 && F.disconnect.body.connected === false
      && typeof F.disconnect.body.disconnected === "boolean");

// --- disconnect wording ------------------------------------------------------------------------------
const tasksText = C.disconnectText(admin.tasks, "");
check("confirm: a built-in says what disappears and that nothing is deleted",
      tasksText.body.startsWith("Tasks will disappear from Dana and the app until it's connected again") && tasksText.body.includes("Nothing is deleted."), tasksText.body);
const moneyText = C.disconnectText({ name: "money", connected: true, credentials: [], settings: [] }, "");
check("disconnect text: money says its data is deleted (not \"Nothing is deleted\")",
      moneyText.title === "Disconnect Money?" && moneyText.body.includes("logins, accounts and transactions are deleted")
      && !moneyText.body.includes("Nothing is deleted."), moneyText.body);
const haText = C.disconnectText(admin.homeassistant, "Bob Builder");
check("confirm: a service for another user names them, what stops and that other data is kept",
      haText.title === "Disconnect Home Assistant for Bob Builder?" && haText.body.includes("Bob Builder's Home Assistant")
      && haText.body.includes("Everything else in Dana is kept."), haText.body);

// --- header ------------------------------------------------------------------------------------------
const head = C.renderHeader({ me: meAdmin, users, target: meAdmin.id });
check("header: who is signed in and their level", head.includes(meAdmin.name) && head.includes(">admin<"));
check("header: an admin gets the user picker", head.includes('id="itg-user"') && users.every((u) => head.includes(`value="${u.id}"`)));
const headOther = C.renderHeader({ me: meAdmin, users, target: meMember.id });
check("header: editing another user is obvious", /itg-banner[\s\S]*You're editing[\s\S]*Bob Builder/.test(headOther) && headOther.includes("data-mine"));
check("header: a member gets no picker", !C.renderHeader({ me: meMember, users: null, target: meMember.id }).includes("itg-user"));
check("header: names are escaped", C.renderHeader({ me: { ...meAdmin, name: "<img src=x>" }, users: null }).includes("&lt;img src=x&gt;"));

// --- no secret reaches the HTML ----------------------------------------------------------------------
const all = [F.list_admin, F.list_member, F.list_admin_for_member, F.list_conflict].flatMap((l) => l.body.addons);
const everyDetail = all.map((i) => C.renderDetail(i, {}) + C.renderRow(i, {}, true)).join("\n");
check("secret: no password input carries a value anywhere", !/type="password"[^>]*\svalue=/.test(everyDetail));
const poisoned = { ...admin.homeassistant, credentials: admin.homeassistant.credentials.map((c) => (c.name === "token" ? { ...c, value: "LEAKED-TOKEN" } : c)) };
check("secret: a secret value in a payload would still not be rendered", !C.renderDetail(poisoned, {}).includes("LEAKED-TOKEN")
      && !C.renderRow(poisoned, {}, true).includes("LEAKED-TOKEN"));
check("secret: an unknown credential without 'secret' is treated as secret",
      C.fieldModel({ can_change: true, credentials: [] }, { name: "x", group: "credentials", value: "V", set: true }).input === "secret");

console.log(`\n${pass}/${pass + failures.length} passed`);
if (failures.length) process.exit(1);
