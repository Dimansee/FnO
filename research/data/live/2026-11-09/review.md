# Daily review 2026-11-09

## NIFTY
- Open 25,076.4 · high 25,084.0 · low 24,838.8 · close 25,044.7 · gap +0.18% vs prev close 25,030.3
- Open→close -0.13% (-32 pts), range 0.98% → range / two-way day; CPR width 0.86% (wide)
- Opening 5-min candle: high 25,084.0, low 24,912.5; morning (to 11:30) high 25,084.0, low 24,838.8
- Autopilot signals:
  - 09:36 fib PE → **Against a big 3-day run (round 42)** · 
  - 09:45 camarilla PE → **Against a big 3-day run (round 42)** · 
  - 09:55 fib PE → **Against a big 3-day run (round 42)** · 
  - 10:40 camarilla PE → **Against a big 3-day run (round 42)** · 
  - 11:20 camarilla PE → **Against a big 3-day run (round 42)** · 
  - 12:05 camarilla PE → **Against a big 3-day run (round 42)** · 
  - 12:05 fib PE → **Against a big 3-day run (round 42)** · 
  - 12:25 camarilla PE → **Against a big 3-day run (round 42)** · 
  - 12:45 camarilla PE → **Against a big 3-day run (round 42)** · 
- Book caught +40 index pts of a -32-pt day.

## BANKNIFTY
- Open 59,219.9 · high 59,741.2 · low 58,877.8 · close 59,741.2 · gap +0.23% vs prev close 59,086.0
- Open→close +0.88% (+521 pts), range 1.46% → **trend day**; CPR width 1.01% (wide)
- Opening 5-min candle: high 59,393.4, low 59,031.3; morning (to 11:30) high 59,393.4, low 58,877.8
- Autopilot signals:
  - 09:25 camarilla CE → **Executed** · 1 lot · exit 10:10 Time stop (45 min, no follow-through) · P&L Rs +1,968
  - 11:31 fib CE → **No automatic attempt logged** · 
- Book caught -36 index pts of a +521-pt day.

## SENSEX
- Open 81,327.3 · high 81,339.4 · low 80,519.3 · close 81,106.6 · gap +0.66% vs prev close 80,793.3
- Open→close -0.27% (-221 pts), range 1.01% → range / two-way day; CPR width 0.70% (wide)
- Opening 5-min candle: high 81,339.4, low 80,769.0; morning (to 11:30) high 81,339.4, low 80,519.3
- Autopilot signals:
  - 09:41 fib PE → **Against a big 3-day run (round 42)** · 
  - 09:55 fib PE → **Against a big 3-day run (round 42)** · 
  - 10:06 fib PE → **Against a big 3-day run (round 42)** · 
  - 10:26 fib PE → **Against a big 3-day run (round 42)** · 
  - 10:41 camarilla PE → **No automatic attempt logged** · 
  - 12:05 fib PE → **Against a big 3-day run (round 42)** · 
  - 12:20 fib PE → **Against a big 3-day run (round 42)** · 

## Blocks without a signal
- {"cat": "slot_busy", "entry": null, "from": "09:26", "label": "A trade was already open in this slot", "msg": "You already have an open signal trade in this instrument.", "n": 45, "ok": false, "opt": 

## Book
- Autopilot trades: 2, P&L Rs +4,033; scalp book: 2 trades, Rs -1,137

## Where we lagged today
- NIFTY: **wide_cpr** — r71: wide-CPR days are skipped on purpose (they lost in every year).
- BANKNIFTY: **trail_exit** (628.2 pts) — r23 / r40: holding to 15:15 or wider trails win on such days but lose overall.
- BANKNIFTY: **wide_cpr** — r71: wide-CPR days are skipped on purpose (they lost in every year).
- SENSEX: **range_day** — r32 / r67 / r69 / r70 / r72: level bounces and range rotations have no edge after option costs; Autopilot skipping range days is by design.
- SENSEX: **wide_cpr** — r71: wide-CPR days are skipped on purpose (they lost in every year).

## Last 20 reviewed sessions
- range_day: 18×
- no_trade_trend: 13×, 7,313 pts ← repeated: check whether its last test is older than 60 sessions
- trail_exit: 9×, 2,986 pts ← repeated: check whether its last test is older than 60 sessions
- range_day_loss: 8×, Rs 19,809 ← repeated: check whether its last test is older than 60 sessions
- wide_cpr: 6×
- breakeven_loss: 3×, Rs 4,188 ← repeated: check whether its last test is older than 60 sessions
- wrong_way: 1×, Rs 8,321

## Judgement (Claude, post-market)
*Written on 10 Nov: Tuesday 10 Nov was a market holiday (Diwali Balipratipada, no index candles), and Monday's data only landed today, so this is Monday's review.*

**What mattered**
1. **Quiet, two-way Monday.** Nifty −0.1% and Sensex −0.3% open→close (range days); only Bank Nifty trended (+0.9%, +521 pts, closing at the high).
2. **Autopilot: 1 trade, Rs +1,968.** Bank Nifty 59000 CE at 09:25, time-stopped at 10:10 (no follow-through). The book's "+4,033" includes your manual Nifty 24950 CE (11:36→11:38, +Rs 2,064). The Bank Nifty rally came after 11:30 (morning high 59,393 → close 59,741), so "trail_exit 628 pts" is the early exit missing a late move — r23/r40 say holding longer loses overall.
3. **The round-42 filter saved money.** All 15 Nifty/Sensex PUT signals were blocked as "against a big 3-day run". Every one of the day's 17 signals would have hit its stop (−17R in the journal). Working as designed.
4. **Data quirk: the app used Sunday's Muhurat session as "yesterday".** The app's CPR was narrow (~0.15–0.19%, so it sized the Bank Nifty trade at 1.2×) and its gap figures were off (Bank Nifty +0.75% vs a real +0.23%). Measured from Friday 6 Nov, CPR was wide on all three (0.70–1.01%), which r71 would have skipped. So today's 3× "wide_cpr" lags are not real skips; the app never skipped anything for wide CPR. This only happens once a year (Muhurat day), but the app should skip the Muhurat session when it builds prior-day levels (CPR, pivots, Camarilla, gap, 3-day run). That's a data fix, not a rule change. Say the word and it can be done.
5. Opening-candle retest (r72) did not fire.

**Your action**
- Settings look fine: auto on, all three indices ticked, all four strategies on.
- Scalp book −Rs 1,137 (2 trades, Sensex PE −402 and a Nifty trade). Nothing to change.

**Rule questions**
- The repeated lags (no_trade_trend 13×, trail_exit 9×, range_day_loss 8×, breakeven_loss 3×) were all tested within the last 60 sessions (r23/r24, r33, r40, r65, r71, r74/r75, r92 recheck). **No backtest, no rule change.**
- The wide_cpr count (6×) is inflated by today's Muhurat artifact. r71 still stands.
