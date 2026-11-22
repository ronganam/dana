/**
 * The web Household screen's and demo banner's pure parts (static/household.js DanaHouseholdCore, static/demo.js DanaDemoCore): the line under a member, who may be removed or lose admin, the add and invite request bodies, what the sample-data banner says and offers.
 *
 * Run: bin/test test_web_household
 */

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const SERVER = path.join(here, "..");

let pass = 0;
const failures = [];
function check(name, cond, detail = "") {
    if (cond) { pass++; console.log(`[PASS] ${name}`); }
    else { failures.push(name); console.log(`[FAIL] ${name}${detail ? " — " + JSON.stringify(detail) : ""}`); }
}
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

const sb = { console, Number, String, JSON, Map, Set, Array, Math, Date, Object };
sb.globalThis = sb;
vm.createContext(sb);
for (const f of ["static/devices.js", "static/household.js", "static/demo.js"]) {
    vm.runInContext(fs.readFileSync(path.join(SERVER, f), "utf8"), sb);
}
const H = sb.DanaHouseholdCore;
const D = sb.DanaDemoCore;

check("household core: loads without a page (no DOM)", H && typeof H.memberLine === "function");
check("devices core: offers the QR drawing the invite reuses", typeof sb.DanaDevicesCore.qrSvg === "function"
      && sb.DanaDevicesCore.qrSvg("dana://pair?code=X") === "");

const now = Date.parse("2026-11-22T12:00:00Z");
check("member line: level, devices, last seen",
      H.memberLine({ level: "admin", devices: 2, last_seen_at: "2026-11-22T11:50:00Z" }, now) === "Admin · 2 devices · seen 10 min ago",
      H.memberLine({ level: "admin", devices: 2, last_seen_at: "2026-11-22T11:50:00Z" }, now));
check("member line: one device, never signed in",
      H.memberLine({ level: "member", devices: 1, last_seen_at: null }, now) === "Member · 1 device · not signed in yet");

const robin = { id: "r", level: "admin", you: true }, gal = { id: "g", level: "admin" }, maya = { id: "m", level: "member" };
check("remove: never yourself", !H.canRemove(robin, [robin, gal, maya]));
check("remove: another admin while one stays, a member", H.canRemove(gal, [robin, gal, maya]) && H.canRemove(maya, [robin, maya]));
check("remove: never the last admin", !H.canRemove({ id: "x", level: "admin" }, [{ id: "x", level: "admin" }, maya]));
check("level: the last admin stays admin", !H.canSetLevel(robin, "member", [robin, maya]) && H.canSetLevel(robin, "admin", [robin, maya]));
check("level: an admin may become member while another admin stays", H.canSetLevel(gal, "member", [robin, gal]));
check("add body: trimmed name, defaults, languages only when given",
      eq(H.newMemberBody("  Noa ", "", "", ""), { name: "Noa", pronoun: "they", level: "member" })
      && eq(H.newMemberBody("Noa", "she", " he, en ", "admin"), { name: "Noa", pronoun: "she", level: "admin", languages: "he, en" }));
check("invite: a phone and a browser pairing for the member",
      eq(H.inviteBodies("m1"), [{ kind: "phone", user_id: "m1" }, { kind: "browser", user_id: "m1" }]));

check("demo banner: none when not loaded or refused", D.banner(null) === null && D.banner({ loaded: false }) === null);
const loaded = { loaded: true, users: [{ id: "s", name: "Sam Carter" }], viewing: null };
check("demo banner: loaded → view it / remove", eq(D.banner(loaded).actions, ["view", "remove"]) && !D.banner(loaded).viewing);
const viewing = D.banner({ ...loaded, viewing: "s" });
check("demo banner: viewing → who, back / remove", viewing.viewing && viewing.text.includes("Sam Carter")
      && eq(viewing.actions, ["back", "remove"]));
check("demo banner: a viewing id that is not a demo user counts as not viewing",
      !D.banner({ ...loaded, viewing: "someone" }).viewing);
check("demo banner: every action has a label", ["view", "back", "remove"].every((a) => D.LABELS[a]));

console.log(`\n${pass}/${pass + failures.length} passed`);
process.exit(failures.length ? 1 : 0);
