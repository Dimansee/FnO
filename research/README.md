# Strategy research (October 2026)

Goal: replace the app's rulebook with the strategy that has the best profit
**on days it was not tuned on**, after realistic costs.

## Data
- 5-minute candles 2 Jan 2023 → 1 Oct 2026 (924 sessions) for Nifty, Bank Nifty,
  Fin Nifty, India VIX and 10 F&O stocks, from Upstox's public history API.
  Fetched by `.github/workflows/research-data.yml` on the `research-data` branch
  (the CSVs live only on that branch, so they are never deployed).
- Daily S&P 500, Nasdaq, Nikkei, Hang Seng (context filters).
- Cross-checked against Yahoo for the overlapping 60 days (median diff < ₹0.01).

## Method
- `engine.py`: replays a strategy bar by bar on the index; buys the option
  (Black-Scholes priced with the India VIX of that 5-min bar × instrument factor),
  0.5% slippage per fill, full Indian F&O charges, ₹2 lakh capital, 1% risk per trade.
- 9 families from books, papers and Indian trading content: opening-range breakout
  (+VWAP/trend, the old rules), 5-min ORB (Zarattini/Aziz 2024), noise-area momentum
  (Zarattini/Aziz/Barbon 2024 "Beat the Market"), VWAP/EMA pullback, Supertrend flip
  + ADX, previous-day high/low + narrow CPR, EMA 9/21 + ADX, gap-and-go/fade,
  9:20 iron fly.
- `optimize.py`: 300 settings per family (2,700 total). Tuned on 3 Jan 2023 → 9 Apr 2026,
  then scored on the **last 30/60/90/120 trading days, never seen by the search**.
- `wf.py`: walk-forward (re-pick every 30 days from the previous 250/500 days).
- `refine.py`: full 3,072-setting grid around the winning family.
- `checks.py`: neighbours, unseen instrument (Fin Nifty), double slippage, stocks.

## Result
Chosen: **noise-area momentum** — band 1.75× usual move-since-open (14 sessions),
checks every 30 min 09:45–14:15, close beyond band and VWAP, VIX ≥ 11, stop 2×ATR,
target 4R, trailing exit on band/VWAP at each check, 1 trade/instrument/day, ATM option.

| Unseen days | Nifty | Bank Nifty | Both | Old ORB rules (both) |
|---|---|---|---|---|
| 30 | +₹2.9k (PF 1.68) | +₹1.3k (PF 1.17) | +₹4.2k | −₹17.9k |
| 60 | +₹5.4k (PF 1.68) | +₹5.2k (PF 1.44) | +₹10.6k | −₹65.7k |
| 90 | +₹9.8k (PF 1.77) | +₹25.5k (PF 2.92) | +₹35.3k | −₹95.0k |
| 120 | +₹10.6k (PF 1.58) | +₹9.0k (PF 1.30) | +₹19.6k | −₹125.2k |

Honest caveats
- The edge is modest. 12 of 13 neighbouring settings were also profitable on the unseen
  120 days, but the train-ranked top settings of the same family ranged from −₹12k to
  +₹20k there — expect results to vary.
- Nifty profitable every year 2023–26; Bank Nifty lost in 2025; Fin Nifty (unseen) ~flat;
  most single stocks lost → trade the indices.
- Context filters (global cues / gap / VIX change) reduced profit; walk-forward re-tuning
  did worse than one fixed robust setting.
- Option selling (iron fly) can't be judged with a VIX-based model (its edge is expiry IV
  crush), so it is not recommended from this study.
- Option prices are modelled, not historical quotes.

Re-run: `python optimize.py 300 && python wf.py 250 && python refine.py && python checks.py && python make_summary.py`

## Round 3 — strategies popular Indian F&O creators teach (`optimize2.py`, `wf2.py`)
Coded as written and run through the same loop (400 settings each, 2,400 total):
5 EMA (Power of Stocks), inside-bar breakout (Bank Nifty creators), 44 MA
(Siddharth Bhanushali), RSI 60/40 with a 15-min bias (Vishal Malkan style), 15-min bias +
5-min swing breakout (Booming Bulls style), Fibonacci 50–61.8% pullback (Magicfibs style).

Best-on-training setting, run on the unseen last 120 days (both indices, ₹2 lakh):
EMA/ADX −₹3.4k, RSI 60/40 −₹12.4k, inside bar −₹13.1k, 5 EMA −₹21.1k, Fibonacci −₹37.1k,
44 MA −₹42.6k, multi-timeframe breakout −₹111.6k — versus **+₹19.6k for noise-area momentum**.
Walk-forward: Fibonacci +₹26k and 5 EMA +₹13k over 2024–26 (≈ break-even per trade), the rest negative.
Not testable with free data: option selling (straddles, strangles, iron condors, adjustments)
and OI/PCR-driven methods.

## Small accounts (₹15,000, 1 lot per signal)
The 1% rule allows ₹150 risk, but one lot risks ₹1,500–6,000, so the default backtest takes
no trades. With "Always 1 lot": Nifty 1-OTM made money every year 2023–26 (+₹10.6k on the last
120 days) but fell up to 47% from a peak, and the worst single trade lost ₹5.7k. Bank Nifty lots
mostly cost more than ₹15,000.

## Round 4 — checked against REAL option prices (`fetch_options.py`, `calib.py`, `intraday_fit.py`, `realcheck.py`)
Profit was always computed on the option premium, but from a formula. Now checked against:
- NSE's official F&O bhavcopy (free): 52,191 closing prices of near-the-money Nifty/Bank Nifty
  options over 735 trading days (Oct 2023 → Oct 2026). The plain formula priced 3–14-day Nifty options
  ~9.5% too high and 1-day options ~7.5% too low → IV is now VIX × the real implied/VIX ratio by
  days-to-expiry and moneyness (`fno/iv_calibration.csv`).
- Upstox public 5-minute candles of all still-listed Nifty/Bank Nifty options (May–Oct 2026, 5,592
  real holding windows): after the IV fix the formula tracked real premium changes within ~4% on moving
  days, but real options lost more on sideways stretches → extra decay charged per hour held
  (1.3% / 0.6% / 0.16% / 0.1% of premium at 1–2 / 3–7 / 8–14 / 15+ days to expiry).
- All loops re-run with the corrected premiums (`FNO_CALIB=1`). Same winner; buying **1 strike in-the-money**
  beat ATM on the training years and on the unseen days, so the rule now buys 1 ITM.
  Unseen last 30/60/90/120 days, both indices: +₹5.6k / +₹11.3k / +₹35.2k / +₹19.0k (old ORB rules −₹144k).
- 11 recent trades had real 5-minute option candles: real P&L ₹3.7k vs ₹7.3k from the corrected formula on
  the same contracts — small sample, but a reminder the backtest is on the optimistic side.
Real intraday history of *expired* options needs the paid Upstox Plus plan (API returns 401 without it).

## Round 5 — remaining families + combining signals (`optimize3.py`, real-price-corrected premiums)
New families (300 settings each): EMA formulas (9/21/50 stack pullback, 20/50 cross, triple-EMA cross), MACD,
Bollinger squeeze/reversion, candlestick reversals (engulfing/hammer/shooting star at VWAP/EMA/BB), Heikin-Ashi,
Donchian breakout, VWAP σ-bands, Camarilla pivots. Only **Camarilla H4/L4 breakout** passed training on both
indices and stayed profitable on the unseen days (best training setting: +₹41.6k on the last 120 days;
14 of 15 neighbouring settings also profitable).

Combining: the noise rule + 13 confirmation filters (Supertrend 5m/15m, EMA stack, MACD, RSI>50, ADX, Heikin-Ashi,
BB width, strong close, beyond PDH/PDL, gap, context, 50 EMA) alone, in pairs, threes and k-of-n votes — 395 combos.
Win rate 35.4% → 35.8% with three filters; 134 combos beat the plain rule on training, only 3 also on unseen days.
Stacking filters = overfitting, not accuracy.

What did help: running Camarilla breakout **alongside** noise-area momentum (daily P&L correlation ≈ 0.3).
Both indices, app risk rules (one open trade per instrument, max 4/day, stop after 2 losers or −2%):
every year positive (2023 +₹31k, 2024 +₹96k, 2025 +₹15k, 2026 +₹71k); unseen 30/60/90/120 days
+₹4.5k / +₹5.3k / +₹53.6k / +₹53.7k (noise alone +₹5.6k / +₹11.3k / +₹35.2k / +₹19.0k); deepest fall ₹64k.
Fin Nifty (never tuned on) lost ₹7.4k on the last 120 days with both strategies — the edge is index-specific.

## Market recorder (from 28 Sep 2026) — collecting what free history doesn't keep
Intraday prices of *expired* options, bid/ask spreads, the whole chain's OI/IV through the day and
participant positioning can't be backtested from free sources, so they are now saved as they happen:
- **App, every 5 min in market hours** (`fno/recorder.py`, from the scheduler tick): Nifty & Bank Nifty option
  chain, nearest 2 expiries, 15 strikes each side — LTP, bid, ask, OI, volume, IV. Source: NSE's public option
  chain (works from the Mumbai server without login) or the connected broker. Kept 45 days in Redis.
- **GitHub job every evening** (`record_day.py`, `.github/workflows/record-day.yml` on main): writes
  `research/data/live/<date>/` — 1-minute OHLCV + OI for Nifty / Bank Nifty / Fin Nifty, India VIX, near futures
  and every option of the nearest 2 expiries within ±4%; NSE F&O bhavcopy; participant-wise OI and volume
  (FII / DII / Pro / Client); the day's chain snapshots. Catches up on missed days (7-day look-back).
  ~1.7 MB per day.
Ideas to test once 15–20 days exist: expiry-day premium decay and straddle selling, OI build-up / PCR shifts
before trend days, FII index-option positioning vs next-day direction, real bid/ask cost per strike, IV crush.

## Round 6 — stress tests of the two strategies (`round6*.py`, `noise_level.py`, `verify_*.py`)
Both strategies, both indices, real-price-corrected premiums, ₹2 lakh, 1% risk (base: ₹213.6k over 3¾ years).
- **Expiry / days to expiry:** options with 1 day left PF 0.87; 3–7 days PF 1.6. → Nifty rolls to the next weekly expiry
  when 1 day or less is left (`pick_trading_expiry`). Bank Nifty on its own expiry day PF 0.53 (52 trades), but the loss
  came from its weekly-expiry era (to Nov 2024) and an independent check found its expiry days only slightly more
  range-bound (not significant) → not adopted.
- **Entering late:** matched trade-by-trade on real 1-minute prices, buying 1 minute after the signal changed P&L by
  +₹4.4k on 840 trades (premium paid ±0.1%) → no measurable cost. Totals of whole runs move ±₹20k from tiny changes
  (starting capital ±3% → ₹197k–₹240k) because marginal trades get sized in or out: treat smaller differences as noise.
  (A first version of this test wrongly suggested a 1-minute delay halved profit; the matched comparison showed the
  gap was trade-set reshuffling, not the delay.)
- **Luck test** (block bootstrap, 5,000 one-year paths): at 1% risk median year +₹55.7k, 13.5% of years negative,
  median deepest fall 17.8% of capital, 1-in-20 34.6%. 1.5–2% risk: lower profit, much deeper falls → keep 1%.
- **Events:** Budget days 4/4 losers → no new signals on Budget day. Day after a US Fed decision PF 0.73 (26 trades),
  RBI days PF 2.28 (22) — too few to act on.
- **Regimes:** all profit from trend days (close far from open, 36% of days: +₹532k, PF 3.0); range days −₹213k,
  mixed −₹105k. VIX 20+ at the open PF 0.26 (21 trades).
- **Real contract history** (listed expiries incl. Bank Nifty weeklies, historical lot sizes, pre-Oct-2024 STT and
  exchange charges): ₹195.5k vs ₹213.6k; unseen 120 days ₹54.8k vs ₹53.7k → results hold.
- Adopted rules (roll + Budget day): ₹223k, unseen 120 days ₹56.5k, deepest fall ₹58k (was ₹64k).

## Round 7 — 80–120 point rallies, scalping, and "stop hit then target" (`m1.py`, `rally.py`, `scalp.py`, `stops.py`, `stops_d.py`)
New data: 1-minute Nifty / Bank Nifty / India VIX 2023-01 → 2026-10 from Upstox's public API (`fetch_1m.py`).
- **Rallies** (zigzag, swing ends on a 25-point pull-back): Nifty makes ~3 moves of 80+ points a day (independent
  re-count 3.0; 3.5 in 2024–26, 1.5 in quieter 2023), on ~87% of days; median 17 minutes. 29% start in the first half
  hour. They start 2.6× more often right after a 40+ point move the other way (V-turn), 2× when VIX > 16, 1.5× on
  gap-down days; rarely after a quiet spell (0.09×). PDH/PDL and Camarilla levels are not special starting points.
- **Real time:** when Nifty is 30 points off a low, it reaches +80 before revisiting the low 44.6% of the time;
  conditions only move that between ~34% (after 14:30) and ~50% (first half hour).
- **After:** once +80 is done, +100 follows 52%, +120 26%, +160 8%; median give-back after the top 48% within 30 min;
  39% fully reversed the same day.
- **Scalping:** 1,458 rules (chase / fade / V-bounce × targets, stops, time limits, filters), on the option with costs:
  0 profitable on training — also 0 with 0.1% slippage. Best edge before costs ≈ 1.3 index points a trade.
- **Stops:** 51% of the app's trades touched the stop; of those 49% came back to entry later, 20% reached +1R, 7.5%
  +2R; price usually went well past the stop first (median 0.47R). Wider (1.25×, 1.5×), tighter (0.75×), stop on a
  5-minute close, or fixed profit-booking at +80/+100/+120 points all earned less than the current stop.
  Generic entries: "stop then target" is ~5 points *less* common on real prices than on a shuffled random market,
  every year (independently confirmed) — no sign of stop hunting beyond chance. On real near-expiry option premiums
  (12 days so far) a 20–40% premium stop is rarely followed by the target the same day.

## Round 8 — an AI model instead of rules? (`ai_data.py`, `ai_data_trail.py`, `ai_train.py`, `ai_policy*.py`, `ai_export.py`, `fno/ai.py`)
Gradient-boosted trees (LightGBM) on 111,630 decision points (every 5-min close 09:30–14:30, both indices, 2023-01 → 2026-10),
64 inputs (price vs open/VWAP/EMAs, noise band, Camarilla & PDH/PDL, RSI/ADX/MACD/Supertrend/HA/Bollinger, VIX, gap, global
cues, time, expiry, today's structure, the two rule signals). The model scores six candidate trades per bar (CALL/PUT ×
stop 1/1.5/2 ATR, target 2R, hold to 15:15). Judged walk-forward: trained only on quarters before each test quarter, 2024-Q1 → 2026-Q4,
option P&L with the corrected premiums and costs.
- v1 (R of the stop/target trade): one seed +₹62k at threshold 0.3 but two other seeds −₹21k; 3-seed average
  +₹21k (PF 1.12, 2025 −₹14k, last 120 days +₹6k) vs the rules' +₹192k on the same period.
- v2 (predict the move to the close, heavier regularisation): no skill; its top inputs (weekday, VIX, CPR width) are overfit;
  as a filter on the rules it removed good trades. v3 (learn entries with the rules' own exits): scores ≈ 0 out of sample.
- Policies (validation-picked thresholds, top-x% of past scores, half-hour bars only, win-probability gates, smoothing): none stable.
- Shipped as an optional, off-by-default strategy ("AI model" in Settings) with a 3-seed ensemble exported to numpy
  (`fno/ai_model.npz`, 1.1 MB) trained on data **before 2026-04-09**; the app's AI backtest trades only days after that cutoff,
  so it never shows in-sample results (in-sample, the same model shows 90%+ wins — a trap worth naming).
Retrain: `python ai_data.py && python ai_data_trail.py && for s in 11 12 13; do AI_SEED=$s AI_FINAL_ONLY=1 python ai_train.py; done && python ai_export.py ",_s12,_s13" 0.3 && python make_round8.py`

## Round 9 — EMA + volume + RSI combinations (`evr.py`)
Five moving-average types (EMA, SMA, DEMA, TEMA, HMA) × pairs 5/13, 9/21, 9/21/50, 8/34, 20/50, 5/20/200, 13/50/200 × four
entry styles (cross, stack, pullback to the fast average, RSI bounce) × five RSI rules (>50, >55, >60, cross 50, cross 60) ×
relative volume ≥ 1 / 1.3 / 1.6 / 2 (10-heavyweight 5-min turnover vs its usual level at that time of day, 20 sessions) ×
exits (2R, 3R, trail on the fast average) — 1,200 of the 5,250 settings, both indices, corrected premiums and costs.
228 profitable on training, 3 robust (both indices, both halves, every year 2023-25), 0 of those profitable on the unseen 120 days;
the best training setting (DEMA 5/20/200 stack, RSI > 60, volume ≥ 1.3, 3R) made ₹183k training and −₹38k unseen.
The volume filter raised the share of profitable settings from 1% (no filter) to 45% (≥ 2× usual) by removing trades, not by picking better ones.

## Round 10 — VWAP + EMA 20/50 + RSI + ADX + volume + ATR + CPR/pivot + OI, in every combination (`combo.py`, `combo_check.py`)
Period Oct 2023 → Oct 2026 (NSE OI data starts then); tuned before 2026-04-09, judged on the unseen last 120 days.
5 triggers (EMA 20/50 cross, VWAP cross, CPR top/bottom cross, momentum candle, "all chosen filters turn true") × all 256 subsets of the
8 confirmations × 2 exits (2R, trail on VWAP) + "at least k of 8" votes = 2,572 runs, both indices, corrected premiums and costs.
- 1,306 profitable on training, 24 robust (both indices, both halves, 2024 and 2025 positive), 8 of those positive on the unseen days.
- Best survivors: **VWAP cross + ATR expanding + volume ≥ 1.3× + yesterday's PCR on side, trail VWAP** — 101 trades, PF 2.04, train +₹81k,
  unseen +₹6k (8 trades), correlation with the rules' daily P&L 0.14, rules + it: unseen +₹62.5k vs +₹56.5k; and **CPR cross + ATR expanding +
  narrow CPR + PCR + RSI > 55, 2R** — 85 trades, PF 2.07, 54% wins, unseen +₹4.5k (6 trades). Both survived threshold nudges on training;
  unseen samples are too small to conclude. Status: watch-list, re-test after ~60 more sessions.
- Ingredient effects (median training P&L with vs without): ATR expansion +₹13k vs −₹13k, volume +₹11k vs −₹15k (both mostly by cutting trades);
  EMA 20/50 and ADX slightly negative; OI, CPR, VWAP, RSI ≈ neutral. 4–5 confirmations best; 7–8 too few trades. Votes (k of 8): negative at every k.

## Round 11 — rules from a web review, tested on 1-minute data (`round11.py`, results `round11.json`)
- **Last-half-hour momentum** (sign of open→15:00, trade 15:00→15:28; variants 14:30 entry, |move| filters, first-half-hour sign): 50–52% wins,
  < 1 index point a trade, option P&L (1 lot, 1-ITM) −₹50k to −₹350k over 3¾ years — decay and costs eat it. Not for options.
- **Initial balance** (09:15–10:15): day's high set in the first hour 41%, low 41.5%, either 77%. Extension trade (close beyond IB after 10:15,
  stop IB mid, target 0.8×IB) lost in all IB-width variants on both indices.
- **Time of day / weekday audit:** 09:15–09:45 avg −0.04%, 45% green, range 0.44% (double any other slot); 12:00–13:30 quietest (0.19–0.20%);
  14:15–14:45 48% green; no 2:30–3:30 effect. Tuesday avg −0.18%, 38% green (186 days); Thursday −0.05%, 43%. Opening move ≥ 0.7% by 09:30:
  15 days, 60% reverted — too few.
- **VIX gate on the rules:** VIX ≥ 14 days PF 1.51 (train +₹126k) vs < 14 PF 1.06 (train −₹6k) — but < 14 made +₹28k on the unseen 120 days
  (2026 +₹48k), so the floor stays at 11. By one-year VIX percentile: 0–25 −₹26/trade, 25–50 +₹105, 50–75 +₹597, 75–100 +₹113.
- **Expiry afternoons:** realised vol 15:00–15:30 is 0.56 vs 0.49 (Nifty) and 0.81 vs 0.67 (Bank Nifty) on expiry vs other days. Previous-day
  max-OI strike crossed after 13:30 on expiry: Nifty 23 trades 65% wins +₹28k (1 ATM lot), Bank Nifty 11 trades 36% −₹12k. Watch-list; needs
  intraday OI (now being recorded) for a real test.
- Not testable yet (need the recorder's 5-min option data): straddle-premium VWAP cross, ΔOI-PCR intraday cross, IV-percentile entries.

## Scalping spec, Oct 2026 (`scalp_spec.py`, results `scalp_spec.json`; live code `fno/scalp.py`, parity test `tests/test_scalp.py`)
Spec from the Oct 2026 research doc, tested as written on 1-minute data 2023-01 → 2026-10-01 (924 days), 1 lot of the 1-strike-ITM
option, real-price-corrected premiums, 0.5% slippage per fill, fees at the 0.15% STT in force since 1 Apr 2026, ₹20k book per index.
- Day type (6 points, every 15 min): narrow CPR, beyond PDH/PDL, outside the 09:15–09:30 range, 80% of 5-min closes on one side of VWAP,
  ADX(14, 5m) > 25 rising, EMA 9/21/50 stack. 4+ Trend, 3 in-between (ORB only), else no trades.
- Setups on 1-min closes: A opening-range breakout (09:30–11:00, ADX rising), B pullback to EMA 9 in Trend, C level break + retest.
  Exits: stop at trigger/pullback low capped at 1 × ATR(1m); +1R → stop to entry + costs, then exit on a close through EMA 9;
  5-bar time stop; close through VWAP; next level outside Trend; 15:15. Confidence ≥ 65 (volume and OI scored flat: no history).
- Governor: −2% day, give-back (peak ≥ 1.5%, 40% given back), fees > 30% of gross after 5 trades, 3 losses → 30-min pause,
  5 losses, a setup off after 2 losses, skip trades with < 3× fees of room to the next level.

| Nifty, 1 lot | 2023 | 2024 | 2025 | 2026 | PF | unseen 120 d |
|---|---|---|---|---|---|---|
| Spec as written | −59.3k | −46.7k | −59.8k | −52.9k | 0.45 | −28.4k |
| 0.1% slippage | −36.4k | −21.2k | −28.3k | −32.4k | 0.66 | −15.6k |
| No governor | −245k | −226k | −291k | −256k | 0.45 | −178k |
| Ride the trend (2×ATR5 stop, trail 5m EMA 21, 1 trade, setup B) | +19.5k | +28.0k | −14.7k | −26.8k | 1.05 train | −14.3k |

Bank Nifty: spec PF 0.36 (−68k / −90k / −103k / −83k). Every other variant (A, B or C alone, no classifier, no time stop, 5-min ATR
stop, confidence 80) negative in all four years on both indices.
- Why: average entry +0.75 index points (round 7 found 1.3); 73% of trades hit the 1-minute stop; 19% wins; fees ≈ ₹72 a round trip
  equal the gross loss again. The classifier traded 84% of Nifty trend days (|open→close| ≥ 0.8%) the right way but lost on them
  with scalp exits; the governor cut the loss ~4× but cannot create an edge.
- The current rules on trend days: Noise + Camarilla trade 69% of Nifty trend days (63% the right way) and make all their profit
  there (+₹4.37 lakh on trend days vs +₹2.07 lakh in total, ₹2L / 1% risk). Missed Nifty up days: 24 of 65 — 10 VIX above the
  ceilings (8 above 20, 2 at 18–20; rules off there since round 18), 7 slow grinds that never cleared the band or a Camarilla level,
  4 a wrong-way first trade (1 trade per rule), 2 opening drives (half the move done by 09:45), 1 VIX < 11. Bank Nifty: 60 of 119 up
  days missed — 23 slow grinds, 19 VIX ceilings, 8 wrong-way first, 6 opening drives, 4 VIX < 11.
- Decision: not adopted. Built as a separate, paper-only **Scalp book** (own ledger and capital, off by default) so the rules can be
  watched live; the live functions reproduce this backtest exactly (day type on 28,720 bars, 440 trades: same setup, stop, exit
  bar and reason — `tests/test_scalp.py`).

**Sizing follow-up (same day):** "buy the most lots the cash allows, and pick the strike among 2 ITM … 2 OTM that gives the most profit".
Strike chosen per signal by the best expected net profit (fees and spread included) if the index reaches the target (next level, max 2R)
within 10 minutes — the same rule the live Scalp book uses (`pick_strike`).

| ₹20,000 | 2023 | 2024 | 2025 | 2026 | PF | strikes picked |
|---|---|---|---|---|---|---|
| Nifty, 1 lot 1 ITM (spec) | −59.3k | −46.7k | −59.8k | −52.9k | 0.45 | all 1 ITM |
| Nifty, max lots 1 ITM | −79.4k | −52.2k | −62.5k | −50.9k | 0.46 | all 1 ITM |
| Nifty, max lots best strike | −85.1k | −50.8k | −65.5k | −60.6k | 0.57 | 82% 2-OTM or 1-OTM |
| Bank Nifty, max lots best strike | −71.2k | −45.4k | −68.4k | −38.6k | 0.36 | mixed |

(Rows above refill ₹20,000 every day.) On one real compounding ₹20,000 account, Nifty max lots + best strike never rose above ₹20,000,
was under ₹10,000 by 17 Feb 2023 and could no longer afford a lot after 27 Jun 2023 (₹1,359 left); Bank Nifty ran out by 23 Oct 2023.
Bigger size and cheaper OTM strikes magnify a losing edge. Built anyway as the Scalp book's default sizing (paper only), with this result on the page.

## Round 23 — the 13 Oct 2026 rally day, and a trend-day catcher (`round23.py`, `round23_harness.py`, `round23_trend.py`, results `round23_results.json`)
Question: Nifty rose ~430 points (low to close) on 13 Oct 2026 and Sensex ~1,500, yet the book lost money that day. Why, and can
rules catch these days without hurting the rest? Same pricing as the app's backtester (its trades are reproduced exactly);
train = 2023-2025, judged on 2026 (Jan - 16 Oct, recorder days added); both indices; ₹2 lakh at 1% risk unless stated.

**What happened on 13 Oct (journal + backtest agree):** trades *were* taken. Nifty Noise CALL at 09:45 (the open drive was already
+186 points), trailing exit at 10:45 when price dipped back under VWAP during the 09:45-13:45 pause (-₹1,229 live); Bank Nifty
Camarilla CALL 09:50, 45-minute time stop at 10:35 (-₹1,566). The Nifty Camarilla H4 break at 09:50 (would have made +₹7,325 by
15:15) was blocked because the Noise trade was still open (one open signal trade per index). The real move came after 14:15,
after the last Noise entry check. Net -₹2,794 on a +1.6% day.

| Variant (both indices) | Train 2023-25 | 2026 | 2025 | 13 Oct |
|---|---|---|---|---|
| V0 current rules | +₹131.0k, PF 1.21, fall -50.7k | +₹104.7k, PF 1.74 | +15.0k | -1.0k |
| No Camarilla 45-min time stop | +71.6k | +98.3k | +7.4k | -1.0k |
| Camarilla time stop 90 min | +101.8k | +102.7k | +12.7k | -1.0k |
| Noise trail on band only | +127.1k | +107.2k | +15.3k | -1.0k |
| Noise without the trailing exit (stop / 4R / 15:15) | +121.7k | +96.1k, fall -28.5k | +16.7k | **+5.9k** |
| Noise and Camarilla may hold together | +195.0k, fall -57.0k | +107.6k | -4.0k | -1.0k |
| One re-entry after a trail / time exit | +108.5k | +90.3k | +28.8k | -1.0k |
| No VIX ceilings | +115.6k | +91.1k | +7.8k | -1.0k |

Holding the 13 Oct trade to the close would have paid, but removing the trailing exit costs money over the 3.8 years and in 2026:
the exits that hurt on this day are the ones that save money on the many days that reverse. The VIX ceilings also hold: 2026's
missed trend days were mostly March-April with VIX 18-28, and trading them lost.

**Trend-day catcher (new, own slot):** 32 settings (first check 10:45 / 11:45, ≥ 80 / 90% of bars on one side of VWAP, VWAP-based or
2×ATR stop, band 1.0 / 1.75×, breakeven at +1R or not). All 16 band-1.75 settings were profitable in training and in 2026; most
lost in 2025 (a range year). Picked on training only: 10:45-14:15 checks, close beyond the 1.75× band and VWAP, ≥ 80% of today's
bars on that side of VWAP, stop = VWAP distance + 0.5 ATR (1-3 ATR), stop to entry at +1R, no target, hold to 15:15.

| ₹2 lakh, 1% risk | 2023 | 2024 | 2025 | 2026 | fall (train) |
|---|---|---|---|---|---|
| Current rules | +22.4k | +93.5k | +15.0k | +104.7k | -50.7k |
| Trend-day catcher alone | +24.3k | +65.1k | -28.7k | +16.2k | -48.4k |
| Current + catcher | +46.7k | +158.6k | -13.8k | +120.8k (PF 1.58, fall -27.1k) | -73.7k |

| ₹20,000, 1 lot | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|
| Current rules | -0.3k | -4.7k | +43.4k | +86.8k |
| Current + catcher | +6.6k | +38.0k | +21.0k | +129.4k |

Caveats: it does **not** fire on 13 Oct (price spent the 09:15-09:45 dip below VWAP, so the 80% rule failed) - it is a second,
slower-exit position on trend days, not a fix for that day. 95% of its trades (242 of 254) are on days the current rules also
trade, so it mostly adds exposure on trending days: more profit in 3 of 4 years, a deeper fall (-50.7k → -73.7k on training).
Nifty carries it (+13k / +39k / -1k / +26k); Bank Nifty is about flat overall. Decision: shipped as a separate strategy
("trend", own slot) in paper Autopilot and as an option in Settings and the Backtest tab; the live record decides real money.

## Round 24 — Sensex, high-VIX spreads, an earlier catcher, a ₹20k account (`round24.py`, `round24_harness.py`, results `round24_results.json`)
New data: Sensex 1-minute history 2 Jan 2023 → 16 Oct 2026 (940 sessions, `fetch_1m.py` on the research-data branch; it also
refreshed Nifty / Bank Nifty / VIX to 16 Oct). Same pricing as the app backtester; train 2023-25, judged on 2026.

**Sensex (never used to tune anything)** — ₹2 lakh, 1% risk, lot 20:

| Rules on Sensex | 2023 | 2024 | 2025 | 2026 | train PF |
|---|---|---|---|---|---|
| Noise + Camarilla (as the app traded it) | -19.2k | -2.3k | +10.0k | +17.2k | 0.96 |
| Noise only | -10.7k | +24.1k | +22.5k | -14.0k | 1.25 |
| Trend-day catcher | -1.8k | +18.0k | +19.6k | +19.2k | 1.32 (2026 PF 1.66) |

Camarilla is flat-to-negative on Sensex (PF 0.97 over 3.8 years); Noise alone flips sign between training and 2026; the catcher is
the only rule positive in training and in 2026. → Autopilot now trades Sensex with the catcher only (`AUTOPILOT_SYM_STRATEGIES`).

**Debit spreads on VIX ≥ 18 days (instead of the VIX ceilings):** buy the 1-ITM option, sell one 2 / 4 / 8 / 12 strikes further out.
The spread trades lost in every year at every width (PF 0.07-0.56; e.g. 12 strikes, ₹2 lakh: -₹42k over 98 trades). With weeks-long
expiries (Bank Nifty monthly) or high IV, neighbouring strikes move almost together, so a narrow spread barely moves while it pays
four fills of slippage and charges; wide spreads are close to the bought option, which already lost on those days. Ceilings stay.

**Earlier start for the catcher** (first check 09:45 / 10:15 / 10:45 × 70 / 80% of bars beside VWAP): 09:45 would have caught 13 Oct
(+₹5.9k) but every earlier setting earned less in training (+₹30-52k vs +₹60.6k) and in 2026. 10:45 / 80% stays.

**₹20,000 account, 1 lot, 2,000 one-year weekly bootstraps** (stop when equity < ₹12k, i.e. a lot is no longer affordable):

| | Median end | 95th pct | Ends below ₹20k | Can't afford a lot |
|---|---|---|---|---|
| Current rules | ₹11.7k | ₹111k | 69.0% | 68.5% |
| + catcher | ₹11.5k | ₹155k | 73.6% | 73.5% |
| + catcher only when the index has no open trade | ₹11.6k | ₹120k | 69.7% | 69.2% |
| + catcher once equity ≥ ₹40k | ₹11.7k | ₹155k | 66.1% | 65.9% |

A ₹20k account is fragile under every rule (two out of three random years end unable to buy a lot - same message as rounds 20 and 22).
Gating the catcher until equity reaches ₹40k keeps its upside and removes its extra small-account risk → adopted for Autopilot
(`TREND_MIN_EQUITY`). Autopilot's 15% per-trade risk cap is not modelled here, so live results at ₹20k will trade less than this.

## Round 25 — sizing a ₹2 lakh account, and the day-end lag review (`round25.py`, `round25_harness.py`, `round25_trend.py`, results `round25_results.json`; live code `fno/lag.py`)
Question: with ₹2 lakh, should the app use more of the capital (more lots)? Per-lot trades of today's rule set (Noise + Camarilla on
Nifty / Bank Nifty, trend-day catcher on all three indices) replayed with compounding from ₹2 lakh and the app's own sizing (lots =
risk % of equity ÷ risk of one lot; 1-lot floor while one lot risks ≤ 15% of equity), free-cash limit, weekly loss cap and the ₹40k
catcher gate. Judged on one path (2023-25 / 2026) **and** on every 12-month window from Jan 2023 to Oct 2025 (34 windows).

| Risk per trade | Median 12 months | Worst 12 months | Median deepest fall | Windows with a fall > 25% | Lots (2026 avg) | Capital in a trade (median) |
|---|---|---|---|---|---|---|
| 1% (current) | +18.9% | -53.0% | -26.9% | 62% | 1.09 | 6.6% |
| 1.5% | +16.9% | -53.1% | -32.0% | 79% | 1.35 | 8.2% |
| 2% | +14.3% | -41.5% | -41.1% | 100% | 1.89 | 11.1% |
| 3% | +23.3% | -80.5% | -58.5% | 100% | 3.44 | 17.5% |
| 4% | -8.3% | -81.3% | -68.4% | 100% | 3.77 | 22.7% |
| 1% + halve size at -10% / quarter at -20% | +18.5% | -53.0% | -27.1% | 65% | 1.07 | 6.6% |
| 1%, 1 lot only while it risks ≤ 2% (≥ ₹1 lakh) | +19.5% | -38.8% | -24.6% | 41% | 1.09 | 6.4% |
| same + throttle | -6.1% | -20.8% | -20.6% | 0% | 1.03 | 6.0% |
| 2% + open risk ≤ 4% + throttle | +12.8% | -35.4% | -26.0% | 53% | 1.60 | 9.2% |

Reading: at ₹2 lakh, 1% is ₹2,000 and one lot risks ₹1,900-2,600, so the account is a 1-lot account and lots step up as equity grows
(about ₹5.5 lakh for 2 lots). More risk per trade raises the best windows but not the median, and every level above 1.5% has falls
over 25% in all windows; 3-4% wipe out most of the account. Throttles and tighter caps stop the account trading after a fall and miss
the recovery (median windows turn negative). "1 lot only while it risks ≤ 2%" was better on the 12-month windows but worse on the
2023-25 path (+15.5% vs +22.6% a year) - not consistent enough to adopt. **Decision: sizing unchanged (1% of equity, compounding).**
Even at 1%, a 12-month fall of 25%+ happened in 62% of windows with the full rule set - the expected range is now shown every evening.

**Day-end lag review (`fno/lag.py`, in every after-close note, Learning tab):** per index - the day's move, how much the book caught,
each trade's exit vs holding to 15:15, why a trend day was missed (VIX ceiling, no signal, opening drive, or a signal the scheduler could
not place and its reason, now logged by `tick()`), and capital use (equity, risk budget, lots, ₹ needed for 2 / 3 lots). A 20-session
tally ranks the causes by points / rupees left and links each to the round that tested its fix and the verdict; a cause seen 3+ times
is marked "watch". Rule numbers are never changed from here.

## Round 26 — large capital: fixed stop + fixed targets / T1-T2-T3 / small targets with more trades (`round26.py`, `round26_harness.py`, `round26_trend.py`, results `round26_results.json`)
Idea tested: with many lots, exit at a fixed target (or T1/T2/T3 partial exits, or one target chosen by the market), no trailing and
no waiting for 15:15, and take more trades with small targets. Same entries as the live rules, **10 lots on every trade**, brokerage
per order (₹20 + GST, so it shrinks per lot), 0.5% slippage per fill. Train 2023-25, judged on 2026 (₹, 10 lots, all three indices).

| Exits | 2023-25 | 2026 | PF (train / 2026) | Win % | Avg win / avg loss (R) | Trades a day |
|---|---|---|---|---|---|---|
| **Current (trail, breakeven, time stop, 15:15)** | **+₹25.2L** | **+₹10.2L** | 1.23 / 1.36 | 32% | +1.64 / -0.50 | ~1.6 |
| Fixed SL + target 0.5R | -₹18.6L | +₹1.4L | 0.80 / 1.07 | 67% | +0.50 / -0.92 | |
| Fixed SL + target 1R | -₹2.7L | +₹0.2L | 0.98 / 1.01 | 52% | +0.95 / -0.86 | |
| Fixed SL + target 1.5R | +₹2.1L | +₹0.2L | 1.01 / 1.01 | 45% | | |
| Fixed SL + target 2R | +₹9.3L | +₹5.0L | 1.07 / 1.13 | 41% | +1.48 / -0.81 | |
| T1/T2/T3 = 0.5 / 1 / 1.5R (thirds) | -₹7.1L | +₹0.3L | 0.93 / 1.01 | 52% | | |
| T1/T2/T3 = 1 / 2 / 3R (thirds) | +₹4.2L | +₹4.4L | 1.03 / 1.13 | 44% | +1.22 / -0.76 | |
| Same + stop to entry after T1 | +₹6.3L | +₹3.7L | 1.05 / 1.11 | 51% | | |
| One target by market (2R trending / 0.75R otherwise) | +₹7.6L | +₹4.4L | 1.06 / 1.12 | 47% | | |
| Target 0.5R + up to 3 more trades after a target | -₹21.8L | -₹1.9L | 0.82 / 0.94 | 68% | +0.50 / -0.93 | more |
| Target 1R + up to 3 more trades after a target | -₹4.2L | -₹0.6L | 0.97 / 0.99 | 53% | | more |

Why: the current exits keep the average loss at **0.5R** (the trailing / time exits leave weak trades early) and let winners run to
**1.6R** on trend days. A fixed stop lets every loser run to the full stop (~0.9R) while a small target caps the winners: 0.5R targets
win two trades in three and still lose money. More lots multiply the per-lot result - a negative edge gets bigger with size, and
real slippage on 10+ lots of an option is larger than the 0.5% modelled, which hurts small targets most. Re-entering after a
target adds trades but each one has the same negative expectancy. Not adopted; exits unchanged for every account size.

## Round 27 — the "ultimate" combination: pick the good pieces (`round27.py`, results `round27_results.json`)
Every rule × index is a building block (Noise, Camarilla, trend-day catcher × Nifty, Bank Nifty, Sensex = 9), traded alone at ₹2,000
risk a trade. Blocks were picked and weighted on **2023-25 only** and judged on 2026.

| Block (₹, ₹2,000 risk a trade) | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|
| Nifty Noise | -0.8k | +51.7k | +22.6k | +19.6k |
| Nifty Camarilla | -12.3k | +23.6k | +6.6k | +61.2k |
| Nifty catcher | +12.2k | +39.2k | -9.6k | +19.2k |
| Bank Nifty Noise | +10.0k | +26.0k | -18.6k | +7.7k |
| Bank Nifty Camarilla | +54.2k | +38.7k | -34.8k | -5.0k |
| Bank Nifty catcher | -1.3k | +26.7k | -34.1k | -13.9k |
| Sensex Noise | -15.8k | +26.9k | +24.5k | -7.3k |
| Sensex Camarilla | -11.5k | -8.2k | -11.6k | +38.5k |
| Sensex catcher | -5.4k | +31.1k | +14.5k | +10.9k |

| Combination (blocks independent) | 2023-25 | fall | Sharpe | 2026 | fall | Sharpe |
|---|---|---|---|---|---|---|
| Current live set | +240k | -119k | 0.89 | +100k | -42k | 1.43 |
| All 9 blocks | +245k | -141k | 0.72 | +131k | -54k | 1.50 |
| **Picked on 2023-25** (profitable, 2 of 3 years positive, PF ≥ 1.05) | **+284k** | **-101k** | **1.02** | **+106k** | -42k | **1.58** |
| Picked + inverse-volatility weights | +280k | -103k | 1.02 | +98k | -42k | 1.50 |
| Top 3 blocks by Sharpe | +155k | -51k | 1.15 | +50k | -22k | 1.38 |
| Walk-forward: re-pick each quarter (last 12 months profitable) | 2024-25 +71k | -73k | 0.46 | +66k | -40k | 1.28 |
| Walk-forward: weights by last-12-month Sharpe | 2024-25 +78k | -90k | 0.49 | +24k | -53k | 0.50 |

The pick: every block except Bank Nifty catcher and Sensex Camarilla. Re-checked under the app's real slot rules (Noise and
Camarilla share a slot per index, catcher its own): **2023-25 +₹197k vs +₹152k, deepest fall -₹80k vs -₹101k, 2025 +₹19k vs -₹40k,
2026 +₹98k vs +₹91k** → adopted for Autopilot (Bank Nifty: Noise + Camarilla; Sensex: Noise + catcher; Nifty: all three).
Re-picking rules by their recent record lost to the fixed pick in every test - rules that did well lately do not keep doing well, so
the app does not rotate rules on recent results (the learning engine only vetoes / halves with strict evidence).

## Round 28 — risk of the round-27 set, Sensex recording, Backtest "Autopilot rules" (`round28.py`, results `round28_results.json`)
Same rolling test as round 25 (1% risk, compounding, app sizing / cash / weekly cap / catcher gate, 34 windows Jan 2023 - Oct 2025):

| | Median 12 months | Worst 12 months | Losing windows | Windows with a fall > 25% |
|---|---|---|---|---|
| ₹2 lakh, old set | +18.9% | -53.0% | 24% | 62% |
| **₹2 lakh, round-27 set** | **+24.9%** | **-4.3%** | **6%** | **32%** |
| ₹20,000, old set | -52.6% | -65.4% | 74% | 100% (68% end unable to buy a lot) |
| ₹20,000, round-27 set | -49.0% | -73.5% | 74% | 100% (62% end unable to buy a lot) |

The round-27 pick mostly removed the 2025-type losing stretch (Bank Nifty catcher), which is why the worst ₹2 lakh window went from
-53% to -4%. A ₹20,000 account is still too small for these rules: one lot is ~10% of it, so normal losing streaks end most years.
Also: `record_day.py` now records Sensex index, near future and options (BSE F&O) every evening so Sensex trades can be checked
against real premiums; the Backtest tab has "Autopilot rules for this index" (default) - exactly what Autopilot trades on each index.

## Round 29 — pivot points (`round29.py`, `round29_levels.py`, `round29_harness.py`, results `round29_results.json`; live rule "fib")
Seven pivot families from the previous session (Classic, Fibonacci, Woodie, Camarilla, DeMark, CPR, previous-day H/L/C), Nifty /
Bank Nifty / Sensex, 2023 → 16 Oct 2026; rules priced on options with costs, train 2023-25, judged on 2026.

**1. Are the levels special?** At the first touch of a level, does price move 0.3% back (reaction) before 0.3% through (break)?
Random levels drawn from the same distances react 51.7%. Classic 52.8%, Fibonacci 52.1%, Woodie 53.3%, DeMark 53.4%, Camarilla
53.8%, previous-day H/L/C 51.8% - indistinguishable from random. Only the **central pivot / CPR** stands out: 55-57% every year, and
against a mirror level at the same distance from the open, +5 to +8 points (z 2.6-5.6 for 0.5% to 0.2% moves). Price really does react there more often.

**2. Rules on the levels** (₹2,000 risk a trade, 1 lot, all three indices; 2023-25 / 2026):

| Rule | 2023-25 | 2026 |
|---|---|---|
| Classic R1/S1 breakout | -31k (PF 0.96) | +37k |
| Classic R2/S2 breakout | +113k (PF 1.34) | -18k |
| **Fibonacci 0.618 breakout (stop 0.382)** | **+120k (PF 1.15)** | **+68k (PF 1.38)** |
| Woodie R1/S1 breakout | -85k | +57k |
| DeMark R1/S1 breakout | -112k | -15k |
| CPR breakout | -459k (PF 0.62) | -90k |
| Camarilla H4/L4 breakout (live) | +45k | +95k |
| Central pivot fade, 1R / 2R | -120k / -90k | -18k / -11k |
| CPR edge fade / Camarilla R3-S3 fade / R1 fade / S1 fade | -13k / -82k / -97k / -90k | -2k / -19k / -2k / -18k |

The central-pivot reaction edge (~5 points of probability on a 0.3% move) is far smaller than option spread, slippage and decay - every
fade loses. Only the Fibonacci 0.618 breakout is positive in both periods (Nifty +76k, Sensex +59k, Bank Nifty -15k in training).

**3. Pivots as filters on the live set** (2023-25 / 2026, live +197k / +98k): skip narrow CPR +177k / +46k; skip wide CPR +209k / +68k;
only with the daily pivot side +131k / +64k; weekly +166k / +29k; monthly +69k / +19k. None improves 2026 - not adopted.
**4. "Narrow CPR = trend day"**: trend days followed 18.8% of narrow-CPR days vs 20.2% of wide ones; it flips by year. Not true here.

**5. Fibonacci breakout with the live set** (picked on 2023-25 for Nifty and Sensex): as an extra own-slot rule +332k / +183k (Sharpe
0.78 / 1.59) vs live +197k / +98k (0.69 / 1.32), deepest fall -123k vs -80k; instead of Camarilla +193k / +88k (worse).
Rolling 12 months from ₹2 lakh: median +41.1% vs +24.9%, worst -16.9% vs -4.3%, losing windows 3% vs 6%, falls > 25% in 79% vs 32% of
windows; 2026 +84% vs +41% (same -17.5% fall). From ₹20k: worse (85% of windows end unable to buy a lot vs 62%).
**Decision:** shipped as rule "fib" (own slot, Camarilla exits, entries to 13:00, VIX ≤ 20) for Nifty and Sensex on paper Autopilot,
switched on only once equity reaches ₹2 lakh (`FIB_MIN_EQUITY`). App backtest reproduces the research trade-for-trade.

## Round 30 — EMA crossover + volume + MACD (+ ADX, VWAP, Donchian) in depth (`round30_engine.py`, `round30_grid.py`, `round30_analyse.py`, results `round30_results.json`)
768 settings × Nifty / Bank Nifty / Sensex (2,304 backtests, 625k trades): entries EMA 9/21, 20/50, 5/13 crossover; MACD(12,26,9)
signal cross; MACD zero cross; EMA cross confirmed by the MACD histogram (9/21, 20/50); Donchian 20-bar breakout (turtle style) ×
5- or 15-minute bars × volume (none / relative turnover ≥ 1.3× / ≥ 1.6×; the 10 Nifty heavyweights vs the same time of day, round 9's
proxy - the index has no volume) × ADX ≥ 20 and rising on/off × price on the VWAP side on/off × exits (opposite signal, trail on the slow
EMA, 2-ATR stop + breakeven + hold to 15:15, fixed 2R). 1-ITM option, same pricing as the app; ₹2,000 risk a trade; up to 2 trades a day.

- 388 of 768 settings profitable in 2023-25, 199 in 2026, 65 in both.
- **Robust on 2023-25** (every year positive, PF ≥ 1.1, ≥ 2 of 3 indices positive, ≥ 100 trades): **23 settings - 2 positive in 2026**
  (Donchian 15-min, volume ≥ 1.6, ADX, opposite-signal exit: +₹6.9k and +₹5.6k on ~35 trades). 18 of the 23 were Donchian 15-min with
  volume ≥ 1.3 and mostly ADX: +₹117k to +₹237k in training with 87-93% of neighbouring settings also profitable - and -₹14k to -₹60k in 2026.
- Medians (2023-25 / 2026): Donchian +48k / -20k; EMA 20/50 +24k / -20k; EMA 20/50 + MACD +29k / -22k; EMA 9/21 -39k / -16k; EMA 9/21 + MACD
  -38k / -15k; EMA 5/13 -18k / -12k; MACD signal cross -14k / -29k; MACD zero cross -8k / -14k.
- Ingredients: volume filter -180k → +42k in training (by cutting trades, as in round 9) but every level negative in 2026; ADX helps a
  little (+20k vs -42k; 2026 -11k vs -26k); VWAP side changes nothing; 15-minute bars beat 5-minute; fixed 2R targets worst.
**Not adopted.** EMA / MACD crossovers have no edge here; Donchian + volume + ADX worked in 2023-24 and stopped in 2025-26.

## Round 32 — One direction a day: trend lines + candle open/close + pivot points + trailing stop (`round32_engine.py`, `round32_grid.py`, `round32_wide.py`, `round32_baseline.py`, `round32_analyse.py`, results `round32_results.json`)
Each day gets one allowed direction set at 09:30: none (control), the first trade's direction, the first 15-minute candle's colour,
price vs the classic daily pivot, price vs the CPR, or candle colour and pivot side agreeing. Entries in that direction only: trend-line
break (line through the last two falling swing highs / rising swing lows), a green candle closing above the previous high, the same after
a red pullback candle, a close through a classic pivot level (S2-R2), or a trend-line break on such a candle. Trailing stop on the
previous candle, the last swing, 2 ATR, or a pivot-level target. 5- or 15-minute candles, 1 or 3 trades a day. 480 settings x 3 indices,
927k trades, same option pricing and costs as the app, Rs 2,000 risk a trade, 2023-01 to 2026-10-16.

- 6 of 480 profitable on 2023-25, 31 in 2026, 4 in both; **none robust** (all 6 lost in 2025).
- Before option costs the entries average +0.01 to +0.05R a trade (index move only); after slippage, decay and charges -0.10 to -0.22R.
  Candle-based stops are tight, so costs are a large share of each trade's risk.
- One-direction bias vs none (same entry / trail / timeframe): candle + pivot agree better in 89% of pairs (median +3.3 lakh in training),
  CPR 81%, pivot 61%, first-candle colour 50%, first-trade lock 39% - mostly by cutting trades (95k vs 199k), not by better trades.
- Best ingredients: pivot-level break, swing trail, 15-minute candles, 1 trade a day. Worst: pullback candle, 5-minute candles.
- Wider version (stop >= 1 ATR, trail only after +1R, 15 min): same result, none robust.
- **Vs the current Autopilot rules** (Rs 2,000 risk a trade): current 2026 +1.83 lakh, fall -53k, Sharpe 1.66; best new pick chosen on
  2023-25 (Nifty: pivot break, swing trail, 15 min, 3 a day) 2026 +43k, fall -52k, Sharpe 0.59; current + pick 2026 +2.26 lakh but fall
  -96k, Sharpe 1.50. Month by month in 2026 the new pick beat the current rules only in January and July.
**Not adopted.** Current rules stay.
