# F&O Trainer – web version

Runs on Render (see render.yaml; free web service, Singapore). Earlier host: Vercel (https://fno-trainer.vercel.app).
Data lives in Supabase Postgres (DATABASE_URL, fno/pgkv.py) since 28 Oct 2026; Upstash Redis is the old store and the
source of the one-time copy on 1 Nov (fno/store.py: portfolio and scalp book from Upstash, newer research / rules / learning kept).

## Round-level break slot on Nifty (round 102, 19 Nov 2026)
- Autopilot trades Nifty when a 15-min candle closes through a round 100-point level (zone ±25) that rejected price at least
  once on that side in the last 5 sessions: CALL up / PUT down, bought in the first 5 minutes of the next 15-min bar (bars
  09:30-14:45). Stop at the signal bar's low / high; once ahead, the stop follows the best 15-min close 1R behind; else 15:15.
  Own slot (`rlevel`), half size, any equity, up to 3 a day, one at a time. Conviction and CPR sizing apply; the big-run skip
  and the round-99 profit trail do not.
- Code: `strategy.rlevel_events / rlevel_signals / rlevel_state / evaluate_rlevel / rlevel_trail`, `service.rlevel_allowed`,
  `backtest._rlevel_trades` (Outcomes and Backtest), `portfolio_bt.replay(rlevel=...)`. Constants `C.RLEVEL_*`.
  Switch: Research → Changes (`rlevel_slot`); Outcomes / portfolio backtest box "Round-level break slot (Nifty)".
- Tests: `tests/test_rlevel_r102.py`; live vs backtest signals: `research/round102/parity.py` (1,197 of 1,197).
  Research: `research/round102/` and the round-102 entry in the Research log.

## Trade page, manual stop-loss / target, one home per feature (13 Nov 2026)
- **Trade** (was Signal + Option chain): hero, open positions, entry checklist, one order ticket with two tabs (Signal trade /
  My own order), charts, option chain (near the money or full chain with any expiry), options flow.
- **Manual orders carry their own exits.** `POST /api/trade/manual` takes optional `sl`, `target`, `trail` (the option's own
- **Squeeze-release shadow slot (round 101).** `fno/squeeze.py` checks the squeeze-release rule on Sensex every minute and records what it would have done (index levels, live at-the-money option bid / ask / price at entry and exit). It places no orders and has no ledger of money; `GET /api/squeeze` shows it (Research page and Autopilot → Improvements), `POST /api/squeeze/settings {on}` stops or starts the recording, and the nightly journal carries it as `squeeze_shadow`. Tests: `tests/test_squeeze_shadow.py`; trade-by-trade parity with the backtest: `research/round101/n_shadow_parity.py` (569 of 569).
  price in rupees); `POST /api/trade/modify` changes or clears them later. `service.manual_levels` validates and stores them,
  `service.manage_positions` (the minute tick) enforces them, `_manual_trail` moves a trailing stop. A manual trade without
  levels is only closed by you or the 15:15 square-off. Rule trades keep their rule's index stop / target and cannot be edited.
- **Open trades are drawn on the charts** (`paneLevels` in the page): entry / SL / target of every open position, plus the
  order or edit being typed. `service.position_view` is the one shape both pages use.
- **Market session** (`service.session_info`): open / square-off / closed (after hours, weekend, holiday) with the next open.
  The dashboard shows "Market closed" after 15:30 (it used to say "No new entries"), and paper orders are refused outside
  09:15-15:15 (they used to open and be squared off at once, with charges).
- **Menu: 20 items down to 15.** Merged: Option chain into Trade; Recommendations into Stocks (first view); Options rulebook,
  Stock rulebook and Glossary into Rules & glossary; Autopilot -> Chart into Levels & replay; Autopilot -> Learning applied
  into Learning. Old links (`#chain`, `#recs`, `#rules`, `#glossary`, `#stockrules`, `#autopilot/chart`) still open the right page.
- **One place per setting:** Autopilot switch, auto-trade, instruments, weekly limit and confidence only in Autopilot ->
  Settings; account amount / starting capital / P&L reset only in Settings -> Paper account money; option-chain recorder
  status on Saved data.
- Test: `python tests/test_manual_levels.py` (also runs on Postgres with `TEST_DATABASE_URL`). UI check with fake data:
  `python tests/serve_mock.py` (`MOCK_NOW=2026-10-01T18:05` shows the closed-market pages).

## Autopilot tab, live chart, journal decisions (31 Oct 2026)
- Autopilot tab: Status (weekly / daily limit, restart weekly count), Decisions, Outcomes, Improvements (weekly review,
  fno/lag.py weekly()), Rules, Settings, Safety (capital shield). The chart is the Levels & replay page.
- Capital shield (service.shield_state): floor % of capital, max open premium % of equity, max planned loss per trade.
- Journal: every automatic attempt is logged (journal.note_auto) and linked to its signal: executed, or not executed + why,
  and what the skipped signal would have done (settled after 15:15).
- Live chart (/api/chart/live): levels, noise band, S/R zones, signals, trades with entry / SL / trailing SL / target, replay.

## Structure
- app.py                – Flask web app / API (gunicorn app:app)
- templates/index.html  – the whole dashboard UI
- fno/config.py         – all strategy rules and settings
- fno/strategy.py       – the rulebook: noise-area momentum (signals, order sizing, exits)
- research/             – the 2023-26 strategy study that chose the rules (see research/README.md)
- fno/market.py         – data: Upstox / Fyers / Yahoo + theoretical option prices
- fno/brokers.py        – Upstox and Fyers API clients (read-only)
- fno/paper.py          – demo money, positions, journal
- fno/service.py        – glue between the above
- fno/backtest.py       – backtester
- fno/gbacktest.py      – crypto / US / gold backtest in money: one coin or the whole book, sized like the paper account
- fno/context.py        – global markets, news, FII/DII
- fno/store.py          – Upstash Redis storage (REST API)
- fno/scheduler.py      – registers the every-minute QStash schedule
- tests/test_app.py     – offline test (python tests/test_app.py; needs `pip install "fakeredis[lua]"`)

## Environment variables (Render dashboard > Environment)
APP_PASSWORD, SESSION_SECRET, CRON_SECRET (set by hand)
KV_REST_API_URL, KV_REST_API_TOKEN (from console.upstash.com > your Redis database > REST API)
QSTASH_TOKEN, QSTASH_URL (from console.upstash.com > QStash)

## Scheduler
Upstash QStash schedule "fno-tick" calls /api/tick every minute on weekdays
09:30-15:29 IST (~360 calls/day, under the 500/day free limit), registered
automatically by the app.

## Deploying changes
The Render service is connected to this repo: every push to `main` deploys automatically.
GitHub Actions read the app address from the repository variable APP_URL (Settings > Secrets and variables > Actions > Variables).
QStash schedules re-register themselves to the new address the first time the app is opened (also adds "fno-warm" at 08:50 IST to wake a sleeping free host).
