/**
 * The web Backups card's pure part (static/backups.js, DanaBackupsCore): sizes in words, the schedule in words
 * (off, daily, weekly), the last backup's line (none, ok with its name and size, failed with the reason) and the
 * PUT /api/backups body (a bad time, a relative folder or a keep out of range refused before sending; an empty
 * folder means the default).
 *
 * Run: bin/test test_web_backups
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

const sb = { console, String, JSON, Object, Array, Boolean, Number, Math };
sb.globalThis = sb;
vm.createContext(sb);
vm.runInContext(fs.readFileSync(path.join(SERVER, "static/backups.js"), "utf8"), sb);
const C = sb.DanaBackupsCore;

check("core: loads without a page (no DOM)", C && typeof C.requestBody === "function");
check("size: bytes", C.sizeText(512) === "512 bytes");
check("size: KB with one decimal under 10", C.sizeText(1536) === "1.5 KB", C.sizeText(1536));
check("size: MB rounded from 10", C.sizeText(25 * 1024 * 1024) === "25 MB", C.sizeText(25 * 1024 * 1024));
check("size: GB", C.sizeText(3.2 * 1024 ** 3) === "3.2 GB", C.sizeText(3.2 * 1024 ** 3));
check("schedule: off", C.scheduleText({ every: "off", at: "03:30", day: "sun" }) === "Off");
check("schedule: daily", C.scheduleText({ every: "daily", at: "03:30", day: "sun" }) === "Every day at 03:30");
check("schedule: weekly names the day", C.scheduleText({ every: "weekly", at: "22:15", day: "sat" }) === "Every Saturday at 22:15");
check("last: none yet", eq(C.lastLine({ last: null }), { tone: "none", text: "No backup yet." }));
check("last: ok with its name and size",
      eq(C.lastLine({ last: { ok: true, name: "dana-backup-20261123-033000.tar.gz", size: 2048 } }),
         { tone: "ok", text: "dana-backup-20261123-033000.tar.gz · 2.0 KB" }));
check("last: failed with the reason", eq(C.lastLine({ last: { ok: false, error: "/backups is a file, not a folder." } }),
                                        { tone: "bad", text: "Failed: /backups is a file, not a folder." }));
check("body: weekly with day, time, folder and keep",
      eq(C.requestBody({ every: "weekly", at: "04:05", day: "sat", destination: " /mnt/nas/dana ", keep: "5" }).body,
         { schedule: { every: "weekly", at: "04:05", day: "sat" }, destination: "/mnt/nas/dana", keep: 5 }));
check("body: an empty folder is the default (sent empty)", C.requestBody({ every: "off", keep: 7 }).body.destination === "");
check("body: no time typed → 03:30", C.requestBody({ every: "daily", at: "", keep: 7 }).body.schedule.at === "03:30");
check("body: a bad time refused", C.requestBody({ every: "daily", at: "25:00", keep: 7 }).error.field === "schedule");
check("body: a relative folder refused", C.requestBody({ every: "off", destination: "backups", keep: 7 }).error.field === "destination");
for (const keep of ["0", "101", "2.5", "x", ""]) {
    check(`body: keep ${JSON.stringify(keep)} refused`, C.requestBody({ every: "off", keep }).error?.field === "keep");
}
check("body: an unknown frequency refused", C.requestBody({ every: "hourly", keep: 7 }).error.field === "schedule");

console.log(`\n${pass}/${pass + failures.length} passed`);
process.exit(failures.length ? 1 : 0);
