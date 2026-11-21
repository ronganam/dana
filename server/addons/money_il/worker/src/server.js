'use strict';
// The HTTP API of README.md over the job table. No browser code here (see scrape.js).

const crypto = require('node:crypto');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');

const COMPANIES = {
  hapoalim: ['userCode', 'password'],
  visaCal: ['username', 'password'],
  psagot: ['username', 'password'],
  pluxee: ['phone'],
};
// Companies whose jobs may carry a device key from an earlier job (README "Psagot").
const DEVICE_COMPANIES = new Set(['psagot']);
const MAX_DEVICE_FIELD = 8192;
const PROFILE = /^[A-Za-z0-9_-]{1,64}$/;
const CODE = /^\d{4,8}$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const MAX_BODY = 64 * 1024;

function send(res, status, body) {
  const text = JSON.stringify(body);
  res.writeHead(status, { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(text) });
  res.end(text);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', chunk => {
      size += chunk.length;
      if (size > MAX_BODY) { reject(Object.assign(new Error('body too large'), { status: 413 })); req.destroy(); return; }
      chunks.push(chunk);
    });
    req.on('end', () => {
      if (!chunks.length) return resolve({});
      try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8'))); } catch {
        reject(Object.assign(new Error('invalid JSON'), { status: 400 }));
      }
    });
    req.on('error', reject);
  });
}

/** True when the request carries the token from `tokenFile` (re-read every time; Dana may replace it). */
function authorized(req, tokenFile) {
  let token;
  try { token = fs.readFileSync(tokenFile, 'utf8').trim(); } catch { return false; }
  const given = String(req.headers.authorization || '').replace(/^Bearer\s+/i, '').trim();
  if (!token || !given) return false;
  const a = Buffer.from(token);
  const b = Buffer.from(given);
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

/** A validated POST /jobs body, or {error}. */
function parseJob(body) {
  if (!body || typeof body !== 'object') return { error: 'expected an object' };
  const fields = COMPANIES[body.company];
  if (!fields) return { error: `unknown company; use one of ${Object.keys(COMPANIES).join(', ')}` };
  if (typeof body.profile !== 'string' || !PROFILE.test(body.profile)) return { error: 'bad profile' };
  const creds = body.credentials;
  if (!creds || typeof creds !== 'object') return { error: 'credentials missing' };
  for (const name of fields) {
    if (typeof creds[name] !== 'string' || !creds[name]) return { error: `credentials.${name} missing` };
  }
  let device = null;
  if (body.device !== undefined && body.device !== null) {
    if (!DEVICE_COMPANIES.has(body.company)) return { error: 'this company takes no device' };
    const d = body.device;
    const ok = d && typeof d === 'object' && ['user', 'udid', 'key'].every(
      name => typeof d[name] === 'string' && d[name] && d[name].length <= MAX_DEVICE_FIELD);
    if (!ok) return { error: 'bad device' };
    device = { user: d.user, udid: d.udid, key: d.key };
  }
  if (typeof body.start_date !== 'string' || !DATE.test(body.start_date)) return { error: 'bad start_date' };
  const startDate = new Date(`${body.start_date}T00:00:00`);
  if (Number.isNaN(startDate.getTime())) return { error: 'bad start_date' };
  return {
    company: body.company,
    profile: body.profile,
    credentials: Object.fromEntries(fields.map(name => [name, creds[name]])),
    device,
    startDate,
  };
}

function createServer({ jobs, tokenFile, profilesDir, version = '0', log = () => {} }) {
  async function handle(req, res) {
    const url = new URL(req.url, 'http://worker');
    const parts = url.pathname.split('/').filter(Boolean);

    if (req.method === 'GET' && url.pathname === '/health') {
      return send(res, 200, { ok: true, version, running: jobs.running().length });
    }
    if (!authorized(req, tokenFile)) return send(res, 401, { error: 'unauthorized' });

    if (parts[0] === 'jobs' && parts.length === 1 && req.method === 'POST') {
      const spec = parseJob(await readBody(req));
      if (spec.error) return send(res, 400, { error: spec.error });
      const started = jobs.start(spec);
      if (started.error === 'busy') return send(res, 409, { error: 'busy', job: started.job });
      if (started.error) return send(res, 429, { error: started.error });
      return send(res, 201, { id: started.job.id, state: started.job.state });
    }

    if (parts[0] === 'jobs' && parts.length >= 2) {
      const job = jobs.get(parts[1]);
      if (!job) return send(res, 404, { error: 'no such job' });
      if (parts.length === 2 && req.method === 'GET') {
        const wait = Math.min(30, Math.max(0, Number(url.searchParams.get('wait')) || 0));
        await job.waitChange(url.searchParams.get('seen'), wait * 1000);
        return send(res, 200, job.view());
      }
      if (parts.length === 2 && req.method === 'DELETE') {
        jobs.cancel(job.id);
        return send(res, 200, { ok: true });
      }
      if (parts.length === 3 && parts[2] === 'code' && req.method === 'POST') {
        const body = await readBody(req);
        const code = String((body && body.code) || '').trim();
        if (!CODE.test(code)) return send(res, 400, { error: 'bad code' });
        if (!job.submitCode(code)) return send(res, 409, { error: 'not_waiting' });
        log(`job ${job.id} got a code`);
        return send(res, 202, { ok: true });
      }
    }

    if (parts[0] === 'profiles' && parts.length === 2 && req.method === 'DELETE') {
      const profile = parts[1];
      if (!PROFILE.test(profile)) return send(res, 400, { error: 'bad profile' });
      if (jobs.profileBusy(profile)) return send(res, 409, { error: 'busy' });
      fs.rmSync(path.join(profilesDir, profile), { recursive: true, force: true });
      log(`profile ${profile} deleted`);
      return send(res, 200, { ok: true });
    }

    return send(res, 404, { error: 'not found' });
  }

  return http.createServer((req, res) => {
    handle(req, res).catch(err => {
      if (!res.headersSent) send(res, err.status || 500, { error: err.status ? err.message : 'internal error' });
      if (!err.status) log(`request failed: ${err.message}`);
    });
  });
}

module.exports = { createServer, parseJob, COMPANIES };
