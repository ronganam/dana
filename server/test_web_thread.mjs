/**
 * The web chat's memory line for the shared conversation (static/app-thread.js): where "Dana remembers
 * from here" goes given the messages, `context_from_seq` / `context_expires_at` and the time.
 *
 * Run: node test_web_thread.mjs
 */

import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const sb = { console, Math, Number, String, JSON };
vm.createContext(sb);
vm.runInContext(fs.readFileSync(path.join(here, "static/app-thread.js"), "utf8") +
    "\nthis.api = { threadSeqOf, applyThreadInfo, threadDivider, getInfo: () => threadInfo };", sb);
const { threadSeqOf, applyThreadInfo, threadDivider, getInfo } = sb.api;

let pass = 0;
const failures = [];
function check(name, cond, detail = "") {
    if (cond) { pass++; console.log(`[PASS] ${name}`); }
    else { failures.push(name); console.log(`[FAIL] ${name}${detail ? " — " + detail : ""}`); }
}
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

check("seq: user, assistant and tool ids", threadSeqOf("msg_user_12") === 12 && threadSeqOf("msg_assistant_7") === 7
    && threadSeqOf("msg_tool_30_call_ab_1") === 30);
check("seq: live ids have none", threadSeqOf("msg_lx3k_1") === null && threadSeqOf("") === null && threadSeqOf(undefined) === null);

const stored = [
    { id: "msg_user_1", role: "user" }, { id: "msg_assistant_2", role: "assistant" },
    { id: "msg_user_3", role: "user" }, { id: "msg_tool_4_c1", role: "tool" }, { id: "msg_assistant_5", role: "assistant" },
];
const info = (fromSeq, expiresAt = 2000, extra = {}) => ({ fromSeq, expiresAt, known: true, ...extra });

check("line before the first remembered message", eq(threadDivider(stored, info(3), 1000), { index: 2, fresh: false }));
check("no line when everything shown is remembered", threadDivider(stored, info(1), 1000) === null);
check("memory reaching above the loaded page: no line", threadDivider(stored.slice(2), info(1), 1000) === null);
check("nothing remembered: the line goes after the last message",
    eq(threadDivider(stored, info(null, null), 1000), { index: 5, fresh: true }));
check("quiet gap ran out: starts fresh", eq(threadDivider(stored, info(3, 1500), 1600), { index: 5, fresh: true }));
check("before the gap runs out: still remembered", eq(threadDivider(stored, info(3, 1500), 1499), { index: 2, fresh: false }));

const live = stored.concat([{ id: "msg_k1_1", role: "user" }, { id: "msg_k1_2", role: "assistant" }]);
check("memory starting at a message sent from this page", eq(threadDivider(live, info(9), 1000), { index: 5, fresh: false }));
check("remembered stored message with live ones after it", eq(threadDivider(live, info(3), 1000), { index: 2, fresh: false }));

check("an older server (no fields): no line", threadDivider(stored, { fromSeq: null, expiresAt: null, known: false }, 1000) === null);
check("no messages: no line", threadDivider([], info(null, null), 1000) === null);
check("an ordinary chat: no line", threadDivider(stored, null, 1000) === null);

applyThreadInfo({ thread: true, context_from_seq: 3, context_expires_at: 99, has_more: true, before: 40 }, { page: true });
check("a loaded page keeps the paging cursor", eq(getInfo(), { fromSeq: 3, expiresAt: 99, hasMore: true, before: 40, known: true }));
applyThreadInfo({ thread: true, context_from_seq: null, context_expires_at: null, has_more: false, before: null });
check("a run's state updates the memory, not the cursor",
    eq(getInfo(), { fromSeq: null, expiresAt: null, hasMore: true, before: 40, known: true }));
applyThreadInfo({ thread: true, has_more: false, before: null }, { page: true });
check("an older server's page: unknown memory", getInfo().known === false);
applyThreadInfo({ session_id: "web_x", messages: [] }, { page: true });
check("an ordinary chat clears it", getInfo() === null);

console.log(`\n${pass}/${pass + failures.length} checks passed`);
if (failures.length) process.exit(1);
