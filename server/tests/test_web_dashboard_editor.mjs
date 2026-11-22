/**
 * The dashboard editor's pure part (static/dashboard-editor.js, DanaDashboardEditorCore): a placed widget as an
 * editable entry (every declared setting, defaults filled), a new widget from the catalog, reordering, removing,
 * size and setting edits (the original list untouched), whether the list is still the preset's own, the PUT
 * body (null widgets for the preset's own, ids kept, new ones without), what the Add menu offers first.
 * The presets and catalog come from the server's web fixture (tests/fixtures/dashboard_web.json).
 *
 * Run: bin/test test_web_dashboard_editor
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
vm.runInContext(fs.readFileSync(path.join(SERVER, "static/dashboard-editor.js"), "utf8"), sb);
const C = sb.DanaDashboardEditorCore;
const fixture = JSON.parse(fs.readFileSync(path.join(here, "fixtures/dashboard_web.json"), "utf8"));
const { presets, widgets: catalog, sizes } = fixture.catalog.body;
const plain = (x) => JSON.parse(JSON.stringify(x));

check("core: loads without a page (no DOM)", C && typeof C.saveBody === "function");
check("fixture: the catalog has the four presets and the sizes",
      eq(presets.map((p) => p.key), ["kitchen", "bedroom", "hall", "living_room"]) && eq(sizes, ["small", "medium", "large"]));

const kitchen = plain(C.presetList(presets, "kitchen", catalog));
check("preset list: the preset's widgets in order, no ids yet",
      eq(kitchen.map((w) => w.widget), ["clock", "now", "shopping", "calendar", "reminders", "ha_lights"])
      && kitchen.every((w) => w.id === ""));
check("preset list: every declared setting present, the catalog's default where the preset has none",
      eq(kitchen.find((w) => w.widget === "ha_lights").settings, { entities: "" }), kitchen);
check("preset list: an unknown preset is empty", eq(plain(C.presetList(presets, "garage", catalog)), []));

const layout = fixture.bedroom_tablet.body.widgets.map((w) => plain(C.entry(w, catalog)));
check("entry: a placed widget keeps its id, size and settings (as strings)",
      eq(layout[3], { id: "ha_climate-1", widget: "ha_climate", size: "medium", settings: { entity: "" } }), layout[3]);
check("entry: a widget whose addon is gone keeps its own settings",
      eq(plain(C.entry({ id: "cameras-1", widget: "cameras", size: "large", settings: { camera: "door" } }, [])).settings,
         { camera: "door" }));
check("is preset: the bedroom tablet's layout is the bedroom preset's own (its settings filled from the catalog too)",
      C.isPreset(layout, presets, "bedroom", catalog) && !C.isPreset(layout, presets, "kitchen", catalog));

const moved = plain(C.move(kitchen, 2, -1));
check("move: up one", eq(moved.slice(0, 3).map((w) => w.widget), ["clock", "shopping", "now"]));
check("move: down one", eq(plain(C.move(kitchen, 0, 1)).slice(0, 2).map((w) => w.widget), ["now", "clock"]));
check("move: not past either end", eq(plain(C.move(kitchen, 0, -1)), kitchen) && eq(plain(C.move(kitchen, 5, 1)), kitchen));
check("move: the original list untouched", kitchen[2].widget === "shopping");
check("move: no longer the preset's own", !C.isPreset(moved, presets, "kitchen", catalog));

const removed = plain(C.remove(kitchen, 5));
check("remove: by position", removed.length === 5 && !removed.some((w) => w.widget === "ha_lights"));
const sized = plain(C.withSize(kitchen, 0, "large"));
check("size: changes one widget only", sized[0].size === "large" && kitchen[0].size === "medium" && sized[1].size === "medium");
const set = plain(C.withSetting(kitchen, 5, "entities", "light.hall"));
check("setting: changes one setting of one widget", eq(set[5].settings, { entities: "light.hall" })
      && eq(kitchen[5].settings, { entities: "" }));
check("setting: edited away and back is the preset's own again",
      C.isPreset(plain(C.withSetting(set, 5, "entities", "")), presets, "kitchen", catalog));

const climate = catalog.find((c) => c.key === "ha_climate");
const added = plain(C.add(kitchen, climate));
check("add: at the end, its first size, settings at their defaults, no id",
      eq(added[added.length - 1], { id: "", widget: "ha_climate", size: climate.sizes[0], settings: { entity: "" } }),
      added[added.length - 1]);

check("save: the preset's own widgets go as null", eq(plain(C.saveBody("kitchen", kitchen, presets, catalog)),
                                                      { preset: "kitchen", widgets: null }));
const body = plain(C.saveBody("bedroom", [layout[3], ...C.add([], climate)], presets, catalog));
check("save: an edited list goes whole, ids kept where there are, none for new ones",
      eq(body, { preset: "bedroom", widgets: [
          { widget: "ha_climate", size: "medium", settings: { entity: "" }, id: "ha_climate-1" },
          { widget: "ha_climate", size: climate.sizes[0], settings: { entity: "" } }] }), body);

const offered = plain(C.addable(catalog, kitchen)).map((c) => c.key);
check("add menu: widgets not yet placed first, the placed ones after (a widget may be placed twice)",
      offered.length === catalog.length && offered.indexOf("money") < offered.indexOf("clock")
      && offered.slice(-6).sort().join() === ["calendar", "clock", "ha_lights", "now", "reminders", "shopping"].sort().join(),
      offered);
check("info: a key's catalog entry, null when its addon is gone",
      C.infoOf(catalog, "money")?.title === "Money" && C.infoOf(catalog, "cameras") === null);
check("labels: sizes", C.sizeLabel("small") === "Small" && C.sizeLabel("large") === "Large");

console.log(`\n${pass}/${pass + failures.length} passed`);
if (failures.length) process.exit(1);
