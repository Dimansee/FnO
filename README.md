# F&O Trainer – web version

Live at https://fno-trainer.vercel.app (Vercel project "fno-trainer", region Mumbai).
Data lives in Upstash Redis (free tier, added via Vercel Storage): hashes fno:settings,
fno:positions and fno:trades.

## Structure
- app.py                – Flask web app / API (Vercel entrypoint)
- templates/index.html  – the whole dashboard UI
- fno/config.py         – all strategy rules and settings
- fno/strategy.py       – the rulebook (signals, order sizing, exits)
- fno/market.py         – data: Upstox / Fyers / Yahoo + theoretical option prices
- fno/brokers.py        – Upstox and Fyers API clients (read-only)
- fno/paper.py          – demo money, positions, journal
- fno/service.py        – glue between the above
- fno/backtest.py       – backtester
- fno/context.py        – global markets, news, FII/DII
- fno/store.py          – Upstash Redis storage (REST API)
- fno/scheduler.py      – registers the every-minute QStash schedule
- tests/test_app.py     – offline test (python tests/test_app.py; needs `pip install "fakeredis[lua]"`)

## Vercel environment variables (already set)
APP_PASSWORD, SESSION_SECRET, CRON_SECRET (set by hand)
KV_REST_API_URL, KV_REST_API_TOKEN (added by the Upstash Redis integration)
QSTASH_TOKEN, QSTASH_URL (added by the Upstash QStash integration)

## Scheduler
Upstash QStash schedule "fno-tick" calls /api/tick every minute on weekdays
09:30-15:29 IST (~360 calls/day, under the 500/day free limit), registered
automatically by the app.

## Deploying changes
This repo is connected to the Vercel project: every push to `main` deploys automatically.
