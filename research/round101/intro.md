# Round 101: every rule reviewed, 24 other families, combinations and overlays (13 Nov 2026)

## The ask
Devansh (13 Nov): review all the strategies, find other strategies, and research and backtest in depth whether any strategy or any combination beats today's rules.

## Bottom line
- **Today's 11 slots all earn their place.** Every one makes money per lot over 2023 – Oct 2026, and removing any one of them makes the account worse (0 of 11 removals pass).
- **Over 3,500 backtests of other things. One passes.** A "squeeze release" slot on Sensex (15-min bars, half size) beats today's rules in all six cells with the worst fall inside the 2-point rule, beats every random-entry control, and stays ahead in 84% (₹2L) and 74% (₹20k) of resampled histories.
- **Not switched on.** Three things hold it back: 2026 on its own is worse with it (₹2L 6.80L → 6.39L, ₹20k 1.20L → 0.60L); 85% of its trades open while today's rules already hold the same side, so it adds to the same bet and deepens the worst fall at ₹5L and above; and with fills 1% worse on each side the advantage at ₹2L is gone. Proposed next step: run it as a shadow slot (signals recorded, no orders) and compare with this backtest.
- **Everything else fails**: 24 indicator families (372 versions), intraday momentum, long straddles, today's rules on the indices where they are not traded, correlation caps, equity-curve sizing, cross-index confirmation, Keltner breakouts.

## Method
- **Baseline:** today's Autopilot exactly as live (rounds 88 + 90 + 99: Noise band, Camarilla, Trend-day, Fibonacci 0.618, Channel 15m; gates ₹40k / ₹2L / ₹5L; 1 OTM below ₹60k, 2 OTM from ₹5L; profit trail; CPR sizing), Outcomes replay.
- **Data:** 1-min Nifty, Bank Nifty, Sensex and India VIX, 2 Jan 2023 – 16 Oct 2026 (940 sessions). Option prices are the house model (India VIX based), with slippage and charges.
- **Design / unseen:** anything chosen is chosen on 2023-24 only and judged on 2025-26.
- **Bar:** better than today in 2023-24, 2025-26 and the full period, at ₹2L and at ₹20k, worst fall no more than 2 points deeper.
- **Controls:** random-entry pieces with the same exit and trade count; random size tilts of the same strength; block bootstrap; cost and timing stress.
- **Reading the tables:** each cell is final money (worst fall). Bold = more money than today.
- **How noisy the yardstick is:** today's rules at 0.8% risk a trade end 2023-24 at ₹11.80L, at 1.0% at ₹30.70L, at 1.2% at ₹22.28L. Final money depends heavily on the path (gates, whole lots, weekly loss limit), so a single better cell means little; that is why the bar asks for all six and the controls matter.

## Results
