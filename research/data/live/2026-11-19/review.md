# Daily review 2026-11-19

## NIFTY
- Open 25,186.8 · high 25,514.3 · low 25,104.8 · close 25,514.3 · gap -0.16% vs prev close 25,228.0
- Open→close +1.30% (+328 pts), range 1.63% → **trend day**; CPR width 0.10% (narrow)
- Opening 5-min candle: high 25,255.6, low 25,151.5; morning (to 11:30) high 25,327.6, low 25,104.8
- Autopilot signals:
  - 12:00 camarilla CE → **Executed** · 1 lot · exit 12:50 Time stop (45 min, no follow-through) · P&L Rs -22
- Book caught +3 index pts of a +328-pt day.

## BANKNIFTY
- Open 62,081.3 · high 63,063.0 · low 61,951.1 · close 63,063.0 · gap +0.07% vs prev close 62,038.7
- Open→close +1.58% (+982 pts), range 1.79% → **trend day**; CPR width 0.03% (narrow)
- Opening 5-min candle: high 62,261.9, low 61,951.1; morning (to 11:30) high 62,626.4, low 61,951.1
- Autopilot signals:
  - 10:00 camarilla CE → **Executed** · 1 lot · exit 11:00 Breakeven stop (moved to entry at +1R) · P&L Rs +221
  - 10:16 fib CE → **No automatic attempt logged** · 
  - 12:15 noise CE → **Not enough money** · 
  - 12:16 trend CE → **No automatic attempt logged** · 
  - 12:45 noise CE → **Executed** · 1 lot · exit 13:15 Trailing exit at 13:15: back inside the band / below VWAP · P&L Rs -523
- Book caught +5 index pts of a +982-pt day.

## SENSEX
- Open 82,074.2 · high 83,043.9 · low 81,698.6 · close 83,021.8 · gap +0.05% vs prev close 82,031.8
- Open→close +1.15% (+948 pts), range 1.64% → **trend day**; CPR width 0.12% (narrow)
- Opening 5-min candle: high 82,177.0, low 81,878.6; morning (to 11:30) high 82,339.3, low 81,698.6
- Autopilot signals:
  - 12:36 camarilla CE → **No automatic attempt logged** · 

## Blocks without a signal
- {"cat": "equity_gate", "entry": null, "from": "09:20", "label": "Rule waits for a bigger account", "msg": "Autopilot adds the Fibonacci breakout once equity reaches \u20b9200,000.", "n": 320, "ok": fa
- {"cat": "equity_gate", "entry": null, "from": "09:20", "label": "Rule waits for a bigger account", "msg": "Autopilot adds the Fibonacci breakout once equity reaches \u20b9200,000.", "n": 320, "ok": fa
- {"cat": "slot_busy", "entry": null, "from": "10:05", "label": "A trade was already open in this slot", "msg": "You already have an open signal trade in this instrument.", "n": 55, "ok": false, "opt": 
- {"cat": "equity_gate", "entry": null, "from": "10:45", "label": "Rule waits for a bigger account", "msg": "Autopilot adds the trend-day catcher once equity reaches \u20b940,000.", "n": 235, "ok": fals
- {"cat": "equity_gate", "entry": null, "from": "10:45", "label": "Rule waits for a bigger account", "msg": "Autopilot adds the trend-day catcher once equity reaches \u20b940,000.", "n": 235, "ok": fals
- {"cat": "slot_busy", "entry": null, "from": "12:05", "label": "A trade was already open in this slot", "msg": "You already have an open signal trade in this instrument.", "n": 45, "ok": false, "opt": 
- {"cat": "slot_busy", "entry": null, "from": "12:51", "label": "A trade was already open in this slot", "msg": "You already have an open signal trade in this instrument.", "n": 24, "ok": false, "opt": 

## Book
- Autopilot trades: 3, P&L Rs -323; scalp book: 5 trades, Rs -1,107

## Where we lagged today
- NIFTY: **trail_exit** (143.5 pts) — r23 / r40: holding to 15:15 or wider trails win on such days but lose overall.
- BANKNIFTY: **trail_exit** (440.8 pts) — r23 / r40: holding to 15:15 or wider trails win on such days but lose overall.
- SENSEX: **no_trade_trend** (947.6 pts) — r23 / r24: trend-day catcher exists; earlier or wider checks earned less.

## Last 20 reviewed sessions
- range_day: 22×
- no_trade_trend: 16×, 9,722 pts ← repeated: check whether its last test is older than 60 sessions
- trail_exit: 10×, 3,216 pts ← repeated: check whether its last test is older than 60 sessions
- range_day_loss: 8×, Rs 15,424 ← repeated: check whether its last test is older than 60 sessions
- wide_cpr: 5×
- breakeven_loss: 3×, Rs 4,188 ← repeated: check whether its last test is older than 60 sessions

## Judgement (Claude, post-market)

**What mattered**
1. **Strong up trend day on all three indices (+1.15% to +1.58%, closed at the high). Autopilot caught almost none of it: Rs -323 on 3 trades.** All 7 Autopilot signals were CE (right side), and every one would have been +1.9R to +3.6R if held to 15:15 (sum +19.3R on the index). The rules saw the day; the exits and the small account let it go.
2. **Exits gave the move back.** Bank Nifty Camarilla CE 10:00 hit +1R, then went back to breakeven at 11:00 (Rs +221) and Bank Nifty ran another ~650 pts. Nifty Camarilla CE 12:00 was cut by the 45-min time stop (Rs -22) before Nifty added ~140 pts. Bank Nifty Noise CE 12:45 was trailed out at 13:15 on a dip below VWAP (Rs -523), right before the last leg up. This is the "trail_exit" lag (10× in 20 sessions).
3. **Small account blocked the best entries.** Bank Nifty trend-day catcher (12:16, +2.4R) and Fibonacci (10:16, +2.8R) are off below Rs 40k / Rs 2L equity; Bank Nifty Noise 12:15 (+3.6R) was "Not enough money" for a lot. Sensex Camarilla 12:36 is not used on Sensex by design (r24/r29), so Sensex got no trade.
4. **Scalp book Rs -1,107 on 5 trades**: first Bank Nifty CE made Rs +2,496, then four quick stops on CE re-entries (Rs -1,801, -1,274, -178, -350). Right direction, entries too late into small pullbacks. Noted only.
5. Your opening-candle retest (r72): no signal today.

**Your action**
- Settings fine: auto on, all three indices ticked, risk 1%, compound sizing, all four strategies on.
- Capital is still under Rs 40k. On days like today that is the main cost: the trend-day catcher and one Noise entry would have run. Adding capital to Rs 40k+ switches the trend catcher on; nothing else to change.

**Rule questions**
- trail_exit (10×) and no_trade_trend (16×) repeat, but both were tested within the last 60 sessions: r23/r40 (hold to 15:15 / wider trails win on trend days but lose overall), r24 (earlier trend checks earned less), r98/r99 (reversal exit + profit trail, Nov 2026). range_day_loss (8×) and breakeven_loss (3×) likewise (r33, r74/r75, r79, r92). **No backtest, no rule change today.** One trend day does not change those results.
- Still open from earlier reviews (waiting for your go-ahead, not pushed): relabel "No automatic attempt logged" as "Rule not used on this index (round 24/29)" or "equity gate"; skip the Muhurat session when building prior-day levels.

## Shadow slot (squeeze release on Sensex, round 101 — no orders)
- Today: slot on, **no squeeze signal**, nothing logged, no errors (a strong steady trend day, no squeeze to release).
- Running tally since 16 Nov (4 sessions): **1 signal, 0 wins, net Rs -1,386 per lot.** 16 Nov: SENSEX 81800 PE at 14:15 (1 s late), Rs 475.6 → 423.05 at 15:15 square-off; r_live -0.30 vs r_rule -0.30; bid-ask gap 0.27%; live prices on 100% of signals.
- No data problems. Decision on this slot is on 22 Dec, not in the daily run.
