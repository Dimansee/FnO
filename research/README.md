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
