/**
 * The web Zones card's pure part (static/zones.js, DanaZonesCore): the zone form's checks and request body (name,
 * latitude, longitude, radius with its default and limits, Home), the radius and centre labels, the line under a
 * zone, a place from the browser's location.
 *
 * Run: bin/test test_web_zones
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

const sb = { console, Number, String, JSON, Math, Object, Array, Boolean };
sb.globalThis = sb;
vm.createContext(sb);
vm.runInContext(fs.readFileSync(path.join(SERVER, "static/zones.js"), "utf8"), sb);
const C = sb.DanaZonesCore;

check("core: loads without a page (no DOM)", C && typeof C.zoneBody === "function");
check("body: a name trimmed, numbers parsed (a decimal comma too), the default radius",
      eq(C.zoneBody({ name: "  Gym  ", latitude: "32,1", longitude: " 34.8 ", radius: "" }).body,
         { name: "Gym", latitude: 32.1, longitude: 34.8, radius_m: 100, is_home: false }));
check("body: Home and a rounded radius",
      eq(C.zoneBody({ name: "Home", latitude: 1, longitude: 2, radius: "149.6", isHome: true }).body,
         { name: "Home", latitude: 1, longitude: 2, radius_m: 150, is_home: true }));
check("body: each bad field named",
      C.zoneBody({ name: " ", latitude: 1, longitude: 1 }).error.field === "name"
      && C.zoneBody({ name: "x".repeat(61), latitude: 1, longitude: 1 }).error.field === "name"
      && C.zoneBody({ name: "A", latitude: "", longitude: 1 }).error.field === "latitude"
      && C.zoneBody({ name: "A", latitude: 91, longitude: 1 }).error.field === "latitude"
      && C.zoneBody({ name: "A", latitude: 1, longitude: "east" }).error.field === "longitude"
      && C.zoneBody({ name: "A", latitude: 1, longitude: 1, radius: 5 }).error.field === "radius_m"
      && C.zoneBody({ name: "A", latitude: 1, longitude: 1, radius: 60000 }).error.field === "radius_m");
check("radius label: metres, then kilometres", C.radiusLabel(150) === "150 m" && C.radiusLabel(1500) === "1.5 km"
      && C.radiusLabel(2000) === "2 km");
check("meta line: Home, the radius, the centre",
      C.metaLine({ is_home: true, radius_m: 100, latitude: 32.0853, longitude: 34.7818 }) === "Home · within 100 m · 32.08530, 34.78180"
      && C.metaLine({ is_home: false, radius_m: 2500, latitude: 1, longitude: -2 }) === "within 2.5 km · 1.00000, -2.00000");
check("the browser's location as a place, with this browser's timezone",
      eq(C.hereAsPlace({ coords: { latitude: 38.7, longitude: -9.1 } }, "Europe/Lisbon"),
         { name: "Your location", label: "Your location", latitude: 38.7, longitude: -9.1, timezone: "Europe/Lisbon",
           country_code: "", currency: "" }));

console.log(`\n${pass}/${pass + failures.length} passed`);
process.exit(failures.length ? 1 : 0);
