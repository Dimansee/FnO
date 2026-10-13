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

## Round 12 — the recorded live sessions, and the AI retrained on them (`live_study.py` → `live_study.json`, `ai_models.py` → `ai_models.json`, `.github/workflows/ai-retrain.yml`)
Nine recorded sessions (2026-09-28 → 10-09: 1-minute index candles with OI, NSE bhavcopy, participant OI, and the app's 5-minute
option-chain snapshots from 10-05) plus the journal. **Nine days is a sample for leads, not for proof** — every number below needs
the recorder to keep running before it can change a rule.
- **Journal (deduplicated per signal candle):** 13 distinct recommendations. Noise-area 10: 2 targets, 1 stop, 7 squared off at 15:15,
  sum +12.4R, average exit +16.7% on the premium (08-10 Nifty PUTs 22500/22550 made +66% / +82%). Camarilla 3: 0 targets, 1 stop, +0.5R.
  Before the dedupe fix the same Nifty PUT idea was logged 6 times on 10-08; now one record per strategy per day.
- **Formula vs real premium** (792 snapshot rows, calibrated BS vs mid price): median −2.7% (Nifty −3.8%, Bank Nifty −2.4%), steady through
  the day; **−15% with 1 day to expiry** — the formula under-prices expiry-day options, which is why the 1-day expiry rule (round 6)
  stays and why demo prices and broker prices differed by ~10 points before the Portfolio relink fix.
- **Bid–ask spread:** 0.26% of premium at the money (Nifty 0.24%, Bank Nifty 0.29%), the same from 3 strikes ITM to 3 OTM and from 09:30 to 15:30 —
  the app's slippage allowance is well above it.
- **ATM straddle through the day** (non-expiry days): 100 → 101–104% by 12:00–13:00 (Nifty peaked 109% on 10-08's fall), back to 97–99% by 15:25 —
  on these days there was no steady morning decay to sell; decay is a last-hour effect.
- **OI and option volume vs the next 30 minutes** (708 Nifty/Bank Nifty snapshots): ΔPCR crosses and extremes ≈ nothing (corr 0.009).
  PCR level < 0.8 → next 30 min −23 pts, up only 27% (n 45); PCR > 1.2 → +8.9 pts, up 56% (n 57). CE share of option volume > 60% → +12 pts (n 19);
  PE share > 60% → −6 pts (n 122). Spot 2+ strikes above the max-OI strike → −6 pts (n 47). Closing within ±36 pts of the 13:30 max-OI strike
  on 3 of 5 Nifty days, never on Bank Nifty. **Watch-list:** the PCR-level and volume-share leads fit the usual "writers are right" story and
  are the only intraday-OI effects with a plausible mechanism; they need 40+ sessions.
- **Straddle vs its running average:** below average → next 30 min −0.29%, above → +0.28% (5 days) — a premium-momentum lead, too small to use.
- **Index 1-minute volume** (1,044 five-minute windows): a 2× volume burst says nothing about direction (corr 0.03) and nothing about the size of
  the next 30-minute move either (|move| corr 0.029); 3× bursts (n 11) were followed by −10 pts. Same answer as round 9's proxy volume.
- **AI retrained on everything to 2026-10-01** (3 seeds, cutoff rolled to **2026-07-09** = latest − 60 sessions, same features, threshold 0.3).
  On the 60 sessions neither model saw (07-09 → 10-01): old model 1 trade +₹3.3k, retrained 4 trades, 3 wins, +₹3.2k. The retrained model
  ships (`fno/ai_model.npz`); the app's AI backtest now starts 2026-07-09. `ai-retrain.yml` (on main, monthly + manual) refreshes the
  5-minute history, rebuilds the table, retrains, exports, runs the tests and pushes the model to both branches with the cutoff kept at
  latest − 60 sessions, so the backtest shown in the app is always out of sample.
- **Other model families on the same table, same walk-forward, same pricing** (`ai_models.py`): ridge regression, logistic (P(win) ranked),
  random forest, extra trees, histogram gradient boosting, a 64×32 neural net, 400-nearest-neighbours; policies fixed 0.2 / top 5% / top 2% of
  each model's own past scores. Best of each over 2024-26 (option P&L, ₹2 lakh): ridge +₹9k (top 5%, 222 trades, PF 1.03), neural net −₹9k
  (top 2%, PF 0.98), extra trees −₹15k, boosting −₹30k, kNN −₹65k, logistic −₹64k, random forest −₹71k; LightGBM +₹24k (fixed 0.2, PF 1.08).
  Every family lands between PF 0.7 and 1.1 — the inputs carry very little predictable signal, and no model family finds more of it than the
  trees already shipped. The rules' +₹192k on the same period stands.

## Round 13 — sizing, exits, strike, re-entry, Bank Nifty tuning, Market Profile (`round13.py` → `round13.json`, log `round13.log`)
Baseline = the round 6 book (noise W + Camarilla CA, both indices, corrected premiums): **training** (2023-01 → 2026-04-17, 809 sessions)
796 trades +₹163.1k PF 1.23 maxDD ₹64k; **unseen** (last 120 sessions, 2026-04-20 → 10-09) 126 trades +₹60.6k PF 1.56 maxDD ₹23k.
Nifty +₹100k / +₹51k, Bank Nifty +₹63k / +₹9k. "Beats" below = more net than that on BOTH halves; nothing was tuned on the unseen half.
One correction to the brief: the shipped noise rule already trails on the Zarattini half-hour band (`s_noise` hard-codes `trail="noise"`,
178 of 303 book exits) — "no trail" is a variant, not the baseline.
- **A. Sizing by regime** (same trades, P&L × a multiplier known at entry; lot rounding ignored). Training per-trade P&L by trailing-250-session
  VIX percentile: <25 ₹128, 25–50 ₹114, **50–75 ₹380**, 75+ ₹66; unseen: ₹−936 (n 5), ₹211, **₹1,047 (n 57)**, ₹−53. Multipliers 0.5/1/1.5/1
  (average exposure 1.04×): train +₹188.6k, net/maxDD 2.79 vs 2.54; unseen +₹92.7k, 3.56 vs 2.65 — the only scheme family better in net *and*
  net/DD on both halves (also 0.5/1/1.5/1.5). Two caveats: the 50–75 band was first seen in round 11 on data that included these unseen days,
  and ₹59.7k of the ₹60.6k unseen baseline came from that band — one bucket, one half-year. Opening-range quartiles: narrow ranges worst
  (₹44 then ₹−232 a trade) but no fixed scheme improved net/DD on both halves. Days to expiry: 5–7 DTE best on both halves (₹330, ₹1,222 a trade),
  1–2 and 8+ weak (₹86/₹−373, ₹92/₹−227) — still no scheme better on both by net/DD. Kelly on training says 2× the 1 % budget in every
  bucket (the cap binds): that is just "risk 2 %" — train DD ₹126k (63 % of capital), net/DD 2.26 vs 2.54. Half-Kelly ≈ flat. **Ship nothing
  yet; the VIX 50–75 overweight is the one lead, re-test after ~60 more sessions.**
- **B. Exits** (69 configs: noise target 3/4/5/none × breakeven off/1R/1.5R × trail none/noise/ATR×2/ATR×3, 12 partial-exit variants, Camarilla
  time stop 30/45/60 × be off/1/1.5). 3 of 48 noise configs beat the base on both halves, all "be 1R + noise trail": +₹0.4k on training and
  +₹3.1k unseen — noise, not an improvement. Best on training (no target, be 1.5R, ATR×3 trail) +₹186.6k then ₹49.4k unseen (−₹11k).
  Dropping the noise trail: −₹11k train / −₹16k unseen; ATR×2 trail worst (₹147.7k / ₹23.5k). Half out at 2R: 0/12 (best ₹170k / ₹55k).
  Camarilla time stop 60: better unseen (₹74.5k) but −₹28k on training; 30: worse on both. **Keep the exits.**
- **C. Strike** (both rules, both indices): 2 ITM ₹148.7k / ₹66.1k, **1 ITM ₹163.1k / ₹60.6k**, ATM ₹137.0k / ₹43.8k, 1 OTM ₹115.2k / ₹42.0k.
  Average Nifty premium ₹186 / 155 / 128 / 106, lots 1.0–1.3. Square-off (15:15) exits are 18 % of noise trades but 37 % (Nifty) / 25 % (Bank Nifty)
  of gross P&L (+₹3.9k / +₹3.2k a trade) and 23 % of Camarilla trades, 58 % / 54 % of gross (+₹4.5k / +₹5.2k); their per-trade P&L falls with
  moneyness (Nifty noise ₹4,054 → ₹3,466 from 2 ITM to 1 OTM) — decay costs the held-to-close trades most, as expected. Bank Nifty noise at 1 OTM is negative. **1 ITM stays.**
- **D. Re-entry** (max_trades 2 / 3, max_losses 2): book ₹156.4k / ₹50.5k (PF 1.18 / 1.34, unseen DD ₹34k vs ₹23k) — worse on both.
  Second attempts: after a noise-trail exit ₹−163 a trade (n 84), after a Camarilla time stop ₹−283 (n 92), after breakeven ₹−646 (n 28);
  **after a Camarilla stop +₹565 (n 65: train +₹20.2k on 56, unseen +₹16.6k on 9)**. A post-hoc book with only that re-entry: ₹183.1k / ₹77.2k,
  PF 1.25 / 1.66, DD ₹61k / ₹26k — picked after seeing the table, 9 unseen trades. **Watch-list, not shipped.**
- **E. Bank Nifty on its own** (noise mult 1.5–2.25 × stop 1.5–2.5 ATR × rr 3–5 = 36; Camarilla vix_max 18/22/26 × time stop 30/45/60 = 9, picked on
  training). Noise default ₹19.0k / ₹8.5k; best on training (mult 1.75, stop 2.5, rr 5) ₹29.0k / ₹2.1k (24 unseen trades); 0/36 beat the
  default on both. Camarilla default ₹53.3k / ₹−3.4k; best (vix_max 18, 45 min) ₹65.0k / ₹−11.4k; 0/9. BN book tuned ₹78.9k / ₹−8.4k vs ₹62.9k /
  ₹9.0k. **The Nifty-tuned settings stay on Bank Nifty.**
- **F. Market Profile** (yesterday's TPO profile from 5-min bars, 10-pt / 25-pt buckets, POC + 70 % value area; VA width median 0.36 % Nifty /
  0.44 % Bank Nifty; the open is inside yesterday's VA on 32 % of days; entries 09:45–14:30, VIX ≥ 11, 1 ITM, one trade a day, costs as the app).
  80 % rule (open outside VA, two closes back inside, target the far side): 451 training trades ₹−239.5k PF 0.57, unseen ₹−31.4k (4R target
  ₹−218k / ₹−46k). Acceptance (two closes beyond VAH/VAL, 2×ATR, 4R): 763 trades ₹−261.7k PF 0.75, unseen ₹−45.8k. Rejection (tail beyond,
  close back, fade to POC): 840 trades ₹−308.2k PF 0.60, unseen ₹−1.3k (4R: ₹−244k / ₹−6k). Open-drive continuation: 39 trades ₹−29.7k, 5 unseen.
  POC-migration filter on the noise rule (today's developing POC vs yesterday's): 328 of 448 trades kept, training ₹112.2k vs ₹107.9k, unseen
  ₹0.8k vs ₹29.5k; fixed 90-minute POC with entries from 10:45: book ₹204.7k / ₹30.0k vs ₹163.1k / ₹60.6k. **No profile rule or filter beats the
  base on both halves; the four entry rules lose on every year 2023–25.**
- Sample sizes: 796 / 126 book trades; per-rule-per-index unseen counts are 24–55, so a Bank Nifty or a single-rule "win" of ±₹10k on the unseen
  half is inside noise. Nothing from this round changes the app's rules.

## Round 14A — re-entry after a stop, pyramiding, trend-day extras (`round14a.py` → `round14a.json`, log `round14a.log`)
Baseline = the round 6 book, reproduced trade-for-trade by a day loop that can re-scan after an exit: **training** 796 trades +₹163.1k PF 1.23
maxDD ₹64k net/DD 2.54; **unseen** (last 120 sessions) 126 trades +₹60.6k PF 1.56 maxDD ₹23k net/DD 2.65; charges ₹69k. Option P&L via
`engine.trade_pnl` (1 ITM, calibrated premiums, slippage, charges), ₹2 lakh, 1 % risk — **98 % of trades are 1 lot**, so "1:0.5:0.5" pyramids
are 1:1:1 in practice. "Beats" = more net AND better net/maxDD on both halves; every pick is made on training. 20 s runtime.
- **1. Re-entry only after a stop** (same rule, one re-entry, never the same strike+type twice). Both rules, any direction: 37 re-entries,
  win 16 %, train +₹8.3k / unseen −₹9.3k (n 4) → book ₹165.1k / ₹51.3k, unseen DD ₹30k. Same direction: −₹12.5k / −₹9.3k (22 trades, win 14 %).
  Opposite direction: +₹19.4k on 16 training trades, **0 unseen trades** → book ₹181.4k / ₹60.6k (unseen = baseline, untested). Camarilla alone,
  opposite: +₹22.4k on 15 (win 20 %, 5 of them breakeven exits); noise alone: every direction loses (−₹3k to −₹11k). 0 of 9 beat on both.
- **2. Within 60 / 120 minutes of the stop.** Median gap from the stop to the next same-rule signal is 50 min; 57 % come inside 60, 95 % inside 120,
  so the window barely changes anything: 60 min any-direction ₹168.9k / ₹55.6k (22 re-entries, +₹1.9k / −₹4.9k), 120 min ₹166.4k / ₹51.3k.
  0 of 18 beat on both; the only positive cells are the same opposite-direction Camarilla trades as item 1, all in training.
- **3. Pyramiding the first trade** (+1 unit at +1R, +1 at +2R, fills at the level, same contract; stop of all units → previous add level;
  rule's own trail/target/15:15 re-simulated from the add bar). 40 % of first trades reach +1R before their exit, 20 % reach +2R (noise 38 / 19 %,
  Camarilla 41 / 21 %; train 40 / 20 %, unseen 41 / 19 %). Both rules 1:1:1: 345 of 865 first trades get an add (170 get two), 515 extra units,
  charges ₹111k vs ₹69k. Book **₹241.8k / ₹166.9k**, PF 1.23 / 2.04, but **train maxDD ₹114k (57 % of capital)**, net/DD 2.13 vs 2.54 — fails on
  training. By final exit: 127 square-offs ₹701k → ₹1,316k, 7 targets ₹73k → ₹167k, but 124 trades stopped at breakeven after the add
  ₹−64k → ₹−367k and 67 stopped at +1R after the second add ₹+115k → ₹−98k. The gain is a handful of trend days: the top-5 pyramided trades are
  121 % of the training gain (the other 292 added trades net negative) and 94 % of the unseen gain (2026-07-08 Nifty ₹16.8k → ₹44.1k,
  06-12 ₹12.8k → ₹32.6k, 10-08, 06-24 Bank Nifty noise target, 05-12). Fair comparison: the baseline × the pyramid's average 1.56 units a trade
  is ₹253.7k / ₹95.2k — **on training the pyramid earns less than simply trading bigger**; on unseen it earns more, on 5 trades. Noise only
  ₹184.1k / ₹74.7k (scaled base ₹188.5k / ₹73.5k), Camarilla only ₹220.8k / ₹152.8k (₹228.4k / ₹82.2k). 1:0.5:0.5 = 1:1:1 ± ₹2k. 0 of 6 beat
  the base or the scaled base on both.
- **4. 3rd / 4th entries on confirmed trend days** (first trade hit target or closed ≥ +2R; either rule, same-option rule). Only 10.8 % of
  training index-days (80) and 13.2 % of unseen (16) confirm, and when they do the first trade is almost always still open at 15:15 — the
  filter allows **4 extra trades in 3.7 years** (+₹2.1k / −₹1.8k). Extras on every day: 125–134 trades, −₹22.6k / −₹17.4k net, unseen −₹26.0k
  on 18 (book ₹165.0k / ₹34.6k, unseen DD ₹34k); extras on unconfirmed days the same. "Trend day confirmed" is known too late to trade on.
- **5. Pyramid + re-entry combined** (re-entry pick on training = Camarilla, opposite direction, no window; pyramid both rules). First trade
  pyramided: ₹262.9k / ₹166.9k, train DD ₹105k, net/DD 2.51 — fails training. Every trade pyramided incl. the re-entries: **₹304.2k / ₹166.9k,
  net/DD 2.64 / 5.71, train DD ₹115k** — the only lines that pass "beats", and also pass the scaled base (×1.60: ₹261.0k / ₹95.7k). What is
  in it: +₹22k from 15 opposite-direction Camarilla re-entries (training only, 0 unseen), +₹29k from pyramiding 2 of those 15, +₹77k / +₹106k from
  pyramiding first trades, of which 5 trades per half are the whole gain. **Not an edge; it is 1.6× exposure plus ~15 trades.**
- **Sanity (uncapped: re-scan after any exit, up to 4 per index-day, same-option rule).** Attempt 1: 865 trades ₹+245 a trade; attempt 2: 165,
  ₹−66 (train +₹19.3k, unseen −₹30.1k on 23); attempt 3: 16, ₹−412; attempt 4: 1. By previous exit: after a stop +₹265 (n 60; noise +₹640 on 25,
  Camarilla −₹4 on 35), after a noise-trail exit +₹98 (n 64), after a time stop −₹207 (n 36), after breakeven −₹1,221 (n 18, Camarilla −₹1,881
  on 9). Round 13's "after a Camarilla stop +₹565" shrinks to −₹4 once the same-option rule and the one-position-per-index merge are applied.
- **Ship nothing.** Re-entry after a stop is flat-to-negative except opposite-direction Camarilla (15 training trades, none unseen) — watch-list.
  Pyramiding is exposure dressed as edge (worse than the scaled baseline on training, 57 % drawdown). Trend-day extras never trigger. The app
  stays at one trade per rule per day.

## Round 14B — expiry-day (0 DTE) option selling on REAL option prices (`round14b.py` → `round14b.json`, log `round14b.log`)
First test of short premium with quotes, not a model. Two samples. **Lots NIFTY 65 / BANKNIFTY 30 for every year, slippage 0.5 %/leg/way,
full charges (4 orders a straddle), margin ASSUMED ₹1.5 lakh a Nifty straddle / ₹2.5 lakh a Bank Nifty one; nothing tuned.** 3 min runtime.
- **Data reality check.** `OPT_live5m.csv.gz` holds only contracts still listed on 1 Oct 2026 (earliest expiry 6 Oct) → **zero** expiry-day
  sessions in it; it serves only as a non-expiry 5-min control (DTE 5–90 Nifty, 26–238 Bank Nifty). The only true 0-DTE intraday sessions we
  have are the 1-min captures of **2026-10-06 and 2026-10-13 (Nifty, 2 days)**; Bank Nifty is monthly and its expiries miss the capture.
  The long sample is the NSE bhavcopy, Oct 2023 → Oct 2026: **157 Nifty expiry days (96 Thu / 53 Tue / 8 other), 82 Bank Nifty**, 582 / 657
  non-expiry control days (nearest listed expiry). On expiry days the bhavcopy's `settle` column is the index's final settlement value, so
  "hold to settle" = intrinsic against it.
- **Daily method is coarse and conservative on stops:** sell at the bhavcopy open (= the 09:15 first print), a per-leg stop counts as hit if
  the leg's day HIGH touched the level (fill = level + slippage, no intraday ordering), else buy back at the day's close (proxy for 15:15) or
  settle. 10:15 entries, combined stops and the "losing side" rule need intraday prices and only run on the 1-min days.
- **First-print bias.** The bhavcopy open is the 09:15 first print; on the 11 live days the ATM straddle credit at 09:15 is **+2.7 % (Nifty)
  / +2.8 % (Bank Nifty)** above the 09:20 open (per leg: median +2.5 % / +1 %, spread −49 % … +81 %). Without the haircut the NON-expiry control
  "earns" ₹314k (Nifty) / ₹101k (BN) selling straddles that barely decay (3.9 % / 1.4 % open→close vs **14 % / 26 % on expiry days**); with the
  haircut the Nifty control falls to ₹116k (PF 1.16) and the BN control to **−₹364k** — the control is the open print, not an edge. Every
  headline number below is quoted after the haircut (raw in the log).
- **Nifty expiry days (157), after haircut:** straddle hold-to-settle **+₹218.3k, win 66 %, PF 1.67, worst −₹22.1k (2026-09-15), maxDD ₹32k,
  146 % on ₹1.5 lakh in 3 years = ₹1,390 (0.9 % of margin) an expiry day**; straddle with 60 % per-leg stop, close +₹220.1k, PF 2.39, worst
  −₹15.0k, maxDD ₹19.6k; strangle ±1 100 % stop +₹229.3k, PF 2.72, worst −₹10.8k, maxDD ₹12.2k; strangle ±1 settle +₹241.8k, PF 1.86.
  Raw (no haircut) by year, straddle settle: 2023 +₹12k (13 days), 2024 +₹89k (52), 2025 +₹75k (53), 2026 +₹83k (39) — positive every year.
  Avg credit 147 pts (₹9.6k), median decay to settle 34 %, P&L p10 / median / p90 −₹7.8k / +₹2.9k / +₹8.2k. By |open→close spot move|:
  < 0.25 % 53 days +₹346k, 0.25–0.5 % 47 days +₹164k, 0.5–1 % 36 days −₹19k, **> 1 % 21 days −₹232k (−₹11.1k a day)** — the whole edge is
  "no big move"; VIX > 15 days earned the most (+₹171k on 44), VIX < 12 least (+₹37k on 43). Tight stops hurt: 25 % stop → 98 % of days have a
  leg stopped, net +₹109k; 40 % → 92 %, +₹142k; 60 % → 82 %, +₹253k; 100 % → 64 %, +₹244k (raw).
- **Bank Nifty expiry days (82, monthly, after haircut):** straddle settle **+₹195.9k, win 72 %, PF 2.2, worst −₹20.6k, maxDD ₹26k, 78 % on
  ₹2.5 lakh** (₹2.4k = 1.0 % an expiry day); strangle ±1 settle +₹204.7k PF 2.33; straddle 60 % stop close only +₹73.9k PF 1.51 (the daily-high
  stop rule fires on 80 % of BN days). Raw by year 2023 +₹0.6k (13), 2024 +₹162k (48), 2025 +₹46k (12), 2026 +₹11k (9); > 1 % move days 11,
  −₹126k. Weekly BN expiries (2023–24) are 64 of the 82 days.
- **Intraday sanity, the 2 real Nifty 0-DTE days (1-min, 09:20 entries):** both were trend days (06 Oct +0.8 %, 13 Oct larger). Straddle no
  stop → settle −₹3.2k / −₹8.7k (−₹11.9k, decay −37 % / −94 %); → 15:15 −₹0.1k / −₹7.7k; 40 % per-leg stop → 15:15 **+₹2.8k / +₹2.5k** (CE
  stopped 09:47 / 09:38, PE decayed); 10:15 entry stop 40 % +₹1.8k / −₹3.1k; combined 30 % −₹0.1k / −₹3.0k; losing-side 10:15 PE +₹3.4k /
  −₹1.3k; strangle ±1 40 % +₹2.0k / +₹1.8k. Two days prove nothing; they do show the daily method's "stop = day high" is not what a real
  intraday stop looks like (the stopped CE then kept rising both days). Non-expiry 1-min / 5-min controls (9–25 days an index) are ±₹10k noise;
  BN non-expiry straddles with tight stops lose ₹12–22k on 11 days.
- **Verdict: a real, data-backed edge on Nifty expiry days (and BN monthly expiry), ~1 % of margin a day, PF 1.7–2.7, but with −₹15–22k days
  (10–15 % of margin) and the 21 "> 1 % move" days giving back ₹232k of ₹450k gross.** The daily test cannot see intraday ordering, the 09:15
  fill is a first print we haircut by 2.7 %, and margin / lot sizes are assumptions. Not shippable from this alone.
- **To ship as an off-by-default "expiry-day selling" strategy the app needs:** (1) sell-side order support — SELL entries, BUY-to-close exits,
  per-leg stop-loss orders resting at the broker (not polled), a hard 15:15 square-off and a kill-switch on a combined-premium stop; (2) margin
  awareness — read SPAN+exposure from the broker (Upstox margin API) before placing, size to 1 straddle per ₹1.5 lakh Nifty / ₹2.5 lakh BN,
  and block entry when free margin < 1.3× the requirement; (3) 0-DTE option quotes at 1-min for both legs (the recorder already captures
  them) and 20–30 more real expiry days recorded before enabling, re-running round14b's intraday suite on them; (4) a gap/ event filter
  (skip when |open gap| > 0.5 %, RBI / budget / US-CPI days) because the loss days are the big-move days; (5) default off, Nifty only,
  1 straddle, 60 % per-leg stop + 15:15 exit (the settle variant needs the broker to allow holding to expiry and physical-settlement-free
  index options, which NSE index options are), and a paper-only mode first.

## Round 14C — market-structure inputs: cross-index, heavyweight breadth, BN/Nifty RS, flow proxy, expected vs realised move (`round14c.py` → `round14c.json`, log `round14c.log`)
Five inputs none of rounds 9–10 had, each as a **filter** on the two rules (engine `p["filters"]`, keep-scanning semantics as round 10)
plus its **inverse** (trade only on disagreement, to show it is not noise), as a **standalone entry** (state turns true → buy the close,
stop 2 × ATR clipped 1–2.5, 4R, no trail, 1 a day, VIX ≥ 11, 1 ITM, 09:45–14:30) and as **k-of-n votes**; plus each input's raw
predictive power on training half-hour bars. Baseline = round 6 book: train ₹163.1k (796, PF 1.23, DD ₹64k, net/DD 2.54) / unseen
₹60.6k (126, PF 1.56, DD ₹23k, 2.65). Nothing tuned on unseen. 38 s runtime.
- **Data.** Both indices and the 10 heavyweights have 5-min bars 2023-01-02 → 2026-10-09 (100 % bar coverage, aligned by date + bar time).
  **No daily FII/DII file exists in `research/data`** (only 10 days of participant OI under `data/live`), so input 4 is honestly a proxy:
  the index's own previous close-to-close and 3-session return. The ATM straddle comes from the NSE bhavcopy (2023-10-03 → 2026-10-01,
  738 index-days each), yesterday's close, nearest expiry ≥ today, scaled to one session as straddle / √(sessions to expiry) — median
  implied 145 pts Nifty / 386 BN, and the median full-day range is 1.3 × that (a straddle prices the close-to-close move, not the range).
  Input 5 is judged against the baseline restricted to straddle days (train ₹97.2k / unseen ₹50.5k, net/DD 1.51 / 2.21).
- **1. Cross-index agreement.** Other index on the same side of its VWAP (`x_vwap`): ₹134.2k / ₹54.6k (737 / 117 trades) — it passes
  90 % of baseline trades and the 80 train trades it drops made ₹+124 a trade (unseen: the 17 dropped made ₹+876 each). Other index
  also beyond its own noise band (`x_nb`): ₹44.6k / ₹17.9k on 263 / 47; beyond its 15-min opening range (`x_or`): ₹117.5k / **−₹3.4k**.
  Inverses: disagreement on VWAP ₹20.8k / ₹1.1k on 169 / 37 (worse per trade, not negative); `inv_x_nb` fires 3 times in 3.7 years.
  Predictive: "other agrees with own VWAP side" → +2.1 bp to 15:15 (t 3.9) on Nifty, +1.7 (t 2.5) BN; "disagrees" +1.5 / −3.1 bp — real
  but tiny, and the rules already demand own-VWAP agreement. 0 of 6 beat.
- **2. Heavyweight breadth** (of RELIANCE HDFCBANK ICICIBANK INFY TCS SBIN AXISBANK BHARTIARTL LT ITC, longs need ≥ K up / shorts ≤ 10−K).
  Above own VWAP, K 6 / 7 / 8: ₹127.4k / ₹35.4k, ₹103.5k / ₹43.4k, ₹89.1k / ₹47.4k — every K costs training profit; unseen net/DD rises
  (3.29, 4.23 at K 7, 8) only because the drawdown shrinks with fewer trades. Up from open, K 6 / 7 / 8: ₹150.2k / ₹28.5k,
  **₹174.9k / ₹17.1k (K 7: train net/DD 3.90, unseen 1.10 — train better, unseen not)**, ₹83.2k / ₹19.9k. On the baseline's own trades
  `breadth7_o` separates −₹31 a trade (290 failing, train) from +₹340 — and on unseen the failing trades made **+₹836 each (55)**: the split
  flips sign. Inverses ₹23.5k / −₹8.9k and ₹15.2k / +₹17.6k. Predictive: breadth 6–7 → +3.3 / +3.2 bp to close (t 3.5 / 2.9) but 8–10 only
  +1.5 / +1.4 (not monotone); up-from-open 8–10 on BN → **−4.0 bp (t −2.6)** and 0–2 → +4.3 (t 2.9): extreme breadth mean-reverts. 0 of 8 beat.
- **3. Relative strength** (own return from open minus the other's; 30-min change). Trade the stronger index (`rs_open`): ₹94.4k / ₹35.6k;
  `rs_30`: ₹15.8k / ₹31.0k; both: ₹18.7k / ₹13.3k. The **inverse** — trade the weaker index — ₹109.5k / ₹21.3k on 335 / 58, train net/DD
  4.68 (DD ₹23k) but unseen 1.87 — the Camarilla rule likes the laggard (₹+84k of it). Predictive: no RS bucket moves the next 30 min
  (|t| < 0.6); BN "weaker from open" → +2.3 bp to close (t 2.7), reversion, not momentum. 0 of 5 beat.
- **4. Flow proxy.** Yesterday up for longs / down for shorts (`flow1`): ₹105.2k / ₹15.3k; 3-session (`flow3`): ₹116.8k / ₹28.2k;
  inverses ₹68.9k / **₹45.3k** and ₹53.9k / ₹32.4k. On the baseline's trades `flow3` pass earns ₹+278 vs fail ₹+123 (train) but ₹+427 vs
  ₹+539 (unseen). Predictive: every bucket |t| ≤ 2.5 with no consistent sign across the indices. 0 of 4 beat; real FII/DII cash flows
  were not testable.
- **5. Expected vs realised.** Realised range so far < 50 / 60 / 80 % of the implied daily move ("room to run"): ₹6.8k / ₹9.5k (39 / 14
  trades), **−₹14.7k / +₹31.6k (85 / 24)**, −₹2.5k / +₹29.8k (193 / 51) — negative on training, the best unseen cells of the round, on 24–51
  trades. The opposite, > 100 % ("already spent"): **₹119.3k / ₹3.7k**, train net/DD 2.76 vs 1.51 (train better, unseen not): on training the
  breakouts that work are the ones that fire after the day has already moved more than the straddle implied. Predictive: |move| to 15:15 is
  29 bp when realised < 50 % and 31.5 bp when > 100 % (Nifty) — "room to run" does not mean bigger moves; signed continuation is
  positive only in the > 100 % bucket (+2.5 bp, t 3.0). 0 of 4 beat the sub-baseline.
- **Votes** (`x_vwap`, `breadth7_v`, `rs_open`[, `em_lt60`]): 2 of 3 ₹100.3k / ₹43.2k, 3 of 3 ₹87.9k / ₹29.6k, 2 of 4 ₹111.6k / **₹64.0k**
  (unseen net/DD 3.90, train 1.70 — on straddle days only ₹42.2k / ₹49.5k vs the sub-baseline ₹97.2k / ₹50.5k), 3 of 4 ₹59.8k / ₹29.5k,
  4 of 4 ₹7.0k / ₹10.6k on 24 / 7. 0 of 5 beat.
- **Standalone entries** (all lose or fail): both indices beyond their noise bands + VWAP, checked every 5 min (`e_xidx_nb`): ₹62.5k /
  **−₹4.1k** on 246 / 43 (PF 1.19 / 0.93); state triggers on own VWAP + the input (`e_xidx_vwap`, `e_breadth7/8`, `e_breadth8_o`, `e_rs`,
  `e_vote3of4`, `e_vote4of4`): 1,000–1,400 trades, **−₹32k to −₹236k train / −₹30k to −₹139k unseen**, DD ₹120–300k — VWAP-state flips
  are a churn machine at 1 ITM with charges. Added to the baseline book (one position per index, book first) every one is worse than the book.
- **Ship nothing.** Market-structure agreement is real but tiny (+2–3 bp to the close, t 2.5–4) and the two rules already carry own-VWAP
  confirmation, so every filter removes more profitable trades than losers on at least one half. Watch-list: `inv_rs_open` (trade the
  laggard, Camarilla), `em_gt100` (breakouts after the implied move is spent), `em_lt60` on unseen — all one-half effects. Real FII/DII
  daily flows still need a data source before input 4 means anything.

## Round 14D — verification of 14B's expiry-day straddle (`round14d.py` → `round14d.json`, log `round14d.log`)
Independent rebuild from the raw bhavcopy + index 5-min file (shares only `fno.indicators.charges` and round 6's lot history with 14B). 16 s.
- **Rebuilt, 157 Nifty expiry days, ATM off the 09:15 index open, 2.7 % haircut, 0.5 %/leg slippage, charges of the time:** hold-to-settle
  **+₹193k (PF 1.66, win 66 %) with the real lot history 50→25→75→65; +₹217k with a flat 65 lot — 14B's ₹218k reproduces** (14B used flat 65).
  60 % per-leg stop → close: ₹163k / ₹213k (PF 2.08 / 2.34) vs claimed ₹220k, PF 2.39. Per unit (lot = 1): **+22 pts an expiry day net,
  median +42, p10/p90 −123 / +123**. Positive every year (pts/unit 2023 161 on 13 days, 2024 1203, 2025 1000, 2026 1119). Haircut 0 / 5 % →
  ₹229k / ₹163k; 1 % slippage ₹187k; STT on exercise ±₹1.4k; ATM off the previous close instead of the open → only ₹84k (PF 1.27), so the
  strike must be picked at the open. Settle column = index settlement (one value a day; median 12 pts off the 15:25 close), debit = intrinsic.
- **Where it breaks.** Top 10 days are 65 % of the net (drop best 5 → ₹116k, best 10 → ₹68k). The 21 days with > 1 % open→close move lose
  ₹210k = 72 % of all losses and every one of them is a loser (ex them +₹403k — cheating, shown only to size the dependence). Worst day
  −₹23.3k (2025-04-17, −310 pts/unit; 2026-09-15 −340 pts/unit); maxDD ₹32k = 490 pts/unit; worst 5 consecutive expiries −₹32k. Thursday
  era (100 days to 2025-08-28) made ₹79k = 17 pts/unit/day; Tuesday era (57 days) ₹114k = 31 pts/unit/day — **more than half the money is
  the last 13 months**, and 8 holiday-shifted Mon/Wed expiries alone made ₹57k. With the 60 % stop the > 1 % days lose only ₹23k, but the
  daily "fill at the stop level" is optimistic: on 2026-10-13 the ATM CE went 71.7 → 133 between 09:15 and 09:20 (+86 %, through a 60 % stop
  in one jump).
- **Control (578 same-week non-expiry days, open → close):** at 0 % haircut it "earns" ₹276k (PF 1.46) — the raw open print is a mirage; at
  2.7 % it is ₹28k (PF 1.04), a random one-per-week pick −₹35k; at 5 % −₹183k. Decay open→close: **median 31–33 % on expiry days vs 6–8 % on
  non-expiry days** (means 11–14 % vs 0–3 %) — the expiry-day decay difference is real and survives the haircut the control does not.
- **Data:** no zero or missing opens at ATM ±2 strikes on any expiry day (the 3 % zero-open rows sit a median 1,250 pts from the settle);
  on the two live days the first 1-min bar is a real trade, so "first real trade" = the open. **Haircut:** across the 11 live days the 09:15
  straddle is +1.6 % mean / +2.4 % median above the 09:20 open but ranges −19 % … +19 % (0-DTE days: +6.2 % on 06 Oct, **−18.7 % on 13 Oct**,
  where the index moved 70 pts in 5 min). Re-pricing the 09:15 quote for the 09:15→09:20 index move alone *raises* the credit (+9 % on
  average, a straddle struck at the open gains when the index leaves it), so the 2.7 % is a quote-width/staleness effect, not an index-move
  effect; 2.7 % is a fair central estimate and the net is only ±15 % sensitive to it anyway. Live 13 Oct path: 140 → 365 by 15:15 (the
  −1.9 % move day of the 1-min sample).
- **Verdict: partly holds.** The number reproduces and the decay difference vs non-expiry days is genuine, but the edge is a ~22 pts/day
  premium for carrying a −300 … −340 pts/unit tail (≈ 15 × the average day), concentrated in 10 days and in the Tuesday era. Worth an
  off-by-default paper strategy with a hard gap/event filter and intraday stop logging — not a live one from this evidence.

## Round 15 — an LLM multi-agent trading desk (TradingAgents-style), tested out of sample (`agent_pack.py`, `agent_eval.py`, `round15_brief.txt`, `round15_decisions.json` → `round15.json`)
Built the TradingAgents pipeline as a prompt (technical / options / macro analysts → bull vs bear → research manager → trader with absolute
levels → aggressive / conservative / neutral risk desk → portfolio manager) and ran it with Claude as the model, one decision per index per
session at 09:30, on the **60 sessions 2026-07-16 → 10-09** — all after the model's knowledge cutoff, so no hindsight. Each pack held only what
a trader knew at 09:30 (10 daily bars, open, gap, PDH/PDL, Camarilla, CPR, VIX, overnight global moves, previous-day PCR and OI walls, DTE,
the three 09:15–09:30 bars, ATR, noise-band levels); the agent could read nothing else. Decisions = BUY CALL / BUY PUT / NO TRADE, entry at
09:30 or on a level trigger before 14:30, stop and target as index levels, hold to 15:15. Scored with the same 1-ITM option pricing,
slippage, charges and ₹2 lakh / 1% sizing as the rules.
- **Desk:** 120 decisions → 32 no-trade, 21 triggers never filled, **58 trades: −₹1.0k, 38% wins, PF 0.99, deepest fall ₹46k.**
  Puts +₹6.1k (35), calls −₹7.1k (23). 09:30 entries +₹18.6k (32, PF 1.49); level-trigger entries −₹19.6k (26, PF 0.49) — the desk's
  "wait for confirmation" entries bought the top of moves. Confidence did not rank outcomes (0.45–0.50 bucket −₹18.7k, 0.50–0.55 +₹11.4k).
- **The two rules on the same 60 sessions:** 63 trades, **+₹19.3k, PF 1.43, deepest fall ₹19k.**
- Where both traded the same index (35 times) they agreed on direction 34 times; the desk made ₹21.7k on those vs the rules' ₹29.2k — same
  idea, worse exits (fixed targets/stops instead of the rules' trail and 4R).
- **As a filter:** rule trades only when the desk agreed with the direction: 37 trades +₹28.3k PF 2.24 vs all 63 +₹19.3k. One half, 60
  sessions, and the desk mostly restates the 09:30 direction the rules themselves require — a lead to re-test on the next 60 sessions, not a result.
- Cost: ~6k tokens per decision (≈ 700k for the test); live, two decisions a day fit a free LLM tier, but the full framework (20–35 calls per
  decision with tool-using analysts) would not.
**Verdict:** the agent desk did not beat the rules — it matched their direction and lost the difference on execution. Nothing ships; the
"desk agrees" filter goes on the watch-list.

## Quick check — three suggested strategies (`nr7_holygrail.py` → `nr7_holygrail.json`)
1. **ORB + VWAP + volume** — already tested: the 15-min ORB with VWAP/trend filters *was* the app's original rulebook (rounds 1–2) and was replaced by the
   noise-area rule; re-run now: Nifty −₹68k (PF 0.92), Bank Nifty −₹233k (PF 0.74) over 3¾ years. Volume filters (proxy, round 9; futures volume, round 12): no edge.
2. **Crabel NR7 + ORB** (trade only when yesterday's range was the narrowest of 7): 148 Nifty / 135 Bank Nifty NR7 days. ORB on NR7 days: Nifty −₹14k, BN −₹26k
   (training losses, small unseen gains of ₹4–8k). The two rules on NR7 days: Nifty +₹18k (PF 1.21) vs +₹134k on the other days; BN −₹4k vs +₹76k.
   NR7 days are *worse* for breakouts here, not better — Indian index narrow days tend to stay narrow (same finding as round 13's opening-range sizing).
3. **Raschke "Holy Grail"** (ADX > 30 rising, pullback to the 20 EMA, enter on the pullback bar's high/low, stop beyond it): fires rarely on 5-min bars — 12 Nifty / 21 BN
   trades in 3¾ years at ADX 30; Nifty lost at every setting (PF 0.1–0.9), BN +₹6–23k at ADX 30 on training, flat/negative unseen; at ADX 20–25 both lose.
   The engine's round-3 cousin (EMA 9/21 cross + ADX + VWAP) was −₹3.4k unseen. Not adopted.

## Round 16 — stocks beyond F&O: cash-equity rules on the Nifty 500 (`round16_stocks.py` → `round16_stocks.json`)
Long-only delivery, no leverage, ₹2 lakh, equal slots (equity / max positions), integer shares, **0.1 % a side on every fill**, signals on
the close → buy at the next open, stops/targets filled at the level (open if gapped through), exit signals → next open. 499 stocks of
**today's** Nifty 500 list (yfinance, 2020-10 → 2026-10) — **survivorship bias: the names that fell out are missing and the names that got
in are there because they rose**, so everything below is an upper bound, worst for the momentum/breakout rules. Nifty daily exists only
from 2023-10; before that the benchmark / 200-day regime / RS use an equal-weight proxy of today's Nifty 50 names (daily corr 0.96 with
the real index on the overlap, but +41 % vs +15 % total — same bias), de-drifted 11 %/yr so 2020-10-13 = 11,934. Stats run from the
first tradeable day 2021-11-01 (252-day warm-up); train to 2025-06-30, unseen 2025-07-01 → 2026-10-12, nothing tuned on unseen,
17 variants fixed up front. "Beats" = higher CAGR *and* shallower max drawdown than Nifty buy-and-hold on **both** halves. 4 s runtime.

| rule (best variant) | train CAGR / maxDD | unseen CAGR / maxDD | trades | win | hold | turnover/yr | top-5 trades = % of net |
|---|---|---|---|---|---|---|---|
| **Nifty buy-and-hold** | **9.1 % / −18.8 %** | **−9.4 % / −15.6 %** | – | – | – | – | – |
| 1 momentum 6m top-10, monthly, no filter | 41.1 % / −32.6 % | 94.6 % / −10.7 % | 211 | 46 % | 46 d | 3.9× | 62 % |
| 1 momentum 6m top-10 + Nifty>200d | 39.9 % / −35.0 % | −0.6 % / −10.7 % | 179 | 48 % | 39 d | 3.6× | 59 % |
| 1 momentum 12-1 top-10 + filter | 36.4 % / −38.9 % | 4.3 % / −13.4 % | 138 | 56 % | 53 d | 2.6× | 60 % |
| 2 52-wk high + vol 1.5×, trail 15 %, 60 d | 22.7 % / −24.8 % | 8.3 % / −15.2 % | 291 | 44 % | 38 d | 5.5× | 56 % |
| 2 same with 10 % stop | 19–32 % / −25…−33 % | −9…−17 % / −22…−28 % | 302–354 | 36 % | 31–35 d | 6× | 68–104 % |
| 3 pullback RSI(2)<10 above 200d, >10d MA / 10 d, stop 7 % | 13.2 % / −27.1 % | −0.5 % / −17.0 % | 3003 | 60 % | 3.4 d | 58× | 27 % |
| 3 pullback RSI(14)<35 | 9.3 % / −21.3 % | −8.8 % / −19.7 % | 1299 | 60 % | 5.2 d | 25× | 64 % |
| 4 RS top decile, 20-d breakout, 3×ATR trail, max 8 | 22.4 % / −29.1 % | 65.4 % / −20.5 % | 328 | 38 % | 25 d | 7.9× | 76 % |
| 4 same, 2×ATR trail (± regime, top quintile) | −3…7 % / −30…−49 % | −5…−15 % / −18…−25 % | 519–797 | 35 % | 11 d | 13–19× | n/a (net ≈ 0) |
| 5 mean reversion: 3-d drop >7 % above 200d, 5 d / +5 % | 6.5 % / −31.1 % | 12.0 % / −8.0 % | 1648 | 58 % | 4.3 d | 33× | 23 % |
| **5 mean reversion: 3-d drop >10 % above 200d, 5 d / +5 %, max 10** | **24.0 % / −14.0 %** | **11.5 % / −4.4 %** | **838** | **64 %** | **3.9 d** | **17×** | **9 %** |
| 5 same + 7 % stop (drop >7 %) | 7.2 % / −35.4 % | 0.0 % / −11.9 % | 2083 | 55 % | 3.2 d | 41× | 40 % |

- **Only one rule beats Nifty on both halves: the 10 %-drop mean reversion** (yearly 2021 +6, 2022 +6, 2023 +41, 2024 +36, 2025 +4,
  2026 +13 % vs Nifty −2 / −1 / +20 / +9 / +11 / −14 %; 41 of 61 months positive, worst month −₹14.7k, best +₹35k; median trade +5.0 %
  (the target, hit 53 % of the time), the 47 % that time out average −3.8 %, worst −26 % (IDBI, Mar 2026); 578 signal days, 3.5 names on
  an average signal day, more than 10 slots needed on only 35 days). **It survives the two checks the others fail:** universe cut to the
  150 most liquid names each day (closer to what was investable at the time): train 22.5 % / −8.1 %, unseen 5.4 % / −4.0 %, 424 trades,
  69 % win — still beats; cost 0.15 % / 0.20 % a side: 21.6 % / 9.8 % and 19.7 % / 8.2 % CAGR. Net is spread thin (top 5 trades 9 % of
  it) — the opposite of every other rule.
- **Momentum is the survivorship-bias poster child.** 41 % train CAGR, +124 % in 2026 unfiltered while the index did −14 % — MTARTECH
  alone (3,080 → 6,950) made ₹80k on a ₹73k slot; top-5 trades are 60 % of the net; on the top-150 universe train drops to 22–28 % and
  unseen goes **negative** (−12 % / −5 %); drawdowns −28…−39 % vs Nifty's −19 %. The 200-day regime filter cuts 2026 but not the train
  drawdown. Same for the 52-week-high and RS breakouts: 2023 is 45–108 % of a 4½-year run, the other years are flat or negative, 5 trades
  are 56–104 % of the net, and the top-150 check leaves 8–9 % CAGR with −31 % drawdowns. RSI pullbacks win 60 % but turn the book
  25–58×/yr and the 0.1 % costs eat them (−0.5 … −9 % unseen).
- **Correlation with the option rules (overlap 2023-01-04 → 2026-10-09, options +₹229k, maxDD ₹59k):** every stock rule is −0.08 … −0.16
  daily, −0.03 … −0.29 monthly (Nifty itself −0.07) — genuinely uncorrelated. ₹2 L in the mean-reversion rule next to ₹2 L in the options:
  **combined +₹509k, maxDD ₹62k = 15.6 % of ₹4 L vs ₹59k on ₹2 L alone** (the two drawdowns never overlapped: sum of the separate maxDDs
  ₹104k). Top-150 variant: +₹434k, DD ₹58k. The momentum rules would add ₹436k but widen the combined drawdown to ₹100–180k.

**Nightly "stock recommendation" for the app (plain words):** after the close, scan the Nifty 500 (prefer the 150 most liquid) for stocks
whose close is above their 200-day average and down more than 10 % over the last three closes; sort by size of the drop; list up to
(10 − open positions) names. Buy at the next day's open with equity ÷ 10 per name (≈ ₹20k, whole shares — skip names where one share is
more than that); place a +5 % limit sell at once; whatever has not filled by the close of the 5th trading day is sold at the next open.
No stop (the 7 % stop version lost — the first bounce is the trade and a stop sells the low). Expect **~7 signals a month on the
liquid-150 list (14 on the full list), 60–70 % hit the +5 %, average +1.4 % a trade after costs, 1–3 names open at a time** so the
₹2 L is mostly idle (median hold 4 days). Expected return: the backtest says 20–24 % a year with an 8–14 % worst drawdown; **plan on
8–12 % a year** — the unseen 15 months did 11.5 % on the full list and only 5.4 % on the liquid-150 list, and this rule is itself
survivorship-exposed (a stock that crashed 10 % and never bounced is more likely to have left the index), so the −3.8 % average on the
47 % of trades that time out is understated. It is extra, uncorrelated income (≈ ₹20–30k a year on ₹2 L at the planned rate), not a second
engine; 2022's +6 % and 2025's +4 % are what a flat year looks like. Not shipped — the data needs a point-in-time index list (the NSE
archive has the Nifty 500 change log) and the rule should be paper-traded for a quarter first.

## Quick check — candlestick patterns on daily stocks (`candles_check.py` → `candles_check.json`)
The 12 textbook patterns (hammer, bullish engulfing, morning star, piercing line; shooting star, bearish engulfing, evening star, dark
cloud cover; doji, spinning top, bullish/bearish harami), standard definitions on adjusted OHLC, 499 Nifty 500 names 2020-10 → 2026-10
(645k stock-days), train < 2025-07-01 / unseen after. Each pattern vs the control (**all** days, and all days after a 5-day fall/rise —
the context the books require): average 10-day forward return and the chance of a ±5 % move in the pattern's direction within 10
sessions. Earlier, round 5 tested candle/Heikin-Ashi entries on the index 5-minute data (`s_candle`, `s_heikin`) — both lost.

| pattern (after the required 5-d fall/rise) | n train/unseen | avg 10-d, train (ctrl) | unseen (ctrl) | hit ±5 % in 10 d, train (ctrl) | unseen (ctrl) |
|---|---|---|---|---|---|
| hammer | 1,700 / 663 | +1.97 % (+1.89) | +0.76 % (+0.47) | 56.5 % (56.5) | 43.3 % (44.6) |
| bullish engulfing | 3,611 / 1,282 | +1.76 % (+1.89) | +0.18 % (+0.47) | 56.0 % (56.5) | 41.4 % (44.6) |
| morning star | 2,748 / 977 | +1.61 % (+1.89) | +1.13 % (+0.47) | 54.9 % (56.5) | 47.2 % (44.6) |
| piercing line | 589 / 155 | +1.96 % (+1.89) | −0.16 % (+0.47) | 57.9 % (56.5) | 35.5 % (44.6) |
| shooting star | 2,790 / 574 | +1.80 % (+1.46) ✗ | +0.38 % (+0.45) | 44.5 % (44.3) | 43.6 % (41.9) |
| bearish engulfing | 10,427 / 2,206 | +1.26 % (+1.46) | +0.12 % (+0.45) | 44.6 % (44.3) | 42.2 % (41.9) |
| evening star | 2,461 / 729 | +1.30 % (+1.46) | +0.12 % (+0.45) | 46.5 % (44.3) | 43.8 % (41.9) |
| dark cloud cover | 1,471 / 269 | +1.30 % (+1.46) | −0.22 % (+0.45) | 41.7 % (44.3) | 44.2 % (41.9) |
| doji | 11,353 / 3,400 | +1.86 % (+1.89) | +0.21 % (+0.47) | 55.9 % (56.5) | 42.3 % (44.6) |
| spinning top | 13,545 / 4,006 | +1.85 % (+1.89) | +0.61 % (+0.47) | 55.7 % (56.5) | 45.6 % (44.6) |
| bullish harami | 13,454 / 3,882 | +1.85 % (+1.89) | +0.56 % (+0.47) | 55.0 % (56.5) | 44.5 % (44.6) |
| bearish harami | 6,065 / 2,163 | +1.10 % (+1.46) | +0.73 % (+0.45) ✗ | 44.5 % (44.3) | 39.4 % (41.9) |

- **None of the 12 beats its control on both halves** (bar: ≥ 0.5 % better 10-day average *and* ≥ 3 points better hit rate, both halves).
  The differences are all within ±1 % and ±3 points of random days with the same context, and flip sign between halves (morning star looks
  best unseen, +1.13 vs +0.47, but is *below* control in training; piercing line is above in training and the worst unseen). Bearish
  patterns after a rise don't even go the right way on average (stocks still rose +1.1…+1.8 % in training). What *does* carry
  information is the context: after a 5-day fall every day averages +1.89 % vs +1.53 % overall — it's the drop, not the candle shape.
- **As a filter on the tested 10 %-drop rule it only subtracts:** rule alone train 75 % hit +5 % in 10 d / unseen 62 % (n 2,110/297);
  requiring a bullish candle on the signal day 67 % (n 148 — and 25 unseen, too few); requiring a doji/spinning top 71 % / 58 %;
  dropping signals with a bearish candle 75 % / 62 % (unchanged, n −7 %). Not adopted; the picks stay as they are.
