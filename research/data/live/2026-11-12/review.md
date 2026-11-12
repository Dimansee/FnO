# Daily review 2026-11-12

## NIFTY
- Open 25,213.5 · high 25,384.8 · low 25,143.0 · close 25,299.3 · gap -0.16% vs prev close 25,255.0
- Open→close +0.34% (+86 pts), range 0.96% → range / two-way day; CPR width 0.01% (narrow)
- Opening 5-min candle: high 25,301.5, low 25,143.0; morning (to 11:30) high 25,384.8, low 25,143.0
- Autopilot: no signal in this index.

## BANKNIFTY
- Open 60,801.8 · high 61,545.8 · low 60,723.1 · close 61,518.5 · gap +0.02% vs prev close 60,792.4
- Open→close +1.18% (+717 pts), range 1.35% → **trend day**; CPR width 0.10% (narrow)
- Opening 5-min candle: high 61,160.2, low 60,723.1; morning (to 11:30) high 61,406.3, low 60,723.1
- Autopilot signals:
  - 09:45 noise CE → **Capital shield (account protection)** · 
  - 10:15 noise CE → **Capital shield (account protection)** · 
  - 10:45 noise CE → **Capital shield (account protection)** · 
  - 10:46 trend CE → **No automatic attempt logged** · 
- Your opening-candle retest (r72) fired: CALL at 11:15 61,220.8 (level 61,160.2, top 61,406.4, stop 61,159.5) → out 12:10 at 61,159.5 = -61 pts (-1.00R) · 61200 CE 1476.8 → 1428.4 (-3%)

## SENSEX
- Open 81,762.3 · high 82,325.6 · low 81,576.1 · close 81,911.0 · gap +0.32% vs prev close 81,503.9
- Open→close +0.18% (+149 pts), range 0.92% → range / two-way day; CPR width 0.38% (middle)
- Opening 5-min candle: high 82,063.9, low 81,576.1; morning (to 11:30) high 82,325.6, low 81,576.1
- Autopilot signals:
  - 09:31 camarilla CE → **Capital shield (account protection)** · 
  - 09:45 noise CE → **Capital shield (account protection)** · 

## Blocks without a signal
- {"cat": "equity_gate", "entry": null, "from": "09:20", "label": "Rule waits for a bigger account", "msg": "Autopilot adds the Fibonacci breakout once equity reaches \u20b9200,000.", "n": 320, "ok": fa
- {"cat": "equity_gate", "entry": null, "from": "09:20", "label": "Rule waits for a bigger account", "msg": "Autopilot adds the Fibonacci breakout once equity reaches \u20b9200,000.", "n": 320, "ok": fa
- {"cat": "equity_gate", "entry": null, "from": "10:45", "label": "Rule waits for a bigger account", "msg": "Autopilot adds the trend-day catcher once equity reaches \u20b940,000.", "n": 235, "ok": fals
- {"cat": "equity_gate", "entry": null, "from": "10:45", "label": "Rule waits for a bigger account", "msg": "Autopilot adds the trend-day catcher once equity reaches \u20b940,000.", "n": 235, "ok": fals

## Book
- Autopilot trades: 0, P&L Rs +0; scalp book: 2 trades, Rs -88

## Where we lagged today
- NIFTY: **range_day** — r32 / r67 / r69 / r70 / r72: level bounces and range rotations have no edge after option costs; Autopilot skipping range days is by design.
- BANKNIFTY: **no_trade_trend** (716.7 pts) — r23 / r24: trend-day catcher exists; earlier or wider checks earned less.
- SENSEX: **range_day** — r32 / r67 / r69 / r70 / r72: level bounces and range rotations have no edge after option costs; Autopilot skipping range days is by design.

## Last 20 reviewed sessions
- range_day: 21×
- no_trade_trend: 15×, 8,774 pts ← repeated: check whether its last test is older than 60 sessions
- range_day_loss: 8×, Rs 20,265 ← repeated: check whether its last test is older than 60 sessions
- trail_exit: 7×, 2,490 pts ← repeated: check whether its last test is older than 60 sessions
- wide_cpr: 6×
- breakeven_loss: 3×, Rs 4,188 ← repeated: check whether its last test is older than 60 sessions
- wrong_way: 1×, Rs 8,321

## Judgement (Claude, post-market)

**What mattered**
1. **Autopilot took no trades today (P&L Rs 0) — and that saved money.** Six signals came up (Bank Nifty noise CE ×3, Sensex noise CE + Camarilla CE, Bank Nifty trend CE). All six would have hit their stop (−6R in total). Five were blocked by the capital shield, the trend one by the Rs 40k equity gate.
2. **Why the shield blocked them: the account is too small for Bank Nifty and Sensex.** With equity Rs 25,000, one Sensex lot cost about Rs 19,000 (over the 50% / 75% premium cap) and a Bank Nifty lot costs more than that. So right now Autopilot can really only trade Nifty, and the trend-day catcher (needs Rs 40k) and Fibonacci breakout (needs Rs 2L) are off.
3. **Bank Nifty trend day missed (+1.18%, +717 pts).** It chopped first: every morning CE signal (09:45–10:46) was stopped out around 11:15–12:10 before the real rally after 12:30. Even with a bigger account Autopilot would have lost here, so this miss is not about the shield. Nifty and Sensex were two-way days — skipping those is by design.
4. **Your opening-candle retest (r72):** only Bank Nifty CALL fired, −1R. Scalp book: 2 Nifty CE trades, Rs −88 net.

**Your action**
- Settings fine: auto on, all three indices ticked, all four strategies on, risk 1%, compound sizing.
- Decide on capital: at Rs 25k, Bank Nifty / Sensex signals will keep getting skipped by the shield. Either accept a Nifty-only Autopilot, or raise the paper amount to Rs 40k+ (unlocks trend-day catcher; Sensex fits under the cap) — only if that matches the money you'd really trade.

**Rule questions**
- Repeated lags (no_trade_trend 15×, range_day_loss 8×, trail_exit 7×, breakeven_loss 3×) were all tested within the last 60 sessions (r23/r24, r33, r74/r75, r79, r92 on 7 Nov, r98/r99 on 11 Nov). **No backtest, no rule change today.**
- Still open from 9 Nov: skip the Muhurat session when building prior-day levels (data fix) — waiting for your go-ahead.
