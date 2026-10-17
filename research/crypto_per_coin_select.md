# Crypto: "best setup per coin" and catch-up entries (17 Oct 2026)

Question from the user: watch BTC, ETH, SOL, BNB, XRP all the time, find the setup with the best win % and profit on each coin, and trade it automatically.

## 1. Should each coin trade its own "best" setup? No.
Walk-forward test (research/crypto_per_coin_select.py): every quarter from Jan 2024, each coin picks the setup that did best on its own last 12 months out of 60 (10 textbook families x 1h/4h/1d x long/both; needs >=12 trades, avg > +0.05R, PF >= 1.1), then trades it the next quarter, which the choice never saw. App engine and costs (16 bps round trip).

| Method | Unseen trades | Avg R | Win % | Total R | PF | t |
|---|---|---|---|---|---|---|
| Pick best avg R per coin | 336 | +0.02 | 30 | +7.6 | 1.04 | 0.2 |
| Pick best win % per coin | 455 | +0.02 | 42 | +6.7 | 1.03 | 0.2 |
| Fixed live trend rules (Supertrend 4h, Donchian 20 daily, Supertrend daily) | 621 | +0.23 | 32 | +142 | 1.38 | 2.0 |

Chasing each coin's recent best setup (or its highest win rate) is break-even after costs: what worked on one coin last year mostly does not carry over. The fixed trend rules pooled across coins did far better (part of 2024 overlaps their design period, so read +0.23R as optimistic; their pure holdout was +0.15R).
Note on win %: the profitable rules win only ~30-35% of trades and make money from a few large winners. High-win-rate setups (mean reversion, small targets) lost after costs in every earlier test. Ranking by win % is the wrong target; average R after costs is the right one.

## 2. The real reason the book looked idle
- The engine only started running on Render this morning (17 Oct). Before that nothing ticked the crypto book.
- The weekly gate passes 3 of 4 crypto rules today (Supertrend 4h on BTC/ETH/SOL/XRP and others; Donchian 20 daily; Supertrend daily). They are slow: about 1-4 entries per coin per month.
- Right now (17 Oct) every coin is flat: BTC/ETH/SOL/XRP 4h trends are down, so the next entries are a long flip 3-5% above price or new short signals.

## 3. Catch-up window (shipped)
A signal used to be dropped if it was more than 10-20 min old (e.g. host asleep at the 4h bar close). Test: entering the live crypto trend rules late (still within 1 ATR of the original entry, stop not hit):

| Rule | on time | 1 bar late | 2 bars | 4 bars |
|---|---|---|---|---|
| Supertrend 4h | +0.085R | +0.080R | +0.049R | -0.032R |
| Donchian 55 4h | +0.134R | +0.112R | -0.112R | -0.008R |
| Supertrend daily | +0.80R | +1.01R | +1.07R | +0.39R |

Shipped: rules on 4h/daily bars may enter up to 4 hours after the signal bar closed (fast bars unchanged); the half-ATR price guard still applies.

## 4. Live scanner (shipped)
Markets tab -> Crypto/US: one row per watched coin and active rule: price, last signal, and either "in a trade" or the exact close that fires the next entry (e.g. "buy if a 4h bar closes above 83,990 (+2.9%)"). Coins with no rule working lately are shown as watched only.
