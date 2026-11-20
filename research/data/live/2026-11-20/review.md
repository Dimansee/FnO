# Daily review 2026-11-20

## NIFTY
- Open 25,368.0 · high 25,808.8 · low 25,345.2 · close 25,800.6 · gap -0.57% vs prev close 25,514.3
- Open→close +1.71% (+433 pts), range 1.83% → **trend day**; CPR width 0.54% (wide)
- Opening 5-min candle: high 25,491.2, low 25,345.2; morning (to 11:30) high 25,572.3, low 25,345.2
- Autopilot signals:
  - 12:06 fib CE → **Rule waits for a bigger account** · 
  - 12:45 noise CE → **Other reason** · 
  - 12:45 trend CE → **Other reason** · 
  - 13:15 noise CE → **Other reason** · 
  - 13:15 trend CE → **Other reason** · 
  - 14:15 noise CE → **Other reason** · 
  - 14:15 trend CE → **Other reason** · 

## BANKNIFTY
- Open 62,656.7 · high 64,680.8 · low 62,598.6 · close 64,635.2 · gap -0.64% vs prev close 63,063.0
- Open→close +3.16% (+1979 pts), range 3.32% → **trend day**; CPR width 0.59% (wide)
- Opening 5-min candle: high 62,942.0, low 62,598.6; morning (to 11:30) high 63,451.4, low 62,598.6
- Autopilot signals:
  - 10:56 fib CE → **Rule not used on this index (round 24/29)** · 
  - 12:00 camarilla CE → **Other reason** · 
  - 12:15 noise CE → **Other reason** · 
  - 12:16 trend CE → **Rule not used on this index (round 24/29)** · 
  - 12:45 noise CE → **Other reason** · 
  - 13:15 noise CE → **Other reason** · 
  - 13:45 noise CE → **Other reason** · 
  - 14:15 noise CE → **Other reason** · 

## SENSEX
- Open 82,558.2 · high 84,038.0 · low 82,403.3 · close 83,919.7 · gap -0.56% vs prev close 83,021.8
- Open→close +1.65% (+1361 pts), range 1.98% → **trend day**; CPR width 0.53% (wide)
- Opening 5-min candle: high 82,936.5, low 82,403.3; morning (to 11:30) high 83,259.4, low 82,403.3
- Autopilot signals:
  - 12:06 fib CE → **Rule waits for a bigger account** · 
  - 12:41 camarilla CE → **Other reason** · 
  - 12:45 noise CE → **Other reason** · 
  - 12:45 trend CE → **Other reason** · 
  - 13:15 noise CE → **Other reason** · 
  - 13:15 trend CE → **Other reason** · 
  - 14:15 noise CE → **Other reason** · 
  - 14:15 trend CE → **Other reason** · 
- Your opening-candle retest (r72) fired: CALL at 10:05 82,987.3 (level 82,936.5, top 83,110.3, stop 82,950.9) → out 10:20 at 82,950.9 = -36 pts (-1.00R) · 83000 CE 985.0 → 1000.0 (+2%)

## Blocks without a signal
- {"cat": "equity_gate", "entry": null, "from": "09:20", "label": "Rule waits for a bigger account", "msg": "Autopilot adds the Fibonacci breakout once equity reaches \u20b9200,000.", "n": 319, "ok": fa
- {"cat": "equity_gate", "entry": null, "from": "09:20", "label": "Rule waits for a bigger account", "msg": "Autopilot adds the round-level break slot once equity reaches \u20b9100,000 (round 102).", "n
- {"cat": "equity_gate", "entry": null, "from": "09:20", "label": "Rule waits for a bigger account", "msg": "Autopilot adds the Fibonacci breakout once equity reaches \u20b9200,000.", "n": 319, "ok": fa
- {"cat": "equity_gate", "entry": null, "from": "10:45", "label": "Rule waits for a bigger account", "msg": "Autopilot adds the trend-day catcher once equity reaches \u20b940,000.", "n": 103, "ok": fals
- {"cat": "equity_gate", "entry": null, "from": "10:45", "label": "Rule waits for a bigger account", "msg": "Autopilot adds the trend-day catcher once equity reaches \u20b940,000.", "n": 103, "ok": fals

## Book
- Autopilot trades: 0, P&L Rs +0; scalp book: 2 trades, Rs -2,277

## Where we lagged today
- NIFTY: **no_trade_trend** (432.6 pts) — r23 / r24: trend-day catcher exists; earlier or wider checks earned less.
- NIFTY: **wide_cpr** — r71: wide-CPR days are skipped on purpose (they lost in every year).
- BANKNIFTY: **no_trade_trend** (1978.5 pts) — r23 / r24: trend-day catcher exists; earlier or wider checks earned less.
- BANKNIFTY: **wide_cpr** — r71: wide-CPR days are skipped on purpose (they lost in every year).
- SENSEX: **no_trade_trend** (1361.5 pts) — r23 / r24: trend-day catcher exists; earlier or wider checks earned less.
- SENSEX: **wide_cpr** — r71: wide-CPR days are skipped on purpose (they lost in every year).

## Last 20 reviewed sessions
- range_day: 22×
- no_trade_trend: 16×, 11,931 pts ← repeated: check whether its last test is older than 60 sessions
- trail_exit: 10×, 3,216 pts ← repeated: check whether its last test is older than 60 sessions
- range_day_loss: 8×, Rs 15,424 ← repeated: check whether its last test is older than 60 sessions
- wide_cpr: 8×
- breakeven_loss: 3×, Rs 4,188 ← repeated: check whether its last test is older than 60 sessions

## Judgement (Claude, post-market)

**What mattered**
1. **Big up trend day on all three indices (Nifty +1.71%, Bank Nifty +3.16%, Sensex +1.65%, all closed near the high after a gap down). Autopilot took 0 trades, P&L Rs 0.** All 23 signals were CE (right side) and would have been +0.4R to +4.4R each held to 15:15 (+31.7R in total on the index).
2. **The reason was the wide-CPR skip (round 71), not a fault.** CPR was 0.53-0.59% on all three (limit 0.5%), so every Noise / Camarilla entry was skipped on purpose. The first allowed entries would have been Bank Nifty Camarilla 12:00 (+2.7R), Bank Nifty Noise 12:15 (+3.0R), Nifty Noise 12:45 (+1.0R) and Sensex Noise 12:45 (+1.7R): roughly +8R left on the table today. The trend catcher and Fibonacci were off anyway (account under Rs 40k / Rs 2L).
3. **Scalp book Rs -2,277 on 2 trades** (Bank Nifty CE time stop Rs -469, Nifty CE stop Rs -1,807, both before 10:05 while the market was still chopping near the open). That hit the day loss limit, and then the **capital shield** tripped: scalp book equity Rs 11,457 is below its Rs 12,000 floor (60% of Rs 20k). The scalp book is now stopped every day until you reset it.
4. Your opening-candle retest (r72) fired on Sensex: CALL 10:05, stopped 10:20 for -1R on the index (the option itself closed +2%). Sensex then ran 1,000 pts higher.

**Your action**
- **Scalp book is switched off by the capital shield** until you reset the scalp book or lower its floor. Decide whether to reset it.
- Main Autopilot settings look fine: auto on, all three indices ticked, risk 1%, compound sizing, Noise / Camarilla / Trend / Fib on. Capital still under Rs 40k, so the trend-day catcher stays off.

**Rule questions**
- **wide_cpr is now 8× in 20 sessions and cost a trend day today.** Round 71 (1 Nov 2026, about 14 sessions ago) found Autopilot trades on wide-CPR days lost money per lot in every year 2023-26 (p = 0.0004). That test is far younger than 60 sessions and its conditions still hold, so **no re-test and no change**. One big day does not undo four losing years.
- no_trade_trend (16×), trail_exit (10×), range_day_loss (8×), breakeven_loss (3×): all tested within 60 sessions (r23/r24/r40, r33, r74/r75, r79, r92, r98/r99). No backtest today.
- Small labelling bug (not a rule): the round-71 skip message ("wide CPR today ...") is not in the journal's reason list (fno/journal.py AUTO_CATS), so 19 of today's skips show as "Other reason". Suggest adding a "Wide CPR day (round 71)" label. Not pushed; waiting for your go-ahead.

## Shadow slot (squeeze release on Sensex, round 101 — no orders)
- Today: slot on, **no squeeze signal**, nothing logged, no errors.
- Running tally since 16 Nov (5 sessions): **1 signal, 0 wins, net Rs -1,386 per lot.** 16 Nov: SENSEX 81800 PE at 14:15 (1 s late), Rs 475.6 → 423.05 at 15:15 square-off; r_live -0.30 vs r_rule -0.30; bid-ask gap 0.27%; live prices on 100% of signals.
- No data problems. Decision on this slot is on 22 Dec, not in the daily run.
