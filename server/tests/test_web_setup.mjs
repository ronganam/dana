/**
 * The setup page's pure part (static/setup.js, DanaSetupCore): which wizard steps show (available and pending; every
 * available one with ?again=1; none during setup mode) and the add-ons offered (suggested, off, available, and free to
 * turn on now: no required account, hardware or credential).
 *
 * Run: bin/test test_web_setup
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

const sb = { console, String, JSON, Object, Array, Boolean, Set };
sb.globalThis = sb;
vm.createContext(sb);
vm.runInContext(fs.readFileSync(path.join(SERVER, "static/setup.js"), "utf8"), sb);
const C = sb.DanaSetupCore;

const wizard = { steps: [{ id: "you", status: "done", available: true }, { id: "home", status: "pending", available: true },
                         { id: "model", status: "pending", available: false }, { id: "phone", status: "skipped", available: true }] };
check("core: loads without a page (no DOM)", C && typeof C.wizardNeeded === "function");
check("steps: a pending available one shows; done, skipped and unavailable ones do not",
      C.wizardNeeded({ wizard }, "home") && !C.wizardNeeded({ wizard }, "you") && !C.wizardNeeded({ wizard }, "phone")
      && !C.wizardNeeded({ wizard }, "model") && !C.wizardNeeded({ wizard }, "sample"));
check("steps: again shows every available one, still not the unavailable",
      C.wizardNeeded({ wizard, again: true }, "you") && C.wizardNeeded({ wizard, again: true }, "phone")
      && !C.wizardNeeded({ wizard, again: true }, "model"));
check("steps: none during setup mode or before the state is known",
      !C.wizardNeeded({ wizard, setup_required: true }, "home") && !C.wizardNeeded({}, "home"));

const addon = (name, extra = {}) => ({ name, title: name.toUpperCase(), description: "d", connected: false, state: "available",
                                       needs: [], credentials: [], settings: [], ...extra });
const payload = { welcome: { suggested: ["shopping", "whatsapp", "knowledge", "calendar", "people", "tasks"] }, addons: [
  addon("shopping"),
  addon("whatsapp", { needs: [{ kind: "hardware", optional: false }] }),
  addon("knowledge", { needs: [{ kind: "env", optional: true }, { kind: "addon", optional: false }] }),
  addon("calendar", { credentials: [{ name: "refresh_token", required: true, set: false }] }),
  addon("people", { connected: true }),
  addon("tasks", { state: "off" }),
  addon("money"),
] };
check("add-ons: suggested, off, available and free to turn on now (an optional need is fine)",
      eq(C.freeAddons(payload).map((a) => a.name), ["shopping", "knowledge"])
      && eq(C.freeAddons(payload)[0], { name: "shopping", title: "SHOPPING", description: "d" }));
check("add-ons: nothing without a payload", eq(C.freeAddons(null), []));

console.log(`\n${pass}/${pass + failures.length} passed`);
process.exit(failures.length ? 1 : 0);
