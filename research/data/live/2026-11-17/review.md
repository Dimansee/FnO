# Daily review 2026-11-17

## NIFTY
- Open 25,277.3 · high 25,501.3 · low 25,267.3 · close 25,387.4 · gap -0.05% vs prev close 25,291.1
- Open→close +0.44% (+110 pts), range 0.93% → range / two-way day; CPR width 0.10% (narrow)
- Opening 5-min candle: high 25,358.0, low 25,267.3; morning (to 11:30) high 25,452.8, low 25,267.3
- Autopilot signals:
  - 09:56 fib CE → **Rule waits for a bigger account** · 
  - 10:20 camarilla CE → **Not enough money** · 
  - 11:16 noise CE → **Not enough money** · 
  - 11:16 trend CE → **Rule waits for a bigger account** · 
  - 11:45 noise CE → **Not enough money** · 
  - 12:15 noise CE → **Not enough money** · 
  - 12:45 noise CE → **Not enough money** · 
- Book caught +3 index pts of a +110-pt day.

## BANKNIFTY
- Open 61,559.9 · high 62,442.2 · low 61,488.0 · close 62,273.2 · gap -0.03% vs prev close 61,575.9
- Open→close +1.16% (+713 pts), range 1.55% → **trend day**; CPR width 0.12% (narrow)
- Opening 5-min candle: high 61,875.1, low 61,488.0; morning (to 11:30) high 62,374.2, low 61,488.0
- Autopilot signals:
  - 09:41 fib CE → **No automatic attempt logged** · 
  - 09:45 camarilla CE → **Executed** · 1 lot · exit 13:06 Profit trail stop (1.5R behind the best move after +2.5R) · P&L Rs +5,211
  - 10:16 noise CE → **A trade was already open in this slot** · 
  - 10:46 trend CE → **No automatic attempt logged** · 
  - 13:15 noise CE → **Executed** · 1 lot · exit 15:15 Square-off 15:15 · P&L Rs +2,666
- Book caught +417 index pts of a +713-pt day.

## SENSEX
- Open 82,150.6 · high 82,897.5 · low 82,091.9 · close 82,532.2 · gap +0.06% vs prev close 82,102.9
- Open→close +0.46% (+382 pts), range 0.98% → range / two-way day; CPR width 0.05% (narrow)
- Opening 5-min candle: high 82,349.6, low 82,091.9; morning (to 11:30) high 82,726.2, low 82,091.9
- Autopilot signals:
  - 09:56 camarilla CE → **No automatic attempt logged** · 
  - 09:56 fib CE → **Rule waits for a bigger account** · 
  - 11:15 noise CE → **Not enough money** · 
  - 11:16 trend CE → **Rule waits for a bigger account** · 
  - 11:45 noise CE → **Not enough money** · 
  - 12:15 noise CE → **Capital shield (account protection)** · 
  - 12:45 noise CE → **Capital shield (account protection)** · 
  - 14:15 noise CE → **Executed** · 1 lot · exit 14:45 Trailing exit at 14:45: back inside the band / below VWAP · P&L Rs -257
- Book caught -91 index pts of a +382-pt day.

## Blocks without a signal
- {"cat": "equity_gate", "entry": null, "from": "09:20", "label": "Rule waits for a bigger account", "msg": "Autopilot adds the Fibonacci breakout once equity reaches \u20b9200,000.", "n": 320, "ok": fa
- {"cat": "equity_gate", "entry": null, "from": "09:20", "label": "Rule waits for a bigger account", "msg": "Autopilot adds the Fibonacci breakout once equity reaches \u20b9200,000.", "n": 320, "ok": fa
- {"cat": "slot_busy", "entry": null, "from": "09:46", "label": "A trade was already open in this slot", "msg": "You already have an open signal trade in this instrument.", "n": 200, "ok": false, "opt":
- {"cat": "equity_gate", "entry": null, "from": "10:45", "label": "Rule waits for a bigger account", "msg": "Autopilot adds the trend-day catcher once equity reaches \u20b940,000.", "n": 235, "ok": fals
- {"cat": "equity_gate", "entry": null, "from": "10:45", "label": "Rule waits for a bigger account", "msg": "Autopilot adds the trend-day catcher once equity reaches \u20b940,000.", "n": 235, "ok": fals
- {"cat": "slot_busy", "entry": null, "from": "13:16", "label": "A trade was already open in this slot", "msg": "You already have an open signal trade in this instrument.", "n": 84, "ok": false, "opt": 
- {"cat": "slot_busy", "entry": null, "from": "14:16", "label": "A trade was already open in this slot", "msg": "You already have an open signal trade in this instrument.", "n": 24, "ok": false, "opt": 

## Book
- Autopilot trades: 5, P&L Rs +7,961; scalp book: 9 trades, Rs +1,347

## Where we lagged today
- BANKNIFTY: **trail_exit** (142.2 pts) — r23 / r40: holding to 15:15 or wider trails win on such days but lose overall.
- SENSEX: **range_day_loss** (Rs 257) — sideways-flag tests (16 Oct, two rounds) / r33: no pre-open or first-30-min flag separates losing range-day breakouts from winners; every filter cut profit.

## Last 20 reviewed sessions
- range_day: 21×
- no_trade_trend: 15×, 8,774 pts ← repeated: check whether its last test is older than 60 sessions
- range_day_loss: 9×, Rs 18,749 ← repeated: check whether its last test is older than 60 sessions
- trail_exit: 8×, 2,632 pts ← repeated: check whether its last test is older than 60 sessions
- wide_cpr: 5×
- breakeven_loss: 3×, Rs 4,188 ← repeated: check whether its last test is older than 60 sessions
- wrong_way: 1×, Rs 8,321

## Judgement (Claude, post-market)

**What mattered**
1. **Bank Nifty was a trend day (+1.16%, +713 pts) and Autopilot caught it.** Camarilla CE at 09:45 rode to 13:06 and closed on the profit trail for **Rs +5,211**; the 13:15 Noise CE added **Rs +2,666** at square-off. Together they caught 417 of 713 pts. The trail gave back 142 pts from the top; trail levels were tested last week (r98/r99), no change.
2. **Nifty and Sensex were two-way days (+0.4-0.5%).** Only one Sensex trade went through: Noise CE at 14:15, out at 14:45 back inside the band, **Rs -257**. Total Autopilot today **Rs +7,620** (book shows +7,961 incl. your two manual Nifty trades, net +341). Scalp book +1,347.
3. **9 signals were skipped for cash ("Not enough money" 7×, capital shield 2×).** While the Bank Nifty lot was open (Rs 12.8k premium), only about Rs 9.5k cash was left, and one Nifty/Sensex lot needed Rs 9.9-12.6k. This was lucky: all 8 of the skipped Nifty/Sensex Noise signals hit their stop (-1R each); the Nifty Camarilla at 10:20 ended flat (+0.05R).
4. **Bank Nifty Fibonacci (09:41, +3.4R) and trend (10:46, +0.4R) were not traded** because Autopilot only runs Noise + Camarilla on Bank Nifty (round 24/29). Same for the Sensex Camarilla at 09:56 (+1.3R). By design, not a fault.

**Your action**
- Settings fine: auto on, all three indices active.
- Capital is about Rs 22k. One Bank Nifty lot uses over half of it, so later signals on other indices get skipped for cash. The trend-day catcher (Rs 40k) and Fibonacci (Rs 2L) stay off until equity grows. Nothing to change unless you add capital.

**Rule questions**
- Repeated lags (no_trade_trend 15×, range_day_loss 9×, trail_exit 8×, breakeven_loss 3×) were all tested within the last 60 sessions (r23/r24, r33, r74/r75, r79, r92, r98/r99). **No backtest, no rule change today.**
- Still open from earlier reviews (waiting for your go-ahead, not pushed): relabel "No automatic attempt logged" as "Rule not used on this index (round 24/29)"; skip the Muhurat session when building prior-day levels.

## Shadow slot (squeeze release on Sensex, round 101 — no orders)
- Today: slot on, **no squeeze signal**, nothing logged, no errors.
- Running tally since 16 Nov (2 sessions): **1 signal, 0 wins, net Rs -1,386 per lot.** 16 Nov: SENSEX 81800 PE at 14:15 (1 s late into the bar), entry Rs 475.6 → exit Rs 423.05 at 15:15 square-off; r_live -0.30 vs r_rule -0.30 (live matches the rule). Bid-ask gap at entry 0.27%. Live prices on 100% of signals.
- No data problems. Decision on this slot is on 22 Dec, not in the daily run.
