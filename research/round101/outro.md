
## Decision
- **No change to today's rules.** Nothing is switched on from this round.
- **Squeeze release on Sensex (n = 14, exit when momentum turns, 1 a day, half size) is the first candidate since the channel slot to meet the bar.** It runs as a **shadow slot** first (built 13 Nov, see below): the app records each signal and what it would have made, places no orders, and after 25 sessions the live signals, fills and timing are compared with this backtest. Reasons for not going straight to live:
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

## Shadow slot (built 13 Nov, recording from 16 Nov)
- **What it does:** `fno/squeeze.py` runs inside the every-minute tick. When a 15-minute Sensex bar closes it checks the rule; on a signal it writes down the index level, the at-the-money option's live bid, ask and price, and the stop. It then follows the trade to its exit (stop, momentum turn or 15:15) and writes down the exit prices. One a day, as tested.
- **What it does not do:** it places no orders and holds no money. The paper account, the scalp book and the carry slot are untouched (asserted in the tests).
- **Same trades as the backtest:** driven through the full history tick by tick, the live code takes **569 of 569** backtest trades (same entry bar and side). All stop and momentum exits are identical; the 166 square-offs differ by a median 2.3 index points (first price at 15:15 against the 15:00 bar's close). A 12-session window of candles (what the live feed gives) produces the same signals as the full history.
- **Where to see it:** Research page and Autopilot → Improvements, card "Shadow slot: squeeze release on SENSEX". It can be stopped there. The nightly journal carries each day's shadow trades as `squeeze_shadow`.
- **What decides it (after 25 sessions, about 21 Dec):** go live at half size only if (1) signals arrive within a minute of the bar close, (2) the real bid-ask gap on the Sensex option is well under 1% a side, and (3) the live result per lot is not worse than the backtest's ₹340 a trade. Otherwise drop it. About 15 signals are expected in 25 sessions, so the fills and timing will be measured well; the profit figure will still be a small sample.

## Not checked
- Crypto and US books, and the stock book, were not part of this round.
- Option selling was left out (the app is buy-only).
- Straddles on expiry day: the pricing engine rolls to the next expiry at 1 day to go, so that case could not be tested.
- Real Sensex option quotes (only Nifty and Bank Nifty daily closing prices are on file); intraday prices are the house model.
- Data ends 16 Oct 2026.

## Code
`research/round101/` (`lib.py` harness; `a_review` … `m_more`; `l_squeeze.py` is the squeeze deep check; `n_shadow_parity.py` checks the live code against the backtest). Shadow slot: `fno/squeeze.py`, `tests/test_squeeze_shadow.py`. Results: `research/round101_results.json`. Log entry r101, pending item p-squeeze.
