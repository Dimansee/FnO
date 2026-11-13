# Daily review 2026-11-13

## NIFTY
- Open 25,209.8 · high 25,246.3 · low 25,054.4 · close 25,144.3 · gap -0.35% vs prev close 25,299.3
- Open→close -0.26% (-66 pts), range 0.76% → range / two-way day; CPR width 0.09% (narrow)
- Opening 5-min candle: high 25,209.8, low 25,107.4; morning (to 11:30) high 25,246.3, low 25,077.5
- Autopilot signals:
  - 11:45 camarilla PE → **Executed** · 1 lot · exit 15:15 Square-off 15:15 · P&L Rs -702
  - 12:11 fib PE → **Rule waits for a bigger account** · 
- Book caught +23 index pts of a -66-pt day.

## BANKNIFTY
- Open 61,300.6 · high 61,300.6 · low 60,780.6 · close 61,034.1 · gap -0.35% vs prev close 61,518.5
- Open→close -0.43% (-266 pts), range 0.85% → range / two-way day; CPR width 0.42% (middle)
- Opening 5-min candle: high 61,300.6, low 60,967.2; morning (to 11:30) high 61,300.6, low 60,862.2
- Autopilot signals:
  - 09:55 camarilla PE → **Against a big 3-day run (round 42)** · 
  - 10:10 camarilla PE → **Against a big 3-day run (round 42)** · 
  - 10:40 camarilla PE → **Against a big 3-day run (round 42)** · 
  - 11:05 camarilla PE → **Against a big 3-day run (round 42)** · 
  - 12:15 noise PE → **Against a big 3-day run (round 42)** · 
- Your opening-candle retest (r72) fired: PUT at 09:30 60,955.2 (level 60,967.2, top 60,862.2, stop 61,028.1) → out 09:40 at 61,028.1 = -73 pts (-1.00R) · 61000 PE 1175.3 → 1171.0 (-0%)

## SENSEX
- Open 81,833.8 · high 81,879.5 · low 81,288.8 · close 81,592.4 · gap -0.09% vs prev close 81,911.0
- Open→close -0.29% (-241 pts), range 0.72% → range / two-way day; CPR width 0.03% (narrow)
- Opening 5-min candle: high 81,835.8, low 81,412.9; morning (to 11:30) high 81,879.5, low 81,300.5
- Autopilot signals:
  - 12:11 camarilla PE → **No automatic attempt logged** · 
  - 12:11 fib PE → **Rule waits for a bigger account** · 

## Blocks without a signal
- {"cat": "equity_gate", "entry": null, "from": "09:20", "label": "Rule waits for a bigger account", "msg": "Autopilot adds the Fibonacci breakout once equity reaches \u20b9200,000.", "n": 319, "ok": fa
- {"cat": "equity_gate", "entry": null, "from": "09:20", "label": "Rule waits for a bigger account", "msg": "Autopilot adds the Fibonacci breakout once equity reaches \u20b9200,000.", "n": 319, "ok": fa
- {"cat": "equity_gate", "entry": null, "from": "10:45", "label": "Rule waits for a bigger account", "msg": "Autopilot adds the trend-day catcher once equity reaches \u20b940,000.", "n": 235, "ok": fals
- {"cat": "equity_gate", "entry": null, "from": "10:45", "label": "Rule waits for a bigger account", "msg": "Autopilot adds the trend-day catcher once equity reaches \u20b940,000.", "n": 235, "ok": fals
- {"cat": "slot_busy", "entry": null, "from": "11:46", "label": "A trade was already open in this slot", "msg": "You already have an open signal trade in this instrument.", "n": 174, "ok": false, "opt":

## Book
- Autopilot trades: 1, P&L Rs -702; scalp book: 2 trades, Rs -1,077

## Where we lagged today
- NIFTY: **range_day_loss** (Rs 702) — sideways-flag tests (16 Oct, two rounds) / r33: no pre-open or first-30-min flag separates losing range-day breakouts from winners; every filter cut profit.
- BANKNIFTY: **range_day** — r32 / r67 / r69 / r70 / r72: level bounces and range rotations have no edge after option costs; Autopilot skipping range days is by design.
- SENSEX: **range_day** — r32 / r67 / r69 / r70 / r72: level bounces and range rotations have no edge after option costs; Autopilot skipping range days is by design.

## Last 20 reviewed sessions
- range_day: 21×
- no_trade_trend: 15×, 8,774 pts ← repeated: check whether its last test is older than 60 sessions
- range_day_loss: 9×, Rs 20,967 ← repeated: check whether its last test is older than 60 sessions
- trail_exit: 7×, 2,490 pts ← repeated: check whether its last test is older than 60 sessions
- wide_cpr: 5×
- breakeven_loss: 3×, Rs 4,188 ← repeated: check whether its last test is older than 60 sessions
- wrong_way: 1×, Rs 8,321

## Judgement (Claude, post-market)

**What mattered**
1. **All three indices were two-way days (down 0.3-0.4% after a gap-down), no trend day to catch.** Autopilot took 1 trade: Nifty Camarilla PE at 11:45, Rs -702. Scalp book: 2 trades, Rs -1,077. Total Rs -1,779.
2. **The Nifty trade was right on the index but lost on the option.** Nifty fell 108 pts in its favour (+1.6R) after entry, then drifted back. The stop moved to breakeven, but the profit trail only starts at +2.5R (round 99), so it rode to the 15:15 square-off at +0.5R on the index. The 25100 PE (4 days to expiry) still fell 121 → 111 from time decay over 3.5 hours, so it closed at a loss. This is the known range-day giveback; trail levels (r92, r98/r99) were tested in the last week.
3. **Round 42 blocked 4 Bank Nifty PUTs (Bank Nifty had rallied 2,668 pts in 3 sessions).** They would have finished at about 0R each, and the 12:15 Noise PUT would have hit its stop (-1R). So the block cost nothing.
4. **The label "No automatic attempt logged" is misleading.** Today's Sensex Camarilla PE (would have been -0.56R), and the Bank Nifty trend/Fibonacci signals on 5, 6, 9, 11 and 12 Nov, were never tried because Autopilot doesn't use those rules on those indices (round 24/29: Bank Nifty = Noise + Camarilla only; Sensex = Noise + trend + Fibonacci). Correction to yesterday's review: the Bank Nifty trend signal on 12 Nov was skipped for this reason, not because of the Rs 40k equity gate.

**Your action**
- Settings fine: auto on, all three indices ticked, all four strategies on, risk 1%, compound sizing.
- Capital is the same as yesterday: at Rs 25k the trend-day catcher (Rs 40k) and Fibonacci (Rs 2L) stay off.

**Rule questions**
- Repeated lags (no_trade_trend 15×, range_day_loss 9×, trail_exit 7×, breakeven_loss 3×) were all tested within the last 60 sessions (r23/r24, r33, r74/r75, r79, r92, r98/r99). **No backtest, no rule change today.**
- Small code fix you may want (not a rule change, not pushed): in `fno/journal.py`, label signals whose rule isn't in `AUTOPILOT_SYM_STRATEGIES` for that index as "Rule not used on this index (round 24/29)" instead of "No automatic attempt logged".
- Still open from 9 Nov: skip the Muhurat session when building prior-day levels (data fix). Waiting for your go-ahead.
