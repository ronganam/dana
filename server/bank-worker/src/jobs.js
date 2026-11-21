'use strict';
// The job table: one scrape per job, its state machine, long polls and the SMS-code hand-off.
// Knows nothing about browsers: the scrape is injected (`runScrape`), so tests run without Chromium.

const crypto = require('node:crypto');

const FINAL = new Set(['done', 'failed', 'cancelled']);
const ERROR_TYPES = new Set(['INVALID_PASSWORD', 'CHANGE_PASSWORD', 'ACCOUNT_BLOCKED', 'CODE_TIMEOUT', 'CODE_REJECTED',
  'LOGIN_UNKNOWN', 'TIMEOUT', 'GENERIC']);
// The states between the password going out and the site letting the scraper in.
const SENT = new Set(['logging_in', 'needs_code']);
// A finished job keeps its profile (and its browser slot) until its browser has closed, at most this long.
const CLOSE_GRACE_MS = 60_000;

/** Library error types → the contract's (README "failed"). */
function normalizeErrorType(type) {
  return ERROR_TYPES.has(type) ? type : 'GENERIC';
}

/**
 * A failure while the password was out and the site had not let us in (a page the worker does not know,
 * a stall, a crash mid-login) might have been a refused password: `LOGIN_UNKNOWN`, which Dana does not
 * retry on its own (README "failed"). The same failure before the password was sent, or after signing
 * in, keeps its type.
 */
function failureType(state, type) {
  const normal = normalizeErrorType(type);
  return (normal === 'GENERIC' || normal === 'TIMEOUT') && SENT.has(state) ? 'LOGIN_UNKNOWN' : normal;
}

/** A short error text with every credential value blanked out. */
function cleanError(message, credentials) {
  // URLs lose their query and fragment (a session id, a token); credentials are masked.
  let text = String(message || '').replace(/\s+/g, ' ').trim()
    .replace(/(https?:\/\/[^\s?#]*)[?#]\S*/gi, '$1');
  for (const value of Object.values(credentials || {})) {
    if (typeof value === 'string' && value.length >= 3) text = text.split(value).join('***');
  }
  return text.slice(0, 300);
}

class Job {
  constructor(company, profile) {
    this.id = crypto.randomUUID().replace(/-/g, '');
    this.company = company;
    this.profile = profile;
    this.state = 'starting';
    this.errorType = null;
    this.error = null;
    this.accounts = null;
    this.device = null; // Psagot: a device key registered in this job (README "Psagot")
    this.deviceDropped = false;
    this.startedAt = new Date();
    this.updatedAt = this.startedAt;
    this.finishedAt = null;
    this.waiters = new Set();
    this.codeWaiter = null;
    this.codeTimedOut = false;
    this.closed = false; // the scrape has returned and its browser is closed
    this.abort = new AbortController();
  }

  get final() {
    return FINAL.has(this.state);
  }

  /** Still running, or finished with its browser not closed yet (briefly): its profile is in use. */
  get holding() {
    if (this.closed) return false;
    return !this.final || Date.now() - this.finishedAt.getTime() < CLOSE_GRACE_MS;
  }

  /** Moves to `state` (a final state never changes again) and wakes the long polls. */
  set(state, fields = {}) {
    if (this.final) return;
    Object.assign(this, fields);
    this.state = state;
    this.updatedAt = new Date();
    if (this.final) this.finishedAt = this.updatedAt;
    for (const wake of this.waiters) wake();
    this.waiters.clear();
  }

  view() {
    return {
      id: this.id,
      company: this.company,
      state: this.state,
      error_type: this.errorType,
      error: this.error,
      accounts: this.state === 'done' ? this.accounts : null,
      device: this.state === 'done' ? this.device : null,
      device_dropped: this.state === 'done' ? this.deviceDropped : false,
      started_at: this.startedAt.toISOString(),
      updated_at: this.updatedAt.toISOString(),
    };
  }

  /** Resolves when the state is no longer `seen`, or after `ms`. */
  waitChange(seen, ms) {
    if (!seen || this.state !== seen || ms <= 0) return Promise.resolve();
    return new Promise(resolve => {
      const done = () => { clearTimeout(timer); this.waiters.delete(done); resolve(); };
      const timer = setTimeout(done, ms);
      this.waiters.add(done);
    });
  }

  /** The site asked for an SMS code: `needs_code` until a code arrives (resolves) or `ms` pass (rejects). */
  awaitCode(ms) {
    if (this.final) return Promise.reject(new Error('job finished'));
    this.set('needs_code');
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.codeWaiter = null;
        this.codeTimedOut = true;
        reject(new Error('CODE_TIMEOUT: no code arrived'));
      }, ms);
      this.codeWaiter = {
        resolve: code => { clearTimeout(timer); this.codeWaiter = null; resolve(code); },
        reject: err => { clearTimeout(timer); this.codeWaiter = null; reject(err); },
      };
    });
  }

  /** Hands a code to a scrape waiting in `needs_code`. False when nothing waits. */
  submitCode(code) {
    if (this.state !== 'needs_code' || !this.codeWaiter) return false;
    this.set('logging_in');
    this.codeWaiter.resolve(code);
    return true;
  }
}

/**
 * The job table.
 * runScrape({company, profile, credentials, device, startDate}, ctx) → the library's result
 * ({success, accounts} or {success: false, errorType, errorMessage}; Psagot adds `device`,
 * `deviceDropped`); ctx has `signal` (aborted on cancel / timeout), `setState(state)`,
 * `requestCode()` and `log(message)`.
 */
function createJobs({
  runScrape,
  maxRunning = 2,
  keepMs = 120_000,
  jobTimeoutMs = 600_000,
  codeTimeoutMs = 300_000,
  log = () => {},
}) {
  const jobs = new Map();
  const running = () => [...jobs.values()].filter(job => job.holding);

  function finish(job, result, credentials) {
    if (job.final) return;
    if (result && result.success) {
      job.set('done', { accounts: result.accounts || [], device: result.device || null,
        deviceDropped: Boolean(result.deviceDropped) });
    } else if (job.codeTimedOut) {
      job.set('failed', { errorType: 'CODE_TIMEOUT', error: 'no sign-in code arrived' });
    } else {
      job.set('failed', {
        errorType: failureType(job.state, result && result.errorType),
        error: cleanError(result && result.errorMessage, credentials),
      });
    }
  }

  function start({ company, profile, credentials, device = null, startDate }) {
    const busy = running().find(job => job.profile === profile);
    if (busy) return { error: 'busy', job: busy.id };
    if (running().length >= maxRunning) return { error: 'too_many' };
    const job = new Job(company, profile);
    jobs.set(job.id, job);
    log(`job ${job.id} ${company} started`);
    const timer = setTimeout(() => {
      job.set('failed', { errorType: failureType(job.state, 'TIMEOUT'), error: 'the job ran too long' });
      job.abort.abort();
    }, jobTimeoutMs);
    const ctx = {
      signal: job.abort.signal,
      setState: state => job.set(state),
      requestCode: () => job.awaitCode(codeTimeoutMs),
      log: message => log(`job ${job.id} ${message}`),
    };
    Promise.resolve()
      .then(() => runScrape({ company, profile, credentials, device, startDate }, ctx))
      .then(
        result => finish(job, result, credentials),
        err => finish(job, { success: false, errorType: err && err.errorType, errorMessage: err && err.message }, credentials),
      )
      .finally(() => {
        clearTimeout(timer);
        job.closed = true;
        if (job.codeWaiter) job.codeWaiter.reject(new Error('job finished'));
        log(`job ${job.id} ${company} ${job.state}${job.errorType ? ' ' + job.errorType : ''} in ` +
          `${Math.round((Date.now() - job.startedAt) / 1000)}s`);
        const forget = setTimeout(() => jobs.delete(job.id), keepMs);
        if (forget.unref) forget.unref();
      });
    return { job };
  }

  function cancel(id) {
    const job = jobs.get(id);
    if (!job) return null;
    if (!job.final) {
      job.set('cancelled');
      if (job.codeWaiter) job.codeWaiter.reject(new Error('cancelled'));
      job.abort.abort();
    }
    return job;
  }

  return {
    start,
    cancel,
    get: id => jobs.get(id),
    running,
    profileBusy: profile => running().some(job => job.profile === profile),
  };
}

module.exports = { createJobs, cleanError, normalizeErrorType, failureType, FINAL };
