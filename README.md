# F&O Trainer – web version

Runs on Render (see render.yaml; free web service, Singapore). Earlier host: Vercel (https://fno-trainer.vercel.app).
Data lives in Upstash Redis (free tier): hashes fno:settings,
fno:positions and fno:trades.

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
