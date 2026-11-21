/**
 * The bank-worker sidecar's job table and HTTP API (bank-worker/src/jobs.js, server.js) without a browser.
 *
 * A scripted scrape stands in for Chromium: states, the SMS-code hand-off (code, timeout, cancel), the
 * job timeout, busy profiles and the browser limit, error types and blanked credentials; then the API of
 * bank-worker/README.md over HTTP: the token file, long polls, codes, cancelling, deleting profiles.
 *
 * Run: bin/test test_bank_worker
 */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { createJobs, cleanError, normalizeErrorType, failureType } = require("../src/jobs.js");
const { createServer, parseJob } = require("../src/server.js");

let pass = 0;
const failures = [];
function check(name, cond, detail = "") {
  if (cond) pass += 1;
  else failures.push(`${name}${detail ? ` — ${detail}` : ""}`);
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function until(fn, ms = 2000) {
  const end = Date.now() + ms;
  while (Date.now() < end) { if (await fn()) return true; await sleep(5); }
  return false;
}

const CREDS = { userCode: "AB12345", password: "s3cret-pass" };
const ACCOUNTS = [{ accountNumber: "12-345-678901", balance: 10, txns: [{ chargedAmount: -5, description: "x" }] }];

// A scrape whose steps the test drives: login, optionally a code, then a result. `hang` waits for the
// abort while logging in (`hang: "scraping"`: after signing in); `failAt: "login"` returns the result
// before signing in; `closeMs` is how long its browser takes to close.
function scripted({ needCode = false, result = { success: true, accounts: ACCOUNTS }, hang = false, failAt = null,
  closeMs = 0 } = {}) {
  const seen = { codes: [], aborted: false };
  const hangHere = () => new Promise((_, reject) => ctx_.signal.addEventListener("abort", () => reject(new Error("closed"))));
  let ctx_ = null;
  const run = async (_spec, ctx) => {
    ctx_ = ctx;
    ctx.signal.addEventListener("abort", () => { seen.aborted = true; });
    try {
      ctx.setState("logging_in");
      if (needCode) seen.codes.push(await ctx.requestCode());
      if (hang === true) await hangHere();
      if (failAt === "login") return typeof result === "function" ? result() : result;
      ctx.setState("scraping");
      if (hang === "scraping") await hangHere();
      return typeof result === "function" ? result() : result;
    } finally {
      if (closeMs) await sleep(closeMs);
    }
  };
  return { run, seen };
}

// ---- pure helpers ----------------------------------------------------------------------------
check("error types pass through", normalizeErrorType("CHANGE_PASSWORD") === "CHANGE_PASSWORD");
check("library-only types become GENERIC", normalizeErrorType("GENERAL_ERROR") === "GENERIC" && normalizeErrorType(undefined) === "GENERIC");
check("a vague failure with the password out is LOGIN_UNKNOWN", failureType("logging_in", "GENERAL_ERROR") === "LOGIN_UNKNOWN"
  && failureType("needs_code", "TIMEOUT") === "LOGIN_UNKNOWN" && failureType("logging_in", "GENERIC") === "LOGIN_UNKNOWN");
check("before the password or after signing in it keeps its type", failureType("starting", "GENERIC") === "GENERIC"
  && failureType("scraping", "TIMEOUT") === "TIMEOUT" && failureType("logging_in", "INVALID_PASSWORD") === "INVALID_PASSWORD"
  && failureType("logging_in", "CODE_REJECTED") === "CODE_REJECTED");
check("credentials are blanked in errors", !cleanError("typing s3cret-pass into #password failed for AB12345", CREDS).includes("s3cret"),
  cleanError("typing s3cret-pass into #password failed for AB12345", CREDS));
check("errors are cut short", cleanError("x".repeat(1000), {}).length === 300);
{
  const text = cleanError("navigation failed at https://login.bankhapoalim.co.il/ng-portals/auth/he/?sid=abc123&token=xyz#frag then gave up", {});
  check("URLs lose their query and fragment", text.includes("https://login.bankhapoalim.co.il/ng-portals/auth/he/ then")
    && !text.includes("abc123") && !text.includes("xyz") && !text.includes("frag"), text);
}

const good = { company: "hapoalim", profile: "src_1", start_date: "2025-11-18", credentials: { ...CREDS, extra: "dropped" } };
const parsed = parseJob(good);
check("a good job parses", !parsed.error && parsed.company === "hapoalim" && parsed.startDate instanceof Date);
check("only the company's fields are kept", JSON.stringify(Object.keys(parsed.credentials)) === JSON.stringify(["userCode", "password"]));
check("unknown company refused", !!parseJob({ ...good, company: "nope" }).error);
check("cal needs a username", !!parseJob({ ...good, company: "visaCal" }).error);
check("cal parses", !parseJob({ ...good, company: "visaCal", credentials: { username: "u", password: "p" } }).error);
check("bad profile refused", !!parseJob({ ...good, profile: "../etc" }).error);
check("missing password refused", !!parseJob({ ...good, credentials: { userCode: "x" } }).error);
check("bad date refused", !!parseJob({ ...good, start_date: "18/11/2025" }).error);

// ---- the job table ---------------------------------------------------------------------------
{
  const s = scripted();
  const jobs = createJobs({ runScrape: s.run, keepMs: 50 });
  const { job } = jobs.start({ company: "hapoalim", profile: "p1", credentials: CREDS, startDate: new Date() });
  check("a job starts", job.state === "starting" || job.state === "logging_in");
  await until(() => job.final);
  check("a job finishes done", job.state === "done", job.state);
  check("done carries the accounts", job.view().accounts?.[0]?.accountNumber === "12-345-678901");
  await sleep(80);
  check("a finished job is forgotten after keepMs", jobs.get(job.id) === undefined);
}
{
  const s = scripted({ needCode: true });
  const jobs = createJobs({ runScrape: s.run });
  const { job } = jobs.start({ company: "hapoalim", profile: "p1", credentials: CREDS, startDate: new Date() });
  await until(() => job.state === "needs_code");
  check("the site asking for a code shows as needs_code", job.state === "needs_code");
  check("a code is refused when nothing waits for it", !createJobs({ runScrape: s.run }).get("x"));
  check("the code reaches the scrape", job.submitCode("123456") === true);
  check("a second code is refused", job.submitCode("654321") === false);
  await until(() => job.final);
  check("the job finishes after the code", job.state === "done" && s.seen.codes[0] === "123456");
}
{
  const s = scripted({ needCode: true, result: { success: false, errorType: "GENERIC", errorMessage: "CODE_TIMEOUT: no code arrived" } });
  const jobs = createJobs({ runScrape: s.run, codeTimeoutMs: 30 });
  const { job } = jobs.start({ company: "hapoalim", profile: "p1", credentials: CREDS, startDate: new Date() });
  await until(() => job.final);
  check("no code in time → CODE_TIMEOUT", job.state === "failed" && job.errorType === "CODE_TIMEOUT", `${job.state} ${job.errorType}`);
}
{
  const s = scripted({ needCode: true });
  const jobs = createJobs({ runScrape: s.run });
  const { job } = jobs.start({ company: "hapoalim", profile: "p1", credentials: CREDS, startDate: new Date() });
  await until(() => job.state === "needs_code");
  jobs.cancel(job.id);
  await sleep(10);
  check("cancel ends the job as cancelled", job.state === "cancelled");
  check("cancel aborts the scrape (closes the browser)", s.seen.aborted);
  check("a cancelled job takes no code", job.submitCode("1234") === false);
}
{
  const s = scripted({ hang: "scraping" });
  const jobs = createJobs({ runScrape: s.run, jobTimeoutMs: 40 });
  const { job } = jobs.start({ company: "hapoalim", profile: "p1", credentials: CREDS, startDate: new Date() });
  await until(() => job.final);
  check("a job past its time fails with TIMEOUT", job.errorType === "TIMEOUT" && s.seen.aborted);
}
{
  const s = scripted({ hang: true });
  const jobs = createJobs({ runScrape: s.run, jobTimeoutMs: 40 });
  const { job } = jobs.start({ company: "hapoalim", profile: "p1", credentials: CREDS, startDate: new Date() });
  await until(() => job.final);
  check("a job stuck mid-login past its time is LOGIN_UNKNOWN", job.errorType === "LOGIN_UNKNOWN" && s.seen.aborted, job.errorType);
}
{
  const s = scripted({ failAt: "login", result: { success: false, errorType: "GENERIC", errorMessage: "Login failed with UNKNOWN_ERROR error" } });
  const jobs = createJobs({ runScrape: s.run });
  const { job } = jobs.start({ company: "hapoalim", profile: "p1", credentials: CREDS, startDate: new Date() });
  await until(() => job.final);
  check("an unknown login result is LOGIN_UNKNOWN", job.errorType === "LOGIN_UNKNOWN", job.errorType);
}
{
  const jobs = createJobs({ runScrape: async () => { throw new Error("net::ERR_NAME_NOT_RESOLVED"); } });
  const { job } = jobs.start({ company: "hapoalim", profile: "p1", credentials: CREDS, startDate: new Date() });
  await until(() => job.final);
  check("a failure before the password went out stays GENERIC", job.errorType === "GENERIC", job.errorType);
}
{
  const s = scripted({ needCode: true, closeMs: 60 });
  const jobs = createJobs({ runScrape: s.run, maxRunning: 1 });
  const { job } = jobs.start({ company: "hapoalim", profile: "p1", credentials: CREDS, startDate: new Date() });
  await until(() => job.state === "needs_code");
  jobs.cancel(job.id);
  const again = jobs.start({ company: "hapoalim", profile: "p1", credentials: CREDS, startDate: new Date() });
  check("a cancelled job keeps its profile until its browser has closed", job.state === "cancelled" && again.error === "busy"
    && jobs.profileBusy("p1"), JSON.stringify(again.error));
  check("and its browser slot", jobs.start({ company: "visaCal", profile: "p2", credentials: CREDS, startDate: new Date() }).error === "too_many");
  await until(() => !jobs.profileBusy("p1"));
  const later = jobs.start({ company: "hapoalim", profile: "p1", credentials: CREDS, startDate: new Date() });
  check("then the profile is free", !later.error && later.job.id !== job.id);
  jobs.cancel(later.job.id);
}
{
  const s = scripted({ hang: true });
  const jobs = createJobs({ runScrape: s.run, maxRunning: 2 });
  const a = jobs.start({ company: "hapoalim", profile: "p1", credentials: CREDS, startDate: new Date() });
  const busy = jobs.start({ company: "hapoalim", profile: "p1", credentials: CREDS, startDate: new Date() });
  check("one job per profile", busy.error === "busy" && busy.job === a.job.id);
  jobs.start({ company: "visaCal", profile: "p2", credentials: CREDS, startDate: new Date() });
  const third = jobs.start({ company: "visaCal", profile: "p3", credentials: CREDS, startDate: new Date() });
  check("at most maxRunning browsers", third.error === "too_many");
  check("profileBusy", jobs.profileBusy("p1") && !jobs.profileBusy("p9"));
  for (const job of jobs.running()) jobs.cancel(job.id);
}
{
  const s = scripted({ result: { success: false, errorType: "INVALID_PASSWORD", errorMessage: "bad s3cret-pass" } });
  const jobs = createJobs({ runScrape: s.run });
  const { job } = jobs.start({ company: "hapoalim", profile: "p1", credentials: CREDS, startDate: new Date() });
  await until(() => job.final);
  check("a login error keeps its type", job.errorType === "INVALID_PASSWORD");
  check("its text never carries the password", !String(job.error).includes("s3cret"), job.error);
}
{
  const jobs = createJobs({ runScrape: async () => { throw new Error("boom AB12345"); } });
  const { job } = jobs.start({ company: "hapoalim", profile: "p1", credentials: CREDS, startDate: new Date() });
  await until(() => job.final);
  check("a thrown scrape is a GENERIC failure", job.errorType === "GENERIC" && !job.error.includes("AB12345"), job.error);
}

// ---- HTTP ------------------------------------------------------------------------------------
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "bank-worker-test-"));
const tokenFile = path.join(tmp, "token");
const profilesDir = path.join(tmp, "profiles");
fs.mkdirSync(path.join(profilesDir, "old_profile"), { recursive: true });
fs.writeFileSync(path.join(profilesDir, "old_profile", "Cookies"), "x");

const gate = { codeNeeded: true };
const s = scripted({ needCode: true });
const jobs = createJobs({ runScrape: async (spec, ctx) => (gate.codeNeeded ? s.run(spec, ctx) : { success: true, accounts: ACCOUNTS }) });
const server = createServer({ jobs, tokenFile, profilesDir, version: "test" });
await new Promise(r => server.listen(0, "127.0.0.1", r));
const base = `http://127.0.0.1:${server.address().port}`;
const call = async (method, url, body, token = "tok-123") => {
  const res = await fetch(base + url, {
    method,
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  return { status: res.status, body: text ? JSON.parse(text) : null, text };
};

try {
  const health = await call("GET", "/health", undefined, null);
  check("health needs no token", health.status === 200 && health.body.ok === true && health.body.version === "test");
  check("no token file → 401", (await call("POST", "/jobs", good)).status === 401);
  fs.writeFileSync(tokenFile, "tok-123\n", { mode: 0o600 });
  check("wrong token → 401", (await call("POST", "/jobs", good, "nope")).status === 401);
  check("no token → 401", (await call("GET", "/jobs/x", undefined, null)).status === 401);
  check("bad body → 400", (await call("POST", "/jobs", { ...good, company: "x" })).status === 400);

  const created = await call("POST", "/jobs", good);
  check("POST /jobs → 201 with an id", created.status === 201 && typeof created.body.id === "string", created.text);
  const id = created.body.id;
  check("a second job for the profile → 409 busy", (await call("POST", "/jobs", good)).status === 409);

  const t0 = Date.now();
  let view = await call("GET", `/jobs/${id}?wait=5&seen=starting`);
  if (view.body.state === "logging_in") view = await call("GET", `/jobs/${id}?wait=5&seen=logging_in`);
  check("the long poll answers on the state change", view.body.state === "needs_code" && Date.now() - t0 < 4000, view.text);
  check("no accounts before done", view.body.accounts === null);
  check("a bad code → 400", (await call("POST", `/jobs/${id}/code`, { code: "12" })).status === 400);
  check("a code → 202", (await call("POST", `/jobs/${id}/code`, { code: "481516" })).status === 202);
  check("a code when nothing waits → 409", (await call("POST", `/jobs/${id}/code`, { code: "481516" })).status === 409);
  let done = await call("GET", `/jobs/${id}?wait=5&seen=logging_in`);
  if (done.body.state !== "done") done = await call("GET", `/jobs/${id}?wait=5&seen=${done.body.state}`);
  check("the job ends done with its accounts", done.body.state === "done" && done.body.accounts?.length === 1, done.text);
  check("no credentials in any answer", !done.text.includes("s3cret") && !created.text.includes("s3cret"));
  const quick = await call("GET", `/jobs/${id}?wait=5&seen=starting`);
  check("a poll with an old seen answers at once", quick.status === 200 && quick.body.state === "done");

  const gone = await call("GET", "/jobs/nope");
  check("unknown job → 404", gone.status === 404);

  gate.codeNeeded = true;
  const second = (await call("POST", "/jobs", { ...good, profile: "p_cancel" })).body.id;
  await until(async () => (await call("GET", `/jobs/${second}`)).body.state === "needs_code");
  check("a busy profile cannot be deleted", (await call("DELETE", "/profiles/p_cancel")).status === 409);
  check("DELETE /jobs cancels", (await call("DELETE", `/jobs/${second}`)).status === 200 &&
    (await call("GET", `/jobs/${second}`)).body.state === "cancelled");

  check("DELETE /profiles removes the folder", (await call("DELETE", "/profiles/old_profile")).status === 200 &&
    !fs.existsSync(path.join(profilesDir, "old_profile")));
  check("a bad profile name → 400", (await call("DELETE", "/profiles/..%2Fetc")).status === 400);
  check("unknown path → 404", (await call("GET", "/nothing")).status === 404);
  const huge = await fetch(`${base}/jobs`, { method: "POST", headers: { Authorization: "Bearer tok-123" }, body: "x".repeat(70_000) }).catch(() => null);
  check("an oversized body is refused", !huge || huge.status === 413);
} finally {
  server.close();
  server.closeAllConnections();
  fs.rmSync(tmp, { recursive: true, force: true });
}

if (failures.length) {
  console.log(`FAIL ${failures.length} of ${pass + failures.length}`);
  for (const f of failures) console.log(`  ✗ ${f}`);
  process.exit(1);
}
console.log(`ok ${pass} checks`);
process.exit(0);
