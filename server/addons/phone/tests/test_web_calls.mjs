/**
 * The Calls page's pure part (addons/phone/web/calls-core.js, DanaCallsCore): the other side of a call,
 * a steady avatar colour, the search over caller / receiver / title / summary, durations and the
 * right-to-left test.
 *
 * Run: bin/test test_web_calls
 */

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const SERVER = path.join(here, "..", "..", "..");
const sandbox = { console, JSON, Object, Array, String, Number, Math, isNaN };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(SERVER, "addons/phone/web/calls-core.js"), "utf8"), sandbox);
const C = sandbox.DanaCallsCore;

let pass = 0;
const failures = [];
function check(name, cond, detail) {
  if (cond) { pass++; console.log(`[PASS] ${name}`); }
  else { failures.push(name); console.log(`[FAIL] ${name}${detail !== undefined ? " — " + JSON.stringify(detail) : ""}`); }
}
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

const calls = [
  { id: "c1", caller: "Me", receiver: "Noa Levi", title: "Dentist appointment", summary: "Moved to Thursday." },
  { id: "c2", caller: "Gal", receiver: "Me", title: "Weekend plans", summary: "Hiking on Saturday." },
  { id: "c3", caller: "", receiver: "", title: "", summary: null },
  { id: "c4", caller: "Me", receiver: null, title: "Voice memo", summary: "שיחה עם הבנק" },
];

check("contact: the receiver when I called, the caller when they did, Unknown for nobody",
  eq(calls.map(C.contactOf), ["Noa Levi", "Gal", "Unknown", "Unknown"]));
check("avatar colour: steady for a name, one of the palette", C.avatarColor("Gal") === C.avatarColor("Gal")
  && /^#[0-9a-f]{6}$/.test(C.avatarColor("Noa Levi")) && /^#[0-9a-f]{6}$/.test(C.avatarColor("")));
check("search: none gives every call, a copy", eq(C.filterCalls(calls, "").map((c) => c.id), ["c1", "c2", "c3", "c4"]) && C.filterCalls(calls, "") !== calls);
check("search: caller, receiver, title or summary, any case",
  eq(C.filterCalls(calls, "noa").map((c) => c.id), ["c1"]) && eq(C.filterCalls(calls, " GAL ").map((c) => c.id), ["c2"])
  && eq(C.filterCalls(calls, "saturday").map((c) => c.id), ["c2"]) && eq(C.filterCalls(calls, "memo").map((c) => c.id), ["c4"])
  && eq(C.filterCalls(calls, "zzz"), []) && eq(C.filterCalls(null, "x"), []));
check("duration: m:ss, 0:00 for none", C.duration(0) === "0:00" && C.duration(null) === "0:00" && C.duration(65) === "1:05" && C.duration(600.7) === "10:00");
check("right to left: Hebrew yes, English no", C.isRtl(calls[3].summary) === true && C.isRtl(calls[0].summary) === false && C.isRtl(null) === false);

console.log(`\n${failures.length ? `${failures.length} check(s) failed` : "All checks passed"} (${pass} passed)`);
process.exit(failures.length ? 1 : 0);
