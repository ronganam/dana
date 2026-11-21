'use strict';
// Psagot Trade (a TradeOne backend behind https://trade.psagot.co.il): a JSON client over HTTPS, no browser.
//
// Read-only by construction: `call` refuses any request not in ALLOWED — the sign-in, the view-only device
// key, and five reads. Orders, cancellations, alerts, settings: none of them can be sent from here.
//
// Signing in (README "Psagot"):
// - With a device key (Psagot's "quick view": a view-only login of a registered device): fetch a challenge,
//   sign it with the device's RSA key (PKCS#1 v1.5, SHA-256), trade the signature for a one-time token and
//   sign in with that token. No password, no SMS. A device Psagot no longer accepts is dropped
//   (`deviceDropped`) and the job signs in with the password instead, in the same run.
// - With the password: `Method: "2FA"`; Psagot answers 401 `MFATokenMissingException` after sending the SMS
//   code, and the same login with `Token: <code>` signs in. Then this worker registers itself as a quick-view
//   device ("Dana") and returns the key (`device`) for Dana to keep, encrypted, for the next syncs.
// The password and a code each go out at most once per job.

const crypto = require('node:crypto');

const BASE = process.env.PSAGOT_BASE_URL || 'https://trade1.psagot.co.il';
const ORIGIN = 'https://trade.psagot.co.il';
const USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';
const TIMEOUT_MS = 45_000;
const DEVICE_NAME = 'Dana';

const ALLOWED = new Set([
  'POST /V2/json2/login',
  'GET /V2/json2/user/verify',
  'POST /V2/json2/user/verify',
  'POST /V2/json2/user/services/quickView',
  'GET /V2/json/accounts',
  'GET /V2/json2/account/view/balances',
  'GET /v2/json2/account/transactions',
  'GET /v2/json2/account/yields/details',
  'GET /V2/json2/market/table/simple',
]);
// The market table's name fields (the balances view's own `hebName` comes back empty, in the app too).
const NAME_FIELDS = ['HebName', 'EngName', 'EngSymbol', 'HebSymbol'];

const BLOCKED = /חסום|נחסם|נחסמה|נעול|blocked|locked|suspended/i;

/** A refusal from Psagot (`type`: its `-ExceptionType`, or `HTTP` / `NETWORK`). Never carries a credential. */
class PsagotError extends Error {
  constructor(status, type, message) {
    super(message);
    this.status = status;
    this.type = type;
  }
}

function failure(errorType, message) {
  return Object.assign(new Error(message), { errorType });
}

function aborted(ctx) {
  if (ctx.signal && ctx.signal.aborted) throw new Error('cancelled');
}

/** A value that may be one object or a list (XML turned JSON) as a list. */
function list(value) {
  if (Array.isArray(value)) return value;
  return value === undefined || value === null ? [] : [value];
}

/** `ddMMyyyy` of a date in the worker's zone (TZ=Asia/Jerusalem in the image). */
function ddmmyyyy(date) {
  const two = n => String(n).padStart(2, '0');
  return `${two(date.getDate())}${two(date.getMonth() + 1)}${date.getFullYear()}`;
}

/** The client: a cookie jar (Psagot's firewall sets cookies), the session header once signed in. */
function createClient({ fetchImpl = fetch, base = BASE, signal = null } = {}) {
  const cookies = new Map();
  const csession = String(crypto.randomInt(1, 2 ** 31 - 1));
  const state = { session: null };

  function remember(res) {
    const setCookies = res.headers && typeof res.headers.getSetCookie === 'function' ? res.headers.getSetCookie() : [];
    for (const line of setCookies) {
      const pair = String(line).split(';')[0];
      const at = pair.indexOf('=');
      if (at > 0) cookies.set(pair.slice(0, at).trim(), pair.slice(at + 1).trim());
    }
  }

  async function call(method, path, { query = null, body } = {}) {
    if (!ALLOWED.has(`${method} ${path}`)) throw new Error(`refused: ${method} ${path} is not a call this worker makes`);
    const url = new URL(path, base);
    for (const [k, v] of Object.entries(query || {})) url.searchParams.set(k, String(v));
    const headers = { Accept: 'application/json', Origin: ORIGIN, Referer: `${ORIGIN}/`, 'User-Agent': USER_AGENT, csession };
    if (state.session) headers.session = state.session;
    if (cookies.size) headers.Cookie = [...cookies].map(([k, v]) => `${k}=${v}`).join('; ');
    if (body !== undefined) headers['Content-Type'] = 'application/json; charset=utf-8';
    const timeout = AbortSignal.timeout(TIMEOUT_MS);
    let res;
    try {
      res = await fetchImpl(url, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
        redirect: 'manual',
      });
    } catch (err) {
      if (signal && signal.aborted) throw new Error('cancelled');
      throw new PsagotError(0, 'NETWORK', `Psagot did not answer (${err && err.name === 'TimeoutError' ? 'timeout' : 'network error'})`);
    }
    remember(res);
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = null; }
    const exception = data && typeof data === 'object' ? data.Exception : null;
    if (exception) {
      throw new PsagotError(res.status, String(exception['-ExceptionType'] || 'Exception'),
        String(exception.Message || exception.Reason || '').replace(/\s+/g, ' ').slice(0, 200));
    }
    if (res.status >= 200 && res.status < 300) return data;
    throw new PsagotError(res.status, 'HTTP', `Psagot answered HTTP ${res.status}`);
  }

  return { call, state };
}

/** Takes the session key from a login answer (`{Login: {SessionKey, ...}}`). */
function useSession(client, data) {
  const key = data && data.Login && data.Login.SessionKey;
  if (typeof key !== 'string' || !key) return false;
  client.state.session = key;
  return true;
}

/** What a refused password login (`stage` "password") or code (`stage` "code") means for Dana. */
function loginFailure(err, stage) {
  if (!(err instanceof PsagotError)) return err;
  const text = `${err.type} ${err.message}`;
  if (err.type === 'PasswordExpiredException') return failure('CHANGE_PASSWORD', 'Psagot wants a new password');
  if (BLOCKED.test(text)) return failure('ACCOUNT_BLOCKED', `Psagot blocked the login${err.message ? `: ${err.message}` : ''}`);
  if (err.type === 'LoginException' || (stage === 'code' && err.type === 'MFATokenMissingException')) {
    return stage === 'code'
      ? failure('CODE_REJECTED', 'Psagot did not accept the sign-in code')
      : failure('INVALID_PASSWORD', `Psagot refused the user or password${err.message ? `: ${err.message}` : ''}`);
  }
  // The password went out and the answer is not one we know: Dana must not retry it on its own.
  return failure('LOGIN_UNKNOWN', `Psagot's sign-in ended with ${err.type}${err.message ? `: ${err.message}` : ''}`);
}

/** The password sign-in, with the SMS code when Psagot asks for one. */
async function passwordLogin(client, ctx, { username, password }) {
  ctx.setState('logging_in');
  const login = { '-Product': 'Android', User: username, Password: password, Method: '2FA' };
  let data;
  try {
    data = await client.call('POST', '/V2/json2/login', { body: { Login: login } });
  } catch (err) {
    if (!(err instanceof PsagotError) || err.type !== 'MFATokenMissingException') throw loginFailure(err, 'password');
    const code = await ctx.requestCode();
    aborted(ctx);
    try {
      data = await client.call('POST', '/V2/json2/login', { body: { Login: { ...login, Token: code } } });
    } catch (err2) {
      throw loginFailure(err2, 'code');
    }
  }
  if (!useSession(client, data)) throw failure('LOGIN_UNKNOWN', 'Psagot signed in without a session');
}

/** A usable device for these credentials: its key, udid and the same user. */
function deviceFor(device, credentials) {
  return device && typeof device === 'object' && typeof device.key === 'string' && typeof device.udid === 'string'
    && device.user === credentials.username ? device : null;
}

/**
 * The view-only sign-in with the device key. True when signed in; false when Psagot no longer accepts the
 * device (or its key cannot sign). A network failure is thrown: the device may be fine.
 * The state stays `starting`: no password goes out here.
 */
async function deviceLogin(client, ctx, device) {
  try {
    const challenge = await client.call('GET', '/V2/json2/user/verify', { query: { user: device.user, method: 'QV', udid: device.udid } });
    const text = challenge && challenge.Data && challenge.Data.Text;
    if (typeof text !== 'string' || !text) return false;
    let signature;
    try {
      signature = crypto.sign('sha256', Buffer.from(text, 'utf8'), device.key).toString('base64');
    } catch {
      return false;
    }
    aborted(ctx);
    const verified = await client.call('POST', '/V2/json2/user/verify',
      { query: { user: device.user }, body: { Method: 'QV', Data: { Text: signature, Udid: device.udid } } });
    const token = verified && verified.Token;
    if (typeof token !== 'string' || !token) return false;
    aborted(ctx);
    const data = await client.call('POST', '/V2/json2/login', { body: { Login: { '-Product': 'Android', User: device.user, Password: token } } });
    return useSession(client, data);
  } catch (err) {
    if (err instanceof PsagotError && err.type !== 'NETWORK') {
      ctx.log(`psagot: the device key was refused (${err.type})`);
      return false;
    }
    throw err;
  }
}

/** Psagot's key as PKCS#8 PEM (it may come as PEM of either kind, or bare base64); null when unreadable. */
function normalizeKey(key) {
  if (typeof key !== 'string' || !key.trim()) return null;
  const body = key.replace(/-----[^-]+-----/g, '').replace(/\s+/g, '');
  const wrap = label => `-----BEGIN ${label}-----\n${body.match(/.{1,64}/g).join('\n')}\n-----END ${label}-----\n`;
  for (const pem of [key, wrap('PRIVATE KEY'), wrap('RSA PRIVATE KEY')]) {
    try {
      return crypto.createPrivateKey(pem).export({ type: 'pkcs8', format: 'pem' });
    } catch { /* the next form */ }
  }
  return null;
}

/** Registers this worker as a quick-view device; returns `{user, udid, key}` or null (logged). */
async function registerDevice(client, ctx, username) {
  const udid = crypto.randomBytes(16).toString('hex');
  try {
    const data = await client.call('POST', '/V2/json2/user/services/quickView', { query: { udid }, body: { DeviceName: DEVICE_NAME } });
    const key = normalizeKey(data && data.Key);
    if (!key) {
      ctx.log(`psagot: no usable device key (registered: ${data && data.Registered})`);
      return null;
    }
    return { user: username, udid, key };
  } catch (err) {
    if (!(err instanceof PsagotError)) throw err;
    ctx.log(`psagot: registering the device failed (${err.type})`);
    return null;
  }
}

/** A security number without its padding (the activity pads to 8 digits, the holdings do not). */
function securityId(value) {
  const text = String(value ?? '').trim();
  return text ? text.replace(/^0+(?=\d)/, '') : '';
}

/** The balances view with real-time prices (`calcMode=RT`, as the app asks; without it today's values
 *  are yesterday's closing prices), else without. */
async function readBalances(client, ctx, account) {
  const query = { account, fields: 'hebName' };
  try {
    return await client.call('GET', '/V2/json2/account/view/balances', { query: { ...query, calcMode: 'RT' } });
  } catch (err) {
    if (!(err instanceof PsagotError) || err.type === 'NETWORK') throw err;
    ctx.log(`psagot: real-time balances refused (${err.type} ${err.status}); closing prices instead`);
    return client.call('GET', '/V2/json2/account/view/balances', { query });
  }
}

/** One market-table row's fields: `{Fields: [{Key, Value}]}` or flat (`-Key` → `Key`). */
function securityFields(item) {
  const out = {};
  for (const f of list(item.Fields)) if (f && typeof f === 'object' && f.Key) out[f.Key] = f.Value;
  for (const [k, v] of Object.entries(item)) {
    const key = k.replace(/^-/, '');
    if (k !== 'Fields' && !(key in out)) out[key] = v;
  }
  return out;
}

/** `{security number: {heb, eng, symbol}}` from `GET /V2/json2/market/table/simple` (`Table.Security`);
 *  rows without a number of their own follow the order asked for, when the counts agree. */
function parseNames(data, ids) {
  const items = list(data && data.Table && data.Table.Security).filter(x => x && typeof x === 'object');
  const text = v => (typeof v === 'string' ? v.trim() : '');
  const out = {};
  items.forEach((item, i) => {
    const f = securityFields(item);
    let id = [f.Key, f.EquityNum, f.EquityNumber, f.Id, f.SecurityId].map(securityId).find(Boolean);
    if (!id && items.length === ids.length) id = ids[i];
    if (!id) return;
    const name = { heb: text(f.HebName), eng: text(f.EngName), symbol: text(f.EngSymbol) || text(f.HebSymbol) || text(f.Symbol) };
    if (name.heb || name.eng || name.symbol) out[id] = name;
  });
  return out;
}

/** A read that may fail on its own without failing the sync (logged; `fallback` instead). */
async function optional(ctx, what, read, fallback) {
  try {
    return await read();
  } catch (err) {
    if (!(err instanceof PsagotError)) throw err;
    ctx.log(`psagot: ${what} not read (${err.type} ${err.status})`);
    return fallback;
  }
}

/**
 * Every account of the user: its balances view (holdings, cash, values), its activity from `startDate` and
 * its daily value. Raw Psagot objects: Dana reads them (addons/money/portfolio.py).
 */
async function readPortfolio(client, ctx, startDate) {
  const listed = await client.call('GET', '/V2/json/accounts');
  const accounts = list(listed && listed.UserAccounts && listed.UserAccounts.UserAccount);
  const from = ddmmyyyy(startDate);
  const to = ddmmyyyy(new Date());
  const out = [];
  for (const account of accounts) {
    aborted(ctx);
    const key = account && account['-key'];
    if (!key) continue;
    const balances = await readBalances(client, ctx, key);
    const view = (balances && balances.View && balances.View.Account) || {};
    const { AccountPosition: position, ...summary } = view;
    const holdings = list(position && position.Balance);
    const ids = [...new Set(holdings.map(h => securityId(h && h.EquityNumber)).filter(Boolean))];
    const names = ids.length ? await optional(ctx, 'security names', async () => parseNames(
      await client.call('GET', '/V2/json2/market/table/simple', { query: { securities: ids.join(','), fields: NAME_FIELDS.join(',') } }), ids), {}) : {};
    const activity = await optional(ctx, 'activity', async () => {
      const data = await client.call('GET', '/v2/json2/account/transactions', { query: { fromDate: from, toDate: to, account: key } });
      return list(data && data.Transactions && data.Transactions.Transaction);
    }, []);
    const history = await optional(ctx, 'daily values', async () => {
      const data = await client.call('GET', '/v2/json2/account/yields/details', {
        query: { account: key, fromDate: from, toDate: to, currencyCode: 'ILS', taxType: 'gross', valueType: 'account', aggregateBy: 'daily' },
      });
      return list(data && data.Graph);
    }, []);
    out.push({
      kind: 'investment',
      accountNumber: String(key),
      nickname: typeof account['-nickName'] === 'string' ? account['-nickName'] : '',
      balance: summary.OnlineValue ?? null,
      summary,
      holdings,
      names,
      activity,
      history,
      txns: [],
    });
  }
  return out;
}

/**
 * One Psagot job (the `runScrape` of scrape.js for `psagot`): sign in (device key, else password and SMS
 * code), register a device after a password sign-in, read the portfolio.
 * Returns `{success, accounts, device, deviceDropped}`; a refused login throws with `errorType`.
 */
async function runPsagot({ credentials, device = null, startDate }, ctx, { fetchImpl, base } = {}) {
  const log = ctx.log || (() => {});
  const c = { ...ctx, log };
  const client = createClient({ fetchImpl, base, signal: ctx.signal });
  const usable = deviceFor(device, credentials);
  let dropped = Boolean(device) && !usable;
  let signedIn = false;
  if (usable) {
    signedIn = await deviceLogin(client, c, usable);
    if (!signedIn) dropped = true;
  }
  let fresh = null;
  if (!signedIn) {
    await passwordLogin(client, c, credentials);
    aborted(ctx);
    fresh = await registerDevice(client, c, credentials.username);
  }
  ctx.setState('scraping');
  const accounts = await readPortfolio(client, c, startDate);
  return { success: true, accounts, device: fresh, deviceDropped: dropped && !fresh };
}

module.exports = { runPsagot, createClient, normalizeKey, PsagotError, ALLOWED, ddmmyyyy, parseNames, securityId };
