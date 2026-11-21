# Money (Israel)

The Israeli banks, card company, investment house and meal card for Dana's money: the `money_il` addon (`addons/money_il/`, `needs=("money",)`). It is a **provider** of money's (docs/money.md, "Providers"): it registers its companies, the Bank of Israel's reference rates and the price checks in Israel, and the **bank-worker** sidecar (`addons/money_il/worker/`, its `README.md` is the HTTP contract) signs in to the sites for it. money runs everything around a sync (sources and their encrypted credentials, the schedule, sync runs and their states, the code broker, the ingest, the hooks, the page, the tools); money_il only reads a site and hands the accounts back in money's ingest format.

```
 money sync.py ──► Company.sync (money_il sync.py) ──► worker_client.py ──► bank-worker (127.0.0.1, a browser profile per source)
                         └─ needs_code ──► SyncJob.ask_code (money's code broker) ──► code back to the job
                   ◄── convert.py: the job's accounts in money's ingest format (+ a device key)
```

## The addon

- `addon.py`: `Addon("money_il", needs=("money",), worker=Worker(port=8090, url_env="MONEY_WORKER_URL"), llm_jobs={"money_market": …})`. Like money it has **no credentials of its own**: connecting it is the on/off switch, each login is a money source. Its Test (`POST /api/addons/money_il/test`) asks whether the worker answers `GET /health`.
- Without it a user can still have money (their own data, notification signals, the page), but cannot add or sync a login at its companies (403 `not_connected` with `addon: "money_il"`), and savings has no policy rate, no FX rates and no price checks.
- Turning it off (`on_change` → `money.sources.provider_changed`) removes the user's logins at its companies (credentials, device keys, pending code requests), stops their running syncs and deletes their browser profiles on the worker; their accounts and transactions stay in money, without a source. Disconnecting money deletes everything (docs/money.md).
- Money's migration `m0002_money_il.py` connected money_il for every user who had money and a source or an account at one of its companies, so existing logins kept syncing.

## Companies

`companies.py`, registered with `providers.register_company` when `addon.py` is imported:

| Company | Kind | Fields | SMS code | Keys |
|---|---|---|---|---|
| `hapoalim` (Bank Hapoalim) | bank | `userCode` "User code", `password` (secret) | sender `BHapoalim`, regex `קוד האימות הוא:?\s*(\d{4,8})` | hashed: `poalim:{hash}:{n}` (no stable id) |
| `visaCal` (Cal) | card | `username` "Username", `password` (secret) | — | `cal:{identifier}:{installment or 0}` |
| `psagot` (Psagot Trade) | investment | `username` "ID number or user", `password` (secret) | sender `Psagot`, regex `פסגות טרייד הוא:?\s*(\d{4,8})` (only on a password sign-in) | trades `psagot:<hash>:<n>` |
| `pluxee` (Pluxee, formerly Cibus) | meal | `phone` "Mobile number" | sender `1700701130` (and its other spellings), regex `(?<!\d)(\d{6})(?!\d)` (when the 24-hour session has ended) | meals `pluxee:<deal id>` |

Every company's `sync` is `sync.sync` and its `forget` is `sync.forget` (delete the browser profiles). Pluxee's `card_line_words` (סיבוס, פלאקסי, pluxee, cibus) find the card line that pays a cycle's overflow (docs/money.md, "Meal cards").

## Worker

`worker_client.py` is the client of the sidecar. It listens on the pod's localhost, `MONEY_WORKER_URL` (default `http://127.0.0.1:8090`), and checks a bearer token read from `MONEY_DIR/worker-token` (`core/paths.py`: `<data dir>/money/`), which Dana creates with `secrets.token_urlsafe(32)` and mode 0600 when it is missing (both containers run as uid 1001; the worker mounts only the volume's `money/` folder and keeps its browser profiles in `money/profiles/`, `addons/money_il/worker/README.md`). Calls: `start_job` (`POST /jobs`), `get_job` (long poll, `wait` ≤ 30 s, `seen`), `post_code`, `cancel_job`, `delete_profile`, `health`. Request bodies carry credentials and codes: nothing logs a body. A worker that cannot be reached is an ordinary sync failure (`GENERIC`, "bank worker not reachable").

**Running it.** `bin/deploy` builds `addons/money_il/worker/` as `<DANA_IMAGE>-money_il` with Dana's tag (GitOps values `workers.money_il.image`; docs/operations.md, "Workers"). With compose: `docker compose --profile money_il up -d` adds `money-il-worker` (image `dana-money_il`). The worker's own tests (`worker/test/*.mjs`) run in `bin/test`.

## Sync

`sync.sync(job)` is the `Company.sync` of every company (money's `sync.py` calls it; docs/money.md, "Sync"):

1. At most 2 jobs run at once (the worker's browser limit; a sync waiting for a slot counts against money's 15-minute timeout). `start_job` for the source: its browser profile is the source id; a profile still busy from before a restart gets its old job cancelled and is started again (up to 10 tries a second apart).
2. Follow the job with long polls (25 s). `needs_code` → `job.ask_code()` (money's code broker); the code goes to the worker; a bank asking again after a code (a wrong code) is asked at most 3 times per sync. Once a job with a code moves on, `job.signed_in()` (the quiet "Signed in to Bank Hapoalim" notice).
3. `done` → `convert.accounts(company, accounts)` (below) and the job's device key (Psagot's, when complete) or `device_dropped`. `failed` → `providers.SyncError` with the worker's `error_type`; `cancelled` → `GENERIC`.
4. A cancelled sync (money's timeout, a removed source) cancels its job.

Every sync is a full sign-in: the library keeps no session between runs. The saved browser profile is what keeps Hapoalim from asking for a code each time (a trusted device), so a normal day is two sign-ins per source plus, for a card, one per payment signal (at most 4). The worker's error text can quote the site: money scrubs it before storing it; the worker itself drops URL query strings and masks the credentials.

## Convert

`convert.py` turns a job's accounts into money's ingest format (docs/money.md, "Ingest format"):

- **Hapoalim, Cal**: the `israeli-bank-scrapers` accounts as they are, except Cal installments (`type == "installments"`): the library moves `date` to the payment's month, so `purchaseDate` (the purchase's local time) comes from `rawTransaction.trnPurchaseDate`.
- **Psagot** → the investment shape (below).
- **Pluxee** → the meal shape (below).

Israeli specifics money still applies to every row (docs/money.md, "Ingest"): the Israeli ID checksum in the scrub, Hapoalim's transfer note ("לטובת: <payee>. עבור: <purpose>."), Cal's own categories and Hapoalim's activity codes in classification.

## Investment accounts (Psagot Trade)

Psagot Trade (`trade.psagot.co.il`, a Flutter web app on a TradeOne backend at `trade1.psagot.co.il`) is read over plain HTTPS by the worker (`worker/src/psagot.js`; no browser), not through a scraper library. It is **read-only by construction**: the client refuses any path outside its allow-list (`ALLOWED`: sign-in, the quick-view device, accounts, balances, transactions, daily values, the market table for security names), so no order, password change or alert call can be made, whatever the caller asks.

- **Signing in.** The first sync sends the password once (`POST /V2/json2/login`, method `2FA`); Psagot texts a code, which reaches the job through the usual code broker (the phone reads the SMS by sender `Psagot`). Signed in, the worker registers a **quick-view device** named "Dana" (Psagot's own "צפייה מהירה": view-only, no password, no SMS) and hands Dana its RSA private key with the job's result (`device {user, udid, key}`). money keeps it encrypted with `CREDENTIALS_KEY` (`money_sources.device`) and hands it to every later job (`SyncJob.device`); those sign in by signing Psagot's challenge with the key: no password and no code go out. The user sees the device in their Psagot settings and can remove it there.
- **A refused device** (removed by the user, expired): the job drops it (`device_dropped`, money clears it), signs in with the password and the SMS code once more and registers a new one in the same job. A device is used only for the username it was registered for: after new credentials for another user, the job signs in with the password and registers a new one.
- **Refusals** map to the usual error types: a wrong password `INVALID_PASSWORD`, a wrong code `CODE_REJECTED`, an expired password `CHANGE_PASSWORD`, a blocked user `ACCOUNT_BLOCKED`, anything else at the login `LOGIN_UNKNOWN` (each stops the source: never retried on its own). A device sign-in that fails sends no password, so it never counts against the login.
- **What is read**, per account: the balances view with real-time prices (`calcMode=RT`, as the app asks; without it the day's values are the previous close, so today's change reads 0; closing prices when refused) — value, cash, today's change from the morning value, profit over cost, the holdings (security number, units, value in ₪, profit, its own currency; **no names**: the positions' `Key` and the meta's `hebName` come back empty, in the app too); the holdings' names from the market table (`GET /V2/json2/market/table/simple?securities=&fields=HebName,EngName,EngSymbol,HebSymbol`, optional); the activity of the sync window and the account's value per day (a year on the first sync; newest first). The account holder's name is left out.
- **Convert** (`convert.investment_account`): the summary → `details`; the holdings (`holding_row`: security numbers lose their padding — the activity writes `01100007`, the holdings `1100007` —, a name from the market table); the activity → trades keyed `psagot:<hash>:<n>` with a kind from the site's Hebrew words (`action_kind`: `buy`, `sell`, `dividend`, `interest`, `deposit` ("התקבל מח.בסניף"), `withdrawal`, `tax`, `split`, `redemption`, `benefit`, `expiry`, `other`); the values per day. Each trade also comes as the cash account's side, twice (`נע-קניה…` on `עו"ש מט"י`, the trade day and the value day, its whole amount in the tax field): those rows are dropped (`is_cash_leg`). Rows on the account's cash (a deposit, tax, interest: `עו"ש`, numbers `99xxxxxx`) keep no security or name.
- **Units** (checked against a real account): the site's money figures are shekels (`OnlineValue`, `OnlineVL` / `OnlineNisVL`, `AveragePriceProfitLossNis`, a trade's `VL` / `NisVL` / `Commission`) → agorot; Tel Aviv prices are agorot (`LastRate`, `BaseRate`, `AveragePrice`, a trade's `Price`, kept as given). A holding's `price` is value ÷ units in its own currency (a decimal: ₪1.25, ₪310.00) and its `cost` value − profit (equal to the site's `AveragePriceVL`). Commissions are negative. The daily values carry no return (`Yield` is 0): deposits and withdrawals move the value, so money's Investments view and tool give the change without them (`flows`).

## Meal card (Pluxee)

Pluxee (`consumers.pluxee.co.il`, formerly Cibus: an Angular app over Cibus's JSON API at `api.consumers.pluxee.co.il/api/main.py`) is read by the worker's own code on the browser (`worker/src/pluxee.js`), not a scraper library. Sign-in needs the browser: Google's invisible reCAPTCHA v3 scores both the SMS request and the code (`api.capir.pluxee.co.il/auth/sendOTP`, `/auth/authToken`) and refuses a browser that says it is automated, so this site's Chromium runs without the automation flags. Once signed in, the worker reads from inside the page through an allow-list of three calls (`prx_get_budgets`, `get_user_cards`, `prx_user_deals`): no order, transfer or card change can be sent.

- **Signing in.** The phone number, then the SMS code through the usual code broker (sender `1700701130`, 6 digits). The site keeps the session in a `token` cookie for 24 hours in the source's browser profile, so a sync within that time needs no SMS; at the regular two slots a day that is about one SMS a day, read by the phone. "Remember this device" applies only to the site's password + SMS sign-in, not to the code-only one. Refusals: reCAPTCHA (`GENERIC`, before the SMS goes out, so it is retried like any failure), an unknown phone `INVALID_PASSWORD`, a wrong code `CODE_REJECTED`, blocked `ACCOUNT_BLOCKED`.
- **What is read**: the current budget cycle (`CreatioBudget` the amount, `CurrBudget` what is left, `CreationDate` / `ExpirationDate`), the active card's last 4 digits (the account's number), and every charge since the job's start date: the place, date and time, the deal id, `price` (the whole meal), `otl_price` (what went on the user's own card once the budget ran out; "שולם באשראי"), `order_type` (0 at the restaurant / pickup, 1 delivery, 2 a voucher), `is_active` (0 = cancelled).
- **Convert** (`convert.meal_account`): the budget → `{amount, left, start, end}` in agorot and ISO dates; each charge (`deal_row`) → `pluxee:<deal id>` with `total`, `card_paid` (`otl_price`) and `budget_paid` (the rest) in agorot, its time in the server's zone and its order type.
- **The card line**: one line a month for a whole cycle ("סיבוס פלאקסי" on Cal on the 10th, about seven weeks after the cycle ended), found by money with the company's `card_line_words` (checked against a real account: every line matched to the agora).

## Bank of Israel

`reference.py`, registered as money's `Rates`. It reads the SDMX CSV API: no key, no user data. `policy_rate(today)` gives `{rate, date, year_ago, year_ago_date}`. `fx_rates(currency, start, end)` gives `{date: rate}` for currencies quoted per unit (`FX_CURRENCIES`; money fetches all of them whenever the user spent abroad, so the requests never say which). Answers are cached for a day in memory and in `MONEY_DIR/boi-cache.json`. The fetch is automatic, as part of computing savings. A failed fetch is logged and not retried for 15 minutes; the items that need the data are left out.

## Price checks

`market.py`, registered as money's `PriceChecks`: the "Check prices" button of a saving card looks up current offers on the web. A check runs **only when the user asks** (`market.start`, called from money's API); nothing runs one in the background.

| Topic | Params (strict: unknown keys refused) | Item (amounts in agorot) | Order |
|---|---|---|---|
| `telecom` | `service` (`mobile` \| `home_internet` \| `triple` \| `mobile_and_internet`), `lines` (1–10 or null), `current_price` (whole ₪ a month, 1–2000) | `{provider, plan, price (regular, a month), promo_price \| null, promo_months \| null, avg_12, conditions, source}` | `avg_12` |
| `electricity` | — | `{supplier, plan, discount_pct, hours, days, smart_meter, source}` | discount, biggest first |
| `fx_card` | — | `{name, kind (card \| wallet \| bank), fx_fee_pct, monthly_fee, conditions, source}` | `fx_fee_pct` |
| `arnona` | `city` (Hebrew letters, spaces, `-` and quotes, ≤ 40), `year` (this year ± 1) | exactly one: `{found, discount_pct \| null, deadline (YYYY-MM-DD) \| null, method, source \| null}` | — |

At most 8 items; `source` is `{title, url}`. `avg_12` is computed in code: (promo price × min(promo months, 12) + price × the rest of the 12 months) / 12, so offers compare by what the first year costs a month.

**What is sent.** The search prompt is a fixed template per topic filled with that topic's params and the current month, nothing else: no names, transactions, merchants, account numbers, or amounts other than `current_price`. The second call gets the search's text and its pages, again without any user data. `test_money_il_market.py` asserts both.

**Pipeline** (`_find`, run as an asyncio task bound to the user, at most `CHECK_TIMEOUT_SECONDS` = 4 min):

1. `llm.search(prompt, purpose="money_market")` asks the `search` model (`SEARCH_MODEL` → `MODEL`, `docs/configuration.md`) with LiteLLM's `web_search_options`. It returns the answer and the pages the provider grounded it on: Gemini's grounding chunks, OpenAI's `url_citation` annotations, or Anthropic's citations. Gemini's grounding supports add a `[n]` after each passage a page backs. With no pages it raises `llm.NoSearch` (the model did not search).
2. Google's grounding redirect links (`vertexaisearch.cloud.google.com/grounding-api-redirect/…`) are resolved with an `httpx` HEAD that follows redirects (5 s). When the HEAD fails, the redirect link is kept. Only http(s) pages can be cited.
3. `llm.complete_json(job="money_market", purpose="money_market")` extracts the topic's schema from the text and the numbered list of usable pages. Each item gives the number of its page.
4. Validation in code drops:
   - an item whose page number is not one of the search's usable pages;
   - a telecom price outside ₪5–₪500 a month (a promotion must cost less than the regular price and last 1–36 months, or it is dropped and the offer kept);
   - an electricity discount outside (0, 30] %;
   - an FX fee outside 0–5 %, a monthly fee over ₪100, or an unknown kind;
   - a repeat: the same provider and plan, the same supplier, plan and hours, or the same card name. The best of them stays.

   For arnona, a "found" without a usable page reads as not found. A discount outside (0, 30] % or a malformed deadline becomes null.

**Outcomes.** A check either ends `ok` with its items, or `failed` with a plain-words `error`: the model did not search; no offer with a source; the search or extraction failed; the check took too long. A telecom, electricity or FX check with no valid item counts as failed. An arnona "not found" is an `ok` result.

**Rate limits.** `start` raises (money's API turns them into its answers):
- `ValueError` for an unknown topic or bad params (400);
- `providers.PriceCheckUnavailable` when the `search` model has no key, or LiteLLM's model map says it has no web search (`llm.can_search`) (503);
- `providers.PriceCheckRunning` while the same check runs, or its row says `checking` and is less than 5 minutes old (409);
- `providers.PriceCheckTooSoon(retry_after)` when the same (user, topic, params) started less than 6 hours ago, or less than 5 minutes ago if it failed (429).

**Storage**: money's table `money_market_checks` keeps one row per `(user_id, topic, params_key)`, where `params_key` is the canonical JSON of the params. Its columns: `params`, `state` (`checking` / `ok` / `failed`), `items` and `sources` (JSON), `model`, `error`, `started_at` and `checked_at`. A new check reuses the row. `items`, `sources` and `checked_at` stay those of the last check that succeeded: they remain visible while a new check runs and after one fails.

`market.latest(user, topic, params)` → `{topic, params, state, started_at, checked_at, stale (checked_at older than 45 days), items, sources [{title, url}], error}`, or None. A `checking` row older than 5 minutes was cut off by a restart (no check runs that long) and reads as `failed`. Every read is scoped to the user. Disconnecting money deletes the rows (`store.delete_all`).

## Tests

`addons/money_il/tests/`: `test_money_il_sync.py` (syncs through a fake worker end to end, the code broker, the API per user, a user with money only, turning money_il off, `convert`), `test_money_il_portfolio.py` (Psagot), `test_money_il_meals.py` (Pluxee), `test_money_il_market.py` (price checks); the worker's `.mjs` tests in `worker/test/`. money's own tests use a made-up provider (`addons/money/tests/test_money_providers.py`).
