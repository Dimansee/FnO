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
