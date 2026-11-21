# bank-worker

A small Node service that logs in to Israeli bank and card sites with a real Chromium and returns their transactions, reads Psagot Trade portfolios over plain HTTPS, and reads the Pluxee (Cibus) meal card with its own code on the browser. It runs as a sidecar container in Dana's pod. Dana (`addons/money/`) decides when to sync, holds the credentials (encrypted) and stores the results; the worker is a browser with no memory beyond its saved browser profiles.

Built on [`israeli-bank-scrapers`](https://github.com/eshaham/israeli-bank-scrapers) (pinned in `package.json`), with our own handling for what the library misses: Hapoalim's SMS-code page (the bank asks when it decides to: a new device or address, not every new profile), Hapoalim's wrong-password message on its new login page, and Cal's forced password change. A login waits for the bank's redirect instead of the library's fixed 20 s, so a code can be entered.

## Where it listens and who may call

- `127.0.0.1:${BANK_WORKER_PORT:-8090}`: only containers in the same pod can reach it. It is not on the Kubernetes Service.
- Every call except `GET /health` needs `Authorization: Bearer <token>`. The token is the content of `${BANK_WORKER_TOKEN_FILE:-/app/db/money/worker-token}`, a file Dana creates (mode 0600) on the shared volume. The worker reads it on each request, so Dana can replace it at any time.
- Browser profiles live in `${BANK_WORKER_PROFILES:-/app/db/money/profiles}/<profile>/` (one per Dana money source; the PVC, so they survive restarts and are in the backups). Chromium runs with `--password-store=basic`, so a profile keeps its cookies without a keyring. Cache folders are removed after each job.
- Never logged: credentials, codes, transactions, balances. Logs carry job ids, companies, states, error types and timings.

## API

All bodies are JSON.

### `GET /health`
`200 {"ok": true, "version": "<package version>", "running": <jobs running>}`

### `POST /jobs`
```json
{"company": "hapoalim", "profile": "<source id>", "start_date": "2025-11-18",
 "credentials": {"userCode": "...", "password": "..."}}
```
- `company`: `hapoalim` (credentials `userCode`, `password`), `visaCal` (`username`, `password`), `psagot` (`username`, `password`) or `pluxee` (`phone`).
- `device` (optional, `psagot` only): `{"user", "udid", "key"}`, the quick-view device an earlier job returned (strings, at most 8192 characters each); a device for another company is a `400`.
- `profile`: `[A-Za-z0-9_-]{1,64}`, Dana's source id.
- `start_date`: the first purchase date wanted (the sites cap it: Hapoalim one year, Cal about 18 months).

Answers `201 {"id": "<job id>", "state": "starting"}`; `409 {"error": "busy", "job": "<id>"}` when that profile already has a running job (a finished or cancelled job keeps its profile and its browser slot until its browser has closed, at most a minute, so two browsers never share a profile); `429 {"error": "too_many"}` at the browser limit (2); `400 {"error": "..."}` for a bad body.

### `GET /jobs/{id}?wait=<seconds, max 30>`
Long poll: answers as soon as the state differs from the one the caller last saw (`&seen=<state>`), or when `wait` runs out.
```json
{"id": "...", "company": "hapoalim", "state": "needs_code",
 "error_type": null, "error": null, "accounts": null,
 "started_at": "2026-11-18T07:00:03.120Z", "updated_at": "2026-11-18T07:00:09.554Z"}
```
States: `starting` → `logging_in` → (`needs_code` → `logging_in`) → `scraping` → `done` | `failed` | `cancelled`.
- `needs_code`: the site sent the user an SMS code and waits for it (Hapoalim, when it decides to ask: a new device or address, not every new profile). The job keeps waiting up to 5 minutes for `POST /jobs/{id}/code`, then fails with `CODE_TIMEOUT`.
- `done`: `accounts` holds the library's result, `includeRawTransaction` on:
  ```json
  [{"accountNumber": "12-762-123456", "balance": 17710.51,
    "txns": [{"type": "normal", "identifier": 123, "date": "2026-11-14T22:00:00.000Z",
              "processedDate": "2026-11-14T22:00:00.000Z", "originalAmount": -8455.9,
              "originalCurrency": "ILS", "chargedAmount": -8455.9, "description": "כאל",
              "memo": "", "status": "completed", "installments": null,
              "rawTransaction": {"...": "site fields"}}]}]
  ```
  Dates are UTC timestamps of local (Asia/Jerusalem) midnight. Cal's `balance` is the next card bill (negative); Cal's `originalCurrency` is a symbol (`₪`, `$`, `€`). For Cal installments the library moves `date` to the payment's month; the real purchase date is `rawTransaction.trnPurchaseDate`.
- `failed`: `error_type` is one of `INVALID_PASSWORD`, `CHANGE_PASSWORD`, `ACCOUNT_BLOCKED`, `CODE_REJECTED` (the bank refused the code: retrying could lock the user out), `LOGIN_UNKNOWN` — Dana must not retry these on its own — then `CODE_TIMEOUT`, `TIMEOUT`, `GENERIC`. `LOGIN_UNKNOWN` is any `GENERIC` / `TIMEOUT` failure between the password going out and the site letting the scraper in (`logging_in` / `needs_code`): a login result the library does not know, a page the worker does not know ("the login did not finish: still on <path> (<heading>)"), a stall or a crash mid-login — it may have been a refused password. The same failure before the password was sent (`starting`: the browser, the login page) or after signing in (`scraping`) keeps its type. `error` is a short text without secrets.
- `device` / `device_dropped` (`psagot`, once `done`): a quick-view device the job registered (`{"user", "udid", "key"}`: Dana keeps it encrypted and sends it with the next jobs) or `null`; `device_dropped: true` when the device sent was refused (Dana forgets it).
- A finished job is kept for 2 minutes after it finishes, then forgotten (404).

### Psagot Trade
`src/psagot.js`, no browser: the TradeOne API behind `trade.psagot.co.il` (`https://trade1.psagot.co.il`). Read-only by construction: the client refuses every path outside `ALLOWED` (sign-in, the quick-view device, accounts, balances, transactions, daily values, the market table), so no order, password or alert call can leave the worker.
- With a `device` for the same username: sign in by signing the site's challenge with the device key (no password, no SMS; the state stays `starting`). A refused device → `device_dropped`, then the password sign-in below.
- Otherwise: the password once (`logging_in`); the site texts a code → `needs_code` → the code once. Wrong password `INVALID_PASSWORD`, wrong code `CODE_REJECTED`, expired password `CHANGE_PASSWORD`, blocked `ACCOUNT_BLOCKED`, anything else at the login `LOGIN_UNKNOWN`; never retried. Signed in, it registers a quick-view device named "Dana" and returns it in `device`.
- Reads per account: the balances view with real-time prices (`calcMode=RT`; without it on refusal), the holdings' names from the market table (optional), the activity since `start_date`, the daily values.
- `accounts` (one per trading account): `{"kind": "investment", "accountNumber", "nickname", "balance": <value>, "summary": {...}, "holdings": [...], "names": {"<security>": {"heb", "eng", "symbol"}}, "activity": [...], "history": [{"Date", "Value", "Yield"}], "txns": []}` — the site's own rows (the holder's name left out; money in shekels, Tel Aviv prices in agorot).

### Pluxee (Cibus)
`src/pluxee.js`, on the browser with the source's profile (scrape.js launches this site without Chromium's automation flags: Google's reCAPTCHA v3 on the sign-in refuses a browser that says it is automated). Read-only by construction: once signed in, every read goes through `read`, which refuses any call type outside `READS` (`prx_get_budgets`, `get_user_cards`, `prx_user_deals`).
- A session left in the profile (the site's `token` cookie lasts 24 hours): straight to `scraping`, no SMS.
- Otherwise the phone on the login page's one-time-code tab; once the site says the SMS is out → `logging_in` → `needs_code` → the code once. Before the SMS goes out (a reCAPTCHA refusal, an unknown phone) the state is still `starting`, so a failure there is never `LOGIN_UNKNOWN`. reCAPTCHA `GENERIC`, unknown phone `INVALID_PASSWORD`, wrong code `CODE_REJECTED`, blocked `ACCOUNT_BLOCKED`.
- `accounts` (one, the meal card): `{"kind": "meal", "accountNumber": "<the active card's last 4>", "balance": <what is left of the budget, ₪>, "budgets": [{"CreatioBudget", "CurrBudget", "CreationDate", "ExpirationDate"}], "deals": [<the site's rows: rest_name, date dd/MM/yyyy, time, deal_id, price, otl_price, order_type, is_active, …>], "txns": []}` — the charges since `start_date`, money in shekels.

### `POST /jobs/{id}/code`
`{"code": "123456"}` (4–8 digits) → `202 {"ok": true}`; `409 {"error": "not_waiting"}` when the job is not in `needs_code`; `400` for a bad code.

### `DELETE /jobs/{id}`
Cancels a running job (closes its browser) → `200 {"ok": true}`.

### `DELETE /profiles/{profile}`
Deletes that profile's folder (a removed source) → `200 {"ok": true}`; `409` while it has a running job.

## Running locally

```bash
cd server/addons/money/worker && PUPPETEER_SKIP_DOWNLOAD=1 npm ci     # uses the system Chromium
BANK_WORKER_TOKEN_FILE=/tmp/token BANK_WORKER_PROFILES=/tmp/profiles \
  CHROMIUM_PATH=/usr/bin/chromium TZ=Asia/Jerusalem node src/main.js
```
Debugging a site change: `BANK_WORKER_HEADFUL=1` shows the browser, `BANK_WORKER_DUMP_DIR=<dir>` saves a screenshot of a failed login, `DEBUG=israeli-bank-scrapers:*` turns on the library's log. Never in production: the screenshot and the library's log show account data.

`test_bank_worker.mjs` (in `bin/test`) covers the job table and the API without a browser; `test_bank_worker_psagot.mjs` the Psagot client against a fake server; `test_bank_worker_pluxee.mjs` the Pluxee sign-in and reads on a fake page.

## Image

`Dockerfile`: Debian's Chromium on `node:22-slim`, `TZ=Asia/Jerusalem`, user 1001 (Dana's, both write `/app/db/money`), `BANK_WORKER_NO_SANDBOX=1` (a pod has no user namespaces for Chromium's sandbox). It is the `money` addon's worker (`Worker(port=8090, url_env="MONEY_WORKER_URL")` in `../addon.py`; this folder is self-contained so it can move with the addon): `bin/deploy` builds it as `$DANA_IMAGE-money:<release>`, the same tag as Dana's image, and writes that image into the GitOps values (`docs/operations.md`, "Workers"), where the deployment's `initContainers` and `extraContainers` entries use it. The sidecar mounts only the `money/` folder of Dana's volume (subPath, at `/app/db/money`): it never sees Dana's database. An init container (the same image) creates that folder as uid 1001 first, since a subPath kubelet creates itself would belong to root. The sidecar runs without privilege escalation, with every capability dropped and the runtime's seccomp profile, and needs no env or secrets.
