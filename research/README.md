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
