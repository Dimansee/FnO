# Daily review 2026-11-11

## NIFTY
- Open 25,337.8 · high 25,424.3 · low 25,095.3 · close 25,255.0 · gap +1.17% vs prev close 25,044.7
- Open→close -0.33% (-83 pts), range 1.30% → range / two-way day; CPR width 0.22% (narrow)
- Opening 5-min candle: high 25,377.4, low 25,263.8; morning (to 11:30) high 25,424.3, low 25,263.8
- Autopilot: no signal in this index.
- Your opening-candle retest (r72) fired: PUT at 14:10 25,255.8 (level 25,263.8, top 25,162.7, stop 25,269.1) → out 15:00 at 25,167.7 = +88 pts (+6.63R) · 25250 PE 273.9 → 326.7 (+19%)

## BANKNIFTY
- Open 60,524.7 · high 61,394.3 · low 60,376.1 · close 60,792.4 · gap +1.31% vs prev close 59,741.2
- Open→close +0.44% (+268 pts), range 1.68% → range / two-way day; CPR width 0.48% (middle)
- Opening 5-min candle: high 60,590.8, low 60,376.1; morning (to 11:30) high 61,241.9, low 60,376.1
- Autopilot signals:
  - 10:45 noise CE → **Executed** · 1 lot · exit 12:45 Trailing exit at 12:45: back inside the band / below VWAP · P&L Rs -496
  - 10:46 trend CE → **No automatic attempt logged** · 
- Book caught -21 index pts of a +268-pt day.
- Your opening-candle retest (r72) fired: CALL at 13:55 60,613.8 (level 60,590.8, top 61,394.3, stop 60,580.8) → out 15:00 at 60,580.8 = -33 pts (-1.00R) · 60600 CE 1592.9 → 1596.0 (+0%)

## SENSEX
- Open 82,248.7 · high 82,520.4 · low 81,417.4 · close 81,503.9 · gap +1.41% vs prev close 81,106.6
- Open→close -0.91% (-745 pts), range 1.34% → **trend day**; CPR width 0.15% (narrow)
- Opening 5-min candle: high 82,359.6, low 81,969.9; morning (to 11:30) high 82,520.4, low 81,969.9
- Autopilot: no signal in this index.
- Your opening-candle retest (r72) fired: PUT at 14:10 81,913.7 (level 81,969.9, top 81,611.1, stop 81,959.2) → out 15:00 at 81,627.4 = +286 pts (+6.29R) · 81900 PE 834.1 → 1013.8 (+22%)

## Blocks without a signal
- {"cat": "slot_busy", "entry": null, "from": "10:46", "label": "A trade was already open in this slot", "msg": "You already have an open signal trade in this instrument.", "n": 119, "ok": false, "opt":

## Book
- Autopilot trades: 1, P&L Rs -496; scalp book: 1 trades, Rs +590

## Where we lagged today
- NIFTY: **range_day** — r32 / r67 / r69 / r70 / r72: level bounces and range rotations have no edge after option costs; Autopilot skipping range days is by design.
- BANKNIFTY: **range_day_loss** (Rs 496) — sideways-flag tests (16 Oct, two rounds) / r33: no pre-open or first-30-min flag separates losing range-day breakouts from winners; every filter cut profit.
- SENSEX: **no_trade_trend** (744.8 pts) — r23 / r24: trend-day catcher exists; earlier or wider checks earned less.

## Last 20 reviewed sessions
- range_day: 19×
- no_trade_trend: 14×, 8,058 pts ← repeated: check whether its last test is older than 60 sessions
- trail_exit: 9×, 2,986 pts ← repeated: check whether its last test is older than 60 sessions
- range_day_loss: 8×, Rs 20,265 ← repeated: check whether its last test is older than 60 sessions
- wide_cpr: 6×
- breakeven_loss: 3×, Rs 4,188 ← repeated: check whether its last test is older than 60 sessions
- wrong_way: 1×, Rs 8,321

## Judgement (Claude, post-market)

**What mattered**
1. **Big gap-up, then a fade everywhere.** All three opened +1.2–1.4% up and gave back much of it. Sensex was the only trend day (−0.91% open→close, −745 pts); Nifty −0.33% and Bank Nifty +0.44% were two-way.
2. **Sensex trend day missed — no signal at all.** The fall stayed inside the noise band (band floor ~80,500; day low 81,417), because the band is anchored on yesterday's close and the gap put the open far above it. A gap fade like this is exactly what r79 (3 Nov) tested: gap-fade entries made money in 2023-24 but none in 2025-26, and random-direction twins did as well. So this miss is known and accepted.
3. **Autopilot: 1 trade, Rs −496.** Bank Nifty 60900 CE at 10:45 (0.85× size for middle CPR), trailed out at 12:45 back inside the band. A small range-day loss — r33 found no filter that removes these without cutting profit. The 10:46 trend CE was blocked because that trade was already open (slot busy), as designed.
4. **Your opening-candle retest (r72) would have done well today:** Nifty PUT +6.6R, Sensex PUT +6.3R, Bank Nifty CALL −1R. One good day; over 2023-26 r72 lost money after option costs in every year, so not a reason to add it.

**Your action**
- Settings look fine: auto on, all three indices ticked, all four strategies on.
- Account amount was set to Rs 25,000 twice this evening (19:13 and 19:35) and a manual Nifty trade closed 15:15 at −Rs 3,720 was removed from the ledger. If that was intended, nothing to do; if not, check the Portfolio tab.
- Scalp book +Rs 590 (Nifty PE). Fine.

**Rule questions**
- Repeated lags (no_trade_trend 14×, trail_exit 9×, range_day_loss 8×, breakeven_loss 3×) were all tested within the last 60 sessions (r23/r24, r33, r40, r74/r75, r79, r92 recheck on 7 Nov). **No backtest, no rule change today.**
- Still open from 9 Nov: skip the Muhurat session when building prior-day levels (data fix, not a rule change) — waiting for your go-ahead.
