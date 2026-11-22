# Money (phone)

The Money place sits under More (only with the `money` addon) and takes it over: `ui/shell/destinations/MoneyDestination.kt` is a wide place — unfolded, a side pane (header, month switcher, "Synced …", the banners, the views as rows) beside a ~808dp stage holding the view, with a transaction or saving item as a 380dp pane beside it when the stage is ≥ 760dp, else over it; on the cover one column (header, banners, the views as a sideways strip, the view) with the item full screen and Back closing it. The live loop (`GET changes` every 30 s, 5 s while a sync runs or a code is pending) runs while Money is on screen. `ui/viewmodels/MoneyController.kt` is its core: the view, the month, the filter, the open detail, the overview, sources and categories, the revision that bumps after a write or a sync, and `write()` (a change with Undo). The server contract (payloads, categories, the forecast, the meal card and portfolio rules) is `server/docs/money.md`; the web Money tab (`server/static/money-dash.js`, `money-core.js`, `money.css`) is the reference for numbers, words and look.

## Dashboards

Overview, Spending, Cash flow, Investments and Meal card. The numbers and words match the web's, so the pure parts are ports of `money-core.js` and are tested on the same captured payloads.

| Where | What |
|---|---|
| `data/money/MoneyDashModels.kt` | Typed parsers of each payload the dashboards read: `overview`, `insights`, `pace`, `balance` (history + forecast: series, events with card bill parts, lows, installment plans), `cashflow`, `trends`, `months`, `merchants`, `recurring`, `portfolio`, `meals` |
| `data/money/MoneyDashText.kt` | The words: money / compact axis values, dates and months, Hebrew isolation, the tiles (net, everyday, balance ahead, fixed), vs typical, coming up and card bill parts, cadences and installment plans, the forecast note, price changes, percentages / units / unit prices, the value change, allocation slices, activity, account names, the meal card's tiles and cycle bars |
| `data/money/MoneyDashGeometry.kt` | The charts' geometry in pixels: ticks, the pace chart, 12-month bars (from `months` or `trends`), cash flow in / out, time lines (bank balance and forecast, portfolio value), sparklines. Pure, so its numbers are the web's |
| `ui/viewmodels/MoneyDashState.kt` | `money.dash`: what each view needs, the requests (a payload counts only while its request matches, so a month switch shows skeletons and a refresh keeps the old data on screen), the switches (balance horizon, investment range, Everyday / Everything, open bills, expanded rows), `forget()` |
| `ui/screens/money/charts/MoneyCharts.kt` | Compose Canvas charts with a touch readout (`ChartFrame`): drag sideways to read a point or month; a tap pins or clears the point on a line chart and opens the month on a bar chart; the series palette (`MoneyPalette`), legends, sparkbars |
| `ui/screens/money/MoneyDashViews.kt` | The shared frame (`DashView`: loads on view / month / revision / horizon, one column or two), sections, rows, tiles, skeletons |
| `MoneyOverviewView.kt`, `MoneySpendingView.kt`, `MoneyCashflowView.kt`, `MoneyHoldingsViews.kt` | The views |

Layout: one column when folded. Unfolded, the stage takes two columns once it is at least 600dp wide (landscape); in portrait it stays in one column. Tiles sit four across in two columns and two by two in one. Charts take the full width of their column.

### Overview

- Tiles: Net so far (opens Cash flow), Everyday spending (Spending), Balance ahead (Cash flow) for the month in progress, or One-offs (Spending) for a past month, and Fixed costs (Cash flow).
- Insights, each with a tone bar. A link with a transaction id opens the transaction; a link with a category focuses that category in its view (as on the web). "N to sort" opens Transactions filtered to uncategorized.
- Coming up (the month in progress only): the next 45 days of the forecast, with a caption, a sparkline marking the 45-day low, and rows grouped by day. An estimated card bill expands into its parts (already on the card, installments, fixed, yearly, everyday), its items and its range.
- Everyday pace: this month adds up day by day against the typical month and its usual range; dragging reads each day.
- Unfolded: Insights | Coming up, with the pace across both columns below. Without a forecast: Insights | Pace.

### Spending

- Where it went: groups with sparkbars and vs typical. A row expands into its categories. The focused category (the filter's) dims the others; its chip offers "See its transactions" and ×.
- Movers against a typical month (a tap focuses the category).
- 12 months: stacked bars in the fixed series order. Everyday (from `trends`, one-offs left out) or Everything (`months`). A tap on a bar opens that month.
- Top merchants (a tap searches its transactions over all time).
- Big purchases: the month's one-offs, each with "Not a one-off" (`PATCH /transactions/{id}` `one_off: false`, with Undo).

### Cash flow

- Money in and out: income above the line; fixed, yearly, everyday and one-offs stacked below; money set aside as a thin bar beside; the net as a line. Below it the legend, the 12-month totals and how to read it. A tap opens the month.
- Bank balance: 3, 6 or 12 months ahead (the same length of past). The history is solid, the forecast dashed with its likely range, and the low point is marked. The forecast note follows.
- Fixed costs: recurring charges by cadence (a bimonthly charge reads "/ 2 months ≈ … a month", never "/ month"), with price changes, yearly charges, installment plans and regular income. A tap on a row searches its transactions.

### Investments

The tab appears only with an investment account.

- Tiles: value (updated time), today since the morning, profit over what was paid, cash share.
- Portfolio value over 1 month, 3 months or 1 year. Money put in or taken out is marked on the line and left out of the change sentence.
- Allocation (bar + rows, the largest holdings, then Other holdings and Cash), holdings, and activity (buys, sells, dividends with tax, money in / out, the site's own words for other actions).
- Read-only: Dana never places orders.

### Meal card

The tab appears only with a meal card.

- Tiles: left this cycle, pace (a day / lasts to / used up), on your own card, meals.
- Cycles, newest first: from the budget and on your card. The card part links to its card line ("On your card <date>" opens the transaction); otherwise it reads "Not on your card yet".
- Places, and the latest meals (cancelled ones struck through, the own-card part noted).

### Tests

`app/src/test/.../money/MoneyDashTextTest.kt`, `MoneyDashGeometryTest.kt` and `MoneyDashHoldingsTest.kt` run against `money_web.json`, `money_portfolio_web.json` and `money_meals_web.json`, read straight from `server/tests/fixtures/` (synthetic data, today 18 Nov 2026). Their expectations are those of the server's `test_web_money.mjs`. Every amount is written by `data/money/MoneyFormat` in the user's currency (`/api/me` `currency`; USD until it is known) and the phone's locale; the unit tests pin en-US and the fixtures' ILS.

## Transactions, Accounts, Save

`money.ledger` (`ui/viewmodels/MoneyLedgerState.kt`) holds their state and writes; the pure parts are `data/money/MoneyLedger.kt` (transaction, account and taxonomy models, day groups, badges, words) and `data/money/MoneySave.kt` (Save sections, tiles, chips, actions, toasts, price checks, offer rows, outcomes, targets), tested by `MoneyLedgerTest.kt` / `MoneySaveTest.kt` against `money_web.json` and `money_savings_web.json`. Views: `MoneyTransactionsView.kt`, `MoneyTxnDetail.kt`, `MoneyAccountsView.kt` (Accounts and the banners), `MoneySaveView.kt`, shared bits in `MoneyLedgerParts.kt`; `MoneyLedgerViews.kt` holds the entry points the destination calls.

- **Transactions**: `GET transactions` with `period` (left out for all time), `q`, `category`, `account`, `min`, `max`, cursor pages of 50 merged without repeats, grouped by purchase day under headers with the day's spending (refunds reduce it; money in shown apart). The filters are `money.filter`, so another view (a category on Spending, "N to sort") opens Transactions focused. Wide, the rows get more columns.
- **Detail** (`GET transactions/{id}`): category (this one or always for the merchant), Counts as (the flows that fit the sign; not on card bills), One-off / Yearly (Yes / No / Automatic), note, same merchant, memo / installment / foreign, the meal cycle a card line paid for, Ask Dana (`money.ask` → the thread with on-screen context `transaction`). Each change is a `PATCH` whose `undo_id` gives the Undo snackbar. A view that shows full screen on the cover reloads on `revision` itself, so Undo refreshes it.
- **Accounts**: `GET sources` / `companies`; Sync now (`POST sources/{id}/sync`: 202 → `watchClosely`, 429 "Try again in N min", 409 the reason); add (`POST sources`), update login (`PATCH`), remove (`DELETE`, optionally with the accounts and their transactions, asked first). Login fields are password inputs, never prefilled, cleared after a successful save, never logged; a 400 naming a field shows at that field. Two columns of logins when wide.
- **Banners**: a pending SMS code (from a source's `code_request`: countdown, 4–8 digits checked before sending) and the logins that need attention (the overview's `attention`, with Sync now).
- **Save**: `GET savings` (tiles and the Bank of Israel line); Ways to save (Now / Coming up / In progress / Done / Your choices) and Spending to watch (Now / Watching / Your choices). Dismissed, accepted, snoozed and targeted items always stay listed with their way back. `PATCH savings/{key}` (`status`, `snooze_days`, `answers`, `target`, `subitem`) with Undo; Check prices = `POST savings/{key}/check`, then `GET savings/{key}` every 3 s for up to 90 s (`needs_answer`, `too_soon`, `unavailable` in words); offers as rows with their sources. The two parts side by side when wide.
