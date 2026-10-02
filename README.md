# F&O Trainer – web version

Live at https://fno-trainer.vercel.app (Vercel project "fno-trainer", region Mumbai).
Data lives in Supabase project "fno-trainer" (tables fno_settings, fno_positions, fno_trades).

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
- fno/store.py          – Supabase storage
- tests/test_app.py     – offline test (python tests/test_app.py)

## Vercel environment variables (already set)
SUPABASE_URL, SUPABASE_KEY, DB_SECRET, SESSION_SECRET, CRON_SECRET, APP_PASSWORD

## Scheduler
Supabase pg_cron job "fno-tick" calls /api/tick every minute on weekdays;
the app only acts between 09:15 and 15:30 IST.

## Deploying changes
This repo is connected to the Vercel project: every push to `main` deploys automatically.
