# Daily review 2026-11-26

## NIFTY
- Open 25,873.2 · high 25,975.0 · low 25,733.5 · close 25,884.5 · gap +0.21% vs prev close 25,819.2
- Open→close +0.04% (+11 pts), range 0.93% → range / two-way day; CPR width 0.09% (narrow)
- Opening 5-min candle: high 25,880.0, low 25,770.4; morning (to 11:30) high 25,975.0, low 25,733.5
- Autopilot signals:
  - 10:00 rlevel CE → **Executed** · 1 lot · exit 13:13 Trailing stop (1R behind the best 15-min close) · P&L Rs -360
  - 13:15 rlevel PE → **Executed** · 1 lot · exit 14:26 Trailing stop (1R behind the best 15-min close) · P&L Rs -1,758
- Book caught +3 index pts of a +11-pt day.

## BANKNIFTY
- Open 63,883.1 · high 64,104.8 · low 63,332.3 · close 63,535.3 · gap +0.28% vs prev close 63,703.2
- Open→close -0.54% (-348 pts), range 1.21% → range / two-way day; CPR width 0.01% (narrow)
- Opening 5-min candle: high 63,894.8, low 63,471.3; morning (to 11:30) high 64,104.8, low 63,471.3
- Autopilot: no signal in this index.

## SENSEX
- Open 84,301.4 · high 84,551.7 · low 83,754.7 · close 84,328.6 · gap +0.43% vs prev close 83,938.3
- Open→close +0.03% (+27 pts), range 0.95% → range / two-way day; CPR width 0.00% (narrow)
- Opening 5-min candle: high 84,326.4, low 83,893.4; morning (to 11:30) high 84,551.7, low 83,754.7
- Autopilot: no signal in this index.

## Blocks without a signal
- {"cat": "data", "entry": null, "from": "09:20", "label": "Market data not reliable", "msg": "Skipped: the latest 5-minute candle is 1075 minutes old - waiting for fresh prices.", "n": 6, "ok": false, 
- {"cat": "data", "entry": null, "from": "09:20", "label": "Market data not reliable", "msg": "Skipped: the latest 5-minute candle is 1075 minutes old - waiting for fresh prices.", "n": 6, "ok": false, 
- {"cat": "slot_busy", "entry": null, "from": "10:01", "label": "A trade was already open in this slot", "msg": "You already have an open round-level break trade in this instrument.", "n": 191, "ok": fa
- {"cat": "equity_gate", "entry": null, "from": "13:13", "label": "Rule waits for a bigger account", "msg": "Autopilot adds the Fibonacci breakout once equity reaches \u20b9200,000.", "n": 87, "ok": fal
- {"cat": "equity_gate", "entry": null, "from": "13:13", "label": "Rule waits for a bigger account", "msg": "Autopilot adds the Fibonacci breakout once equity reaches \u20b9200,000.", "n": 87, "ok": fal
- {"cat": "slot_busy", "entry": null, "from": "13:16", "label": "A trade was already open in this slot", "msg": "You already have an open round-level break trade in this instrument.", "n": 70, "ok": fal

## Book
- Autopilot trades: 2, P&L Rs -2,118; scalp book: 0 trades, Rs +0

## Where we lagged today
- NIFTY: **range_day_loss** (Rs 2,118) — sideways-flag tests (16 Oct, two rounds) / r33: no pre-open or first-30-min flag separates losing range-day breakouts from winners; every filter cut profit.
- BANKNIFTY: **range_day** — r32 / r67 / r69 / r70 / r72: level bounces and range rotations have no edge after option costs; Autopilot skipping range days is by design.
- SENSEX: **range_day** — r32 / r67 / r69 / r70 / r72: level bounces and range rotations have no edge after option costs; Autopilot skipping range days is by design.

## Last 20 reviewed sessions
- range_day: 23×
- no_trade_trend: 16×, 11,931 pts ← repeated: check whether its last test is older than 60 sessions
- wide_cpr: 11×
- trail_exit: 10×, 3,216 pts ← repeated: check whether its last test is older than 60 sessions
- range_day_loss: 8×, Rs 15,128 ← repeated: check whether its last test is older than 60 sessions
- breakeven_loss: 1×, Rs 1,775

## Judgement (Claude, post-market)

**What mattered**
1. **Range day on all three (open→close +0.04% Nifty, -0.54% Bank Nifty, +0.03% Sensex; CPR narrow everywhere). No trend day, so nothing was missed. Autopilot P&L Rs -2,118 (2 trades, both Nifty).**
2. **The round-level break slot (round 102) traded for the first time today, and lost on both sides of a range.** It was blocked by the Rs 1L equity gate on 20, 23 and 25 Nov; today it was not, so the account is now at or above Rs 1L.
   - 10:00 25800 CE (Nifty reclaimed 25,800 on the 09:45 bar, entry 25,829). Nifty ran to 25,975; the best 15-min close was 25,956 (+127 pts, about 1.3R). Then it drifted back, and the trailing stop 1R behind (25,856) hit at 13:13. The index exit was +12 pts, but the premium had lost time value: **Rs -360**.
   - 13:15 25800 PE (broke back below 25,850). Nifty dipped to 25,771 (+64 pts), then rallied to close 25,884. Trailing stop at 14:26: **Rs -1,758**.
   - This is the known range-day cost of a breakout slot (round 102 kept it with these losses priced in). It is one day's evidence, so no change.
3. **Your opening-candle retest (r72) did not fire on any index.**
4. **Data problems (small, no trade lost):**
   - The pre-open global cues all failed today with "YFRateLimitError" (Yahoo rate limit), so the global cues panel was empty. It worked on 20, 23 and 25 Nov. Round 107 found global cues do not change the rules, so trading was not affected. Watch if it repeats.
   - 09:20-09:25: 6 Fib-slot checks said "latest 5-minute candle is 1075 minutes old". That was the first bar of the day not yet in. The Fib slot is gated at Rs 2L anyway, so nothing was lost.
   - NSE bhavcopy / participant OI were not yet available at record time (normal for the 18:30 recorder).

**Your action**
- **Check the account size.** The round-level slot only opens at Rs 1L equity. If you added capital or reset the paper account on purpose, fine: the round-level slot (and the trend-day catcher) are now live, and Fib still waits for Rs 2L. If you did not, check the account in the app.
- Settings otherwise look right: auto on, Nifty / Bank Nifty / Sensex ticked, risk 1%, compound sizing, Noise / Camarilla / Trend / Fib on.

**Rule questions**
- range_day_loss (8× in 20 sessions, Rs 15,128): tested in r33, r104, r106, r109 and the round-level slot itself in r102 (20 Nov), all within 60 sessions. Conditions hold. **No re-test.**
- no_trade_trend (16×) and trail_exit (10×): tested in r23/r24/r40, r74/r75, r79, r92, r98/r99, r104, r106, all within 60 sessions. **No backtest today, no rule change.**

## Shadow slot (squeeze release on Sensex, round 101, no orders)
- Today: **one signal.** 84100 CE opened 10:00:01 (1 s late into the bar; signal bar 09:45), entry Rs 843.35 (ask; bid 840.25, LTP 841.95), Sensex 84,087, stop 83,732. Closed 13:15 on "Momentum turned" at Rs 795.0 (Sensex 84,069). r_live -0.05 vs r_rule -0.04, **net Rs -1,302 per lot** (incl. Rs 93 charges). Bid-ask gap at entry 0.37%. Live prices.
- Running tally since 16 Nov (8 sessions): **3 signals, 1 win, net Rs +8,020 per lot.** Average r_live +0.83 vs r_rule +0.84; average bid-ask gap at entry 0.32%; live prices on 100% of signals.
  - 16 Nov: 81800 PE, 475.6 → 423.05 (15:15 square-off), r -0.30, Rs -1,386
  - 23 Nov: 83400 PE, 683.4 → 1,223.75 (15:15 square-off), r +2.83, Rs +10,708
  - 26 Nov: 84100 CE, 843.35 → 795.0 (momentum turned), r -0.05, Rs -1,302
- No data problems: key present, slot on, no "signal not recorded" / error / "Closed late". Live fills track the rule closely (r_live vs r_rule within 0.02R). Decision on this slot is on 22 Dec, not in the daily run.
