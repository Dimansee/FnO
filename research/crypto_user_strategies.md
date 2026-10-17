# Your strategies on crypto (17 Oct 2026)

The strategies you gave the India app, re-coded for 24-hour crypto (UTC day = session) and run on BTC, ETH, SOL, BNB, XRP.
Data: Binance spot 5m Jan 2024 - Oct 2026 (new, research-data branch) and 1h Jan 2023 - Oct 2026. Costs 8 bps a side (app's crypto cost).
Design = before 1 Apr 2025, unseen = after. Your parameters, no tuning. Script: research/crypto_user_strategies.py; full numbers in crypto_user_strategies_results.json.
Pass = profit in both periods, unseen PF >= 1.1, profitable on at least 3 of 5 coins.

## Result: 0 of 32 passed

| Strategy (unseen period) | Trades | Avg per trade | Before costs | Win % | PF |
|---|---|---|---|---|---|
| India Noise band, 5m | 6,189 | -0.13% | +0.03% | 17 | 0.64 |
| India Camarilla H4/L4, 5m | 1,942 | -0.12% | +0.04% | 17 | 0.74 |
| D1 break reversal, D1 target, 5m | 1,574 | -0.12% | +0.04% | 15 | 0.75 |
| Chandelier Exit 1/2, 5m / 15m / 1h / 4h | 65,520 / 18,672 / 4,312 / 1,116 | -0.16 / -0.16 / -0.11 / -0.02% | ~0 to +0.15% | 20-32 | 0.44-0.99 |
| Chandelier + ZLSMA 50 (any timeframe) | | -0.15 to -0.19% | | | 0.38-0.87 |
| SMA200 + RSI(10), 1h / 4h / daily | 257 / 56 / 5 | +0.02 / -0.47 / +4.7% | | 62-80 | 1.04 / 0.67 / 3.6 |
| 5 EMA alert candle, 5m / 15m / 1h | 62,211 / 17,991 / 4,761 | -0.17 / -0.19 / -0.25% | | 22 | 0.38-0.67 |
| 44 MA bounce, 1h / 4h / daily | 951 / 223 / 32 | -0.18 / -0.36 / +0.32% | | 32-38 | 0.75-1.12 |
| Inside-bar breakout, 1h / 4h / daily | 3,300 / 907 / 122 | -0.08 / +0.10 / -0.62% | | 31-37 | 0.85-1.07 |
| Holy Grail, 1h / 4h | 132 / 38 | -0.05 / +0.01% | | 37-39 | 0.93-1.01 |
| RSI 60/40, 15m / 1h / 4h | 7,200 / 1,804 / 442 | -0.12 / -0.08 / -0.04% | | 23-29 | 0.80-0.98 |
| NR7 breakout, 4h / daily | 2,488 / 433 | -0.21 / +0.14% | | 34-48 | 0.53 / 1.20 |

Closest misses: inside-bar 4h (design -0.00%), NR7 daily (design -0.04%), SMA200 + RSI(10) daily (17 trades in ~4 years: too few to trust).

## Reading
- Every 5m and 15m rule is flat before costs and loses after them: fees decide, exactly as slippage did on Nifty.
- Nothing here beats the trend rules already trading crypto (Supertrend 4h about +0.15R a trade on the same unseen period).
- Live rules unchanged. Research-tab card `cus`.
