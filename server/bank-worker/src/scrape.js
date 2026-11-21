'use strict';
// One scrape: a Chromium on the source's saved profile, the site's scraper, cleanup (Psagot: psagot.js,
// no browser; Pluxee: pluxee.js, our own code on the browser instead of the library's).

const fs = require('node:fs');
const path = require('node:path');
const puppeteer = require('puppeteer');
const { SITES } = require('./sites');
const { runPsagot } = require('./psagot');
const { runPluxee } = require('./pluxee');

// Sites read over plain HTTPS: no browser, no profile folder.
const HTTP_SITES = { psagot: runPsagot };
// Sites with our own code on the browser (no library scraper). They launch without Chromium's automation
// flags: Pluxee's reCAPTCHA refuses a browser that says it is automated.
const BROWSER_SITES = { pluxee: runPluxee };

const PROFILES = process.env.BANK_WORKER_PROFILES || '/app/db/money/profiles';
const CHROMIUM = process.env.CHROMIUM_PATH || '/usr/bin/chromium';
const HEADFUL = process.env.BANK_WORKER_HEADFUL === '1';
const NO_SANDBOX = process.env.BANK_WORKER_NO_SANDBOX === '1';
// Debugging a site change: a screenshot of a failed login lands here (never set in production:
// a screenshot can show account data).
const DUMP_DIR = process.env.BANK_WORKER_DUMP_DIR || '';

// Big, rebuildable folders; the cookies and site storage that make a device trusted stay.
const CACHES = ['Cache', 'Code Cache', 'GPUCache', 'DawnGraphiteCache', 'DawnWebGPUCache',
  path.join('Service Worker', 'CacheStorage'), path.join('Service Worker', 'ScriptCache')];
const ROOT_CACHES = ['GrShaderCache', 'GraphiteDawnCache', 'ShaderCache', 'component_crx_cache',
  'extensions_crx_cache', 'optimization_guide_model_store', 'Crashpad'];

function pruneCaches(dir) {
  for (const name of CACHES) fs.rmSync(path.join(dir, 'Default', name), { recursive: true, force: true });
  for (const name of ROOT_CACHES) fs.rmSync(path.join(dir, name), { recursive: true, force: true });
}

/** A Chromium killed mid-run leaves its lock behind and the next launch refuses the profile. */
function clearLocks(dir) {
  for (const name of ['SingletonLock', 'SingletonSocket', 'SingletonCookie']) {
    fs.rmSync(path.join(dir, name), { force: true });
  }
}

/** The `runScrape` of jobs.js. */
async function runScrape({ company, profile, credentials, device, startDate }, ctx) {
  if (HTTP_SITES[company]) return HTTP_SITES[company]({ credentials, device, startDate }, ctx);
  const site = SITES[company];
  const own = BROWSER_SITES[company];
  if (!site && !own) return { success: false, errorType: 'GENERIC', errorMessage: `unknown company ${company}` };
  const dir = path.join(PROFILES, profile);
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  clearLocks(dir);
  const args = ['--password-store=basic', '--no-first-run', '--no-default-browser-check', '--disable-dev-shm-usage',
    '--lang=he-IL'];
  if (NO_SANDBOX) args.push('--no-sandbox');
  if (own) args.push('--disable-blink-features=AutomationControlled');
  const browser = await puppeteer.launch({
    executablePath: CHROMIUM,
    headless: !HEADFUL,
    userDataDir: dir,
    args,
    ...(own ? { ignoreDefaultArgs: ['--enable-automation'] } : {}),
    defaultViewport: { width: 1280, height: 900 },
  });
  const close = () => browser.close().catch(() => {});
  ctx.signal.addEventListener('abort', close, { once: true });
  try {
    if (own) return await own(browser, { credentials, startDate }, ctx);
    const Scraper = site(ctx);
    const scraper = new Scraper({
      companyId: company,
      startDate,
      browser,
      skipCloseBrowser: true,
      includeRawTransaction: true,
      defaultTimeout: 60_000,
      ...(DUMP_DIR ? { storeFailureScreenShotPath: path.join(DUMP_DIR, `${company}-${Date.now()}.png`) } : {}),
    });
    scraper.onProgress((_id, progress) => {
      if (progress.type === 'LOGGING_IN') ctx.setState('logging_in');
      if (progress.type === 'LOGIN_SUCCESS') ctx.setState('scraping');
    });
    const result = await scraper.scrape(credentials);
    if (!result.success && scraper.loginError) {
      return { success: false, errorType: scraper.loginError.type, errorMessage: scraper.loginError.message };
    }
    return result;
  } finally {
    ctx.signal.removeEventListener('abort', close);
    await close();
    try { pruneCaches(dir); } catch { /* best effort */ }
  }
}

module.exports = { runScrape };
