/**
 * The bank-worker's Psagot Trade client (bank-worker/src/psagot.js) against a fake TradeOne server.
 *
 * The password sign-in with and without the SMS code, the quick-view device (registered after a password
 * sign-in, then used with a signed challenge; a refused one dropped and replaced in the same job), wrong
 * password / wrong code / expired password / blocked, a network failure on the device (no password sent),
 * the session and csession headers and the firewall cookie, the read-only allow-list, and the portfolio
 * read in the shapes Psagot really answers (accounts; real-time balances, closing prices when refused;
 * holdings without names and the market table's names in its three forms; activity with padded security
 * numbers; daily values; one failing read does not fail the job). Values are made up. No network.
 *
 * Run: bin/test test_bank_worker_psagot
 */

import crypto from "node:crypto";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { runPsagot, createClient, normalizeKey, ALLOWED, ddmmyyyy, parseNames, securityId } = require("../src/psagot.js");
const { parseJob } = require("../src/server.js");

let pass = 0;
const failures = [];
function check(name, cond, detail = "") {
  if (cond) pass += 1;
  else failures.push(`${name}${detail ? ` — ${detail}` : ""}`);
}

const USER = "012345678";
const PASSWORD = "pa55-word";
const CODE = "482913";

// A fake TradeOne: the sign-in flows, device registration and the reads, recording every request.
function fakePsagot({ mfa = true, password = PASSWORD, code = CODE, expired = false, blocked = false,
  keyForm = "pkcs1", failActivity = false, deviceRefused = false, networkOnChallenge = false,
  refuseRealTime = false, namesForm = "fields" } = {}) {
  const { privateKey, publicKey } = crypto.generateKeyPairSync("rsa", { modulusLength: 1024 });
  const devices = new Map(); // udid → public key
  const challenges = new Map(); // udid → text
  const tokens = new Set();
  const sessions = new Set();
  const requests = [];
  const json = (status, body, extra = {}) => new Response(JSON.stringify(body), {
    status, headers: { "Content-Type": "application/json", ...extra },
  });
  const exception = (status, type, message = "") => json(status, { Exception: { "-ExceptionType": type, Message: message } });
  const fetchImpl = async (url, init) => {
    const u = new URL(url);
    const body = init.body ? JSON.parse(init.body) : null;
    const headers = init.headers || {};
    requests.push({ method: init.method, path: u.pathname, query: Object.fromEntries(u.searchParams), body, headers });
    const route = `${init.method} ${u.pathname}`;
    if (route === "POST /V2/json2/login") {
      const login = body.Login;
      if (tokens.has(login.Password)) {
        tokens.delete(login.Password);
        const key = `qv-session-${sessions.size}`;
        sessions.add(key);
        return json(200, { Login: { SessionKey: key } });
      }
      if (login.User !== USER || login.Password !== password) return exception(401, "LoginException", "שם משתמש או סיסמה שגויים");
      if (blocked) return exception(401, "UserBlockedException", "המשתמש חסום");
      if (expired) return exception(401, "PasswordExpiredException", "expired");
      if (mfa && login.Method === "2FA" && !login.Token) return exception(401, "MFATokenMissingException", "sent");
      if (mfa && login.Token !== code) return exception(401, "LoginException", "bad token");
      const key = `session-${sessions.size}`;
      sessions.add(key);
      return json(200, { Login: { SessionKey: key, Capabilities: { Capability: [] } } }, { "Set-Cookie": "TS01=abc; Path=/" });
    }
    if (route === "GET /V2/json2/user/verify") {
      if (networkOnChallenge) throw new TypeError("fetch failed");
      if (u.searchParams.get("method") !== "QV" || !devices.has(u.searchParams.get("udid")) || deviceRefused) {
        return exception(401, "QuickViewException", "device not registered");
      }
      const text = crypto.randomBytes(12).toString("hex");
      challenges.set(u.searchParams.get("udid"), text);
      return json(200, { Method: "QV", Data: { Text: text } });
    }
    if (route === "POST /V2/json2/user/verify") {
      const udid = body.Data.Udid;
      const text = challenges.get(udid);
      const ok = text && crypto.verify("sha256", Buffer.from(text), devices.get(udid), Buffer.from(body.Data.Text, "base64"));
      if (!ok) return exception(401, "QuickViewException", "bad signature");
      const token = crypto.randomBytes(8).toString("hex");
      tokens.add(token);
      return json(200, { Token: token });
    }
    // Everything below needs a session.
    if (!sessions.has(headers.session)) return exception(500, "ArgumentNullException", "Value cannot be null.");
    if (route === "POST /V2/json2/user/services/quickView") {
      devices.set(u.searchParams.get("udid"), publicKey);
      const pem = privateKey.export({ type: keyForm, format: "pem" });
      const key = keyForm === "bare" ? "" : pem;
      return json(200, { Registered: true, Key: keyForm === "bare"
        ? privateKey.export({ type: "pkcs8", format: "der" }).toString("base64") : key });
    }
    if (route === "GET /V2/json/accounts") {
      return json(200, { UserAccounts: { UserAccount: { "-key": "150-123456", "-name": "Someone", "-nickName": "Main" } } });
    }
    if (route === "GET /V2/json2/account/view/balances") {
      // As Psagot answers: no names in the positions (Key null), the meta's hebName empty.
      if (refuseRealTime && u.searchParams.get("calcMode")) return exception(400, "ArgumentException", "calcMode");
      const rt = u.searchParams.get("calcMode") === "RT";
      return json(200, { View: { Account: {
        OnlineValue: rt ? 1240.5 : 1234.5, MorningValue: 1234.5, OnlineCash: 100, CurrencyCode: "ILS",
        AccountPosition: { Balance: [{ EquityNumber: "1100007", Key: null, OnlineNV: 10, OnlineVL: 1134.5, OnlineNisVL: 1134.5, LastRate: 11345, Source: "TA" }] },
        OnlineCashByCurrency: [{ Code: "ILS", Value: 100 }],
      }, Meta: { Security: [{ "-Key": "1100007", hebName: null }] } } });
    }
    if (route === "GET /V2/json2/market/table/simple") {
      const ids = (u.searchParams.get("securities") || "").split(",");
      if (namesForm === "fail") return exception(403, "NotAllowedException", "market data");
      const rows = ids.map(id => namesForm === "flat" ? { "-Key": id, HebName: "קרן לדוגמה", EngName: "Example Fund" }
        : namesForm === "ordered" ? { Fields: [{ Key: "HebName", Value: "קרן לדוגמה" }] }
        : { Key: id, Fields: [{ Key: "HebName", Value: "קרן לדוגמה" }, { Key: "EngSymbol", Value: "EXF" }] });
      return json(200, { Table: { Security: rows.length === 1 ? rows[0] : rows } });
    }
    if (route === "GET /v2/json2/account/transactions") {
      if (failActivity) return exception(500, "SomethingException", "nope");
      return json(200, { Transactions: { Transaction: [
        { ActionDate: "08/10/2026", Action: "קניה בבורסה", EquityNumber: "01100007", NV: 10, Price: 11000, VL: -1100, NisVL: -1100, Commission: -1.5, Name: "קרן לדוגמה" },
        { ActionDate: "08/10/2026", Action: "נע-קניה110000", EquityNumber: "99700001", NV: 1101.5, VL: -1101.5, NisVL: -1101.5, TaxVL: 1101.5, Name: "עו\"ש" },
      ] } });
    }
    if (route === "GET /v2/json2/account/yields/details") {
      return json(200, { Graph: [{ Date: "2026-11-17T00:00:00", Value: 1200 }, { Date: "2026-11-18T00:00:00", Value: 1234.5 }] });
    }
    return exception(404, "NotFound", route);
  };
  return { fetchImpl, requests, devices };
}

function context({ code = CODE } = {}) {
  const seen = { states: [], codes: 0, logs: [] };
  return {
    seen,
    ctx: {
      signal: new AbortController().signal,
      setState: s => seen.states.push(s),
      requestCode: async () => { seen.codes += 1; return code; },
      log: m => seen.logs.push(m),
    },
  };
}

const CREDS = { username: USER, password: PASSWORD };
const START = new Date(2025, 10, 18);
const sends = (requests, value) => requests.filter(r => JSON.stringify(r.body || {}).includes(value)).length;

// --- The password sign-in with the SMS code, then a device -----------------------------------------------
{
  const server = fakePsagot();
  const { ctx, seen } = context();
  const result = await runPsagot({ credentials: CREDS, startDate: START }, ctx, { fetchImpl: server.fetchImpl });
  check("password + code: success", result.success === true);
  check("password + code: one code asked", seen.codes === 1);
  check("password + code: the password went out twice (the login, then with the code), never more",
    sends(server.requests, PASSWORD) === 2, String(sends(server.requests, PASSWORD)));
  const logins = server.requests.filter(r => r.path === "/V2/json2/login");
  check("the first login asks for 2FA", logins[0].body.Login.Method === "2FA" && logins[0].body.Login["-Product"] === "Android"
    && !("Token" in logins[0].body.Login));
  check("the second carries the code", logins[1].body.Login.Token === CODE);
  check("states: logging_in then scraping", seen.states.join(",") === "logging_in,scraping", seen.states.join(","));
  check("a device is registered and returned", result.device && result.device.user === USER
    && /^[0-9a-f]{32}$/.test(result.device.udid) && result.device.key.includes("BEGIN PRIVATE KEY"));
  check("registered as Dana", server.requests.some(r => r.path === "/V2/json2/user/services/quickView" && r.body.DeviceName === "Dana"));
  check("no device dropped", result.deviceDropped === false);
  const reads = server.requests.filter(r => r.method === "GET" && r.path.toLowerCase().includes("/account"));
  check("reads carry the session", reads.length >= 3 && reads.every(r => r.headers.session === "session-0"));
  check("every request carries the same csession", new Set(server.requests.map(r => r.headers.csession)).size === 1
    && /^\d+$/.test(server.requests[0].headers.csession));
  check("the firewall cookie is sent back", reads.every(r => (r.headers.Cookie || "").includes("TS01=abc")));
  check("requests look like the web app", server.requests.every(r => r.headers.Origin === "https://trade.psagot.co.il"));
  const account = result.accounts[0];
  check("one account (a single object listed)", result.accounts.length === 1 && account.accountNumber === "150-123456"
    && account.kind === "investment" && account.nickname === "Main");
  check("the account holder's name is not passed on", !JSON.stringify(result.accounts).includes("Someone"));
  check("the balances view with real-time prices: value, summary, holdings", account.balance === 1240.5 && account.summary.OnlineCash === 100
    && !("AccountPosition" in account.summary) && account.holdings.length === 1 && account.holdings[0].EquityNumber === "1100007");
  check("security names from the market table (the holdings have none)", JSON.stringify(account.names) === JSON.stringify({ 1100007: { heb: "קרן לדוגמה", eng: "", symbol: "EXF" } }),
    JSON.stringify(account.names));
  const table = server.requests.find(r => r.path === "/V2/json2/market/table/simple");
  check("the market table is asked for the holdings' numbers and name fields only", table && table.query.securities === "1100007"
    && table.query.fields === "HebName,EngName,EngSymbol,HebSymbol" && table.method === "GET");
  check("activity (the worker passes every row; Dana drops the cash side) and daily values", account.activity.length === 2 && account.history.length === 2);
  const activity = server.requests.find(r => r.path === "/v2/json2/account/transactions");
  check("activity from the start date to today", activity.query.fromDate === "18112025" && activity.query.toDate === ddmmyyyy(new Date())
    && activity.query.account === "150-123456");
  const balances = server.requests.find(r => r.path === "/V2/json2/account/view/balances");
  check("balances asks for real-time prices (and the app's fields)", balances.query.fields === "hebName" && balances.query.calcMode === "RT");

  // --- The next sync: the device, no password, no code ------------------------------------------------
  const { ctx: ctx2, seen: seen2 } = context();
  const before = server.requests.length;
  const again = await runPsagot({ credentials: CREDS, device: result.device, startDate: START }, ctx2, { fetchImpl: server.fetchImpl });
  const later = server.requests.slice(before);
  check("device sync: success", again.success === true && again.accounts.length === 1);
  check("device sync: no password sent", sends(later, PASSWORD) === 0);
  check("device sync: no code asked", seen2.codes === 0);
  check("device sync: never logging_in (no password out)", !seen2.states.includes("logging_in"), seen2.states.join(","));
  check("device sync: no new device, none dropped", again.device === null && again.deviceDropped === false);
  const verify = later.find(r => r.method === "POST" && r.path === "/V2/json2/user/verify");
  check("the challenge is signed (QV, with the udid)", verify && verify.body.Method === "QV" && verify.body.Data.Udid === result.device.udid
    && verify.query.user === USER);
  check("device sync reads with the quick-view session", later.filter(r => r.path === "/V2/json/accounts")[0].headers.session === "qv-session-1");
}

// --- A refused device: dropped, the password and code once, a new device -----------------------------------
{
  const server = fakePsagot();
  const first = await runPsagot({ credentials: CREDS, startDate: START }, context().ctx, { fetchImpl: server.fetchImpl });
  const refusing = fakePsagot({ deviceRefused: true });
  const { ctx, seen } = context();
  const result = await runPsagot({ credentials: CREDS, device: first.device, startDate: START }, ctx, { fetchImpl: refusing.fetchImpl });
  check("refused device: still signs in with the password", result.success === true && seen.codes === 1);
  check("refused device: a new device replaces it", result.device && result.device.udid !== first.device.udid && result.deviceDropped === false);
  check("refused device: logged without secrets", seen.logs.some(l => l.includes("QuickViewException")) && !seen.logs.join(" ").includes(PASSWORD));
}
{
  const server = fakePsagot();
  const other = { ...((await runPsagot({ credentials: CREDS, startDate: START }, context().ctx, { fetchImpl: server.fetchImpl })).device), user: "999" };
  const { ctx } = context();
  const result = await runPsagot({ credentials: CREDS, device: other, startDate: START }, ctx, { fetchImpl: server.fetchImpl });
  check("another user's device is not used", result.success && !server.requests.slice(-12).some(r => r.query.user === "999"));
}
{
  const server = fakePsagot();
  const first = await runPsagot({ credentials: CREDS, startDate: START }, context().ctx, { fetchImpl: server.fetchImpl });
  const broken = fakePsagot({ networkOnChallenge: true });
  const { ctx, seen } = context();
  let error = null;
  try { await runPsagot({ credentials: CREDS, device: first.device, startDate: START }, ctx, { fetchImpl: broken.fetchImpl }); } catch (e) { error = e; }
  check("network failure on the device: the job fails", error !== null);
  check("…without sending the password or asking a code", sends(broken.requests, PASSWORD) === 0 && seen.codes === 0);
  check("…and before logging_in (an ordinary failure, retried later)", !seen.states.includes("logging_in") && !error.errorType);
}

// --- Refusals ---------------------------------------------------------------------------------------------
async function refused(options, creds = CREDS, code = CODE) {
  const server = fakePsagot(options);
  const { ctx, seen } = context({ code });
  try {
    await runPsagot({ credentials: creds, startDate: START }, ctx, { fetchImpl: server.fetchImpl });
    return { error: null, seen, server };
  } catch (error) {
    return { error, seen, server };
  }
}
{
  const r = await refused({}, { username: USER, password: "wrong" });
  check("wrong password → INVALID_PASSWORD", r.error && r.error.errorType === "INVALID_PASSWORD", r.error && r.error.errorType);
  check("wrong password: no code asked, one login", r.seen.codes === 0 && r.server.requests.length === 1);
}
{
  const r = await refused({}, CREDS, "111111");
  check("wrong code → CODE_REJECTED", r.error && r.error.errorType === "CODE_REJECTED", r.error && r.error.errorType);
  check("wrong code: asked once, sent once", r.seen.codes === 1 && r.server.requests.filter(x => x.body?.Login?.Token).length === 1);
}
{
  const r = await refused({ expired: true });
  check("expired password → CHANGE_PASSWORD", r.error && r.error.errorType === "CHANGE_PASSWORD");
}
{
  const r = await refused({ blocked: true });
  check("blocked → ACCOUNT_BLOCKED", r.error && r.error.errorType === "ACCOUNT_BLOCKED");
}
{
  const r = await refused({ mfa: false });
  check("no SMS asked: signs in without a code", r.error === null && r.seen.codes === 0);
}
{
  const r = await refused({ failActivity: true });
  check("a failing activity read does not fail the job", r.error === null);
  check("…it is logged", r.seen.logs.some(l => l.includes("activity not read")));
}
{
  const server = fakePsagot({ refuseRealTime: true });
  const { ctx, seen } = context();
  const result = await runPsagot({ credentials: CREDS, startDate: START }, ctx, { fetchImpl: server.fetchImpl });
  const asked = server.requests.filter(r => r.path === "/V2/json2/account/view/balances");
  check("real-time refused: closing prices instead, the job goes on", result.accounts[0].balance === 1234.5 && asked.length === 2
    && asked[0].query.calcMode === "RT" && !("calcMode" in asked[1].query));
  check("…it is logged", seen.logs.some(l => l.includes("real-time balances refused")));
}
{
  const r = await refused({ namesForm: "fail" });
  check("names refused: the job goes on without them", r.error === null && r.seen.logs.some(l => l.includes("security names not read")));
  const flat = await runPsagot({ credentials: CREDS, startDate: START }, context().ctx, { fetchImpl: fakePsagot({ namesForm: "flat" }).fetchImpl });
  check("names: flat rows (-Key)", flat.accounts[0].names["1100007"].heb === "קרן לדוגמה" && flat.accounts[0].names["1100007"].eng === "Example Fund");
  const ordered = await runPsagot({ credentials: CREDS, startDate: START }, context().ctx, { fetchImpl: fakePsagot({ namesForm: "ordered" }).fetchImpl });
  check("names: rows without a number follow the order asked for", ordered.accounts[0].names["1100007"].heb === "קרן לדוגמה");
  check("names: rows without a number and a different count are dropped",
    JSON.stringify(parseNames({ Table: { Security: [{ Fields: [{ Key: "HebName", Value: "א" }] }] } }, ["1", "2"])) === "{}"
    && JSON.stringify(parseNames(null, ["1"])) === "{}" && JSON.stringify(parseNames({ Table: { Security: [{ Key: "0005", HebName: "  " }] } }, ["5"])) === "{}");
  check("security numbers lose their padding", securityId("01100007") === "1100007" && securityId(1100007) === "1100007"
    && securityId("000") === "0" && securityId(null) === "" && securityId(" 99400001 ") === "99400001");
}

// --- Keys and the allow-list -------------------------------------------------------------------------------
{
  const { privateKey } = crypto.generateKeyPairSync("rsa", { modulusLength: 1024 });
  const pkcs1 = privateKey.export({ type: "pkcs1", format: "pem" });
  const bare = privateKey.export({ type: "pkcs8", format: "der" }).toString("base64");
  check("a PKCS#1 key becomes PKCS#8 PEM", normalizeKey(pkcs1).startsWith("-----BEGIN PRIVATE KEY-----"));
  check("a bare base64 key is read", (normalizeKey(bare) || "").startsWith("-----BEGIN PRIVATE KEY-----"));
  check("junk is no key", normalizeKey("not a key") === null && normalizeKey("") === null && normalizeKey(undefined) === null);
  const r = await refused({ keyForm: "bare" });
  check("a bare key from Psagot still gives a device", r.error === null);
}
{
  const client = createClient({ fetchImpl: async () => { throw new Error("must not be called"); } });
  for (const [method, path] of [["POST", "/v2/json2/order/"], ["POST", "/v2/json2/order/cancel/"], ["PUT", "/v2/json2/order/update/"],
    ["POST", "/V2/json2/password/change"], ["POST", "/V2/json2/market/alert"], ["DELETE", "/V2/json/accounts"]]) {
    let error = null;
    try { await client.call(method, path, { body: {} }); } catch (e) { error = e; }
    check(`refused before sending: ${method} ${path}`, error && /refused/.test(error.message), error && error.message);
  }
  check("no order path is allowed", [...ALLOWED].every(r => !/order|password|alert|watchlist|agreement/i.test(r)));
  check("only sign-in calls are POSTs", [...ALLOWED].filter(r => r.startsWith("POST")).every(r => /login|verify|quickView/.test(r)));
  check("the market table is the only market call, and a GET", [...ALLOWED].filter(r => /market/i.test(r)).join() === "GET /V2/json2/market/table/simple");
}

// --- The job body ------------------------------------------------------------------------------------------
{
  const good = { company: "psagot", profile: "src1", start_date: "2025-11-18", credentials: { username: "u", password: "p" } };
  check("psagot parses", !parseJob(good).error && parseJob(good).device === null);
  const device = { user: "u", udid: "ab", key: "k" };
  check("a device is passed on", JSON.stringify(parseJob({ ...good, device }).device) === JSON.stringify(device));
  check("a bad device is refused", !!parseJob({ ...good, device: { user: "u", udid: "", key: "k" } }).error);
  check("a huge device field is refused", !!parseJob({ ...good, device: { ...device, key: "x".repeat(9000) } }).error);
  check("a bank takes no device", !!parseJob({ ...good, company: "hapoalim", credentials: { userCode: "u", password: "p" }, device }).error);
}

if (failures.length) {
  console.log(`FAIL ${failures.length} / ${pass + failures.length}`);
  for (const f of failures) console.log(`  - ${f}`);
  process.exit(1);
}
console.log(`ok ${pass}`);
