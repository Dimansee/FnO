# Daily review 2026-11-18

## NIFTY
- Open 25,323.2 · high 25,390.8 · low 25,141.8 · close 25,228.0 · gap -0.25% vs prev close 25,387.4
- Open→close -0.38% (-95 pts), range 0.98% → range / two-way day; CPR width 0.01% (narrow)
- Opening 5-min candle: high 25,390.8, low 25,292.0; morning (to 11:30) high 25,390.8, low 25,206.2
- Autopilot signals:
  - 09:45 camarilla PE → **Executed** · 1 lot · exit 10:39 Time stop (45 min, no follow-through) · P&L Rs +63
  - 09:46 fib PE → **Rule waits for a bigger account** · 
- Book caught -2 index pts of a -95-pt day.
- Your opening-candle retest (r72) fired: PUT at 09:55 25,273.8 (level 25,292.0, top 25,206.2, stop 25,304.0) → out 10:00 at 25,304.0 = -30 pts (-1.00R) · 25250 PE 242.0 → 238.6 (-1%)

## BANKNIFTY
- Open 62,204.1 · high 62,363.8 · low 61,765.4 · close 62,038.7 · gap -0.11% vs prev close 62,273.2
- Open→close -0.27% (-165 pts), range 0.96% → range / two-way day; CPR width 0.33% (middle)
- Opening 5-min candle: high 62,363.8, low 62,142.2; morning (to 11:30) high 62,363.8, low 61,765.4
- Autopilot: no signal in this index.

## SENSEX
- Open 82,376.6 · high 82,578.9 · low 81,780.5 · close 82,031.8 · gap -0.19% vs prev close 82,532.2
- Open→close -0.42% (-345 pts), range 0.97% → range / two-way day; CPR width 0.03% (narrow)
- Opening 5-min candle: high 82,578.9, low 82,267.2; morning (to 11:30) high 82,578.9, low 81,979.6
- Autopilot signals:
  - 09:46 camarilla PE → **No automatic attempt logged** · 
  - 12:36 fib PE → **Rule waits for a bigger account** · 

## Blocks without a signal
- {"cat": "equity_gate", "entry": null, "from": "09:20", "label": "Rule waits for a bigger account", "msg": "Autopilot adds the Fibonacci breakout once equity reaches \u20b9200,000.", "n": 320, "ok": fa
- {"cat": "equity_gate", "entry": null, "from": "09:20", "label": "Rule waits for a bigger account", "msg": "Autopilot adds the Fibonacci breakout once equity reaches \u20b9200,000.", "n": 320, "ok": fa
- {"cat": "slot_busy", "entry": null, "from": "09:55", "label": "A trade was already open in this slot", "msg": "You already have an open signal trade in this instrument.", "n": 45, "ok": false, "opt": 
- {"cat": "equity_gate", "entry": null, "from": "10:45", "label": "Rule waits for a bigger account", "msg": "Autopilot adds the trend-day catcher once equity reaches \u20b940,000.", "n": 235, "ok": fals
- {"cat": "equity_gate", "entry": null, "from": "10:45", "label": "Rule waits for a bigger account", "msg": "Autopilot adds the trend-day catcher once equity reaches \u20b940,000.", "n": 235, "ok": fals

## Book
- Autopilot trades: 1, P&L Rs +63; scalp book: 2 trades, Rs -2,941

## Where we lagged today
- BANKNIFTY: **range_day** — r32 / r67 / r69 / r70 / r72: level bounces and range rotations have no edge after option costs; Autopilot skipping range days is by design.
- SENSEX: **range_day** — r32 / r67 / r69 / r70 / r72: level bounces and range rotations have no edge after option costs; Autopilot skipping range days is by design.

## Last 20 reviewed sessions
- range_day: 22×
- no_trade_trend: 15×, 8,774 pts ← repeated: check whether its last test is older than 60 sessions
- range_day_loss: 9×, Rs 18,749 ← repeated: check whether its last test is older than 60 sessions
- trail_exit: 8×, 2,632 pts ← repeated: check whether its last test is older than 60 sessions
- wide_cpr: 5×
- breakeven_loss: 3×, Rs 4,188 ← repeated: check whether its last test is older than 60 sessions
- wrong_way: 1×, Rs 8,321

## Judgement (Claude, post-market)

**What mattered**
1. **A quiet, slightly red day on all three indices (-0.3% to -0.4%, range under 1%). No trend day, so nothing was missed.** Autopilot made one trade: Nifty Camarilla PE at 09:54 (25200 PE, 146.8 → 148.85), closed at 10:39 on the 45-min time stop, **Rs +63**. Holding it to 15:15 would have ended about the same (+0.12R on the index), so the time stop cost nothing.
2. **The other three signals all lost on the index (-1R each) and were skipped, which saved money.** Nifty Fibonacci 09:45 and Sensex Fibonacci 12:35 wait for Rs 2L equity; Sensex Camarilla 09:45 is not used on Sensex by design (round 24/29; Autopilot runs Noise, trend and Fib there) and one Sensex lot (Rs 20k) was too big for the account anyway.
3. **Scalp book lost Rs -2,941 on 2 quick stops** (Nifty 25350 PE 09:50-09:54 Rs -2,038; Sensex 81800 PE 10:46-10:48 Rs -904). Both were "trend" mode shorts on a day that turned out two-way. Two trades is too little to judge; noted only.
4. Your opening-candle retest (r72) gave a Nifty PUT at 09:55 that was stopped at 10:00 (-1R, premium -1%).

**Your action**
- Settings fine: auto on, all three indices ticked, risk 1%, compound sizing.
- Capital is still under Rs 40k, so the trend-day catcher and Fibonacci stay off. Nothing to change unless you add capital.

**Rule questions**
- Repeated lags (no_trade_trend 15×, range_day_loss 9×, trail_exit 8×, breakeven_loss 3×) were all tested within the last 60 sessions (r23/r24, r33, sideways-flag tests, r74/r75, r79, r92, r98/r99). **No backtest, no rule change today.**
- Still open from earlier reviews (waiting for your go-ahead, not pushed): relabel "No automatic attempt logged" as "Rule not used on this index (round 24/29)"; skip the Muhurat session when building prior-day levels.

## Shadow slot (squeeze release on Sensex, round 101 — no orders)
- Today: slot on, **no squeeze signal**, nothing logged, no errors.
- Running tally since 16 Nov (3 sessions): **1 signal, 0 wins, net Rs -1,386 per lot.** 16 Nov: SENSEX 81800 PE at 14:15 (1 s late), Rs 475.6 → 423.05 at 15:15 square-off; r_live -0.30 vs r_rule -0.30; bid-ask gap 0.27%; live prices on 100% of signals.
- No data problems. Decision on this slot is on 22 Dec, not in the daily run.
