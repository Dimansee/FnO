# Daily review 2026-11-27

## NIFTY
- Open 25,839.2 · high 25,909.2 · low 25,565.9 · close 25,607.2 · gap -0.17% vs prev close 25,884.5
- Open→close -0.90% (-232 pts), range 1.33% → **trend day**; CPR width 0.08% (narrow)
- Opening 5-min candle: high 25,909.2, low 25,814.5; morning (to 11:30) high 25,909.2, low 25,670.5
- Autopilot signals:
  - 11:00 camarilla PE → **Executed** · 1 lot · exit 15:15 Square-off 15:15 · P&L Rs +4,985
  - 11:00 rlevel PE → **Executed** · 1 lot · exit 15:15 Square-off 15:15 · P&L Rs +4,985
  - 11:21 fib PE → **Rule waits for a bigger account** · 
- Book caught +280 index pts of a -232-pt day.

## BANKNIFTY
- Open 63,326.2 · high 63,931.7 · low 62,957.7 · close 63,040.9 · gap -0.33% vs prev close 63,535.3
- Open→close -0.45% (-285 pts), range 1.54% → range / two-way day; CPR width 0.19% (narrow)
- Opening 5-min candle: high 63,724.3, low 63,324.5; morning (to 11:30) high 63,931.7, low 63,198.8
- Autopilot: no signal in this index.

## SENSEX
- Open 84,093.2 · high 84,286.2 · low 83,208.3 · close 83,339.3 · gap -0.28% vs prev close 84,328.6
- Open→close -0.90% (-754 pts), range 1.28% → **trend day**; CPR width 0.14% (narrow)
- Opening 5-min candle: high 84,286.2, low 83,932.3; morning (to 11:30) high 84,286.2, low 83,558.9
- Autopilot signals:
  - 10:36 camarilla PE → **Rule not used on this index (round 24/29)** · 
  - 11:21 fib PE → **Rule waits for a bigger account** · 

## Blocks without a signal
- {"cat": "equity_gate", "entry": null, "from": "09:20", "label": "Rule waits for a bigger account", "msg": "Autopilot adds the Fibonacci breakout once equity reaches \u20b9200,000.", "n": 320, "ok": fa
- {"cat": "equity_gate", "entry": null, "from": "09:20", "label": "Rule waits for a bigger account", "msg": "Autopilot adds the Fibonacci breakout once equity reaches \u20b9200,000.", "n": 320, "ok": fa
- {"cat": "slot_busy", "entry": null, "from": "11:01", "label": "A trade was already open in this slot", "msg": "You already have an open signal trade in this instrument.", "n": 219, "ok": false, "opt":
- {"cat": "slot_busy", "entry": null, "from": "11:01", "label": "A trade was already open in this slot", "msg": "You already have an open round-level break trade in this instrument.", "n": 219, "ok": fa

## Book
- Autopilot trades: 2, P&L Rs +9,971; scalp book: 0 trades, Rs +0

## Where we lagged today
- BANKNIFTY: **range_day** — r32 / r67 / r69 / r70 / r72: level bounces and range rotations have no edge after option costs; Autopilot skipping range days is by design.
- SENSEX: **no_trade_trend** (754.0 pts) — r23 / r24: trend-day catcher exists; earlier or wider checks earned less.

## Last 20 reviewed sessions
- range_day: 23×
- no_trade_trend: 16×, 12,068 pts ← repeated: check whether its last test is older than 60 sessions
- wide_cpr: 11×
- trail_exit: 10×, 3,216 pts ← repeated: check whether its last test is older than 60 sessions
- range_day_loss: 8×, Rs 15,128 ← repeated: check whether its last test is older than 60 sessions
- breakeven_loss: 1×, Rs 1,775

## Judgement (Claude, post-market)

**What mattered**
1. **Good day: Autopilot P&L Rs +9,971 (2 trades, both Nifty, both won).** Nifty and Sensex were down trend days (-0.90% each); Bank Nifty was two-way (-0.45%). Trend days caught: Nifty yes, Sensex no.
2. **Nifty: two slots bought the same option at the same time.** At 11:00 the Camarilla rule (10:55 close below L3 25,818) and the round-level break slot (break below 25,800) both bought the 25800 PE at Rs 201.15. Both held to the 15:15 square-off at Rs 279.2: Rs +4,985 each. This is how the slots are built (each is its own slot; round 102 tested them together), but it means a day like this is a double bet on one move. It worked today; on a fake break both lose together.
3. **Sensex trend day missed, by design.** Sensex fell 754 pts. The Camarilla PE at 10:36 (close below L4 83,890) was the day's best signal (+2.1R, ran 647 pts), but Camarilla is not used on Sensex (rounds 24/29, re-checked in round 101 on 13 Nov). The trend-day catcher did not fire on either index. The Fib PE at 11:21 waited for Rs 2L equity.
4. **Your opening-candle retest (r72) did not fire on any index.** Data clean: global cues worked again (yesterday's Yahoo rate limit did not repeat); bhavcopy / participant OI not yet out at record time (normal).

**Your action**
- Nothing needed. Settings look right: auto on, Nifty / Bank Nifty / Sensex ticked, risk 1%, compound sizing, Noise / Camarilla / Trend / Fib on. Fib still waits for Rs 2L equity.

**Rule questions**
- no_trade_trend (16× in 20 sessions, 12,068 pts), trail_exit (10×), range_day_loss (8×): all tested within the last 60 sessions (r101 every-rule review 13 Nov, r104, r106, r108, r109, r102). Conditions hold. **No backtest today, no rule change.**
- The "two slots, same option" overlap is part of the round 102 backtest; the slot's own review is due about 26 Dec.

## Shadow slot (squeeze release on Sensex, round 101, no orders)
- Today: **no signal.** Slot on, log empty, no errors.
- Running tally since 16 Nov (9 sessions): **3 signals, 1 win, net Rs +8,020 per lot.** Average r_live +0.83 vs r_rule +0.84; average bid-ask gap at entry 0.32%; live prices on 100% of signals.
  - 16 Nov: 81800 PE, 475.6 → 423.05 (15:15 square-off), r -0.30, Rs -1,386
  - 23 Nov: 83400 PE, 683.4 → 1,223.75 (15:15 square-off), r +2.83, Rs +10,708
  - 26 Nov: 84100 CE, 843.35 → 795.0 (momentum turned), r -0.05, Rs -1,302
- No data problems. Decision on this slot is on 22 Dec, not in the daily run.
