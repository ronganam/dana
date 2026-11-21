'use strict';
// Pluxee (Cibus) meal card: consumers.pluxee.co.il, an Angular app over Cibus's JSON API (README "Pluxee").
//
// Signing in needs the real browser: Google's invisible reCAPTCHA v3 scores both the SMS request
// (`api.capir.pluxee.co.il/auth/sendOTP`) and the code (`/auth/authToken`), and refuses a browser that
// looks automated (scrape.js launches this site without the automation flags). The site keeps the
// session in a `token` cookie for 24 hours, in the source's browser profile: a sync within that time
// signs in with no SMS.
//
// Read-only by construction: once signed in, the worker reads through `read`, which refuses any call
// type outside READS, from inside the page (its cookies and headers). Orders, transfers, card changes:
// none of them can be sent from here.

const SITE = process.env.PLUXEE_SITE_URL || 'https://consumers.pluxee.co.il';
const API = process.env.PLUXEE_API_URL || 'https://api.consumers.pluxee.co.il/api/main.py';
const APP_ID = 'E5D5FEF5-A05E-4C64-AEBA-BA0CECA0E402'; // the web app's own Application-Id header
const READS = new Set(['prx_get_budgets', 'get_user_cards', 'prx_user_deals']);
// Cibus's "not signed in" answers (the app's authorizeErrorsCodes).
const SIGNED_OUT = new Set([172, 173, 174, 175, 176, 177, 179]);
const STEP_MS = 40_000; // the site answering the SMS request / the code
const BLOCKED = /חסום|נחסם|נעול|blocked|locked|too many|יותר מדי|ניסיונות/i;

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

function failure(errorType, message) {
  return Object.assign(new Error(message), { errorType });
}

function aborted(ctx) {
  if (ctx.signal && ctx.signal.aborted) throw new Error('cancelled');
}

/** `dd/MM/yyyy` of a date in the worker's zone (TZ=Asia/Jerusalem in the image), as the site asks. */
function ddmmyyyy(date) {
  const two = n => String(n).padStart(2, '0');
  return `${two(date.getDate())}/${two(date.getMonth() + 1)}/${date.getFullYear()}`;
}

/** An Israeli mobile number as the site takes it (`05XXXXXXXX`), or null. */
function normalizePhone(value) {
  let digits = String(value || '').replace(/[\s()+-]/g, '');
  if (digits.startsWith('972')) digits = `0${digits.slice(3)}`;
  return /^05\d{8}$/.test(digits) ? digits : null;
}

/** What a refused SMS request (`stage` "phone") or code (`stage` "code") means for Dana. */
function refusal(stage, status, body) {
  const message = String((body && body.error && body.error.message) || (body && body.message) || '').replace(/\s+/g, ' ').slice(0, 200);
  if (/recaptcha/i.test(message)) return failure('GENERIC', 'Pluxee took the browser for a robot (reCAPTCHA); try again later');
  if (BLOCKED.test(message)) return failure('ACCOUNT_BLOCKED', `Pluxee blocked the sign-in${message ? `: ${message}` : ''}`);
  if (stage === 'code') {
    const left = body && body.error && body.error.message && body.error.message.remainingLoginAttempts;
    return failure('CODE_REJECTED', `Pluxee did not accept the code${left !== undefined ? ` (${left} tries left)` : ''}`);
  }
  if (status >= 500) return failure('GENERIC', `Pluxee answered HTTP ${status}`);
  return failure('INVALID_PASSWORD', `Pluxee does not know this phone number${message ? `: ${message}` : ''}`);
}

/** Runs one allowed read from inside the page; returns the parsed answer. */
async function read(page, type, params = {}) {
  if (!READS.has(type)) throw new Error(`refused: ${type} is not a call this worker makes`);
  const answer = await page.evaluate(async (api, appId, body) => {
    const res = await fetch(api, {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json; charset=utf-8', 'Application-Id': appId, 'accept-language': 'he' },
      body: JSON.stringify(body),
    });
    return { status: res.status, text: await res.text() };
  }, API, APP_ID, { ...params, type });
  let data = null;
  try { data = JSON.parse(answer.text); } catch { data = null; }
  if (answer.status === 401 || (data && SIGNED_OUT.has(data.code))) return { signedOut: true };
  if (answer.status !== 200 || !data || (data.code !== undefined && data.code !== 0)) {
    throw failure('GENERIC', `Pluxee refused ${type} (HTTP ${answer.status}${data && data.code ? `, code ${data.code}` : ''})`);
  }
  return data;
}

/** The next answer of the sign-in service to `path` (`/auth/sendOTP`, `/auth/authToken`): {status, body}. */
function nextAuthAnswer(page, path, ms) {
  return page.waitForResponse(res => res.url().includes(`/auth/${path}`) && res.request().method() === 'POST', { timeout: ms })
    .then(async res => {
      let body = null;
      try { body = await res.json(); } catch { body = null; }
      return { status: res.status(), body };
    }, () => null);
}

/** Clicks the form's visible "continue" button (the hidden tab has one too). */
function clickContinue(page) {
  return page.evaluate(() => {
    const button = [...document.querySelectorAll('button.cib-btn')].find(el => el.offsetWidth || el.offsetHeight);
    if (button) button.click();
    return Boolean(button);
  });
}

/** The code sign-in: phone → SMS (state `needs_code`) → code. The phone goes out once, the code once. */
async function signIn(page, phone, ctx) {
  try { await page.click('#onetrust-accept-btn-handler'); } catch { /* no cookie banner */ }
  // The page opens on the password tab; the one-time-code tab shows the phone field. The app may not have
  // drawn its tabs yet: click until the field shows.
  const deadline = Date.now() + 20_000;
  let shown = false;
  while (!shown && Date.now() < deadline) {
    aborted(ctx);
    shown = await page.evaluate(() => {
      const field = document.querySelector('#firstInput');
      if (field && (field.offsetWidth || field.offsetHeight)) return true;
      const tab = [...document.querySelectorAll('.tab, [role="tab"], div, span, button')]
        .find(el => !el.children.length && (el.innerText || '').trim() === 'קוד חד פעמי');
      if (tab) tab.click();
      return false;
    }).catch(() => false); // mid-navigation
    if (!shown) await sleep(700);
  }
  if (!shown) throw failure('GENERIC', 'the Pluxee login page changed: no phone field');
  await page.type('#firstInput', phone, { delay: 60 });
  await sleep(400);
  const sent = nextAuthAnswer(page, 'sendOTP', STEP_MS);
  if (!(await clickContinue(page))) await page.keyboard.press('Enter');
  const answer = await sent;
  aborted(ctx);
  if (!answer) throw failure('GENERIC', 'Pluxee did not answer the SMS request');
  if (answer.status !== 201) throw refusal('phone', answer.status, answer.body);
  // The SMS is out: from here a failure may be the login's (README "failed": LOGIN_UNKNOWN).
  ctx.setState('logging_in');
  const code = await ctx.requestCode();
  aborted(ctx);
  const field = await page.waitForSelector('ngx-otp-input input, input[autocomplete="one-time-code"]', { visible: true, timeout: 10_000 })
    .catch(() => null);
  if (!field) throw failure('GENERIC', 'the Pluxee code field was not found');
  const checked = nextAuthAnswer(page, 'authToken', STEP_MS);
  const fields = await page.$$('ngx-otp-input input, input[autocomplete="one-time-code"]');
  if (fields.length >= code.length) {
    for (let i = 0; i < code.length; i += 1) await fields[i].type(code[i], { delay: 80 });
  } else {
    await fields[0].type(code, { delay: 80 });
  }
  // The form sends a complete code by itself; press continue if it has not after a moment.
  const early = await Promise.race([checked, sleep(2500).then(() => 'later')]);
  if (early === 'later') await clickContinue(page);
  const result = early === 'later' ? await checked : early;
  aborted(ctx);
  if (!result) throw failure('GENERIC', 'Pluxee did not answer the code');
  if (result.status >= 400) throw refusal('code', result.status, result.body);
}

/**
 * The `runScrape` of a Pluxee job, on a browser scrape.js launched with the source's profile.
 * Returns {success, accounts: [{kind: "meal", accountNumber, balance, budgets, deals, txns: []}]}.
 */
async function runPluxee(browser, { credentials, startDate }, ctx) {
  const phone = normalizePhone(credentials.phone);
  if (!phone) return { success: false, errorType: 'INVALID_PASSWORD', errorMessage: 'not an Israeli mobile number' };
  const page = await browser.newPage();
  await page.setUserAgent((await browser.userAgent()).replace('HeadlessChrome', 'Chrome'));
  await page.goto(`${SITE}/login`, { waitUntil: 'networkidle2', timeout: 60_000 });
  aborted(ctx);
  // A session left in the profile (the 24-hour cookie) needs no SMS.
  let budgets = await read(page, 'prx_get_budgets');
  if (budgets.signedOut) {
    await signIn(page, phone, ctx);
    await sleep(1500);
    budgets = await read(page, 'prx_get_budgets');
    if (budgets.signedOut) throw failure('GENERIC', 'Pluxee accepted the code but the session did not start');
  }
  ctx.setState('scraping');
  const cards = await read(page, 'get_user_cards');
  const today = new Date();
  const deals = await read(page, 'prx_user_deals', { from_date: ddmmyyyy(startDate), to_date: ddmmyyyy(today) });
  if (cards.signedOut || deals.signedOut) throw failure('GENERIC', 'the Pluxee session ended while reading');
  const card = (cards.cards || []).find(c => c.activation_status === 'active') || (cards.cards || [])[0] || {};
  const last4 = String(card.cibus_card_number || card.cibus_card || '').replace(/\D/g, '').slice(-4);
  const list = Array.isArray(budgets.data) ? budgets.data : [];
  const left = list.reduce((sum, b) => sum + (Number(b.CurrBudget) || 0), 0);
  return {
    success: true,
    accounts: [{
      kind: 'meal',
      accountNumber: last4 || 'pluxee',
      balance: Math.round(left * 100) / 100,
      // The site's own rows: the budgets of the current cycle and every charge since start_date.
      budgets: list.map(({ SCard, ...rest }) => rest),
      deals: Array.isArray(deals.list) ? deals.list : [],
      txns: [],
    }],
  };
}

module.exports = { runPluxee, normalizePhone, refusal, ddmmyyyy, read, READS };
