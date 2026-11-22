/**
 * The web model card's pure part (static/llm.js, DanaLlmCore): the PUT /api/llm body (a key only when typed, an
 * address and model for a local model, a key with spaces refused) and the line about the key (never the key: set,
 * its last four, from the server's env, none yet).
 *
 * Run: bin/test test_web_llm
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

const sb = { console, String, JSON, Object, Array, Boolean };
sb.globalThis = sb;
vm.createContext(sb);
vm.runInContext(fs.readFileSync(path.join(SERVER, "static/llm.js"), "utf8"), sb);
const C = sb.DanaLlmCore;
const openai = { id: "openai", label: "OpenAI", key_env: "OPENAI_API_KEY", local: false };
const ollama = { id: "ollama", label: "Local model (Ollama)", key_env: "", local: true };

check("core: loads without a page (no DOM)", C && typeof C.requestBody === "function");
check("body: the provider and the typed key, trimmed", eq(C.requestBody(openai, { key: "  sk-abc  " }).body, { provider: "openai", key: "sk-abc" }));
check("body: no key typed keeps the stored one (no key sent)", eq(C.requestBody(openai, {}).body, { provider: "openai" }));
check("body: a key with spaces refused", C.requestBody(openai, { key: "sk abc" }).error.field === "key");
check("body: a local model sends its address and name",
      eq(C.requestBody(ollama, { baseUrl: " http://gpu:11434 ", model: " qwen3 " }).body, { provider: "ollama", base_url: "http://gpu:11434", model: "qwen3" }));
check("body: no provider", C.requestBody(null).error.field === "provider");
check("key line: set with its last four", C.keyLine({ provider: "openai", key: { set: true, from_env: false, last4: "WXYZ" } }, openai) === "A key is set, ending in WXYZ.");
check("key line: from the server's env", C.keyLine({ provider: "openai", key: { set: true, from_env: true, last4: "" } }, openai) === "Set on the server (OPENAI_API_KEY).");
check("key line: another provider's key does not count", C.keyLine({ provider: "claude", key: { set: true, last4: "QRST" } }, openai) === "No key yet.");
check("key line: none for a local model", C.keyLine({ provider: "ollama", key: { set: false } }, ollama) === "");

console.log(`\n${pass}/${pass + failures.length} passed`);
process.exit(failures.length ? 1 : 0);
