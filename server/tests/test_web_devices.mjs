/**
 * The web Devices page's pure part (static/devices.js, DanaDevicesCore): kinds and their icons, the line under a device (kind, room / role, last seen, this device, not paired), times ago, the code shown in two groups, the line under a token of no device, the pairing countdown, the pairing request body (a role and room only for a tablet or a TV), the QR code's SVG.
 *
 * Run: bin/test test_web_devices
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
vm.runInContext(fs.readFileSync(path.join(SERVER, "static/devices.js"), "utf8"), sb);
const C = sb.DanaDevicesCore;

check("core: loads without a page (no DOM)", C && typeof C.metaLine === "function");
check("kinds: the server's six, each with an icon",
      eq(C.KINDS.map(k => k.id), ["phone", "watch", "tablet", "tv", "browser", "other"]) && C.KINDS.every(k => k.icon));
check("kinds: an unknown kind reads as Other", C.kindOf("toaster").id === "other");
check("roles: the household roles and Personal",
      eq(C.ROLES.map(r => r.id), ["", "kitchen", "bedroom", "hall", "living_room"]) && C.roleLabel("living_room") === "Living room");

const now = Date.parse("2026-11-22T12:00:00Z");
check("ago: just now / minutes / hours / days",
      C.ago("2026-11-22T11:59:30Z", now) === "just now" && C.ago("2026-11-22T11:55:00Z", now) === "5 min ago"
      && C.ago("2026-11-22T09:00:00Z", now) === "3 h ago" && C.ago("2026-11-19T12:00:00Z", now) === "3 days ago");
check("ago: no time → never", C.ago(null, now) === "never" && C.ago("", now) === "never");

const phone = { kind: "phone", room: "", role: "", paired: true, current: false, last_seen_at: "2026-11-22T11:50:00Z" };
check("meta: a phone seen a while ago", C.metaLine(phone, now) === "Phone · seen 10 min ago", C.metaLine(phone, now));
check("meta: the device making the request", C.metaLine({ ...phone, current: true }, now) === "Phone · this device");
check("meta: a household tablet with its room and role",
      C.metaLine({ ...phone, kind: "tablet", room: "Kitchen", role: "kitchen" }, now) === "Tablet · Kitchen · seen 10 min ago",
      C.metaLine({ ...phone, kind: "tablet", room: "Kitchen", role: "kitchen" }, now));
check("meta: a room and a different role both show",
      C.metaLine({ ...phone, kind: "tv", room: "Den", role: "living_room" }, now) === "TV · Den · Living room · seen 10 min ago");
check("meta: a device moved from push registrations, not paired",
      C.metaLine({ ...phone, paired: false }, now) === "Phone · not paired: pair it again");

const tok = { id: "t1", label: "old phone", created_at: "2026-11-19T12:00:00Z", last_used_at: "2026-11-22T09:00:00Z", current: false };
check("token meta: made and last used (a token of no device)",
      C.tokenMetaLine(tok, now) === "made 3 days ago · last used 3 h ago", C.tokenMetaLine(tok, now));
check("token meta: never used; used by this browser",
      C.tokenMetaLine({ ...tok, last_used_at: null }, now) === "made 3 days ago · last used never"
      && C.tokenMetaLine({ ...tok, current: true }, now) === "made 3 days ago · used here");
check("code: shown in two groups of four", C.showCode("ABCDEFGH") === "ABCD EFGH");
check("countdown: m:ss left, 0:00 once past",
      C.countdown("2026-11-22T12:04:59Z", now) === "4:59" && C.countdown("2026-11-22T11:00:00Z", now) === "0:00");
check("pairing body: a phone sends only its kind", eq(C.pairingBody("phone", "kitchen", "Kitchen"), { kind: "phone" }));
check("pairing body: a tablet sends its role and room",
      eq(C.pairingBody("tablet", "kitchen", " Kitchen "), { kind: "tablet", role: "kitchen", room: "Kitchen" }));
check("pairing body: a personal TV sends no role", eq(C.pairingBody("tv", "", ""), { kind: "tv" }));
check("QR: nothing without the QR library", C.qrSvg("dana://pair?code=X") === "");
sb.qrcode = () => ({ addData(t) { this.t = t; }, make() {}, createSvgTag() { return `<svg>${this.t}</svg>`; } });
check("QR: the library's SVG of the pairing string (the wizard's phone step shares it)",
      C.qrSvg("dana://pair?code=X") === "<svg>dana://pair?code=X</svg>");

console.log(`\n${pass}/${pass + failures.length} passed`);
process.exit(failures.length ? 1 : 0);
