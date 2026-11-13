
## Decision
- **No change to today's rules.** Nothing is switched on from this round.
- **Squeeze release on Sensex (n = 14, exit when momentum turns, 1 a day, half size) is the first candidate since the channel slot to meet the bar.** It is recommended as a **shadow slot** first: the app records each signal and what it would have made, places no orders, and after 4–6 weeks the live signals, fills and timing are compared with this backtest. Reasons for not going straight to live:
  - **2026 alone is worse** at both capitals, although one continuous run from 2023 stays ahead at every year end.
  - **It is the same bet, not a new one.** 85% of its trades open while today's rules already hold the same side, and it loses on today's worst days too. At ₹5L and ₹10L the worst fall deepens by 4–7 points; at full size it deepens to −39% at ₹2L.
  - **Thin against costs.** +0.5% worse fills each side: still more money in all six cells (worst fall a fraction over the 2-point rule). +1%: 2023-24 and the full period at ₹2L fall below today. Sensex option prices here are modelled, not real quotes.
  - **The worst fall is sensitive.** Small changes (exit one bar later, third size) move the 2023-24 fall between −27% and −38%.
  - **Some hindsight in n = 14.** The version found by the strict search (n = 20) also passes, but fails when the entry is one bar late. n = 14 was the best of a neighbour grid on 2023-24 and survives one-bar-late entry and exit.
- **Watch list (no action):**
  - Keltner breakout on Nifty: makes money alone in both periods in 20 of 30 versions, but as a slot it deepens the worst fall, and with the account's risk lowered to compensate the advantage does not hold in 2025-26 (0 of 360 pass even with hindsight).
  - Average-price band 2.0 trend (15-min): 7 of its 8 versions make money alone in both periods; on Sensex at half size it is better in all six cells but the 2025-26 fall is 2 points too deep.
  - Channel trades not confirmed by the other index average −0.08R (328 trades; +0.15R when confirmed). Skipping them helps at ₹5L+ in 2023-24 only. Re-check with more data.
- **Closed:** correlation cap between Nifty and Sensex (the twin trades are the good ones: 0.22R against 0.06R), equity-curve sizing (no better than random tilts of the same strength), intraday momentum (no effect on Indian indices; if anything a small reversal, under 1.5 bps), long straddles, Supertrend, Parabolic SAR, Ichimoku, Stochastic, Heikin-Ashi, DMI, RSI(2), initial balance, value-area 80% rule, efficiency ratio, linear-regression trend, and today's rules on Bank Nifty (Channel, Fibonacci, Trend-day) and Sensex (Camarilla).

## Not checked
- Crypto and US books, and the stock book, were not part of this round.
- Option selling was left out (the app is buy-only).
- Straddles on expiry day: the pricing engine rolls to the next expiry at 1 day to go, so that case could not be tested.
- Real Sensex option quotes (only Nifty and Bank Nifty daily closing prices are on file); intraday prices are the house model.
- Data ends 16 Oct 2026.

## Code
`research/round101/` (`lib.py` harness; `a_review` … `m_more`; `l_squeeze.py` is the squeeze deep check). Results: `research/round101_results.json`. Log entry r101.
