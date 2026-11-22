/**
 * Contract test for the web Addons page (static/addons.js): grouping, status, setup steps and the welcome.
 *
 * Loads the page's pure part (DanaAddonsCore: view models, HTML, request bodies, error text) without
 * a browser and feeds it the *actual payloads* the API answered, captured in
 * tests/fixtures/addons_web.json by `WRITE_WEB_FIXTURE=1 bin/test test_addons_api` (the first
 * admin, a member, an admin acting for the member, a WhatsApp account conflict, a new user with the welcome,
 * addons not connected, a field-level 400, a 403, a connection check, an addon that failed to load, one off
 * on the server, the catalog, a refused install). Checks the groups (On / Available / Not available /
 * Catalog for admins) and each status; the steps to turn an addon on (what to do outside Dana, the sign-in,
 * the fields with their help, link and example, Connect), what it adds and what to try; the welcome; what
 * would be sent (untouched credentials never are, read-only fields have no input, a secret never reaches the
 * HTML); the admin's server actions, the catalog and the install confirmation with the warning and restart.
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
const admin = byName(F.list_admin), member = byName(F.list_member), fresh = byName(F.list_new_user);
const forMember = byName(F.list_admin_for_member), carl = byName(F.list_conflict);
const meAdmin = F.me_admin.body, meMember = F.me_member.body, users = F.users.body.users;
const inputOf = (html, name) => (html.match(new RegExp(`<input[^>]*data-name="${name}"[^>]*>`)) || [""])[0];
const plain = (o) => JSON.parse(JSON.stringify(o));  // values made in the sandbox, compared here
const names = (items) => plain(items).map((i) => i.name);

check("loaded the page's pure part without a DOM", C && typeof C.renderList === "function");
check("fixture: the core shapes are there", F.list_admin.status === 200 && F.list_new_user.status === 200 && meAdmin.access === "admin");
check("fixture: nothing of the machine it was captured on", !JSON.stringify(F).includes("/home/") && F.catalog_admin.body.origin === "/scratch/catalog.json");

// --- groups ----------------------------------------------------------------------------------------
const g = C.groupItems(F.list_member.body.addons);
check("groups: On holds what the member has, Available what they can turn on, Not available the rest",
      names(g.on).join() === "essentials,homeassistant,whatsapp,calendar,cameras"
      && names(g.available).includes("tasks") && names(g.available).includes("gmail")
      && names(g.unavailable).join() === "broken", JSON.stringify([names(g.on), names(g.unavailable)]));
const gNew = C.groupItems(F.list_new_user.body.addons);
check("groups: a locked addon (the new member's cameras: an admin's choice) is Not available",
      names(gNew.unavailable).includes("cameras") && !names(gNew.available).includes("cameras"));
const listAdmin = C.renderList({ items: F.list_admin.body.addons, ui: {}, me: meAdmin, target: meAdmin.id, canManage: true, catalog: F.catalog_admin.body });
check("list: On, then Available, then Not available, then the Catalog for an admin",
      /data-group="on"[\s\S]*data-group="available"[\s\S]*data-group="unavailable"[\s\S]*data-group="catalog"/.test(listAdmin));
check("list: no Catalog for a member", !C.renderList({ items: F.list_member.body.addons, ui: {}, me: meMember, target: meMember.id, canManage: false }).includes('data-group="catalog"'));
check("list: a row has the manifest's icon, title and one-line description, and opens the addon",
      /data-open="homeassistant"[\s\S]*data-lucide="house"[\s\S]*Home Assistant[\s\S]*Lights, devices and the TV/.test(listAdmin));
check("list: an addon without an icon gets a plug", C.iconOf({ icon: "" }) === "plug" && C.iconOf({ icon: "bad icon\"" }) === "plug");
check("list: loading and failed states",
      C.renderList({ items: null }).includes("itg-skel")
      && /Couldn't load addons[\s\S]*boom[\s\S]*data-retry/.test(C.renderList({ items: null, error: "boom" })));

// --- status ----------------------------------------------------------------------------------------
const st = (item, ui) => plain(C.statusOf(item, ui));
check("status: on", st(admin.homeassistant).key === "on" && st(admin.homeassistant).label === "On");
check("status: essentials is always on", st(admin.essentials).label === "Always on");
check("status: a service not set up needs setup; an addon without fields is just off",
      st(fresh.homeassistant).key === "setup" && st(fresh.calendar).key === "setup" && st(fresh.money).key === "setup"
      && st(fresh.tasks).key === "off", JSON.stringify([st(fresh.tasks), st(fresh.money)]));
check("status: an admin's choice says so", st(fresh.cameras).key === "locked" && st(fresh.cameras).reason === "Set by an admin");
const conflict = st(carl.whatsapp);
check("status: the conflict is an error with the number and what to do",
      conflict.key === "error" && conflict.reason.includes(C.accountText(carl.whatsapp.account_conflict)) && conflict.reason.includes("another user"));
check("status: a failed check makes an error with the reason and what to fix",
      st(member.homeassistant, { test: { checked: true, ok: false, reason: "Unauthorized (401)" } }).reason
      === "Unauthorized (401). Check the details below, save them, and test again.");
check("status: failed to load, with the error for admins only",
      st(admin.broken).key === "failed" && st(admin.broken).reason.includes("ImportError") && !("error" in member.broken));
const offShopping = byName(F.list_member_disabled).shopping;
check("status: off on the server says an admin turned it off for everyone",
      st(offShopping).key === "server-off" && st(offShopping).reason.includes("for everyone"));
check("list: an error row shows its reason before opening", C.renderRow(carl.whatsapp, {}).includes("already connected by another user"));
check("account: a WhatsApp id reads as a number", C.accountText("972500000001@c.us") === "+972500000001");

// --- steps -----------------------------------------------------------------------------------------
const kinds = (item) => plain(C.stepsFor(item)).map((s) => s.kind).join(",");
check("steps: WhatsApp: what to do outside Dana, then the details, then Connect",
      kinds(fresh.whatsapp) === "outside,fields,connect" && plain(C.stepsFor(fresh.whatsapp))[0].lines.length === 3, kinds(fresh.whatsapp));
check("steps: a Google addon: the admin's one-time setup, then the sign-in (it connects; no typed token)",
      kinds(fresh.calendar) === "outside,signin" && plain(C.stepsFor(fresh.calendar))[1].url === "/api/google/connect"
      && !C.renderSteps(fresh.calendar, {}).includes('data-name="refresh_token"'));
check("steps: Telegram: its own sign-in button", plain(C.stepsFor(fresh.telegram_account)).at(-1).label === "Connect Telegram");
check("steps: Home Assistant asks only for what connecting needs (address, token), settings come after",
      plain(C.stepsFor(fresh.homeassistant))[0].fields.map((f) => f.name).join() === "host,token");
check("steps: an addon without fields is one Turn on", kinds(fresh.tasks) === "connect" && plain(C.stepsFor(fresh.tasks))[0].label === "Turn on");
check("steps: none once on, for a locked addon, or one off on the server",
      kinds(admin.homeassistant) === "" && kinds(fresh.cameras) === "" && kinds(offShopping) === "");
check("steps: an admin acting for someone types the Google credential (no sign-in for others)",
      !forMember.contacts.connect_url && kinds(forMember.contacts).endsWith("fields,connect"));
const haSteps = C.renderSteps(fresh.homeassistant, {});
check("steps: numbered, with Connect saying it checks the connection",
      (haSteps.match(/<li class="itg-step/g) || []).length === 2 && haSteps.includes("checks that it answers") && haSteps.includes('data-act="save"'));
const token = fresh.homeassistant.credentials.find((c) => c.name === "token");
check("field help: the manifest's label, where to find it and a link that opens elsewhere",
      haSteps.includes(">Access token<") && haSteps.includes(C.esc(token.help))
      && haSteps.includes(`href="${token.url}" target="_blank" rel="noopener noreferrer"`));
check("field help: the example inside the empty input", inputOf(haSteps, "host").includes('placeholder="e.g. https://home.example.com"'));
check("field help: only an https link is ever rendered", C.safeUrl("javascript:alert(1)") === null && C.safeUrl("http://x") === null
      && !C.renderField({ ...fresh.homeassistant, credentials: [{ ...token, url: "javascript:alert(1)" }] }, { ...token, url: "javascript:alert(1)", group: "credentials" }, {}).includes("javascript:"));
const waSteps = C.renderSteps(fresh.whatsapp, {});
check("steps: the setup sentences are shown in one first step, the optional key says optional",
      fresh.whatsapp.setup.every((l) => waSteps.includes(C.esc(l))) && (waSteps.match(/<li class="itg-step/g) || []).length === 3 && /WAHA API key <em>optional<\/em>/.test(waSteps));

// --- one addon -------------------------------------------------------------------------------------
const haDetail = C.renderDetail(admin.homeassistant, {}, true);
check("detail: what it adds (the manifest's features)", haDetail.includes("What it adds") && admin.homeassistant.features.every((f) => haDetail.includes(C.esc(f))));
check("detail: a page it adds is named", C.renderAdds(member.calendar).includes("<strong>Calendar</strong> page"));
const ilNeeds = C.renderNeeds({ ...fresh.money, name: "money_example", needs: [{ kind: "addon", what: "money", why: "Its accounts", optional: false }] },
                              F.list_new_user.body.addons);
check("needs: another addon it needs, by its title, required", /Addon<\/span>[\s\S]*<strong dir="auto">Money<\/strong>/.test(ilNeeds)
      && !/<strong dir="auto">Money<\/strong> <em>optional/.test(ilNeeds));
const gNeeds = plain(C.needLines(admin.gmail, F.list_admin.body.addons));
check("needs: required first; an env value as code; optional ones marked; every one with its why",
      gNeeds[0].kind === "env" && gNeeds[0].code === true && gNeeds[0].what === "GOOGLE_CLIENT_ID"
      && gNeeds.findIndex((n) => n.optional) === gNeeds.length - 1 && gNeeds.every((n) => n.why)
      && gNeeds.some((n) => n.kind === "account" && n.what === "A Google account"), JSON.stringify(gNeeds));
const haNeeds = C.renderNeeds(admin.homeassistant, F.list_admin.body.addons);
check("detail: Needs and May touch, from the payload",
      haDetail.includes("<h4>Needs</h4>") && haDetail.includes("<h4>May touch</h4>")
      && haNeeds.includes("Connects to your Home Assistant server") && haNeeds.includes("Acts for you outside Dana")
      && haNeeds.includes("Keeps passwords, tokens or sign-ins") && haNeeds.includes("<em>optional</em>"));
check("may touch: other addons' data by their titles; nothing declared says so; unknown (did not load) shows nothing",
      plain(C.touchLines(admin.tasks.touches, F.list_admin.body.addons)).join() === "Reads your data from People, Phone"
      && plain(C.touchLines(admin.shopping.touches)).join() === "Nothing beyond Dana's own data"
      && C.renderNeeds(admin.broken, []) === "" && plain(C.touchLines(null)).length === 0);
check("needs: an addon needing nothing says so", C.renderNeeds(admin.essentials, []).includes("Nothing beyond Dana itself"));
check("detail: once on, its settings with suggestions, Test again and Disconnect",
      haDetail.includes(">Settings<") && haDetail.includes('data-name="person"') && haDetail.includes('data-act="test"')
      && haDetail.includes('data-act="disconnect"') && haDetail.includes('list="itg-dl-homeassistant-person"'));
check("detail: Try asking Dana, with the manifest's examples", haDetail.includes("Try asking Dana")
      && admin.homeassistant.examples.every((e) => haDetail.includes(`data-ask="${C.esc(e)}"`)));
check("detail: no examples before it's on", !C.renderDetail(fresh.homeassistant, {}).includes("Try asking Dana"));
check("detail: a back link to the list", haDetail.includes("data-close"));
const opts = F.options_member.body.settings;
const haBob = C.renderDetail(member.homeassistant, { options: opts, optionsState: "ok" });
check("options: the member's own candidates offered (value + label), free text still an input",
      opts.person.every((o) => haBob.includes(`<option value="${o.value}">${o.label}</option>`)) && inputOf(haBob, "person").includes('type="text"'));
const host = inputOf(haDetail, "host"), tokenIn = inputOf(haDetail, "token");
check("address: the admin sees and edits the current address", host.includes('type="url"') && host.includes(`value="${admin.homeassistant.credentials[0].value}"`), host);
check("secret: an empty password input, no value, 'saved' beside it, 'leave empty to keep'",
      tokenIn.includes('type="password"') && !/\svalue=/.test(tokenIn) && /itg-set on">saved</.test(haDetail) && tokenIn.includes("Leave empty to keep the saved one"));
check("rule: the address rule is shown", haDetail.includes(admin.homeassistant.credentials[0].rule));
const calOn = C.renderDetail(member.calendar, {});
check("detail: a signed-in Google addon offers signing in again and hides its token", calOn.includes("Sign in with Google again") && !calOn.includes('data-name="refresh_token"'));

// --- what the caller may not change ----------------------------------------------------------------
const cams = C.renderDetail(member.cameras, {});
check("read-only addon: the member's cameras have no input and no Save / Disconnect",
      member.cameras.can_change === false && !cams.includes("<input") && !cams.includes('data-act="save"') && !cams.includes('data-act="disconnect"'), cams);
check("read-only addon: the reason is shown", cams.includes("Set by an admin"));
const waRo = F.member_readonly_field.body;
const waRoHtml = C.renderDetail(waRo, {});
check("read-only field: the member's WAHA address (no origin allowed) has no input, with the reason",
      !inputOf(waRoHtml, "url") && /data-field="url"[\s\S]*Set by an admin/.test(waRoHtml), waRoHtml);
const voice = C.renderDetail(member.whatsapp, {});
check("telegram chat: a numeric input with the rule for the member",
      inputOf(voice, "voice_telegram_chat_id").includes('inputmode="numeric"') && voice.includes("Only your own Telegram id"));
check("admin for the member: cameras and the Telegram chat are editable",
      C.renderDetail(forMember.cameras, {}).includes('data-name="url"') && inputOf(C.renderDetail(forMember.whatsapp, {}), "voice_telegram_chat_id"));

// --- what is sent ----------------------------------------------------------------------------------
check("send: nothing typed → nothing to save", C.buildChanges(admin.homeassistant, { values: {}, remove: [] }) === null);
check("send: an untouched credential is not sent",
      JSON.stringify(plain(C.buildChanges(admin.homeassistant, { values: { person: "person.x" } }))) === JSON.stringify({ settings: { person: "person.x" } }));
check("send: an empty secret input is not sent", C.buildChanges(admin.homeassistant, { values: { token: "  " } }) === null);
check("send: a typed secret is sent, nothing else",
      JSON.stringify(plain(C.buildChanges(admin.homeassistant, { values: { token: "new-token" } }))) === JSON.stringify({ credentials: { token: "new-token" } }));
check("send: the address unchanged (retyped) is not sent", C.buildChanges(admin.homeassistant, { values: { host: admin.homeassistant.credentials[0].value } }) === null);
check("send: an emptied required address is sent as '' (the server names it)", plain(C.buildChanges(admin.homeassistant, { values: { host: "" } })).credentials.host === "");
check("send: a read-only field is never sent", C.buildChanges(member.cameras, { values: { url: "http://x:1" } }) === null
      && C.buildChanges(waRo, { values: { url: "http://x:1" } }) === null);
check("send: removing an optional saved secret sends null",
      JSON.stringify(plain(C.buildChanges(member.whatsapp, { values: {}, remove: ["api_key"] }))) === JSON.stringify({ credentials: { api_key: null } }));
check("send: a required secret is never removed", C.buildChanges(admin.homeassistant, { values: {}, remove: ["token"] }) === null);
check("send: a Telegram chat goes as a number", plain(C.buildChanges(forMember.whatsapp, { values: { voice_telegram_chat_id: "-100123" } })).settings.voice_telegram_chat_id === -100123);
check("send: a setting emptied goes back to its default (null)", plain(C.buildChanges(admin.homeassistant, { values: { weather_entity: "" } })).settings.weather_entity === null);
check("send: a list setting goes as a list",
      JSON.stringify(plain(C.buildChanges(member.calendar, { values: { selected_calendars: "a@x, b@y" } })).settings.selected_calendars) === JSON.stringify(["a@x", "b@y"]));
check("send: connecting sends what was typed",
      JSON.stringify(plain(C.buildChanges(fresh.homeassistant, { values: { host: "http://h:1", token: "t", person: "" } }))) === JSON.stringify({ credentials: { host: "http://h:1", token: "t" } }));
check("send: turning on an addon without fields sends {}", JSON.stringify(plain(C.buildChanges(fresh.tasks, {}))) === "{}");
check("path: own addons without ?user=, another user's with it",
      C.path("homeassistant", "", meAdmin.id, meAdmin.id) === "/api/addons/homeassistant"
      && C.path("homeassistant", "test", meMember.id, meAdmin.id) === `/api/addons/homeassistant/test?user=${meMember.id}`);

// --- answers -----------------------------------------------------------------------------------------
const fe = C.errorText(F.error_field.status, F.error_field.body);
check("400: the message goes next to its field", fe.field === "host" && fe.message === F.error_field.body.message);
check("400: rendered under the field", C.renderDetail(carl.homeassistant, { errors: { host: fe.message } }).includes(`data-ferr="host" role="alert">${C.esc(fe.message)}`));
check("403: an admin's change in plain words", C.errorText(F.error_forbidden.status, F.error_forbidden.body).message === "Only an admin can change this.");
check("503 credentials_key: explained in a sentence", C.errorText(503, { error: "credentials_key" }).message === C.KEY_TEXT);
check("network failure: said plainly", C.errorText(0, null).message.startsWith("Couldn't reach the server"));
check("check ok", C.testText(F.test_ok.body, member.homeassistant).ok === true);
check("check failed: the reason and what to fix", C.testText(F.test_failed.body, carl.whatsapp).ok === false
      && C.testText(F.test_failed.body, carl.whatsapp).text.startsWith(F.test_failed.body.reason.replace(/\.$/, "")) && C.testText(F.test_failed.body, carl.whatsapp).text.includes("Check the details"));
check("connected: and it works", plain(C.connectedText(member.homeassistant, F.test_ok.body)).text === "Home Assistant is connected, and it works.");
check("connected: but the check failed, with what to fix", plain(C.connectedText(carl.whatsapp, F.test_failed.body)).ok === false
      && C.connectedText(carl.whatsapp, F.test_failed.body).text.includes("connected, but the check failed"));
check("connected: an addon without a check is just on", C.connectedText(fresh.tasks, null).text === "Tasks is on.");
check("checks in the payload: Home Assistant has one, Tasks none", admin.homeassistant.check === true && admin.tasks.check === false);

// --- turning off -------------------------------------------------------------------------------------
const tasksText = plain(C.disconnectText(admin.tasks, ""));
check("turn off: a built-in says it stops and nothing else is deleted",
      tasksText.title === "Turn off Tasks?" && tasksText.body.includes("Nothing else is deleted.") && tasksText.button === "Turn off");
const moneyText = C.disconnectText(admin.money, "");
check("turn off: money says its data is deleted (the manifest's warning)",
      moneyText.body.includes(admin.money.disconnect_warning) && !moneyText.body.includes("Nothing else is deleted."));
const haText = C.disconnectText(admin.homeassistant, "Bob Builder");
check("disconnect: a service for another user names them and that keys go",
      haText.title === "Disconnect Home Assistant for Bob Builder?" && haText.body.includes("keys and settings are removed"));

// --- the welcome -------------------------------------------------------------------------------------
const w = C.welcomeModel(F.list_new_user.body);
check("welcome: a new user is welcomed with the addons they can set up, in order",
      w.show === true && names(w.items).join() === F.list_new_user.body.welcome.suggested.join() && names(w.items)[0] === "homeassistant");
check("welcome: not for someone who already turned an optional addon on", C.welcomeModel(F.list_member.body).show === false);
const wHtml = C.renderWelcome(w, "modal");
check("welcome: a dialog with each suggestion opening its setup, and Not now",
      wHtml.includes('role="dialog"') && w.items.every((i) => wHtml.includes(`data-open="${i.name}"`)) && wHtml.includes("data-welcome-dismiss") && wHtml.includes("Not now"));
check("welcome: on the page too, above the groups",
      /itg-welcome[\s\S]*data-group="available"/.test(C.renderList({ items: F.list_new_user.body.addons, welcome: w, ui: {}, me: { id: "x" }, target: "x" })));
const dismissedList = C.renderList({ items: F.list_new_user.body.addons, ui: {}, me: { id: "x" }, target: "x",
                                     welcome: C.welcomeModel({ ...F.list_new_user.body, welcome: { show: false, dismissed: true, suggested: F.list_new_user.body.welcome.suggested } }) });
check("welcome: dismissed, the suggestions stay under Available and it can be brought back",
      !dismissedList.includes("Welcome to Dana") && dismissedList.includes("data-welcome-again")
      && /data-group="available"[\s\S]*data-open="homeassistant"/.test(dismissedList));

// --- header ------------------------------------------------------------------------------------------
const head = C.renderHeader({ me: meAdmin, users, target: meAdmin.id });
check("header: an admin gets the user picker", head.includes('id="itg-user"') && users.every((u) => head.includes(`value="${u.id}"`)));
const headOther = C.renderHeader({ me: meAdmin, users, target: meMember.id });
check("header: editing another user is obvious", /itg-banner[\s\S]*You're editing[\s\S]*Bob Builder/.test(headOther) && headOther.includes("data-mine"));
check("header: a member gets no picker", !C.renderHeader({ me: meMember, users: null, target: meMember.id }).includes("itg-user"));
check("header: names are escaped", C.renderHeader({ me: meAdmin, users: [{ id: "a", name: "<img src=x>", access: "admin" }, { id: "b", name: "b", access: "member" }], target: "a" }).includes("&lt;img src=x&gt;"));

// --- running the server's addons (admins) --------------------------------------------------------------
check("server actions: admins turn an official addon off for everyone (no remove), turn a disabled one on",
      JSON.stringify(plain(C.serverActions(admin.tasks, true)).map((a) => a.act)) === '["disable"]'
      && JSON.stringify(plain(C.serverActions(F.disable.body, true)).map((a) => a.act)) === '["enable"]'
      && C.renderDetail(F.disable.body, {}, true).includes('data-act="enable"'));
check("server actions: none for a member, none for essentials, remove for an installed one",
      C.serverActions(offShopping, false).length === 0 && C.serverActions(admin.essentials, true).length === 0
      && plain(C.serverActions({ ...admin.tasks, source: "installed" }, true)).map((a) => a.act).includes("remove"));
check("failed: no connect, no server off, nothing to remove (a site addon)", !/data-act="(save|disable|remove)"/.test(C.renderDetail(admin.broken, {}, true)));
check("source badge: none for official, the source for the others", C.sourceLabel(admin.tasks) === "" && C.sourceLabel(admin.broken) === "Site");

const cat = F.catalog_admin.body, weatherEntry = cat.entries[0];
check("catalog: members are refused", F.catalog_member.status === 403);
check("catalog: an entry not installed can be installed", C.catalogState(weatherEntry).action === "install");
check("catalog: installed at another commit → Update; at its commit → Installed; restart to load",
      C.catalogState({ ...weatherEntry, installed: { commit: "b".repeat(40), loaded: true }, update_available: true }).action === "update"
      && C.catalogState({ ...weatherEntry, installed: { commit: weatherEntry.source.ref, loaded: false } }).label.includes("restart")
      && C.catalogState({ ...weatherEntry, installed: { commit: weatherEntry.source.ref, loaded: true } }).action === null);
check("catalog: official names, other sources and too-new entries are not installable",
      C.catalogState({ ...weatherEntry, official: true }).action === null && C.catalogState({ ...weatherEntry, present: "site" }).action === null
      && C.catalogState({ ...weatherEntry, compatible: false }).label.includes(weatherEntry.min_dana));
const catHtml = C.renderCatalog({ catalog: cat, catalogUi: {} });
check("catalog: the full-access warning, the entry with Install, the skipped entries, the git form",
      catHtml.includes(C.esc(C.FULL_ACCESS)) && catHtml.includes('data-install="weather"') && catHtml.includes(C.esc(cat.problems[0])) && catHtml.includes('data-git-field="ref"'));
check("catalog: an entry's icon when given", "icon" in weatherEntry && C.renderCatalogEntry({ ...weatherEntry, icon: "cloud-sun" }, {}).includes('data-lucide="cloud-sun"')
      && C.renderCatalogEntry({ ...weatherEntry, icon: "" }, {}).includes('data-lucide="package"'));
check("catalog: an unreadable catalog says so with a retry", /Couldn't read the catalog[\s\S]*boom[\s\S]*data-catalog-retry/.test(C.renderCatalog({ catalogError: "boom" })));
check("catalog: a homepage that is not http(s) is not linked", !C.renderCatalogEntry({ ...weatherEntry, homepage: "javascript:alert(1)" }, {}).includes("javascript:"));
check("install form: a full commit is required", C.installBody({ repo: "https://x/r.git", ref: "main" }).error
      && JSON.stringify(plain(C.installBody({ repo: " https://x/r.git ", path: "/addons/w/", ref: "A".repeat(40) }))) === JSON.stringify({ repo: "https://x/r.git", path: "addons/w", ref: "a".repeat(40) }));
const conf = C.renderConfirm(C.installConfirm(weatherEntry.source, "weather", false, weatherEntry.touches));
check("install confirm: the repository, the folder, the commit, the full-access warning and the restart after",
      conf.includes(weatherEntry.source.repo) && conf.includes(weatherEntry.source.path) && conf.includes(weatherEntry.source.ref)
      && conf.includes(C.esc(C.FULL_ACCESS)) && conf.includes("restart") && conf.includes(">Install<"));
check("install confirm: what the entry says it may touch, before Install",
      conf.includes("It may touch") && conf.includes("Connects to api.weather.example") && conf.includes("Keeps passwords")
      && !conf.includes("Acts for you"));
check("install confirm: an entry that does not say what it touches says Dana checks the code",
      C.renderConfirm(C.installConfirm(weatherEntry.source, "weather", false, null)).includes(C.esc("reads it from the addon's code")));
check("catalog: an entry's touches on its row", C.renderCatalogEntry(weatherEntry, {}).includes("May touch: Connects to api.weather.example"));
const more = { hosts: [], acts_outside: true, stores_credentials: false, reads: [] };
const ask = C.renderConfirm(C.touchesConfirm("weather", { ...weatherEntry.touches, acts_outside: true }, more, true));
check("update that adds a touch: asks again with only what is new",
      ask.includes("may touch more") && ask.includes("New in this version") && ask.includes("Acts for you outside Dana")
      && !ask.includes("api.weather.example") && ask.includes(">Update<"));
const fresh409 = C.renderConfirm(C.touchesConfirm("weather", weatherEntry.touches, weatherEntry.touches, false));
check("install the code says touches more (409): asks with everything it touches", fresh409.includes("Its code says it may touch")
      && fresh409.includes("api.weather.example") && fresh409.includes(">Install<"));
check("install refused: the server's sentence", F.install_refused.status === 400 && C.errorText(F.install_refused.status, F.install_refused.body).message.includes("40-character"));
check("restart banner: only when needed; Restart now for admins only",
      C.renderRestartBanner({}) === "" && C.renderRestartBanner({ restartNeeded: true, canManage: true }).includes("data-restart")
      && C.renderRestartBanner({ restartNeeded: true, canManage: true }).includes("Restart to finish")
      && !C.renderRestartBanner({ restartNeeded: true, canManage: false }).includes("data-restart")
      && C.renderRestartBanner({ restarting: true }).includes("restarting"));

// --- no secret reaches the HTML ----------------------------------------------------------------------
const all = [F.list_admin, F.list_member, F.list_admin_for_member, F.list_conflict, F.list_new_user].flatMap((l) => l.body.addons);
const every = all.map((i) => C.renderDetail(i, {}, true) + C.renderRow(i, {})).join("\n");
check("secret: no password input carries a value anywhere", !/type="password"[^>]*\svalue=/.test(every));
const poisoned = { ...admin.homeassistant, credentials: admin.homeassistant.credentials.map((c) => (c.name === "token" ? { ...c, value: "LEAKED-TOKEN" } : c)) };
check("secret: a secret value in a payload would still not be rendered", !C.renderDetail(poisoned, {}).includes("LEAKED-TOKEN") && !C.renderRow(poisoned, {}).includes("LEAKED-TOKEN"));
check("secret: an unknown credential without 'secret' is treated as secret",
      C.fieldModel({ can_change: true, credentials: [] }, { name: "x", group: "credentials", value: "V", set: true }).input === "secret");

console.log(`\n${pass}/${pass + failures.length} passed`);
if (failures.length) process.exit(1);
