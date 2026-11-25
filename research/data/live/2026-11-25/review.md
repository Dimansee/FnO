# Daily review 2026-11-25

## NIFTY
- Open 25,658.3 · high 25,951.7 · low 25,619.0 · close 25,819.2 · gap +1.15% vs prev close 25,366.4
- Open→close +0.63% (+161 pts), range 1.30% → range / two-way day; CPR width 0.79% (wide)
- Opening 5-min candle: high 25,756.0, low 25,619.0; morning (to 11:30) high 25,942.7, low 25,619.0
- Autopilot signals:
  - 11:15 noise CE → **Wide-CPR day skipped (round 71)** · 
  - 11:15 trend CE → **Wide-CPR day skipped (round 71)** · 
  - 11:45 noise CE → **Wide-CPR day skipped (round 71)** · 
  - 11:45 trend CE → **Wide-CPR day skipped (round 71)** · 
  - 12:15 noise CE → **Wide-CPR day skipped (round 71)** · 
  - 12:15 trend CE → **Wide-CPR day skipped (round 71)** · 
  - 13:15 rlevel PE → **Wide-CPR day skipped (round 71)** · 

## BANKNIFTY
- Open 63,408.6 · high 64,133.8 · low 63,247.9 · close 63,703.2 · gap +1.28% vs prev close 62,609.2
- Open→close +0.46% (+295 pts), range 1.40% → range / two-way day; CPR width 1.32% (wide)
- Opening 5-min candle: high 63,849.2, low 63,301.2; morning (to 11:30) high 64,039.6, low 63,301.2
- Autopilot signals:
  - 11:45 camarilla CE → **Wide-CPR day skipped (round 71)** · 

## SENSEX
- Open 83,436.9 · high 84,498.2 · low 83,380.8 · close 83,938.3 · gap +1.21% vs prev close 82,436.0
- Open→close +0.60% (+501 pts), range 1.34% → range / two-way day; CPR width 0.88% (wide)
- Opening 5-min candle: high 83,869.6, low 83,380.8; morning (to 11:30) high 84,470.4, low 83,380.8
- Autopilot signals:
  - 10:45 noise CE → **Wide-CPR day skipped (round 71)** · 
  - 10:45 trend CE → **Wide-CPR day skipped (round 71)** · 
  - 11:15 noise CE → **Wide-CPR day skipped (round 71)** · 
  - 11:15 trend CE → **Wide-CPR day skipped (round 71)** · 
  - 11:45 noise CE → **Wide-CPR day skipped (round 71)** · 
  - 11:45 trend CE → **Wide-CPR day skipped (round 71)** · 
  - 12:15 noise CE → **Wide-CPR day skipped (round 71)** · 
  - 12:15 trend CE → **Wide-CPR day skipped (round 71)** · 

## Blocks without a signal
- {"cat": "equity_gate", "entry": null, "from": "09:20", "label": "Rule waits for a bigger account", "msg": "Autopilot adds the Fibonacci breakout once equity reaches \u20b9200,000.", "n": 234, "ok": fa
- {"cat": "equity_gate", "entry": null, "from": "09:20", "label": "Rule waits for a bigger account", "msg": "Autopilot adds the round-level break slot once equity reaches \u20b9100,000 (round 102).", "n
- {"cat": "equity_gate", "entry": null, "from": "09:20", "label": "Rule waits for a bigger account", "msg": "Autopilot adds the Fibonacci breakout once equity reaches \u20b9200,000.", "n": 234, "ok": fa

## Book
- Autopilot trades: 0, P&L Rs +0; scalp book: 0 trades, Rs +0

## Where we lagged today
- NIFTY: **range_day** — r32 / r67 / r69 / r70 / r72: level bounces and range rotations have no edge after option costs; Autopilot skipping range days is by design.
- NIFTY: **wide_cpr** — r71: wide-CPR days are skipped on purpose (they lost in every year).
- BANKNIFTY: **range_day** — r32 / r67 / r69 / r70 / r72: level bounces and range rotations have no edge after option costs; Autopilot skipping range days is by design.
- BANKNIFTY: **wide_cpr** — r71: wide-CPR days are skipped on purpose (they lost in every year).
- SENSEX: **range_day** — r32 / r67 / r69 / r70 / r72: level bounces and range rotations have no edge after option costs; Autopilot skipping range days is by design.
- SENSEX: **wide_cpr** — r71: wide-CPR days are skipped on purpose (they lost in every year).

## Last 20 reviewed sessions
- range_day: 22×
- no_trade_trend: 16×, 11,931 pts ← repeated: check whether its last test is older than 60 sessions
- wide_cpr: 11×
- trail_exit: 10×, 3,216 pts ← repeated: check whether its last test is older than 60 sessions
- range_day_loss: 8×, Rs 15,424 ← repeated: check whether its last test is older than 60 sessions
- breakeven_loss: 3×, Rs 4,188 ← repeated: check whether its last test is older than 60 sessions

## Judgement (Claude, post-market)

**What mattered**
1. **Gap-up and fade-back range day on all three (gap +1.15% to +1.28%, then high by 11:00 and drift lower; open→close only +0.46% to +0.63%). Autopilot took 0 trades, P&L Rs 0.** No trend day today, so nothing was missed.
2. **The wide-CPR skip (round 71) saved money today.** CPR was 0.79% / 1.32% / 0.88% (all wide). All 16 skipped signals (15 CALL, 1 PUT) hit their stop: **-15.5R in total on the index**. The best one (Sensex Noise/Trend CALL 10:45) went +407 pts first, then reversed to the stop at 12:55. After 20 and 23 Nov (wide days that ran), today is the kind of wide day that rounds 71 / 108 warned about: big range both ways, no follow-through.
3. This also answers the r109 / r110 idea live: at 11:00 today all three were near the high with a clean move (the "one-way score" looked good), and the day then faded. You already dropped the exception-day mode (r111); today supports that.
4. Your opening-candle retest (r72) did not fire on any index. No data problems: journal, candles and the skip labels are all there ("Wide-CPR day skipped (round 71)" now shows correctly, the 20 Nov labelling bug is fixed).

**Your action**
- Nothing urgent. Settings look right: auto on, Nifty / Bank Nifty / Sensex ticked, risk 1%, compound sizing, Noise / Camarilla / Trend / Fib on. Account is still under Rs 40k, so the trend-day catcher, round-level and Fib slots stay off by design.

**Rule questions**
- wide_cpr (11× in 20 sessions): tested today in round 108 on the realised replay (0 of 10 versions better) and r112 (psych-level breakout, not adopted). Conditions hold. **No re-test, no change.**
- no_trade_trend (16×), trail_exit (10×), range_day_loss (8×), breakeven_loss (3×): all tested within the last 60 sessions (r23/r24/r40, r33, r74/r75, r79, r92, r98/r99, r104, r106, r109). **No backtest today.**

## Shadow slot (squeeze release on Sensex, round 101, no orders)
- Today: slot on, **no squeeze signal**, nothing logged, no errors.
- Running tally since 16 Nov (7 sessions): **2 signals, 1 win, net Rs +9,322 per lot.** Average r_live +1.27 vs r_rule +1.28; average bid-ask gap at entry 0.30%; live prices on 100% of signals.
  - 16 Nov: 81800 PE at 14:15 (1 s late), Rs 475.6 → 423.05 at 15:15 square-off, r_live -0.30 / r_rule -0.30, Rs -1,386, gap 0.27%.
  - 23 Nov: 83400 PE at 10:00 (0 s late), Rs 683.4 → 1,223.75 at 15:15 square-off, r_live +2.83 / r_rule +2.85, Rs +10,708, gap 0.33%.
- No data problems. Decision on this slot is on 22 Dec, not in the daily run.
