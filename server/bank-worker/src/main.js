'use strict';
// Entry point: the job table with the real browser scrape, served on 127.0.0.1.

const { createJobs } = require('./jobs');
const { createServer } = require('./server');
const { runScrape } = require('./scrape');
const { version } = require('../package.json');

const HOST = process.env.BANK_WORKER_HOST || '127.0.0.1';
const PORT = Number(process.env.BANK_WORKER_PORT || 8090);
const TOKEN_FILE = process.env.BANK_WORKER_TOKEN_FILE || '/app/db/money/worker-token';
const PROFILES = process.env.BANK_WORKER_PROFILES || '/app/db/money/profiles';

const log = message => console.log(`${new Date().toISOString()} bank-worker: ${message}`);
const jobs = createJobs({ runScrape, log });
const server = createServer({ jobs, tokenFile: TOKEN_FILE, profilesDir: PROFILES, version, log });

server.listen(PORT, HOST, () => log(`v${version} listening on ${HOST}:${PORT}`));

function shutdown() {
  for (const job of jobs.running()) jobs.cancel(job.id);
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 5000).unref();
}
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
