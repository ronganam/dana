# Money

Bank and credit-card transactions in Dana, investment accounts and the meal card from work: the `money` addon (`addons/money/`). Dana signs in to the user's bank, card, trading and meal-card sites through the **bank-worker** sidecar (`addons/money/worker/`, its `README.md` is the HTTP contract), stores accounts, transactions, holdings and meals per user, and keeps them current on a schedule. It supports Bank Hapoalim, Cal, Psagot Trade (read-only: "Investment accounts" below) and Pluxee (Cibus; read-only: "Meal cards" below). From the same data it finds ways to save and spending to watch ("Savings" below).

```
 schedule (2×/day) · signal · "Sync now" · first sync ──► sync.py (one source)
     ──► worker_client.py ──► bank-worker sidecar (127.0.0.1, a browser profile per source)
           └─ needs_code ──► otp.py ──► push otp_request + Telegram ──► code back via API / Telegram
     ◄── accounts + transactions
 ingest.py: scrub → keys → upsert (rules) ──► money_* tables ──► hooks: signals.reconcile → classify (background) ──► API
 phone: Wallet notification (Inbox observer) · card app push (POST /api/money/signals) ──► signals.py ──► provisional row + request_soon
```

## The addon and its sources

- `money` is an ordinary addon (`addon.py`) with **no credentials of its own**: connecting it is the on/off switch (Addons page). It is offered in realtime voice. Its `check` (the Addons page's Test) asks whether the worker answers `GET /health`.
- Each bank or card login is a **source**: a row of `money_sources` (`sources.py`). A user may have several (two cards, a bank and a card). The login fields come from `companies.py`:

  | Company | Kind | Fields | SMS code |
  |---|---|---|---|
  | `hapoalim` (Bank Hapoalim) | bank | `userCode` "User code", `password` (secret) | sender `BHapoalim`, regex `קוד האימות הוא:?\s*(\d{4,8})` |
  | `visaCal` (Cal) | card | `username` "Username", `password` (secret) | — |
  | `psagot` (Psagot Trade) | investment | `username` "ID number or user", `password` (secret) | sender `Psagot`, regex `פסגות טרייד הוא:?\s*(\d{4,8})` (only on the first sign-in: "Investment accounts") |
  | `pluxee` (Pluxee, formerly Cibus) | meal | `phone` "Mobile number" | sender `1700701130` (and its other spellings), regex `(?<!\d)(\d{6})(?!\d)` (when the 24-hour session has ended: "Meal cards") |

- Credentials are checked against the company's fields (all required, no unknown names), stored only as a Fernet token (`core/addons/crypto.py`, `CREDENTIALS_KEY`) and decrypted only by `sync.py` to start a worker job. No API response, payload or log line carries them (`credentials_set` says whether they exist).
- Adding a source starts its first sync at once (`trigger="connect"`). New credentials (`sources.update`) clear any login problem: the source becomes `new`, its failures reset, and it syncs at once. Removing a source (`sources.remove`) stops its running sync, closes its code request, deletes its browser profile on the worker (best-effort) and, if asked, its accounts and their transactions; otherwise the accounts stay with no source.
- Disconnecting `money` deletes all of that user's money rows and the Money section of their weekly reviews, and asks the worker to delete their profiles (`sources.on_change` → `store.delete_all`). A review's Money section is shown only to a user who has `money`.

Source states: `new` → `syncing` → (`needs_code` → `syncing`) → `ok` | `failing` | `needs_attention`. `state_reason` says why in plain words, `error_type` is the worker's.

## Storage

Migration `m0025_money.py` (its docstring describes each column). All tables are per user (`user_id`, cascade on user delete); ids are uuid4 hex; times ISO UTC; amounts integer agorot, signed (money out is negative).

- `money_sources` — logins (above), with sync bookkeeping: `failures`, `last_sync_at`, `last_ok_at`, `next_sync_at`, the signal-sync count of a local day, `last_manual_at`.
- `money_accounts` — a bank account or a card, unique per `(user, company, number)`: a bank's number is the full account number (shown masked to its last 3 digits), a card's the last 4 Cal gives. `balance` (bank), `next_charge` / `next_charge_date` (a card's next bill), `billed_from_account_id`, `hidden`.
- `money_transactions` — unique per `(account, ext_key)`; `source` `scrape` | `notification`, `status` `provisional` | `pending` | `completed` | `unmatched`; `purchase_date` / `charge_date` (local dates), `occurred_at` (an aware local time when known); `description` (scrubbed), `merchant` (display name), `merchant_key`, `memo`; `category` + `category_source` (`bank` | `memory` | `model` | `rule` | `user`), `flow`; installment n / total; `note`; `one_off` / `yearly` (the user's marks, `m0027_money_dashboards.py`: NULL = Dana decides); `raw` (the scrubbed scraper row, JSON). Full-text search: `money_transactions_fts` (merchant, description, memo, note), kept in step by triggers.
- `money_rules` (merchant key → category / display name / flow), `money_aliases` (learned spellings of a merchant), `money_syncs` (one row per sync run), `money_code_requests` (the code itself is never stored).
- Investment accounts (`m0028_money_portfolio.py`): `money_accounts.kind = 'investment'` with its summary in `details` (JSON) and its value in `balance`; `money_sources.device` (the encrypted quick-view key); `money_holdings` (one row per security, replaced each sync), `money_account_values` (the account's value per day) and `money_trades` (the account's activity, unique per `(account, ext_key)`). "Investment accounts" below.
- Meal cards (`m0031_money_meals.py`): `money_accounts.kind = 'meal'` with what is left of the budget in `balance` and the cycle in `details` (JSON); `money_meals` (one row per charge, unique per `(account, ext_key)`). "Meal cards" below.
- `money_savings` (`m0029_money_savings.py`): one row per item Dana found, unique per `(user, key)`. It holds the status and when it was set, `snooze_until`, `first_seen_at`, `answers` (JSON: question answers and `sub:<line>` choices), `target` (agorot a month), `baseline` (JSON), `snapshot` (the last detected payload) and `done_at`. "Savings" below.

## Worker

`worker_client.py` is the client of the sidecar. It listens on the pod's localhost, `MONEY_WORKER_URL` (default `http://127.0.0.1:8090`), and checks a bearer token read from `MONEY_DIR/worker-token` (`core/paths.py`: `<data dir>/money/`), which Dana creates with `secrets.token_urlsafe(32)` and mode 0600 when it is missing (both containers run as uid 1001; the worker mounts only the volume's `money/` folder and keeps its browser profiles in `money/profiles/`, `addons/money/worker/README.md`). Calls: `start_job` (`POST /jobs`; a profile still busy from before a restart gets its old job cancelled and is started again), `get_job` (long poll, `wait` ≤ 30 s, `seen`), `post_code`, `cancel_job`, `delete_profile`, `health`. Request bodies carry credentials and codes: nothing logs a body. A worker that cannot be reached is an ordinary sync failure (`GENERIC`, "bank worker not reachable").

## Sync

`sync.start(user, source_id, trigger)` (`trigger`: `schedule` | `signal` | `manual` | `connect` | `agent`) writes a `money_syncs` row, marks the source `syncing` and runs in the background as the user (`users.acting_as`); one sync per source at a time (starting another returns the running one's id). `sync.run` does the same and waits for the end. Steps:

1. Decrypt the credentials; start a job from the last good sync minus 10 days (the first sync: one year back). At most 2 jobs run at once (the worker's browser limit); one sync gives up after 15 minutes.
2. Follow the job with long polls. `needs_code` → the code broker (below); the code goes to the worker, and once the job moves on the phone gets a quiet "Signed in to Bank Hapoalim" notice.
3. `done` → `ingest.apply` in a thread → every hook (`after_sync.on_transactions`) → the source is `ok`, `failures = 0`, `last_ok_at`, `next_sync_at` = its next slot (or a sooner time a signal set while it ran).
4. `failed`:
   - `INVALID_PASSWORD`, `CHANGE_PASSWORD`, `ACCOUNT_BLOCKED`, `CODE_REJECTED` (the bank refused the code), `LOGIN_UNKNOWN` (the password went out and the site neither let the worker in nor said why: an error page the worker does not know, a stall, a crash mid-login — it may have been a refused password) → `needs_attention` with what to do ("Cal wants a new password: change it on Cal's site, then update it here"), `next_sync_at` empty: Dana never signs in again on its own (not the loop, a payment signal or the agent), because a repeated wrong password is what locks a login. New credentials restart it; after `CODE_REJECTED` and `LOGIN_UNKNOWN` the user's own "Sync now" (trigger `manual`) also signs in once more (`store.HAND_RETRY_ERRORS`; the source payload's `can_sync`).
   - No code in time (`CODE_TIMEOUT`) → `failing`, "no sign-in code arrived", next sync at the next regular slot (no quick retry, so no stream of SMS codes); "Sync now" is allowed. A second one in a row → `needs_attention` (each try sends the user an SMS) until the user's "Sync now".
   - Anything else → `failing` with the reason; retried after 30 minutes, then 2 hours, then at the regular slots (so a source that keeps failing signs in no more often than a healthy one: payment signals never pull a failing source).

   The worker's error text can quote the site, so it is scrubbed (`ingest.scrub`: ID, card and account numbers) before it is stored as `state_reason` or the run's `error`; the worker itself drops URL query strings and masks the credentials.

Each finished sync writes the health mark `money_sync:<user id>`. After a restart, the `recover` job (`hooks.py`) ends what the previous process left mid-sync ("interrupted by a restart": the run failed, the code request cancelled, the source `failing`); the interruption counts as a failure and is retried like one (30 minutes, 2 hours, then the slots), so a restart loop cannot sign in over and over.

## Sign-in codes

`otp.py`, for Hapoalim's SMS code on a new browser profile:

1. On `needs_code`: a `money_code_requests` row (expires in 4 minutes; an older pending one of the source is cancelled), the source `needs_code`, a push `otp_request {request_id}` to the user's phone (only with `phone`; TTL 4 minutes in `push.TTL`) and a Telegram message: "Bank Hapoalim sent you a sign-in code for Dana. Reply here with the code within 4 minutes."
2. The phone fetches `GET /api/money/code-requests/{id}` (`senders`, `code_regex`: a new bank needs no app update), reads the SMS and posts the code. The Money page can post it too, and a Telegram message that is only 4–8 digits, while the user has a pending request, is taken as the code (`core/telegram_bot.py` → `otp.telegram_reply`): answered "Thanks, signing in to Bank Hapoalim…", never logged, never shown to the agent or kept in history. Such a message with no request pending, within 15 minutes of one (`otp.LATE_CODE_MINUTES`), is still kept from the agent: "Dana already has the code" when the phone was quicker, "That code came too late" when the request expired.
3. `otp.submit(user, request_id, code, via)` checks the request is the user's, pending, unexpired and the code `^\d{4,8}$`, marks it `done` with `answered_by` (`phone` | `web` | `telegram`) and wakes the waiting sync through an in-memory future. The code exists only in memory until the sync posts it to the worker.
4. Nothing within 4 minutes: the request is `expired`, the worker job cancelled, the source `failing` until its next regular slot; a second miss in a row stops it (`needs_attention`) until the user's "Sync now" (above).

## Investment accounts

Psagot Trade (`trade.psagot.co.il`, a Flutter web app on a TradeOne backend at `trade1.psagot.co.il`) is read over plain HTTPS by the worker (`addons/money/worker/src/psagot.js`; no browser), not through a scraper library. It is **read-only by construction**: the client refuses any path outside its allow-list (`ALLOWED`: sign-in, the quick-view device, accounts, balances, transactions, daily values, the market table for security names), so no order, password change or alert call can be made, whatever the caller asks.

- **Signing in.** The first sync sends the password once (`POST /V2/json2/login`, method `2FA`); Psagot texts a code, which reaches the job through the usual code broker (the phone reads the SMS by sender `Psagot`). Signed in, the worker registers a **quick-view device** named "Dana" (Psagot's own "צפייה מהירה": view-only, no password, no SMS) and hands Dana its RSA private key with the job's result (`device {user, udid, key}`). Dana stores it encrypted with `CREDENTIALS_KEY` (`money_sources.device`, `store.set_device`) and sends it with every later job; those sign in by signing Psagot's challenge with the key: no password and no code go out. The user sees the device in their Psagot settings and can remove it there.
- **A refused device** (removed by the user, expired): the job drops it (`device_dropped`, Dana clears it), signs in with the password and the SMS code once more and registers a new one in the same job. A device is used only for the username it was registered for: after new credentials for another user, the job signs in with the password and registers a new one.
- **Refusals** map to the usual error types: a wrong password `INVALID_PASSWORD`, a wrong code `CODE_REJECTED`, an expired password `CHANGE_PASSWORD`, a blocked user `ACCOUNT_BLOCKED`, anything else at the login `LOGIN_UNKNOWN` (each stops the source: never retried on its own). A device sign-in that fails sends no password, so it never counts against the login.
- **What is read**, per account: the balances view with real-time prices (`calcMode=RT`, as the app asks; without it the day's values are the previous close, so today's change reads 0; closing prices when refused) — value, cash, today's change from the morning value, profit over cost, the holdings (security number, units, value in ₪, profit, its own currency; **no names**: the positions' `Key` and the meta's `hebName` come back empty, in the app too); the holdings' names from the market table (`GET /V2/json2/market/table/simple?securities=&fields=HebName,EngName,EngSymbol,HebSymbol`, optional); the activity of the sync window and the account's value per day (a year on the first sync; newest first). The account holder's name is left out; every raw row is scrubbed like a bank row.
- **Ingest** (`portfolio.store_account`, from `ingest.apply` for a company of kind `investment`): the account upserted (masked like a bank's, last 3 digits), its activity upserted by `ext_key` (`psagot:<hash>:<n>`) with a kind from the site's Hebrew words (`portfolio.action_kind`: `buy`, `sell`, `dividend`, `interest`, `deposit` ("התקבל מח.בסניף"), `withdrawal`, `tax`, `split`, `redemption`, `benefit`, `expiry`, `other`), its holdings replaced (a name from the market table, else from the security's latest activity row, else none: "Security #<number>"), its values per day upserted (today's from the summary). Security numbers lose their padding (the activity writes `01100007`, the holdings `1100007`). Each trade also comes as the cash account's side, twice (`נע-קניה…` on `עו"ש מט"י`, the trade day and the value day, its whole amount in the tax field): those rows are dropped (`portfolio.is_cash_leg`). Rows on the account's cash (a deposit, tax, interest: `עו"ש`, numbers `99xxxxxx`) keep no security or name. An investment account **never makes `money_transactions`**: spending, income and the dashboards are the bank's and the cards'; a transfer from the bank to the broker stays "set aside" there.
- **Units** (checked against a real account): the site's money figures are shekels (`OnlineValue`, `OnlineVL` / `OnlineNisVL`, `AveragePriceProfitLossNis`, a trade's `VL` / `NisVL` / `Commission`) → agorot; Tel Aviv prices are agorot (`LastRate`, `BaseRate`, `AveragePrice`, a trade's `Price`, kept as given). A holding's `price` is value ÷ units in its own currency (a decimal: ₪1.25, ₪310.00) and its `cost` value − profit (equal to the site's `AveragePriceVL`). Commissions are negative. The daily values carry no return (`Yield` is 0): deposits and withdrawals move the value, so the Investments view and the tool give the change without them (`flows`).

## Meal cards

Pluxee (`consumers.pluxee.co.il`, formerly Cibus: an Angular app over Cibus's JSON API at `api.consumers.pluxee.co.il/api/main.py`) is read by the worker's own code on the browser (`addons/money/worker/src/pluxee.js`), not a scraper library. Sign-in needs the browser: Google's invisible reCAPTCHA v3 scores both the SMS request and the code (`api.capir.pluxee.co.il/auth/sendOTP`, `/auth/authToken`) and refuses a browser that says it is automated, so this site's Chromium runs without the automation flags. Once signed in, the worker reads from inside the page through an allow-list of three calls (`prx_get_budgets`, `get_user_cards`, `prx_user_deals`): no order, transfer or card change can be sent.

- **Signing in.** The phone number, then the SMS code through the usual code broker (sender `1700701130`, 6 digits). The site keeps the session in a `token` cookie for 24 hours in the source's browser profile, so a sync within that time needs no SMS; at the regular two slots a day that is about one SMS a day, read by the phone. "Remember this device" applies only to the site's password + SMS sign-in, not to the code-only one. Refusals: reCAPTCHA (`GENERIC`, before the SMS goes out, so it is retried like any failure), an unknown phone `INVALID_PASSWORD`, a wrong code `CODE_REJECTED`, blocked `ACCOUNT_BLOCKED`.
- **What is read**: the current budget cycle (`CreatioBudget` the amount, `CurrBudget` what is left, `CreationDate` / `ExpirationDate`), the active card's last 4 digits (the account's number), and every charge since the job's start date: the place, date and time, the deal id, `price` (the whole meal), `otl_price` (what went on the user's own card once the budget ran out; "שולם באשראי"), `order_type` (0 at the restaurant / pickup, 1 delivery, 2 a voucher), `is_active` (0 = cancelled).
- **Ingest** (`meals.store_account`, from `ingest.apply` for a company of kind `meal`): the account upserted (`balance` = what is left, `details` = `{amount, left, start, end}`), each charge upserted by `pluxee:<deal id>` into `money_meals` with `total`, `card_paid` (`otl_price`) and `budget_paid` (the rest) in agorot, the place scrubbed. A meal card **never makes `money_transactions`**: the employer's budget is not the user's spending.
- **The card line.** What the budget did not cover is not charged per meal: the user's card shows one line a month for a whole cycle ("סיבוס פלאקסי" on Cal on the 10th, about seven weeks after the cycle ended), and that line is ordinary spending. `meals.billed_lines` finds each cycle's line: a card or bank row whose merchant key holds סיבוס / פלאקסי / pluxee / cibus, of exactly the cycle's `card_paid`, dated after the cycle ended and within 75 days, each line used once (checked against a real account: every line matched to the agora). `meals.card_line(user, txn_id)` gives the transaction detail's `meal_cycle`.
- **Cycles** run from the budget's day of the month (the 22nd: 22 Oct – 21 Nov); past cycles take the current one's day. The current cycle's pace: `per_day_left` (what is left ÷ the days left) and `runs_out` (the day the budget ends at the cycle's pace so far; today when nothing is left; None when it lasts).

## Schedule

`schedule.py` + the job `start_due_syncs` (`hooks.py`, every minute) look at every user with `money`, as that user, and starts each source whose `next_sync_at` has come, unless it is `syncing`, `needs_code` or `needs_attention`.

- Regular slots: `MONEY_SYNC_TIMES` (default `07:00,19:00`, local time) moved by a stable per-source jitter within ±20 minutes (`slots.py`).
- `request_soon(user, source_id, minutes=30, reason="signal")` pulls `next_sync_at` earlier (an earlier time already set is kept): at most 4 times per source per local day, never in 00:00–06:00 (moved to 06:00 + the jitter), never for a `failing` or `needs_attention` source. The loop then starts it as a `signal` sync.
- `sync_now(user, source_id, trigger="manual")`: "Sync now" (the page, the agent). `manual` / `agent` syncs are refused within 10 minutes of the last one (`TooSoon.retry_after`, seconds) and for `needs_attention` (`NeedsAttention.reason`; a `manual` one is allowed after `CODE_REJECTED`, `LOGIN_UNKNOWN` or two missed codes: `store.hand_sync_allowed`); a running sync's id is returned as is.

Every sync is a full sign-in: the library keeps no session between runs. The saved browser profile is what keeps Hapoalim from asking for a code each time (a trusted device), so a normal day is two sign-ins per source plus, for a card, one per payment signal (at most 4).

## Ingest

`ingest.apply(user, source, accounts)` stores one sync's result in one transaction and returns `{"new": [ids], "changed": [ids], "accounts": [ids]}`.

- **Accounts**: upserted by `(user, company, number)` (`accountNumber`), claimed by the syncing source. A bank's `balance` → `balance` (agorot). Cal's `balance` is the next card bill (negative) → `next_charge` (positive agorot), `next_charge_date` = the first `charge_date` from today among its rows. An account with no transactions and no balance is still stored.
- **Amounts**: `chargedAmount` × 100, signed; `originalAmount` / `originalCurrency` kept (`₪` → ILS, `$` → USD, `€` → EUR, others as given).
- **Dates**: the scrapers give UTC instants of local midnight → local dates in the server's zone (`core.clock`). Cal's `date` carries the real time of a purchase → `occurred_at` when it is not local midnight. For Cal installments (`type == "installments"`) the library moves `date` to the payment's month, so the purchase date and time come from `rawTransaction.trnPurchaseDate`; `installments.number` / `total` are kept.
- **Keys** (`ext_key`): Cal with an identifier → `cal:{identifier}:{installment number or 0}` (payments of one purchase share the identifier); Hapoalim has no stable id → `poalim:{sha1(purchase_date|charge_date|amount|description|memo)[:16]}:{n}`, `n` counting identical tuples in the pull in order (two identical transfers on one day stay two rows); any other row → `x:{same hash}:{n}`. The hash is over scrubbed text.
- **Pending rows**: every sync deletes the account's scraped `pending` rows that are not in the new pull (provisional rows from notifications are left alone). Completed rows upsert by key; when a scraped field changed (amount, dates, status, text, installments) the row is updated and reported as `changed`. Category, merchant, flow and note are never overwritten by a pull.
- **Text**: `description` and `memo` as the bank wrote them, scrubbed; `merchant` starts as the description (classification replaces it with a clean name); `merchant_key(text)` = lower case, quotes dropped, other punctuation as spaces, number-only tokens (branch numbers) removed, spaces collapsed.
- **Transfers the user sent**: Hapoalim's note "לטובת: <payee>. עבור: <purpose>." (`ingest.transfer_payee(memo)` → `(payee, purpose)`, typed by the user in the bank's app) makes the payee the row's merchant key and, without its legal suffix (`clean_payee`: "בית ההשקעות הראשון בע"מ" → "בית ההשקעות הראשון"), its display name: the merchant memory and "always for this payee" rules then work per payee, not for the bank's generic "העב' לאחר-נייד". The purpose stays in the memo.
- **Scrub** (description, memo and every string in `raw`, plus integers in `raw`): 9-digit numbers that pass the Israeli ID checksum → `[id]` (a salary memo carries the user's ID), 16-digit card numbers and `NN-NNN-NNNN…` account numbers → `••••` + their last 3 digits. Names stay.
- **Category**: none from the bank here (Cal's own category text stays in `raw` for classification); a `money_rules` row for the merchant key sets `category` (`category_source = 'rule'`) and, when it has them, the display name and flow.

`after_sync.on_transactions(fn)` (`after_sync.py`) registers `fn(user, source_id, new_ids, changed_ids)` (sync or async); `sync.py` calls every hook after each ingest, as the user, logging and swallowing errors.

## Categories

`categories.py`: Dana's own two-level list. Groups → categories:

| Group | Categories |
|---|---|
| `food` | `groceries`, `restaurants`, `delivery` |
| `housing` | `housing` (rent, mortgage), `utilities` (electricity, water, gas, municipality), `home` (furniture, home goods, repairs) |
| `transport` | `fuel`, `parking` (and tolls), `public_transport`, `car` (insurance, service, licence) |
| `bills` | `phone_internet`, `subscriptions` (software, streaming), `insurance` |
| `health` | `health` |
| `shopping` | `clothing`, `electronics`, `shopping` |
| `leisure` | `entertainment`, `travel` |
| `family` | `education`, `kids`, `pets` |
| `people` | `transfers`, `gifts_donations` |
| `finance` | `fees`, `cash`, `taxes`, `investments` |
| `other` | `other` |
| `income` | `salary`, `refunds`, `benefits`, `interest`, `transfers_in`, `other_income` |

Each has an English and a Hebrew label. `resolve(term)` maps a group key, a category key or a label (either language, any case) to category keys ("food" → groceries, restaurants, delivery); a group wins over its same-named category.

Flows: `purchase`, `refund`, `transfer_out`, `transfer_in`, `income`, `card_bill`, `own_transfer`, `fee`, `cash`, `loan`, `investment`, `other`. `SPENDING_FLOWS` = purchase, refund (negative, reduces spending), transfer_out, fee, cash, loan; `card_bill` (the bank's line for the card bill: the card's own rows are the spending), `own_transfer`, `investment` and the income flows (`INCOME_FLOWS` = income, transfer_in) never count as spending.

`rules.set_category(user, txn_id, category, for_merchant=True)` sets the row's category (`category_source = 'user'`); with `for_merchant` it also saves a rule for the row's merchant key and applies it to the user's other rows with that key, except those set by hand. `rules.set_note(user, txn_id, note)`. `rules.set_flow(user, txn_id, flow)` sets what one row counts as ("Counts as": spending, a transfer to a person, money set aside, my own money, income, …; `USER_FLOWS`), and the row becomes the user's (`category_source = 'user'` unless a rule set it), so classification never changes it again. `rules.set_mark(user, txn_id, "one_off" | "yearly", True | False | None)` stores the user's call in `money_transactions.one_off` / `yearly` (NULL = Dana decides), on every payment of an installment plan (same account, merchant key, purchase date, payment count and purchase amount). Each registers an Undo (`core/undo.py`) that restores the previous values and rule.

## API

`addons/money/api.py`. Every route needs the `money` addon (403 `not_connected` otherwise); another user's ids answer 404, like missing ones. JSON; amounts in agorot.

| Route | Answer |
|---|---|
| `GET /api/money/companies` | `{companies: [{id, name, kind, fields: [{name, label, secret}]}]}` |
| `GET /api/money/sources` | `{sources: [source]}` |
| `POST /api/money/sources {company, label?, credentials}` | 201 source (its first sync started) · 400 `unknown_company` · 400 `invalid` `{field, message}` · 503 `credentials_key` |
| `PATCH /api/money/sources/{id} {label?, credentials?}` | source (new credentials: every field of the company) |
| `DELETE /api/money/sources/{id}?transactions=1` | `{ok: true}` |
| `POST /api/money/sources/{id}/sync` | 202 `{sync_id}` · 429 `{error: "too_soon", retry_after}` · 409 `{error: "needs_attention", reason}` |
| `GET /api/money/syncs?source=&limit=` | `{syncs: [{id, source_id, trigger, state, error_type, error, added, updated, started_at, finished_at}]}`, newest first |
| `GET /api/money/code-requests?status=pending` | `{requests: [{id, source_id, company, company_name, status, expires_at}]}` |
| `GET /api/money/code-requests/{id}` | `{id, status, company, company_name, senders, code_regex, expires_at}` |
| `POST /api/money/code-requests/{id} {code, via?: "phone"}` | `{ok: true}` · 400 `bad_code` · 404 · 409 `not_pending` |
| `GET /api/money/meals?months=12` | `{accounts: [{id, label, number, company, company_name, balance_at, left, budget, start, end, days_left, used, card_paid, runs_out, current {start, end, days, days_left, budget, left, used, card_paid, count, per_day_left, runs_out}}], cycles: [{start, end, total, budget_paid, card_paid, count, billed: {txn_id, date, amount} \| null}] (newest first), meals: [{id, date, occurred_at, place, order_type, total, budget_paid, card_paid, active}] (newest first, 40), places: [{place, count, total}], kinds: [{order_type, label, count, total}], totals {total, budget_paid, card_paid, count}}` (`meals.meals`; `months` ≤ 24) |
| `GET /api/money/portfolio?days=365&trades=50` | `{total {value, cash, day_change, profit, cost, as_of}, accounts: [{id, label, number, company_name, value, value_at, cash, day_change, profit, profit_pct, holdings: [...], history: [{date, value}]}], history (the total per day), trades (newest first), flows: [{date, amount}] (deposits +, withdrawals −), allocation: [{name, value, share}]}` (`portfolio.portfolio`) |

A source:

```json
{"id": "3c7c…", "company": "hapoalim", "company_name": "Bank Hapoalim", "label": null,
 "state": "needs_code", "state_reason": "Bank Hapoalim sent a sign-in code by SMS", "error_type": null,
 "last_sync_at": "2026-11-18T05:02:11+00:00", "last_ok_at": "2026-11-17T17:01:40+00:00",
 "next_sync_at": "2026-11-18T16:52:00+00:00", "credentials_set": true, "can_sync": true,
 "accounts": [{"id": "1e30…", "kind": "bank", "number": "••••901", "label": null, "balance": 123456,
               "balance_at": "2026-11-17T17:01:40+00:00", "next_charge": null, "next_charge_date": null}],
 "code_request": {"id": "9a1f…", "expires_at": "2026-11-18T05:06:11+00:00"}}
```

`can_sync`: the user's "Sync now" is allowed by the state (false for a `needs_attention` source that needs new login details). A card account: `{"kind": "card", "number": "8763", "balance": null, "next_charge": 345081, "next_charge_date": "2026-12-15", …}`; an investment account: `{"kind": "investment", "number": "••••456", "balance": <its value>, "balance_at": …}`. The overview lists investment accounts under `investments` (value, cash, today's change, profit). A code request's status is `pending` | `done` | `expired` | `cancelled` (a pending one past its expiry reads `expired`).

`store.py` holds the reads behind the API (`list_sources`, `get_source`, `source_payload`, `list_accounts`, `list_syncs`, `list_transactions(user, *, start, end, account_id, limit)` — purchase date, `start` inclusive, `end` exclusive — `get_transaction`, `code_requests`), every one scoped to the user.

## Health check

`money` in `core/checks.py` (for users with the addon): it fails while a source waits for a code (`needs_code`), needs its login fixed (`needs_attention`), or has had no successful sync for 24 hours (a new source: since it was added); the detail names the source and why, and the facts block lists every problem. A failing check becomes a Now card like the others.

## Classification

`classify.py`, an ingest hook registered in `addon.py` after the reconciliation of provisional rows (below). The hook returns at once and the work runs in the background as the user, one run per user at a time: a sync never waits for the model.

A run takes the sync's new and changed scraped rows plus up to 200 rows (newest first) still missing something: a flow, a category (except `card_bill` / `own_transfer` rows, which take none) or a display name (`merchant` still equals the description and its key has no remembered name). Each step fills only what is still missing; a category set by the user (`user`) or a rule (`rule`) is never changed.

1. **Merchant memory** → `category_source = 'memory'` (an income category is never remembered for a merchant nor taken from memory, except by a bank row with money in: a refund filed as income says nothing about what the merchant sells): the user's latest scraped row of the same merchant key whose category came from `user` / `rule` / `model` / `memory` gives its category, its display name (one the user or a rule gave beats a remembered one) and, from a row of the same account kind and sign, its flow. A bank line learns only from bank rows with the same memo (generic lines such as "העברה לחשבון" differ by memo) or from card rows. An alias in `money_aliases` is followed to its merchant, and the key's remembered display name is used.
2. **Cal's own category** (`raw.category` / `rawTransaction.branchCodeDesc`) through `CAL_CATEGORIES` → `'bank'`, used only **without a model key or when the model gives nothing usable** for a card row ("other", or an income category for a purchase); otherwise it goes to the model as a hint, because it is often too broad or wrong (online marketplaces under food, the electricity company under furniture): מזון ומשקאות → groceries, מסעדות / מזון מהיר → restaurants, אופנה → clothing, רפואה ובריאות → health, חינוך → education, ילדים → kids, עמותות ותרומות → gifts_donations, מלונאות ואירוח / תיירות → travel, פנאי בילוי / אירועים → entertainment, ריהוט ובית → home. The ambiguous ones are the model's (each with its reason in the dict): אנרגיה (fuel stations, but also the electricity and gas companies), תקשורת ומחשבים, ביטוח ופיננסים, מוסדות, שונות, תעשיה ומכירות, ציוד ומשרד, רכב ותחבורה, טיפוח ויופי, and none.
3. **Bank lines**:
   - Own transfers: a debit and a credit of the same amount on two of the user's bank accounts within 2 days → both `own_transfer`, no category (only rows without a user / rule category whose flow is empty, a transfer or `other`). A card company moving charges to a replacement card (equal and opposite rows of one merchant on two of the user's cards the same day, not installments) is an own transfer too.
   - A transfer the user sent with a payee in its note (above) is named after the payee and goes to the model whatever its activity code says: a business → `purchase` with the category of what was bought, an investment house or a purpose such as "השקעה" / "חיסכון" → `investment`, a person → `transfer_out` (`gifts_donations` for an occasion, else `transfers`).
   - A description naming a card company, as whole words (כאל, CAL, מקס, MAX, ישראכרט, אמריקן אקספרס, דיינרס, לאומי קארד, …) → `card_bill`, no category: the card's own rows are the spending (a card company paying back to the bank is `card_bill` too).
   - Hapoalim's `rawTransaction.activityTypeCode` → `'bank'`: 515 → loan / housing (the mortgage), 507 → cash / cash (ATM), 485 → transfer_out / transfers (a transfer by phone), 471 → investment / investments, 121 → income / interest (dividend), 122 → income / other_income (a bank bonus), 205 → transfer_in / transfers_in (a cheque deposit). Any other line goes to the model.
4. **Card rows' flow**: money out → `purchase`, money in → `refund`.
5. **The model**, for rows still missing a category, a display name or (bank) a flow: `llm.complete_json(job="money", purpose="money_classify")` (`MONEY_MODEL` → `BACKGROUND_MODEL`), 40 questions a call. A card merchant is asked once per merchant key, a bank line once per key + memo + sign, and the answer applies to every row of the group. Each question is `{id: "r1"…, account, date, amount (shekels), description, memo, bank_category}`; each answer `{id, merchant, category, flow}` is validated (an unknown id ignored, a category not in the list → `other`, a bank flow not in `FLOWS` → `other`, a card row's flow ignored) → `category_source = 'model'`. Writes are checked again when made, so a category the user set meanwhile stays. Display names are for card merchants only: a name (one line, at most 40 characters, `classify.NAME_MAX`) is set on every card row of that key still showing its description and, when it named rows (in the same write, so a run that ends after Money was turned off leaves nothing), becomes the key's remembered name (an alias of the key to itself); it is never asked again. A bank line keeps the bank's own description as its name: its memo carries the other side's transfer note, and a name the model drew from it would be a stranger's words in every tool result. Descriptions and memos are written by others: the prompt calls them data, the schema allows only those four fields, and nothing from the answer is run. Without a key for the job (`llm.configured("money")`) the step is skipped; a failing call is logged and what is left waits for the next run.
6. **Card ↔ bank**: a card without `billed_from_account_id` gets the bank account whose `card_bill` lines equal the card's completed total per charge date (within 3 days); when no single card matches, all the source's cards together. Two bank accounts matching equally leave it unset.

**Catch-up** (`classify.catch_up_user()`, the `recover` job of `hooks.py` when the app starts, after `sync.recover`): for each user with `money`, classification runs in rounds of 200 until a round loads fewer rows, the model classifies nothing or ten rounds have run — so rows a migration cleared (m0027 cleared bank transfers with a payee, card rows whose category came from Cal's or was an income one, and the card moves) are redone within minutes of a deploy.

What callers can rely on: once a run has ended with a model configured, every scraped row has a `flow`, `category` is NULL only on `card_bill` and `own_transfer` rows, and `merchant` is a display name (the description on those two flows and when the description is empty). Between the ingest and the end of the run (seconds), and for good without a model key, rows the deterministic steps did not settle have `category` NULL, bank lines may have `flow` NULL and `merchant` equals the description; they are retried at every run.

The merchant memory (`classify.py`, each taking an open connection): `alias(conn, user, alias_key)` → `{alias_key, merchant_key, merchant, category}` or None; `learn_alias(conn, user, alias_key, merchant_key, merchant=None, category=None)`; `remember_name(conn, user, merchant_key, merchant, category=None)` (a key known as another merchant's spelling is left alone); `memory(conn, user, merchant_key, kind=, amount=, memo=, exclude_id=, sources=)` → `{category, merchant, flow}`.

## Instant updates from the phone

`signals.py`. A payment notification on the phone becomes a transaction at once and pulls that card's sync earlier. Two ways in:

- **Google Wallet** (`com.google.android.apps.walletnfcrel`), already in the Inbox: `processing.schedule` hands every batch of new notification ids to the phone addon's observers (`addons/phone/notifications/observers.py`), where `addon.py` registers `signals.observe` (the phone addon never imports money). It records the user's own new Wallet notifications, only for users with `money`.
- **The card / bank apps** (`com.onoapps.cal4u` Cal, `com.ideomobile.hapoalim`, `com.max.android`, `com.isracard.isracardapp`) never reach the Inbox: the phone posts them to `POST /api/money/signals`:

  ```json
  {"notifications": [{"key": "0|com.onoapps.cal4u|12|null|10123", "package": "com.onoapps.cal4u", "app_name": "Cal",
                      "title": "Cal", "text": "…", "posted_at": 1763460000000}]}
  ```

  → 200 `{"accepted": 1, "duplicates": 0}` (`accepted`: newly stored); 400 `invalid` when `notifications` is not a list; 403 `not_connected` without `money`. At most 200 items; one without a package or without any text is dropped. The phone sends the texts redacted and re-sends what is still in the shade when its listener reconnects: a push with a key and text already stored is a duplicate.

**Storage**: `money_signals` (`m0026_money_signals.py`), one row per signal: `source` `notification` (`notification_id`, once per user) or `phone_app` (once per user, key and text); title and text with codes redacted and ID / card / account numbers scrubbed; `posted_at`; `parsed`, `amount` (signed agorot: a payment is negative), `currency`, `card_last4`, `merchant`; `transaction_id` (the row it became or joined). Every push is kept, parsed or not, so parsers can be written from real samples. A user's signals older than 60 days are deleted at each of their signal batches and syncs.

**Parsing** (pure functions): `parse_wallet(title, text)`: the title is the merchant, the text holds the amount (`₪350.30`, `$12.00`, `€35.00`, `₪1,234.50`, the Hebrew-locale `350.30 ₪` with bidi marks) and the card (`••8763`); "refund" / "זיכוי" / "החזר" make it positive. `parse_push(title, text)`, a best effort until real samples exist: an amount with ₪ / ש"ח / NIS / $ / € (or USD / EUR / GBP) plus the card's last 4 (`••8763`, `בכרטיס 8763`, `המסתיים ב-8763`, `ending in 8763`) or a merchant (`בבית העסק …`, `ב-…`, `at …`); a bill or statement notice is not a payment. Only Wallet and the card / bank apps above (`signals.PAYMENT_PACKAGES`) are parsed: a push the phone sends from any other package is stored unparsed and never becomes a transaction (an app can call itself anything, not another app's package).

**Provisional transactions**: a parsed shekel payment whose last 4 digits are one of the user's card accounts (`number`):

1. a provisional row of that amount on the card within 15 minutes exists (Wallet and the card's app announcing one payment) → the signal joins it;
2. else a scraped row is already there (the sync was quicker: same amount, that day or the day before, within 12 hours when both times are known) → the signal joins it, its time and notification are copied when missing and the alias is learned;
3. else, unless it is 5 days old or more, a new row: `source='notification'`, `status='provisional'`, `ext_key='n:<signal id>'`, `occurred_at` the posted time, `purchase_date` its local date, `amount` negative agorot, `description` the merchant as notified, `merchant` its remembered display name (else the same), `merchant_key`, the category from the merchant memory (aliases followed; Cal's category of the merchant's earlier rows counts here) as `'memory'`, `flow='purchase'`, `notification_id` (Wallet). Then `schedule.request_soon(user, <the card's source>, minutes=30, reason="signal")`.

A foreign-currency payment (its shekel charge is not known yet), a refund, another card (one not in Dana, Cibus) and a push that does not parse are only recorded.

**Reconciliation**: `signals.reconcile`, the first ingest hook, so a merge carries its category into classification. Each provisional (or unmatched) row of the synced source's accounts is matched to the scraped row (pending or completed) of the same account with exactly its amount, a purchase date within 2 days and no signal yet: the most similar merchant first (a learned alias, the same key, then common words or a common prefix of the key or display name), ties → the nearest time. The merge copies `occurred_at` (when the scraped row has none), `notification_id`, a category the user gave the provisional row (or any, when the scraped row has none) and its note to the scraped row, points the provisional row's signals at it, deletes the provisional row and learns the alias (`money_aliases`: the notified merchant's key → the scraped row's key, display name and category), so the next payment there is named and categorised as it arrives. A signal whose scraped row a pull replaced (a pending row back as completed under another key) is matched again, so its time and notification follow. A provisional row still alone 5 days after its `purchase_date` becomes `unmatched` (declined or reversed; checked at each sync and each signal batch). Sync's pending cleanup deletes only scraped rows, so provisional rows survive syncs until merged.

What the statuses mean for totals: `provisional` is a real payment not scraped yet and counts; `unmatched` is shown greyed and never counts (it can still merge if its row turns up); a merged provisional row no longer exists, so nothing is counted twice.

The Inbox stores a notification once per content, so two identical Wallet payments (same merchant and amount) arrive as one signal; likewise two identical pushes under one shade key.

## Page API and insights

One engine gives the page, Dana's tools, Now and the weekly review the same numbers: `periods.py` (named periods), `queries.py` (totals, lists, the overview), `insights.py` (recurring, yearly and unusual charges), `dashboard.py` (kinds, cash flow, the balance and its forecast, trends, merchants, insights: "Dashboards" below). Every function takes the user and reads only their rows; another user's account or transaction id answers like a missing one (`queries.NotFound`, 404). Totals, counts and breakdowns leave out `unmatched` rows and ₪0 lines (a card company's notice rows); lists show both.

**What counts.** Spending = rows whose flow is in `SPENDING_FLOWS`, shown positive (−sum of the amounts, so refunds reduce it); income = the `INCOME_FLOWS` rows. A row with no flow yet counts by its account (`store.FLOW_SQL`): on a card, money out is a purchase and money in a refund; on a bank account it is `other` (not spending). `unmatched` rows never count (lists show them, badged); provisional and pending rows do. Card-bill lines and own transfers are never spending. Every period is by `purchase_date`, so all of an installment plan's payments count on the purchase date. Hidden accounts are left out everywhere. A row with no category is "Uncategorized" (key `uncategorized`, as a filter too).

**Periods** (`periods.resolve(name | start/end, now)`, today from `core.clock`): `today`, `yesterday`, `this_week` / `last_week` (weeks start on Sunday), `this_month`, `last_month`, `last_30_days`, `this_year`, `last_year`, `YYYY-MM`, `YYYY`, or `start` + `end` (both inclusive; `end` defaults to today). A period in progress ends today and compares with the same elapsed span before it ("at this point last month": 1–18 Oct for 1–18 Nov, clamped to the shorter month) and, for a month or year, with all of the previous one; a whole past period compares with the whole one before; a custom range with the same number of days before it.

**Queries** (`queries.py`): `summary(user, period, category=, merchant=, account_id=, flow=, group_by=)` → `{period, flow, category_label, total, count, breakdown: [{key, label, total, count}], compare: [{label, start, end, total, count}]}` — `category` is a group, a category, a label in either language or `uncategorized`; `merchant` matches the merchant key's prefix or the full-text index (merchant, description); `flow` is `spending` (default), `income` or one flow; `group_by` is `group` | `category` | `merchant` | `account` | `month` | `week` | `day` (time groups oldest first, the rest largest first). `category_tree` (groups → categories), `search` (full text over merchant / description / memo / note with every word a prefix, or an amount when the query is only a number; absolute ₪ `min_amount` / `max_amount`; newest first by purchase date, then time, then id, with a keyset cursor), `transaction` (one row in full), `overview`, `months`, `changes`, `week` (the weekly review's section), `find_account` (an id, last digits, label, company or kind → one account), `chat_context` / `context_prompt` (the `transaction` chat kind).

**Recurring** (`insights.recurring(user)` → `{items: [money out], income: [money in], monthly_total}`): rows of the last 800 days grouped by merchant key and direction; a series is recurring when every gap fits a cadence (one stray gap allowed from 3 gaps up) — weekly 6–8 days in ≥ 4 weeks, monthly 26–35 days in ≥ 3 months, every two months 50–80 days in ≥ 3 months (utilities, the municipality: a bimonthly bill wanders around its date), yearly 350–380 days in ≥ 2 years — its last charge is recent (17 / 45 / 90 / 400 days) and consecutive amounts stay within 5 % of each other, with at most one price change (flagged `price_change {from, to, since}`); a bill whose amount follows usage or the household (categories utilities, insurance, phone & internet, subscriptions, health, housing, salary, benefits) needs only the cadence (`variable: true`, typical amount = the median of the last three of its latest run). A merchant whose rows come under two spellings (two keys, one display name) is tried again by display name; the item lists both in `merchant_keys`. The next date keeps the usual day of the month (the most common of the last six: a salary paid late once on the 2nd is still expected on the 1st). Card installments, card bills and own transfers are never recurring. Each item: merchant, category, cadence, typical `amount`, `monthly_cost` (`amount × insights.per_month(cadence)`: a bimonthly ₪600 bill is ₪300 a month), `count`, first / last / next date, account. `insights.cadence_text(cadence)` gives the words ("a month", "every 2 months", …) for tools and lines.

**Yearly** (`insights.yearly(user)`, `detect_yearly(rows, today, fixed_keys)`): charges that come once a year — insurance renewals, the car licence and service, a yearly subscription, school fees, a tax. Money out in the categories insurance, car, subscriptions, education, taxes, ₪300–₪10,000 a renewal (an installment plan counts at its total: a ₪3,600 policy in 12 payments is one ₪3,600 renewal), not a merchant already in a monthly / bimonthly series. Rows within 45 days of each other are one renewal, and the next is expected 12 months after the last (a single renewal is enough); two renewals less than 300 days apart mean it is not yearly; it stops being expected 60 days after its next date passes. The user's marks win: `yearly = 1` makes any row a renewal, `yearly = 0` or `one_off = 1` keeps it out. Each: `{merchant, merchant_key, category, category_label, amount (the last renewal), payments, last_date, next_date, monthly_cost, marked, row_ids, account_id, account_kind}`. **Unusual** (`insights.unusual(user, since)`, newest first, one reason each): the same amount at the same merchant twice within 10 minutes (both with exact times, not a notification row beside a scraped one; never two payments of installment plans), ≥ 3× the median of ≥ 4 earlier charges of the merchant (and ≥ ₪200), or a first charge from a merchant over ₪500 — not in the first 60 days of the user's data (every merchant is new then). An installment plan is judged once, by its total; each with `title` ("₪1,500.00 at סופר פארם"), `why` ("about 14× the usual ₪105.00 there") and the transaction.

Routes (`api.py`, its page section; every amount in agorot, dates ISO, `end` inclusive). `tests/fixtures/money_web.json` holds every payload of a synthetic two-user dataset (written by `WRITE_WEB_FIXTURE=1 bin/test test_money_page`):

| Route | Answer |
|---|---|
| `GET /api/money/overview?month=YYYY-MM` | `{month, period, spent {total, count, compare}, income {total, count}, cards [{id, label, number, company, company_name, next_charge, next_charge_date, billed_from_account_id}], banks [{id, label, number, company, company_name, balance, balance_at}], recurring_monthly, uncategorized, sources [{id, company, company_name, label, name, state, state_reason, last_ok_at, last_sync_at, next_sync_at}], last_sync_at, attention [{source_id, name, state, reason}], unusual [{transaction, reason, title, why, text, typical?, duplicate_of?}]}` + the dashboard tiles (`everyday`, `net`, `income_month`, `income_typical`, `set_aside`, `one_off`, `fixed_monthly`, `yearly_monthly`, `forecast`: "Dashboards") |
| `GET /api/money/pace?month=` | `dashboard.everyday_pace`: `{month, label, days, today, this_month [cumulative everyday agorot, null after today], typical [day by day] \| null, low, high (quartiles) \| null, months}` |
| `GET /api/money/cashflow?n=12&end=YYYY-MM` | `dashboard.cashflow` (`n` ≤ 36) |
| `GET /api/money/balance?days=180` | `{history [{date, balance}], forecast \| null}` (`days` ≤ 366) |
| `GET /api/money/trends?month=` | `dashboard.trends` |
| `GET /api/money/merchants?period=\|start=&end=&limit=15` | `{period, merchants [...]}` (default this month; `limit` ≤ 50) |
| `GET /api/money/insights?month=` | `{month, items [{kind, tone, title, detail, amount, link}]}` |
| `GET /api/money/categories?period=\|start=&end=` | `{period, total, count, compare, groups [{key, label, total, count, categories [{key, label, total, count}]}], taxonomy {groups [{key, label, label_he, income, categories [{key, label, label_he}]}], flows, spending_flows, income_flows}}` (default this month) |
| `GET /api/money/months?n=12` | `{months [{month, label, total, count, groups {group: agorot}}], groups [{key, label}]}` |
| `GET /api/money/transactions?period=\|start=&end=&q=&category=&account=&min=&max=&cursor=&limit=` | `{transactions [item], next_cursor}` — no period: all time; `limit` ≤ 200 (50) |
| `GET /api/money/transactions/{id}` | the item + `memo`, `source`, `notification_id`, `installment {number, total, purchase_amount}`, `foreign {amount, currency}`, `bank_category`, `created_at`, `updated_at`, `same_merchant {count, total, first_date}`, `one_off`, `yearly` (the user's marks: true / false / null); never the stored `raw` |
| `PATCH /api/money/transactions/{id} {category?, for_merchant?: true, note?, flow?, one_off?, yearly?}` | `{transaction, changed, undo_id}` — `rules.set_category` / `set_note` / `set_flow` / `set_mark` (`one_off` / `yearly`: true, false, or null = back to Dana's call); one undo id for everything the call changed; 400 `invalid` for an unknown category key or flow, or nothing to change |
| `GET /api/money/recurring` | `insights.recurring` + `yearly [insights.yearly]`, `yearly_monthly` |
| `GET /api/money/changes?since=<cursor>` | `{changed, cursor}`: a hash of the count and newest `updated_at` of the user's transactions, accounts and sources and of their pending code requests; the page polls it every 30 s while open and refetches when `changed` |

A list item: `{id, account_id, account_kind, account_label, purchase_date, charge_date, occurred_at, amount, merchant, merchant_key, description, memo, category, category_label, group, group_label, category_source, flow (as the numbers use it), spending, kind (fixed \| yearly \| one_off \| everyday \| null: not spending), status, note, badges [{type: provisional | pending | unmatched} | {type: installment, number, total} | {type: foreign, amount, currency}]}`. Malformed periods, months, numbers and cursors answer 400 `{error: "invalid", field?, message}`.

### Price checks

`market.py`: the "Check prices" button of a saving card looks up current offers on the web. A check runs **only when the user asks** (`market.start`, called from the API); nothing runs one in the background.

| Topic | Params (strict: unknown keys refused) | Item (amounts in agorot) | Order |
|---|---|---|---|
| `telecom` | `service` (`mobile` \| `home_internet` \| `triple` \| `mobile_and_internet`), `lines` (1–10 or null), `current_price` (whole ₪ a month, 1–2000) | `{provider, plan, price (regular, a month), promo_price \| null, promo_months \| null, avg_12, conditions, source}` | `avg_12` |
| `electricity` | — | `{supplier, plan, discount_pct, hours, days, smart_meter, source}` | discount, biggest first |
| `fx_card` | — | `{name, kind (card \| wallet \| bank), fx_fee_pct, monthly_fee, conditions, source}` | `fx_fee_pct` |
| `arnona` | `city` (Hebrew letters, spaces, `-` and quotes, ≤ 40), `year` (this year ± 1) | exactly one: `{found, discount_pct \| null, deadline (YYYY-MM-DD) \| null, method, source \| null}` | — |

At most 8 items; `source` is `{title, url}`. `avg_12` is computed in code: (promo price × min(promo months, 12) + price × the rest of the 12 months) / 12, so offers compare by what the first year costs a month.

**What is sent.** The search prompt is a fixed template per topic filled with that topic's params and the current month, nothing else: no names, transactions, merchants, account numbers, or amounts other than `current_price`. The second call gets the search's text and its pages, again without any user data. `test_money_market.py` asserts both.

**Pipeline** (`_find`, run as an asyncio task bound to the user, at most `CHECK_TIMEOUT_SECONDS` = 4 min):

1. `llm.search(prompt, purpose="money_market")` asks the `search` model (`SEARCH_MODEL` → `MODEL`, `docs/configuration.md`) with LiteLLM's `web_search_options`. It returns the answer and the pages the provider grounded it on: Gemini's grounding chunks, OpenAI's `url_citation` annotations, or Anthropic's citations. Gemini's grounding supports add a `[n]` after each passage a page backs. With no pages it raises `llm.NoSearch` (the model did not search).
2. Google's grounding redirect links (`vertexaisearch.cloud.google.com/grounding-api-redirect/…`) are resolved with an `httpx` HEAD that follows redirects (5 s). When the HEAD fails, the redirect link is kept. Only http(s) pages can be cited.
3. `llm.complete_json(job="money", purpose="money_market")` extracts the topic's schema from the text and the numbered list of usable pages. Each item gives the number of its page.
4. Validation in code drops:
   - an item whose page number is not one of the search's usable pages;
   - a telecom price outside ₪5–₪500 a month (a promotion must cost less than the regular price and last 1–36 months, or it is dropped and the offer kept);
   - an electricity discount outside (0, 30] %;
   - an FX fee outside 0–5 %, a monthly fee over ₪100, or an unknown kind;
   - a repeat: the same provider and plan, the same supplier, plan and hours, or the same card name. The best of them stays.

   For arnona, a "found" without a usable page reads as not found. A discount outside (0, 30] % or a malformed deadline becomes null.

**Outcomes.** A check either ends `ok` with its items, or `failed` with a plain-words `error`: the model did not search; no offer with a source; the search or extraction failed; the check took too long. A telecom, electricity or FX check with no valid item counts as failed. An arnona "not found" is an `ok` result.

**Rate limits.** `start` raises:
- `ValueError` for an unknown topic or bad params;
- `Unavailable` when the `search` model has no key, or LiteLLM's model map says it has no web search (`llm.can_search`);
- `Running` while the same check runs, or its row says `checking` and is less than 5 minutes old;
- `TooSoon(retry_after)` when the same (user, topic, params) started less than 6 hours ago, or less than 5 minutes ago if it failed.

**Storage** (`m0030_money_market.py`): `money_market_checks` keeps one row per `(user_id, topic, params_key)`, where `params_key` is the canonical JSON of the params. Its columns: `params`, `state` (`checking` / `ok` / `failed`), `items` and `sources` (JSON), `model`, `error`, `started_at` and `checked_at`. A new check reuses the row. `items`, `sources` and `checked_at` stay those of the last check that succeeded: they remain visible while a new check runs and after one fails.

`market.latest(user, topic, params)` → `{topic, params, state, started_at, checked_at, stale (checked_at older than 45 days), items, sources [{title, url}], error}`, or None. A `checking` row older than 5 minutes was cut off by a restart (no check runs that long) and reads as `failed`. Every read is scoped to the user. Disconnecting `money` deletes the rows (`store.delete_all`).

## Dashboards

`dashboard.py` turns the rows into the numbers of the Money tab's four views, Dana's `money_overview` / `money_forecast` / `money_insights` and the insight cards. Every function reads one `Book` (`dashboard.load(user)`: the user's rows of the last 800 days with their effective flow, their recurring and yearly charges and each spending row's kind), built once per request; `build(rows, today)` is pure, for the tests and backtests.

**Kinds** (`spend_kinds`): each spending row is one of

- **fixed**: a loan payment (the mortgage), or a merchant of a monthly / bimonthly / weekly series (the municipality, electricity, the phone, insurance paid monthly, subscriptions);
- **yearly**: a renewal found by `insights.detect_yearly` (above), or marked yearly by the user;
- **one_off**: marked by the user, or a purchase of at least ₪2,000 (`ONE_OFF_MIN_AGOROT`; an installment plan at its total) that is neither fixed nor yearly — a car, furniture, a wedding gift, a flight; a refund within 120 days at the same merchant as a one-off is a one-off too;
- **everyday**: the rest.

The user's marks win (`one_off`, `yearly`: 1 / 0; NULL = Dana decides). Money set aside (`investment` flow), own transfers, card bills and income are not spending and have no kind. **Typical** everywhere = the median of the last 6 whole months before (`TYPICAL_MONTHS`, `whole_months`: from the user's first month with data; a month with nothing counts as 0), so one ₪27k month never moves the baseline.

**Cash flow** (`cashflow(book, n=12, end=)`): per month, `income`, `fixed`, `yearly`, `everyday`, `one_off`, `spent` (their sum), `set_aside`, `net` (income − spent; money set aside is not spent) and `savings_rate` (net / income); `totals` over the span and `typical` (medians of the whole months shown).

**Balance history** (`balance_history`): the bank balance at the end of each day, rebuilt backwards from the bank accounts' last balance through their own rows (Hapoalim gives every movement, so the line is exact).

**Forecast** (`forecast(user, days=180)`, pure `project(book, accounts, balance, as_of, days)`): the bank balance day by day from its last balance, **one-offs left out**:

- recurring income and bank debits (the salary, the mortgage, standing orders) on their next dates at their typical amounts (`estimated` when the amount varies); a date already passed by a little moves to tomorrow; a payment due on a Saturday reaches the bank on Sunday (`bank_day`);
- each card bill on the card's billing day (from `next_charge_date`, else its most common charge day), holding `parts`: `known` (rows already charged to that date, or the card company's `next_charge` when larger), `installments` (the remaining payments of installment plans), `fixed` (the card's recurring charges falling in that cycle), `yearly` (renewals spread over their payments) and `everyday` (everyday spending at its usual rate, day by day on the card most of it went to in the last 60 days); a purchase reaches the first bill at least 2 days after it (`bill_date`);
- yearly charges on their next dates (an insurance renewal in 12 payments lands on 12 bills; a bank one on the bank);
- everyday bank spending at its usual rate, a little each day.

The usual everyday rate is the mean of the last 6 whole months without the busiest and the quietest (from 5 months up; a median under-counts, as busy months are further above it than quiet months below), and the band (`low` / `high` on every day, `range` on a card bill) runs from the quietest to the busiest of those months. The result: `{as_of, end, balance, series [{date, balance, low, high}], events [{date, kind (income | fixed | yearly | card_bill), label, amount, estimated, parts?, items?, range?, account_id?}], soon_low {date, balance, days} (the lowest in the next 45 days, `SOON_DAYS`), low_point {date, balance} (the lowest after today), rates {card {low, typical, high, months}, bank}, plans [{merchant, amount, dates, total, left, number, of, account_id}], yearly [the renewals ahead], left_out [the biggest one-offs of the last 12 months], left_out_total (net of their refunds)}`. Backtested on a year of real data, the expected balance was within about ₪1,100 at 14 days and ₪2,900 at 30; further out it follows the last months' spending, so a season that spends more than they did shows as a drift the band does not cover.

**Trends** (`trends(book, month=)`): spending by group and category for a month, one-offs left out — `total`, `typical`, `on_track` (a month in progress, from day 7: spent so far + the typical everyday spending of the days left + recurring charges still due this month; scaling up what was spent would make one ₪1,500 payment a ₪2,500 month), `delta` and 12 months of values; `everyday {total, typical, on_track}`; **movers** (the categories whose everyday spending is furthest from typical: at least ₪150 and 25 %, `up` / `down` / `new`; fixed and yearly charges never — a bimonthly bill doubles its category every other month, a renewal is expected, and a fixed cost that changes is a price change; drops only once the month is over); the month's one-offs. `everyday_pace(book, month)`: everyday spending day by day (cumulative) against the median of the months before and their quartiles.

**Merchants** (`merchants(book, start, end_exclusive)`): the merchants spent at most in a period, each with count, total, average, first date, months active of the last 12, 12 monthly totals, and whether every row was a one-off.

**Insights** (`month_insights(user, month=)`, rules in `insights_for`): a ranked list `[{kind, tone (good | bad | warn | info), title, detail, amount, link {view, category?, id?}}]` whose every number comes from the engine — everyday spending on track for vs typical (a past month: its total vs typical), the lowest balance in the next 45 days (the month in progress), the net of a past month, the movers, price changes since the month before, yearly charges in the next 45 days, what is left on installment plans, the month's biggest one-off, unusual charges (not one-offs, and a yearly renewal only when charged twice), money set aside this year. Cards from other engines are added at position 2 of the month in progress (`on_insights`: the savings card). **Tiles** (`tiles`, in the overview): `everyday`, `net`, `income_month`, `income_typical`, `set_aside`, `one_off`, `fixed_monthly` (recurring charges a month + yearly ones spread), `yearly_monthly`, `forecast {balance, as_of, soon_low, low_point, end, end_balance, next [4 events]}` (the month in progress only). `annotate` / `with_kind` add `kind` to transaction payloads.

## Savings

`savings.py` finds ways the user could save in their own rows and keeps their choices about them. The Bank of Israel's public data comes from `reference.py`, and the price checks the user asks for come from `market.py`. Every number is computed in code. A model never produces one. Every saving says its **basis**:

- `measured`: from the user's own charges, such as fees paid, a markup or interest not earned;
- `increase`: what a price rise costs a year;
- `market`: against a price check.

An item without a credible saving has `saving: null` and `at_stake` (money a year; a `spree`'s is the total of its one burst of orders).

Two groups:

- **Ways to save** (`save`), with statuses `open`, `doing`, `done`, `dismissed`, `snoozed`.
- **Spending to watch** (`watch`), with statuses `open`, `accepted` ("worth it"), `watching` (with a monthly target).

**Detectors** (`DETECTORS`). They are pure over a `Context`: the `Book`, accounts, investment accounts and trades, the policy rate, FX rates, the user's answers and their latest price checks. A failing detector is logged and skipped. All amounts are agorot.

| Type (key) | Fires when | Numbers |
|---|---|---|
| `bill` (`bill:<merchant key>`) | A recurring charge in phone & internet, insurance, subscriptions, health or utilities rose (`price_change`) in the last 18 months, by ≥ 10 % and ≥ ₪10 a month. חברת החשמל is left out: its bill follows usage. | `increase` = the rise × 12 a year. `at_stake` = the new price a year. Phone & internet asks what the bill is for (`service`), and how many `lines` for mobile, before its `telecom` check (`params {service, lines, current_price}` in whole ₪). A check makes it `market`: high = (now − the best 12-month average) × 12, low = from the 3rd best or the median. There is a Hebrew call script. |
| `electricity` | חברת החשמל billed in the last 12 months and no private supplier did. | `at_stake` = the median bill of the last 12 months × the bills a year (6 when bimonthly): 12 months can hold 5 or 7 bimonthly bills. After an `electricity` check, the 95 % a discount applies to × the best all-day plan without a smart meter (low) up to the best plan (high). |
| `cash` (`cash:<account id>`) | An investment account holds ≥ ₪2,000 cash with no interest in 90 days. | `measured`: cash × (policy rate − 0.3 %). It names a money market fund (כספית) the account already holds. |
| `cushion` | The bank balance's lowest point over 90 days (≥ 60 days of history) leaves ≥ ₪3,000 idle. | Idle = floor − a month of typical fixed costs, and floor − ₪3,000 (low / high), × (policy rate − 0.3 %). |
| `fx` (`fx:<card account id>`) | Foreign-currency purchases on a card in 12 months: ≥ ₪3,000 charged and ≥ ₪100 over the Bank of Israel rate of each day. | `measured`: the markup. After an `fx_card` check, the markup minus each alternative's fee % and monthly fee × 12: the best (high), the median (low). If no alternative costs less, the saving is null. |
| `fees` | Bank and card fees (flow `fee` or category `fees`, penalties left out) total ≥ ₪60 in 12 months. | `measured`: the 12 months' fees. There is a Hebrew script. |
| `arnona` (`arnona:<merchant key>:<year>`) | 1 Nov to 15 Feb, for a monthly municipality charge (utilities). The year is next year from November. | `at_stake` = the monthly amount × 12. After an `arnona` check (`params {city, year}`): discount − the yearly amount × (policy rate − 0.3 %) × 5.5 / 12. When that is ≤ 0, the saving is null and the card says it isn't worth it. `timing.until` is the check's deadline. |
| `renewal` (`renewal:<merchant key>:<YYYY-MM>`) | A yearly insurance charge (or a car one whose text has "ביטוח"). Listed all year so the date is in view: coming up until 60 days before its next date (`timing.from`), due from then until 14 days after. | `at_stake` = last year's amount. A compulsory policy ("חובה") links the regulator's calculator (car.cma.gov.il). |
| `insurance_review` | Insurance paid monthly and yearly totals ≥ ₪1,500 a year. | `at_stake`, with הר הביטוח (harb.cma.gov.il) as the first step. |
| `subscriptions` | Subscription lines (recurring, yearly, or ≥ 2 charges in 12 months; the rest grouped by display name, so a service's spellings and invoice codes are one line) total ≥ ₪300 a year. | `subitems`, one per line. The lines marked `cancel` give a `measured` saving. A cancelled line whose expected charge did not come leaves the list and the total, and stays as an inactive subitem. |
| `mortgage` | A monthly loan payment ≥ ₪2,000. | "Worth checking" only. `at_stake` = the payments a year, with the policy rate now and a year ago. |
| `creep` (`creep:<bucket>`) | Eating out, shopping, entertainment or subscriptions: the last half of the whole months vs the half before (6 vs 6 from a year of data, 5 vs 5 from 11 months, from 6 months up) are ≥ 1.5×, ≥ ₪200 a month more, and the median ≥ 1.3×. One-offs and foreign rows are left out. | `at_stake` = (new − old monthly average) × 12. `suggested_target` = halfway back, rounded to ₪50. |
| `spree` (`spree:<merchant>:<start date>`) | ≥ 5 discretionary orders at one merchant within 7 days, totalling ≥ ₪500. The latest such run counts. | The run's total. |
| `leak` (`leak:<merchant>`) | A merchant (by display name, as for sprees and overlaps: a cloud bill whose every description carries an invoice code is one merchant; targets match the name) that is not fixed and was charged in the last 45 days: ≥ 6 small charges (median ≤ ₪60, ≥ ₪250 in 12 months), or the last 3 months ≥ 1.5× and ≥ ₪60 over the 3 before. | 12 months' total. |
| `overlap` (`overlap:<niche>`) | Two services of one niche (AI assistants, video, music, cloud storage; subscriptions and entertainment rows only) were both charged in 60 days. | Each one's recurring cost a year, else what it cost in the last 12 months (never a projection of a few recent charges). |
| `penalties` | Fines, late fees, interest and withdrawal fees ("קנס", "דוח חניה", "ריבית פיגורים", "עמלת משיכה", …; never the cash withdrawn) total ≥ ₪1 in 12 months. | 12 months' total. |
| `late_night` | ≥ 8 discretionary purchases (or ≥ ₪1,000) between 23:00 and 05:00 in 90 days. | × 365 / 90. |

**Item payload** (`GET /api/money/savings` → `{as_of, rate {policy, date, year_ago, year_ago_date} | null, totals {low, high, count, saved, saved_yearly, watch_count}, items}`). Each item has these keys:

- **Identity and status:** `key`, `type`, `group`, `status`, `status_at`, `snooze_until`, `first_seen` (a local date), `new` (first seen ≤ 7 days ago), `active` (its detector fires now).
- **What it is:** `title`, `summary`, `saving {low, high, basis} | null`, `at_stake`, `effort {minutes, how: call | online | app | review | compare}`, `timing {from, until, label} | null`.
- **Why:** `evidence {lines, series [{date, amount}], link {view, q?, category?} | null}` and `math` (lines).
- **What to do:** `steps [{text, url}]`, `script {lang: "he", text} | null`, `questions [{id, text, options [{value, label}], answer}]`.
- **Price check:** `market {topic, params, state: none | checking | ok | failed | unavailable, checked_at, stale, items, sources, error, needs_answer} | null`.
- **Lines and scope:** `subitems [{key, merchant, amount, cadence, yearly, next_date, choice: keep | cancel | null, active}] | null`, and `scope {merchant_keys | categories, keywords?, hours?} | null`. A merchant scope counts all of the merchant's spending. The others leave out one-offs and foreign rows.
- **Watch items:** `target {monthly, this_month, on_track (null in a month's first 2 days while under), days_left, months [{month, spent, target, under}], saved} | null` and `suggested_target`.
- **Ways to save:** `outcome {state: waiting | confirmed | no_change, saved, saved_yearly, detail} | null`.

Order: due items first (`timing.from` passed), then the saving's midpoint per minute of effort, then the money at stake. Totals add up the open / doing active ways to save that have a saving. `saved` = confirmed outcomes plus targets' `saved`.

**The user's choices are never hidden.** A row of `money_savings` holds the first sighting, the last detected payload (`snapshot`), the status, the answers, the target and the baseline. An item whose detector stops firing stays listed, rendered from its snapshot with `active: false`, while it has a status other than `open`, a target, or a subscription choice. Every status can be changed back:

- `open` gives the item back to Dana: an inactive item then drops out, and comes back when its detector fires again.
- A snooze (default 30 days) ends by itself.
- A target is set, edited or removed. A number makes the item `watching`. `watching` without a number takes `suggested_target`. Removing the target sets the item back to `open`.

The first `doing` / `done` / `watching` stores the **baseline** (the numbers it was found with, plus `at`). Going doing ↔ done and editing a target keep that baseline; each target change is added to the baseline's `targets` [{month, monthly}], so a finished month is always judged by the target it had. Every write is one `savings.update` with one Undo (`core/undo.py`, source `money.savings`).

**Outcomes** (doing / done, measured from rows after `done_at` or the baseline's `at`):

| Type | Confirmed when | Saved |
|---|---|---|
| `bill` | The bill's next charge (≥ 40 % of it: a purchase from the same company is not the bill) is ≤ 97 % of the baseline — a drop below 60 % needs a second lower charge — or a new merchant of the same category is cheaper. | The difference since; `saved_yearly` a year. |
| `electricity` | A private supplier (by name) bills, and חברת החשמל doesn't after it. | The check's low a year, pro rata. |
| `cash` | The account's cash is below 25 % of the baseline. | |
| `cushion` | Money set aside from the bank since (investments, own transfers out) is at least half the idle amount (a lower balance alone may be a trip). | |
| `fx` | From ₪500 of foreign purchases since: the markup is ≤ half the baseline's percent. | |
| `fees` | From 35 days: fees run at ≤ half the monthly baseline. | |
| `arnona` | A January / February payment is ≥ 80 % of the year's amount. | |
| `renewal` | The next renewal is cheaper than last year's. | |
| `subscriptions` | Expected charges of a cancelled line didn't come. | Those charges; the line's yearly cost. |

`no_change` covers the opposite: still charged after 30 or 70 days, or the deadline passed; for `renewal` and `fx` also no renewal / no foreign purchase on that card long after (moved to another insurer or card: not measurable here). `waiting` covers the time before.

**Price checks** (`market.py`). Its interface is `TOPICS` (`telecom`, `electricity`, `fx_card`, `arnona`), `latest(user, topic, params)` → the newest check `{topic, params, state, checked_at, stale, items, sources, error}` | None, and `start(user, topic, params)` → `{state: "checking"}`, raising `Unavailable` / `Running` / `TooSoon(retry_after)`. Checks run only when the user asks. An item reads the check with exactly its own `params`. After `Unavailable`, the topic shows `unavailable` for an hour.

**Bank of Israel** (`reference.py`). It reads the SDMX CSV API: no key, no user data. `policy_rate(today)` gives `{rate, date, year_ago, year_ago_date}`. `fx_rates(currency, start, end)` gives `{date: rate}` for currencies quoted per unit (all of `FX_CURRENCIES` are fetched whenever the user spent abroad, so the requests never say which); `rate_on` takes the same day or up to 7 days back. Answers are cached for a day in memory and in `MONEY_DIR/boi-cache.json`. The fetch is automatic, as part of computing the items. A failed fetch is logged and not retried for 15 minutes. The items that need the data are left out: `cash` and `cushion`, and the arnona's net.

Routes (`api.py`; keys are URL-encoded, since they hold `:` and Hebrew). Another user's key answers 404, and a user without `money` gets 403 `not_connected`. `tests/fixtures/money_savings_web.json` holds `{savings, item_check, patch}` from a synthetic dataset (written by `WRITE_WEB_FIXTURE=1 bin/test test_money_savings`):

| Route | Answer |
|---|---|
| `GET /api/money/savings` | The overview above. |
| `GET /api/money/savings/{key}` | One item · 404 |
| `PATCH /api/money/savings/{key} {status?, snooze_days?, answers?, target?, subitem?}` | `{item, undo_id}`. `target` is agorot a month or null, `answers` maps a question id to an option's value, `subitem` is `{key, choice: keep \| cancel \| null}`. 400 `invalid {field, message}` for a status of the other group, a target on a way to save, an unknown answer or line, or nothing to change. 404 for an unknown key. |
| `POST /api/money/savings/{key}/check` | 202 `{state: "checking"}` · 400 `no_check` · 400 `needs_answer {question}` · 409 `running` · 429 `too_soon {retry_after}` · 503 `unavailable` |

Elsewhere:

- **Chat:** "Ask Dana" on a savings card opens the `saving` context chat (`core/context_chats.py`, also an on-screen kind): the item's numbers, math, steps and script, its merchant text fenced as written by others.
- **Now:** a way to save due within 14 days, or a bill increase found in the last 7 days (`money_save:<key>`), and a target at 80 % of this month's or last month's result (`money_target:<key>:<YYYY-MM>`) (docs/now.md). There are no push notifications.
- **Weekly review:** one line in the Money section (`savings.week_line`).
- **Overview insights:** the month in progress gets a `savings` card ("N ways to save ₪a–₪b a year", `link {view: "save"}`: it opens the Save view), which `addon.py` registers with `dashboard.on_insights`.

## Agent tools

`tools.py`, offered for every user with `money` and in realtime voice. The model passes a period name or dates; `periods.resolve` works out the dates, `queries` / `insights` the numbers; results are short text with amounts as `₪1,234.50`, errors as `Error: …` in words. An account is named by its last digits, label, company or kind (`queries.find_account`; several matches are listed back).

| Tool | Flags | What |
|---|---|---|
| `money_summary(period, start?, end?, category?, merchant?, account?, flow?, group_by?)` | read, `untrusted_result` | The total, a breakdown (by group by default; by category when the category is a group) and the comparisons: "Spent on Food this month (1–21 Nov 2026): ₪2,140.00 in 3 transactions. By category: Groceries ₪1,320.00, Restaurants & cafés ₪820.00. At this point last month (1–21 Oct 2026): ₪2,430.00. …" |
| `search_transactions(query?, period?, start?, end?, category?, min_amount?, max_amount?, account?)` | read, `untrusted_result` (descriptions and transfer memos are written by others) | Up to 30 rows, newest first, each with its id |
| `money_overview()` | read, `untrusted_result` | Spent, income and the net this month (spent vs the same point last month), everyday spending vs a typical month, balances, next card bills, the lowest balance in the next 45 days, fixed costs a month, the last good sync, what needs attention |
| `recurring_charges()` | read, `untrusted_result` | Subscriptions and standing orders ("₪600.00 every 2 months (₪300.00 a month)") with price changes and next dates, charges that come once a year, regular income |
| `money_forecast(days=45)` | read, `untrusted_result` | The balance ahead (expected, range, lowest point), the everyday rate it assumes, what it leaves out, the events with each card bill's parts, installment plans |
| `money_insights(month?)` | read, `untrusted_result` | The month's insight cards in words |
| `meal_card()` | read, `untrusted_result` (places are named by the businesses) | What is left of the meal card's budget and until when, the pace, what went on the user's own card this cycle, each past cycle (from the budget, on the card, when the card was billed), the places used most, the latest meals |
| `investment_portfolio()` | read, `untrusted_result` | Each investment account's value, today's change, profit, cash and holdings (units, value, profit), and the latest activity; read-only (Dana never trades) |
| `categorize_transaction(transaction_id, category, for_merchant=true)` | `is_write` (runs at once; `rules.set_category` registers the Undo), `untrusted_result` | A group name is refused with its categories |
| `sync_money(account?)` | not `confirm`, `untrusted_result` | `schedule.sync_now(..., trigger="agent")` for each login (or one account's); too soon / needs attention said plainly |
| `saving_opportunities()` | read, `untrusted_result` | The totals, the open ways to save (`[key]`, the saving and its basis or the money at stake, effort, timing, the first step), spending to watch, this month's targets, outcomes of what was acted on, the user's choices |
| `update_saving(key, status?, monthly_target?)` | `is_write` (runs at once with Undo), `untrusted_result` | A status of the item's group, or a monthly target in ₪ for spending to watch; mistakes are answered `Error: …` in words |

Prompt: the addon's parts (`tools`, `read_examples`, `undoable`, and a `guides` paragraph: money questions go to these tools with a period name, amounts in ₪, what "spent" means, ways to save go through `saving_opportunities` / `update_saving` with the tool's numbers) and its `prompt_section` (`kind.accounts_section`: the user's accounts and cards), which `core/agent.py` adds to the chat system prompt after the About section. Elsewhere: Now's unusual-charge cards (`core/now.py::_money_items`) and savings cards (`_savings_items`), the weekly review's Money section (`core/weekly_review.py::money_week` → `queries.week`, with `savings.week_line`), and the `transaction` chat kind (`core/context_chats.py`, `SCREEN_KINDS`).

Every tool's result is `untrusted_result`: merchant names and descriptions come from the merchants and the banks, so after one runs the turn's `acts_outside` tools ask first (docs/agent-actions.md). The `transaction` chat's prompt puts the merchant, the description and the memo (cut at 300 characters) between `<<external <nonce>>>` / `<</external <nonce>>>` fences with a random nonce per turn, named in the warning above them; a turn in such a chat, or with a transaction on screen, runs with `untrusted_content` (`session_runner.UNTRUSTED_CONTEXT_KINDS`), so its `acts_outside` tools ask too.

## Money tab

The web page, the addon's (`web=` in `addon.py`, files in `web/`): `web/money.js` (the controller, registered with `Dana.panel`: state, loading, events, the Transactions view, sheets, the Save view's writes and price-check polling), `web/money-dash.js` (`DanaMoneyDash`: renders the Overview, Spending, Cash flow, Save, Investments and Meal card views and their SVG charts; no network, no state of its own), `web/money-core.js` (the pure part: ₪ formatting, dates, chart geometry, tile and sentence words, day grouping, filters ↔ query string / hash, the Save view's sections, chips, actions, offer rows and target progress; tested by `test_web_money.mjs` against `tests/fixtures/money_web.json`, `money_portfolio_web.json`, `money_meals_web.json` and `money_savings_web.json`) and `web/money.css`; the two scripts load before `money.js` (`Dana.panel` `scripts`). The shell lists the page only for a user with `money` on (`GET /api/addons`); a 403 `not_connected` shows the page's own "not connected" state.

The header has the month switcher (hidden on Save, Investments and Meal card, which are not tied to a month), "Synced 2 h ago" (spinner while a sync runs), Accounts, and the views: Overview · Spending · Cash flow · Save · Investments (only for a user with an investment account) · Meal card (only with a meal card) · Transactions. On a phone the views' strip scrolls sideways when the names don't fit. Each view loads only its own endpoints (plus `overview`, `sources` and `categories`, which every view uses), and a month switch shows skeletons, never the old month's numbers.

| View / part | What | Endpoints |
|---|---|---|
| Banners | SMS code (input + countdown), a login that needs attention | `code_request` on each source of `GET sources` · `POST code-requests/{id} {code}` |
| **Overview** · tiles | Net this month (saved / over, the share of income kept, vs a typical month) · Everyday spending (so far, on track for, vs typical) · Balance ahead (the lowest in the next 45 days and where it ends; for a past month the month's one-offs instead) · Fixed costs a month (share of income, yearly charges spread) | `overview` (tiles), `cashflow?n=12&end=` (the typical net) |
| Overview · insights | The month's insight cards with their tone; a card opens its view, category or transaction; "N to sort" → the uncategorized filter | `insights?month=` |
| Overview · coming up | The month in progress: the next 45 days' events by day over a balance sparkline; a card bill opens into its parts (on it already, installments, recurring, yearly, everyday estimate) and the items on it | `balance?days=` |
| Overview · everyday pace | This month's everyday spending, cumulative, against the typical month (median line) and its usual range; today marker, hover crosshair | `pace?month=` |
| **Spending** · where it went | Groups → categories, one-offs left out, each with a 12-month sparkline, the total, on track and typical; a click focuses the view on that category (the same filter as Transactions) | `trends?month=` |
| Spending · movers | The categories furthest from typical; a click focuses | `trends` |
| Spending · 12 months | Bars stacked by group, **Everyday** (trends: one-offs left out) / **Everything** (every charge); a bar opens its month | `trends`, `months?n=12&end=` |
| Spending · top merchants, big purchases | The month's merchants (count, average, sparkline; a click searches the list); the one-offs, each with "Not a one-off" | `merchants?period=YYYY-MM`, `trends.one_offs`, `PATCH {one_off: false}` |
| **Cash flow** · in and out | 12 months: income up; fixed, yearly, everyday and one-offs stacked down; money set aside beside; the net line; the totals line | `cashflow?n=12&end=` |
| Cash flow · bank balance | History and the forecast with its likely range and lowest point; horizon 3 / 6 / 12 months | `balance?days=90\|180\|365` |
| Cash flow · fixed costs | Recurring charges at their own cadence ("₪117.30 / 2 months · ≈ ₪58.65 a month") with price changes and next dates, yearly charges, installment plans with what is left, regular income | `recurring`, `balance.forecast.plans`, `overview.fixed_monthly` |
| **Save** | Two tiles (could save ₪low–₪high a year with the number of ideas, saved so far ≈ a year) and the source line (the Bank of Israel rate and its date). **Ways to save** and **Spending to watch**, each in sections: Now (open, due or untimed), Coming up (open, `timing.from` ahead), In progress, Watching (the target's progress on the card), Done (the outcome on the card), **Your choices** (dismissed, accepted, snoozed, each with its date and its way back on the card: Flag again, Unsnooze). Every item stays listed whatever its status; an inactive one says "Dana no longer sees this"; a section collapses to its header and count. A card shows its title, summary and chips (saving a year or money at stake, effort, basis, timing, New). Open (click, Enter; Esc closes; one at a time): the outcome, the target (progress bar, on track, days left, past months, saved; Cut back / Edit target takes whole ₪ prefilled with `suggested_target`), the evidence (a sparkline of the series, its lines, a link to Transactions over all time or the named view), the math, the questions (chips), **Prices** (only on "Check prices" / "Check again": a question it needs first, a spinner while it runs, the offers table — stacked rows on a phone — with each offer's source, the check's date and sources, "prices may have changed", the error or the wait in words), the subscription lines (Keep / Cancel), the steps, "What to say" (the Hebrew script, right-to-left, Copy) and the actions of its status (open: I'm on it · I did it · Snooze a month · Not for me, or Cut back · Worth it; doing: I did it · Not doing it; done: Not done; watching: Edit target · Stop tracking). Each write updates the card in place, offers Undo and refetches. Not tied to the month switcher | `savings` · `savings/{key}` (every 3 s for up to 90 s while a check runs) · `PATCH savings/{key}` · `POST savings/{key}/check` · `POST /api/undo/{id}` |
| **Investments** | Tiles (value, today, profit over what was paid, cash); the value per day over 1 month / 3 months / 1 year with a hover crosshair, money put in or taken out marked on the line and left out of the change ("Up ₪1,200 since 18 Nov 2025, besides ₪20,000 put in"); allocation (the largest holdings, the rest, cash); holdings (units × price, share, profit); the activity. Not tied to the month switcher | `portfolio?days=365&trades=50` |
| **Meal card** | Tiles (left this cycle and until when, the pace: "₪40 a day" / "Lasts to 13 Dec" / "Used up", what went on the user's own card this cycle, meals); each cycle as a bar of its budget part and its own-card part, with its card line linked ("On your card 10 Nov" opens that transaction); places by visits with delivery vs restaurant; the latest meals (cancelled ones struck through). A card line's detail says which cycle it paid for and links here. Not tied to the month switcher | `meals?months=12` |
| **Transactions** | By purchase day (sticky headers with the day's spending), each row with its kind badge, search, chips for category / account / amount / only uncategorized, this month or all time, cursor paging on scroll | `transactions?…` |
| Detail (a pane from 1180 px, a sheet below) | Category picker (only this one / always for this merchant, default always), **Counts as** (the flows that fit the amount's sign; not on card bills), **One-off** and **Yearly** (Yes / No / Automatic), each with an Undo toast; note, same merchant, Ask Dana | `transactions/{id}` · `PATCH transactions/{id}` · `POST /api/undo/{id}` · `POST /api/chat/context {kind: "transaction", id}` → `danaOpenSession` |
| Accounts view | Each login's state, accounts, Sync now (429 / 409 in words), update login, remove (optionally with its transactions), add a bank or card | `sources`, `companies`, `POST/PATCH/DELETE sources`, `POST sources/{id}/sync` |

**Live.** `GET changes?since=` every 30 s while the tab is open and on focus / visibility, every 5 s while a sync runs or a code is pending (and for 4 minutes after Sync now, a sent code or a new login); when `changed`, the page refetches and keeps the scroll, the open detail, the filters and anything being typed (the list refetches the rows already loaded, up to 200). The service worker never caches `/api/money/`.

**State in the URL**: `#money/<spending|cashflow|save|investments|transactions>?month=2026-10&view=accounts&q=&category=&account=&min=&max=&all=1` (the Overview has no path part; `replaceState`, only what differs from the default; the filters whatever the view, so a category focused on Spending carries to Transactions; cleared when the tab closes). Credentials travel only in the POST / PATCH bodies: the login form's secret fields are password inputs, never prefilled, never kept after a successful save, never in the URL, the console or local storage.

Keys: `/` search, `j` / `k` or the arrows move through the list, Enter opens, Esc closes, Ctrl+Enter saves a note.

## Phone app
