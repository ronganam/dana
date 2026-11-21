'use strict';
// Per-site login handling on top of israeli-bank-scrapers: wait for the real outcome instead of a
// fixed 20 s, type Hapoalim's SMS code (a new device), and recognise what the library misses.
// A scraper class is made per job, bound to that job's ctx (setState / requestCode / signal).

const HapoalimScraper = require('israeli-bank-scrapers/lib/scrapers/hapoalim').default;
const VisaCalScraper = require('israeli-bank-scrapers/lib/scrapers/visa-cal').default;
const { LoginResults } = require('israeli-bank-scrapers/lib/scrapers/base-scraper-with-browser');

const LOGIN_WAIT_MS = 90_000; // after the password (and again after a code), until the site decides
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

const INVALID = /שגוי|אינם תואמים|אינו תואם|לא תקינ|לא נכונ|incorrect|invalid|wrong/i;
const BLOCKED = /חסום|נחסם|נחסמה|נעול|blocked|locked|suspended/i;

/** What a login-page error text means: a wrong password, a blocked user, or (unknown) treated as wrong. */
function loginErrorType(text) {
  if (BLOCKED.test(text)) return 'ACCOUNT_BLOCKED';
  // Unknown errors on the login form count as a wrong password: Dana never retries those,
  // and a retried wrong password is what locks the user code.
  return 'INVALID_PASSWORD';
}

function aborted(ctx) {
  if (ctx.signal.aborted) throw new Error('cancelled');
}

// ---- Hapoalim -------------------------------------------------------------------------------

const OTP_FORM = 'form.auth-otp-login';
const OTP_SUBMIT = `${OTP_FORM} .btn-red_1, ${OTP_FORM} button[type="submit"], .btn-red_1`;
const PAGE_ERRORS = '.errors-rb .error-message, .auth-otp-login .error, .error-message';
const CODE_MARK = 'data-dana-code';

/**
 * The login page as the scraper needs it: where it is, whether it asks for the SMS code, any error
 * shown, and its heading (for the error when the login stalls). The code fields found are marked
 * with CODE_MARK (their order) so typeHapoalimCode types into exactly those.
 * The code page is recognised from the form seen in the wild (PR #1084 of the library) and, in case
 * the bank changes it, from generic one-time-code fields on a page that talks about a code.
 */
function hapoalimState(page) {
  return page.evaluate((otpForm, pageErrors, mark) => {
    const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
    const typed = 'input:not([type]), input[type="text"], input[type="tel"], input[type="number"], input[type="password"]';
    const all = sel => [...document.querySelectorAll(sel)].filter(visible);
    document.querySelectorAll(`[${mark}]`).forEach(el => el.removeAttribute(mark));
    let fields = [];
    const form = document.querySelector(otpForm);
    if (visible(form)) fields = [...form.querySelectorAll(typed)].filter(visible);
    if (!fields.length) fields = all('input[autocomplete="one-time-code"]');
    if (!fields.length) {
      const digits = all(typed).filter(el => el.maxLength === 1);
      if (digits.length >= 4) fields = digits;
    }
    if (!fields.length && !visible(document.querySelector('#userCode'))) {
      const text = (document.body && document.body.innerText) || '';
      if (/קוד (ה)?אימות|קוד חד[ -]פעמי|הקוד שנשלח|SMS/i.test(text)) {
        fields = all(typed).filter(el => !/user|id|password/i.test(`${el.id} ${el.name}`));
      }
    }
    fields.forEach((el, i) => el.setAttribute(mark, String(i)));
    const error = all(pageErrors).map(el => (el.innerText || '').trim()).filter(Boolean)
      .join(' | ').slice(0, 200);
    const heading = all('h1, h2, h3').map(el => (el.innerText || '').trim()).find(Boolean) || '';
    return { url: location.href, otp: fields.length > 0, error, heading: heading.slice(0, 80) };
  }, OTP_FORM, PAGE_ERRORS, CODE_MARK);
}

/** Types the code (one field per digit, or one field) and submits with a real click (Angular). */
async function typeHapoalimCode(page, code) {
  const marked = [];
  for (const handle of await page.$$(`[${CODE_MARK}]`)) {
    const index = Number(await handle.evaluate((el, mark) => el.getAttribute(mark), CODE_MARK));
    if (await handle.boundingBox()) marked.push({ index, handle });
  }
  const fields = marked.sort((a, b) => a.index - b.index).map(m => m.handle);
  if (!fields.length) throw new Error('the code fields were not found');
  if (fields.length >= code.length) {
    for (let i = 0; i < code.length; i += 1) {
      await fields[i].click({ clickCount: 3 });
      await page.keyboard.press('Backspace');
      await fields[i].type(code[i], { delay: 50 });
      await sleep(100);
    }
  } else {
    await fields[0].click({ clickCount: 3 });
    await page.keyboard.press('Backspace');
    await fields[0].type(code, { delay: 60 });
  }
  await sleep(300);
  let submit = null;
  for (const handle of await page.$$(OTP_SUBMIT)) {
    if (await handle.boundingBox()) { submit = handle; break; }
  }
  if (submit) await submit.click();
  else await page.keyboard.press('Enter');
}

function hapoalim(ctx) {
  return class PatientHapoalim extends HapoalimScraper {
    getLoginOptions(credentials) {
      const opts = super.getLoginOptions(credentials);
      opts.possibleResults[LoginResults.Success].push(/\/homepage/i);
      opts.possibleResults[LoginResults.InvalidPassword].push(async () => this.loginError?.type === 'INVALID_PASSWORD');
      opts.postAction = async () => {
        let deadline = Date.now() + LOGIN_WAIT_MS;
        let codes = 0;
        let lastError = '';
        let state = null;
        while (Date.now() < deadline) {
          aborted(ctx);
          try {
            state = await hapoalimState(this.page);
          } catch {
            await sleep(1000); // mid-navigation
            continue;
          }
          if (/homepage/i.test(state.url) || /errorcode=|\/MCP\/START|ABOUTTOEXPIRE/i.test(state.url)) return;
          if (state.otp) {
            if (codes > 0) {
              // Still on the code form after typing one: rejected (or expired). Never type another.
              this.loginError = { type: 'CODE_REJECTED', message: `the bank did not accept the code${state.error ? `: ${state.error}` : ''}` };
              return;
            }
            const code = await ctx.requestCode();
            aborted(ctx);
            codes += 1;
            await typeHapoalimCode(this.page, code);
            deadline = Date.now() + LOGIN_WAIT_MS;
            await sleep(8000); // the form stays up a few seconds while the bank checks the code
            continue;
          }
          // An error on the login form, seen on two polls in a row (not a flash while it redirects).
          if (state.error && /\/auth\//i.test(state.url) && state.error === lastError) {
            this.loginError = { type: loginErrorType(state.error), message: state.error };
            return;
          }
          lastError = state.error;
          await sleep(1000);
        }
        // Neither signed in nor a page we know: say where it stopped, so a site change is visible.
        if (state && /\/auth\//i.test(state.url)) {
          const where = new URL(state.url).pathname;
          throw new Error(`the login did not finish: still on ${where}${state.heading ? ` (${state.heading})` : ''}`);
        }
      };
      return opts;
    }
  };
}

// ---- Cal ------------------------------------------------------------------------------------

/** Cal's login outcome: the dashboard, an error in the login frame, or the change-password form. */
async function calOutcome(page) {
  let url = '';
  try { url = await page.evaluate(() => location.href); } catch { return null; }
  if (url.endsWith('site-tutorial')) {
    try { await page.click('button.btn-close'); } catch { /* not there yet */ }
  }
  if (/dashboard/i.test(url)) return 'dashboard';
  for (const frame of page.frames()) {
    if (/change-password/i.test(frame.url())) return 'change_password';
    try {
      if (await frame.$('change-password, .change-password-title, .change-password-subtitle')) return 'change_password';
    } catch { /* detached */ }
    if (!frame.url().includes('connect')) continue;
    try {
      const err = await frame.$eval('div.general-error > div', el => (el.innerText || '').trim());
      if (err) return { error: err };
    } catch { /* no error */ }
  }
  return null;
}

function visaCal(ctx) {
  return class PatientCal extends VisaCalScraper {
    getLoginOptions(credentials) {
      const opts = super.getLoginOptions(credentials);
      opts.possibleResults[LoginResults.InvalidPassword].push(async () => this.loginError?.type === 'INVALID_PASSWORD');
      opts.postAction = async () => {
        const deadline = Date.now() + LOGIN_WAIT_MS;
        while (Date.now() < deadline) {
          aborted(ctx);
          const outcome = await calOutcome(this.page);
          if (outcome === 'dashboard' || outcome === 'change_password') return;
          if (outcome && outcome.error) {
            this.loginError = { type: loginErrorType(outcome.error), message: outcome.error };
            return;
          }
          await sleep(1000);
        }
      };
      return opts;
    }
  };
}

const SITES = { hapoalim, visaCal };

module.exports = { SITES, loginErrorType, hapoalimState, typeHapoalimCode };
