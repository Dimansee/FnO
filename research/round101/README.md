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

### Today (reference)

| | ₹2L 2023-24 | ₹2L 2025-26 | ₹2L full | ₹20k 2023-24 | ₹20k 2025-26 | ₹20k full |
|---|---|---|---|---|---|---|
| Today's rules | 30.70L (-26%) | 21.27L (-28%) | 183.02L (-26%) | 1.94L (-66%) | 2.21L (-77%) | 19.96L (-66%) |

### A. The 11 slots traded today (1 lot, ₹ per lot by year) and what happens if one is removed

| Slot | Trades | Wins | Avg R | 2023 | 2024 | 2025 | 2026 | Avg R 2023-24 | Avg R 2025-26 | Removed: cells better (of 6) | Fall rule | Verdict |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Noise band NIFTY | 124 | 41% | 0.33 | +1,496 | +40,650 | +27,882 | -313 | 0.38 | 0.28 | 1 | deeper | no |
| Trend-day NIFTY | 161 | 39% | 0.30 | +19,187 | +47,702 | -5,280 | +22,217 | 0.39 | 0.20 | 0 | ok | no |
| Trend-day SENSEX | 169 | 40% | 0.30 | +7,950 | +40,839 | +17,207 | +11,056 | 0.29 | 0.30 | 1 | deeper | no |
| Fibonacci 0.618 NIFTY | 337 | 32% | 0.26 | -5,108 | +42,514 | +63,616 | +36,341 | 0.17 | 0.39 | 1 | ok | no |
| Camarilla BANKNIFTY | 310 | 33% | 0.24 | +54,480 | +60,181 | -6,704 | +15,356 | 0.39 | 0.07 | 1 | deeper | no |
| Fibonacci 0.618 SENSEX | 348 | 32% | 0.24 | -3,843 | +44,250 | +37,762 | +11,534 | 0.24 | 0.24 | 1 | ok | no |
| Noise band SENSEX | 214 | 36% | 0.24 | -2,596 | +30,293 | +34,309 | -10,703 | 0.24 | 0.24 | 1 | deeper | no |
| Camarilla NIFTY | 352 | 30% | 0.22 | +7,015 | +33,049 | +22,673 | +73,345 | 0.16 | 0.28 | 1 | deeper | no |
| Noise band BANKNIFTY | 122 | 39% | 0.14 | -6,858 | +5,679 | -2,140 | +22,300 | 0.12 | 0.16 | 1 | ok | no |
| Channel 15m SENSEX | 994 | 47% | 0.13 | +8,128 | +77,384 | +27,322 | -18,708 | 0.12 | 0.14 | 0 | ok | no |
| Channel 15m NIFTY | 1019 | 47% | 0.11 | +31,170 | +78,522 | +33,160 | +14,251 | 0.10 | 0.11 | 0 | ok | no |

### B. Indicator families not in today's rules: 372 versions (1116 index x version cells)

Versions making money (three indices together, 1 lot, after costs): design 2023-24 **133**, unseen 2025-26 **29**, both **15** of 372.

| Family | Versions | + in 2023-24 | + in 2025-26 | + in both | Median ₹/trade 2023-24 | Median ₹/trade 2025-26 | Best ₹/trade 2025-26 |
|---|---|---|---|---|---|---|---|
| avg-price band 2.0 trend | 16 | 14 | 7 | 7 | +168 | -47 | +69 |
| supertrend 10/3 | 16 | 8 | 1 | 1 | -11 | -86 | +15 |
| keltner 2.0 | 16 | 10 | 0 | 0 | +71 | -88 | -2 |
| keltner 1.5 | 16 | 5 | 0 | 0 | -47 | -94 | -19 |
| supertrend 7/3 | 16 | 5 | 0 | 0 | -63 | -104 | -12 |
| ttm squeeze release | 16 | 5 | 4 | 3 | -67 | -117 | +130 |
| supertrend 10/2 | 16 | 1 | 6 | 0 | -186 | -131 | +168 |
| ichimoku cross+cloud | 16 | 9 | 4 | 4 | +13 | -151 | +58 |
| afternoon break of morning range | 16 | 12 | 0 | 0 | +125 | -159 | -126 |
| ichimoku cloud break | 16 | 8 | 0 | 0 | -18 | -174 | -54 |
| heikin 3 strong | 16 | 6 | 0 | 0 | -64 | -178 | -75 |
| avg-price band 1.5 trend | 16 | 11 | 0 | 0 | +70 | -179 | -99 |
| initial balance failed break | 16 | 0 | 0 | 0 | -177 | -183 | -110 |
| psar 0.01 | 16 | 4 | 1 | 0 | -106 | -191 | +18 |
| psar 0.02 | 16 | 0 | 0 | 0 | -214 | -194 | -84 |
| stoch momentum 80/20 | 16 | 8 | 0 | 0 | -36 | -198 | -150 |
| initial balance break | 16 | 12 | 0 | 0 | +48 | -201 | -134 |
| dmi cross adx>20 | 16 | 2 | 0 | 0 | -153 | -209 | -52 |
| linreg trend r2>0.7 | 16 | 12 | 0 | 0 | +117 | -217 | -126 |
| efficiency ratio>0.5 + 20-bar high | 16 | 0 | 6 | 0 | -318 | -229 | +1,014 |
| rsi2 pullback in trend | 16 | 1 | 0 | 0 | -52 | -240 | -121 |
| stoch reversal 20/80 | 16 | 0 | 0 | 0 | -201 | -247 | -142 |
| avg-price band 2.5 fade | 16 | 0 | 0 | 0 | -258 | -255 | -83 |
| value area 80% rule | 4 | 0 | 0 | 0 | -185 | -289 | -251 |

Top 10 versions by 2023-24 result (what one would have picked), and what they did afterwards:

| Version | Trades 2023-24 | ₹/trade 2023-24 | Trades 2025-26 | ₹/trade 2025-26 |
|---|---|---|---|---|
| ichimoku cross+cloud 15m, trail 1.5R, stop 1 ATR, 3/day | 655 | +327 | 552 | -83 |
| keltner 2.0 15m, trail 1.5R, stop 1 ATR, 3/day | 1202 | +274 | 1126 | -73 |
| keltner 2.0 15m, 3R target, stop 2 ATR, 3/day | 1070 | +271 | 978 | -26 |
| linreg trend r2>0.7 15m, trail 1.5R, stop 1 ATR, 1/day | 914 | +250 | 892 | -295 |
| linreg trend r2>0.7 5m, 3R target, stop 2 ATR, 1/day | 1407 | +237 | 1294 | -201 |
| avg-price band 2.0 trend 15m, trail 1.5R, stop 1 ATR, 1/day | 836 | +236 | 725 | +19 |
| ichimoku cloud break 15m, trail 1.5R, stop 1 ATR, 3/day | 636 | +229 | 643 | -243 |
| linreg trend r2>0.7 15m, trail 1.5R, stop 1 ATR, 3/day | 975 | +221 | 976 | -296 |
| keltner 2.0 15m, trail 1.5R, stop 1 ATR, 1/day | 954 | +216 | 909 | -121 |
| ichimoku cross+cloud 15m, trail 1.5R, stop 1 ATR, 1/day | 603 | +207 | 520 | -104 |

### C. Intraday momentum (first half-hour predicts last half-hour)

| Index | Period | Days | Correlation with last 30 min | Avg move 15:00-close when following the sign (bps) | Hit rate |
|---|---|---|---|---|---|
| NIFTY | 2023-24 | 490 | -0.080 | -0.08 | 51% |
| NIFTY | 2025-26 | 444 | -0.059 | -0.64 | 48% |
| BANKNIFTY | 2023-24 | 490 | -0.091 | -0.40 | 49% |
| BANKNIFTY | 2025-26 | 444 | -0.074 | -1.27 | 46% |
| SENSEX | 2023-24 | 490 | -0.094 | +0.57 | 52% |
| SENSEX | 2025-26 | 444 | -0.080 | -0.77 | 50% |

As bought options (1 lot, after costs): 168 index x version cells; positive in 2023-24: 1; in 2025-26: 20; in both: 0. Median ₹ per trade: 2023-24 -140, 2025-26 -144.

### D. Buying both sides (long straddle), model prices

| Day filter | Versions | + in 2023-24 | + in 2025-26 | + in both |
|---|---|---|---|---|
| 3+ days to expiry | 48 | 4 | 0 | 0 |
| NR7 yesterday | 48 | 0 | 4 | 0 |
| all days | 48 | 2 | 0 | 0 |
| inside day yesterday | 48 | 0 | 1 | 0 |
| low VIX (bottom 30%) | 48 | 0 | 0 | 0 |
| narrow CPR | 48 | 0 | 0 | 0 |

### E. Today's rules on indices where they are not traded (added to today)

All 11 combinations of these four were also run: none passes (best 2 of 6 cells).

| Added | ₹2L 2023-24 | ₹2L 2025-26 | ₹2L full | ₹20k 2023-24 | ₹20k 2025-26 | ₹20k full | Verdict |
|---|---|---|---|---|---|---|---|
| Channel 15m BANKNIFTY | 12.89L (-30%) | **25.78L** (-28%) | 96.63L (-39%) | 1.94L (-66%) | 2.21L (-77%) | 19.85L (-66%) | no (1/6, fall deeper) |
| Fibonacci 0.618 BANKNIFTY | 28.00L (-29%) | 20.62L (-28%) | 159.61L (-32%) | **1.96L** (-66%) | 2.20L (-77%) | **20.55L** (-66%) | no (2/6, fall deeper) |
| Trend-day BANKNIFTY | **31.65L** (-27%) | 9.86L (-31%) | 165.15L (-29%) | **2.30L** (-63%) | **2.24L** (-75%) | 9.93L (-63%) | no (3/6, fall deeper) |
| Camarilla SENSEX | 30.17L (-32%) | **24.91L** (-29%) | **203.22L** (-32%) | **1.95L** (-83%) | **2.45L** (-68%) | 12.48L (-83%) | no (4/6, fall deeper) |
| Camarilla SENSEX (exact rebuild of the Sensex set) | 24.00L (-29%) | **25.35L** (-29%) | **193.15L** (-30%) | 0.05L (-86%) | **2.91L** (-72%) | 2.30L (-86%) | no (3/6, fall deeper) |

### F. Portfolio overlays on today's trades

| Overlay | ₹2L 2023-24 | ₹2L 2025-26 | ₹2L full | ₹20k 2023-24 | ₹20k 2025-26 | ₹20k full | Verdict |
|---|---|---|---|---|---|---|---|
| corr cap: both twins at half size | 7.79L (-27%) | 7.61L (-27%) | 40.16L (-27%) | 1.85L (-68%) | **2.22L** (-73%) | 7.60L (-68%) | no (1/6) |
| corr cap: both twins at 0.75x | 10.45L (-28%) | 13.39L (-27%) | 93.69L (-28%) | 1.85L (-68%) | **2.21L** (-77%) | 10.45L (-68%) | no (1/6, fall deeper) |
| corr cap: drop the Sensex twin | 5.58L (-27%) | 5.06L (-22%) | 31.23L (-27%) | 1.85L (-44%) | 1.22L (-75%) | 4.97L (-44%) | no (0/6) |
| corr cap: drop the Nifty twin | 3.94L (-27%) | 5.58L (-19%) | 14.86L (-27%) | 1.74L (-47%) | 1.12L (-65%) | 4.39L (-47%) | no (0/6) |
| corr cap: drop the twin that enters second | 9.00L (-23%) | 7.76L (-23%) | 52.82L (-28%) | **1.95L** (-44%) | 1.30L (-76%) | 7.74L (-44%) | no (1/6) |
| corr cap: second twin at half size | 11.59L (-28%) | 16.01L (-27%) | 100.88L (-28%) | 1.85L (-68%) | **2.22L** (-75%) | 15.84L (-68%) | no (1/6) |
| opposite: twins 1.25x (agreement as confirmation) | **35.50L** (-39%) | **42.60L** (-30%) | **197.51L** (-39%) | 1.93L (-66%) | **2.35L** (-77%) | **35.70L** (-66%) | no (5/6, fall deeper) |
| equity curve: slot's last 10 trades < 0R -> half size | 10.50L (-28%) | 15.12L (-28%) | 65.92L (-28%) | **2.03L** (-59%) | **2.23L** (-63%) | 7.31L (-59%) | no (2/6, fall deeper) |
| equity curve: slot's last 10 trades < 0R -> skip | 5.02L (-33%) | 11.82L (-23%) | 22.04L (-33%) | 1.68L (-61%) | 1.73L (-36%) | 5.77L (-61%) | no (0/6, fall deeper) |
| equity curve: last 10 trades >0R 1.25x, <0R 0.75x | 23.85L (-30%) | **26.60L** (-30%) | 158.22L (-30%) | 1.83L (-66%) | **2.35L** (-75%) | **22.63L** (-66%) | no (3/6, fall deeper) |
| equity curve: slot's last 20 trades < 0R -> half size | 19.17L (-27%) | 15.33L (-28%) | 138.03L (-28%) | **2.05L** (-61%) | 1.95L (-65%) | 9.24L (-61%) | no (1/6) |
| equity curve: slot's last 20 trades < 0R -> skip | 10.77L (-26%) | 4.86L (-27%) | 69.93L (-27%) | 1.61L (-56%) | 1.31L (-57%) | 4.73L (-56%) | no (0/6) |
| equity curve: last 20 trades >0R 1.25x, <0R 0.75x | **33.53L** (-27%) | **23.05L** (-30%) | **195.68L** (-27%) | 1.92L (-66%) | **2.32L** (-72%) | 17.00L (-66%) | no (4/6) |
| equity curve: slot's last 30 trades < 0R -> half size | 20.02L (-26%) | 17.93L (-28%) | 159.72L (-26%) | **2.05L** (-61%) | 2.05L (-74%) | 9.14L (-61%) | no (1/6) |
| equity curve: slot's last 30 trades < 0R -> skip | 10.95L (-25%) | 18.70L (-25%) | 108.07L (-26%) | 1.86L (-64%) | 1.87L (-70%) | 18.67L (-64%) | no (0/6) |
| equity curve: last 30 trades >0R 1.25x, <0R 0.75x | **33.85L** (-29%) | **36.11L** (-29%) | **194.84L** (-29%) | 1.93L (-66%) | **2.33L** (-77%) | **36.27L** (-66%) | no (5/6, fall deeper) |
| skip unconfirmed channel trades | 27.66L (-26%) | **22.60L** (-28%) | 169.98L (-26%) | 1.94L (-66%) | 2.21L (-77%) | **21.11L** (-66%) | no (2/6) |
| unconfirmed channel trades at half size | **31.37L** (-26%) | 20.82L (-28%) | 180.43L (-26%) | 1.94L (-66%) | 2.21L (-77%) | 17.28L (-66%) | no (1/6) |

### H. Size tilts against random tilts of the same strength

Share of random tilts (same multipliers, same number of trades tilted, random trades) that the real tilt beats, per cell.

| Tilt | ₹2L 2023-24 | ₹2L 2025-26 | ₹2L full | ₹20k 2023-24 | ₹20k 2025-26 | ₹20k full | Better in all six | Fall rule |
|---|---|---|---|---|---|---|---|---|
| equity curve last 20: 1.25x / 0.75x | **33.53L** (beats 75%) | **23.05L** (beats 32%) | **195.68L** (beats 88%) | 1.92L (beats 53%) | **2.32L** (beats 63%) | 17.00L (beats 43%) | no | kept |
| equity curve last 20: 1.15x / 0.85x | **36.15L** (beats 88%) | **23.25L** (beats 35%) | **198.42L** (beats 98%) | 1.82L (beats 30%) | 2.03L (beats 28%) | 13.76L (beats 25%) | no | deeper |
| equity curve last 30: 1.25x / 0.75x | **33.85L** (beats 82%) | **36.11L** (beats 87%) | **194.84L** (beats 90%) | 1.93L (beats 47%) | **2.33L** (beats 75%) | **36.27L** (beats 93%) | no | deeper |
| equity curve last 30: 1.15x / 0.85x | **30.77L** (beats 52%) | **27.47L** (beats 73%) | **184.92L** (beats 65%) | 1.93L (beats 45%) | 2.04L (beats 58%) | **23.92L** (beats 75%) | no | deeper |
| equity curve last 40: 1.25x / 0.75x | **34.25L** (beats 87%) | **31.35L** (beats 83%) | **190.01L** (beats 88%) | 1.93L (beats 52%) | **2.34L** (beats 72%) | **23.25L** (beats 52%) | no | deeper |
| equity curve last 40: 1.15x / 0.85x | 30.24L (beats 55%) | **34.32L** (beats 95%) | **194.58L** (beats 100%) | 1.93L (beats 62%) | 2.04L (beats 40%) | **24.00L** (beats 67%) | no | deeper |
| equity curve last 60: 1.25x / 0.75x | **32.51L** (beats 73%) | **35.64L** (beats 93%) | 171.40L (beats 25%) | 1.93L (beats 60%) | **2.34L** (beats 65%) | **28.76L** (beats 83%) | no | deeper |
| equity curve last 60: 1.15x / 0.85x | 29.30L (beats 55%) | **33.60L** (beats 88%) | 170.44L (beats 27%) | 1.93L (beats 70%) | 2.04L (beats 18%) | **25.37L** (beats 65%) | no | deeper |
| cross-index confirmed 1.25x / unconfirmed 0.75x (known at entry) | 26.05L (beats 68%) | **33.72L** (beats 97%) | 173.55L (beats 73%) | 1.93L (beats 58%) | 1.67L (beats 87%) | **28.16L** (beats 88%) | no | deeper |
| cross-index confirmed 1.15x / unconfirmed 0.85x | 23.06L (beats 12%) | **29.24L** (beats 88%) | 179.44L (beats 60%) | 1.94L (beats 87%) | 1.36L (beats 40%) | **21.55L** (beats 62%) | no | kept |
| channel unconfirmed skipped + Fib confirmed 1.25x | 25.84L (beats 62%) | **29.97L** (beats 97%) | 170.27L (beats 83%) | 1.86L (beats 30%) | 2.21L (beats 0%) | **29.51L** (beats 93%) | no | deeper |

### G. Best new families of 2023-24 added to today's Autopilot as extra slots

| Added piece (full size) | Alone 2023-24 ₹/lot | Alone 2025-26 ₹/lot | ₹2L 2023-24 | ₹2L 2025-26 | ₹2L full | ₹20k 2023-24 | ₹20k 2025-26 | ₹20k full | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| ichimoku cross+cloud 15m [trail 1.5R, 3/day] NIFTY | +98,077 | -15,289 | 20.80L (-34%) | 6.51L (-35%) | 151.24L (-36%) | **2.38L** (-70%) | 0.05L (-90%) | 6.51L (-70%) | no (1/6, fall deeper) |
| keltner 2.0 15m [trail 1.5R, 3/day] NIFTY | +183,439 | -444 | 23.01L (-42%) | 18.08L (-32%) | **192.70L** (-42%) | **3.98L** (-62%) | **2.40L** (-68%) | **95.42L** (-62%) | no (4/6, fall deeper) |
| keltner 2.0 15m [3R target, 3/day] NIFTY | +142,814 | +61,441 | 24.62L (-42%) | **40.44L** (-29%) | **204.02L** (-42%) | **3.15L** (-54%) | **5.85L** (-58%) | **41.04L** (-54%) | no (5/6, fall deeper) |
| linreg trend r2>0.7 15m [trail 1.5R, 1/day] NIFTY | +106,313 | -42,752 | **45.91L** (-40%) | 11.35L (-35%) | 173.13L (-40%) | **2.83L** (-44%) | 2.01L (-80%) | 11.35L (-44%) | no (2/6, fall deeper) |
| avg-price band 2.0 trend 15m [trail 1.5R, 1/day] NIFTY | +87,571 | +47,051 | 18.58L (-40%) | **27.13L** (-31%) | 158.69L (-40%) | **2.66L** (-52%) | **4.05L** (-72%) | 16.33L (-52%) | no (3/6, fall deeper) |
| ichimoku cross+cloud 15m [trail 1.5R, 3/day] BANKNIFTY | +37,124 | -43,613 | 14.95L (-32%) | 8.82L (-33%) | 97.51L (-35%) | **2.01L** (-84%) | 1.34L (-69%) | 4.67L (-84%) | no (1/6, fall deeper) |
| keltner 2.0 15m [trail 1.5R, 3/day] BANKNIFTY | +15,913 | -89,231 | **34.28L** (-39%) | 10.96L (-36%) | 132.32L (-49%) | **2.78L** (-51%) | 1.60L (-77%) | 12.11L (-51%) | no (2/6, fall deeper) |
| keltner 2.0 15m [3R target, 3/day] BANKNIFTY | +29,974 | -75,327 | 6.96L (-30%) | **21.77L** (-34%) | 102.63L (-30%) | **2.48L** (-64%) | 1.78L (-79%) | **21.71L** (-64%) | no (3/6, fall deeper) |
| linreg trend r2>0.7 15m [trail 1.5R, 1/day] BANKNIFTY | +28,452 | -155,225 | 11.16L (-30%) | 4.83L (-42%) | 64.53L (-49%) | 1.80L (-53%) | 0.05L (-94%) | 3.46L (-53%) | no (0/6, fall deeper) |
| avg-price band 2.0 trend 15m [trail 1.5R, 1/day] BANKNIFTY | +25,763 | -54,216 | 19.53L (-30%) | 13.16L (-35%) | 122.56L (-36%) | **3.04L** (-43%) | 0.05L (-92%) | 13.86L (-43%) | no (1/6, fall deeper) |
| ichimoku cross+cloud 15m [trail 1.5R, 3/day] SENSEX | +79,243 | +13,297 | 14.79L (-36%) | 17.15L (-30%) | 160.43L (-36%) | **2.66L** (-67%) | **2.67L** (-57%) | **20.25L** (-67%) | no (3/6, fall deeper) |
| keltner 2.0 15m [trail 1.5R, 3/day] SENSEX | +129,479 | +7,568 | **33.67L** (-45%) | 20.06L (-34%) | **205.48L** (-45%) | 0.04L (-86%) | **2.64L** (-75%) | 0.04L (-86%) | no (3/6, fall deeper) |
| keltner 2.0 15m [3R target, 3/day] SENSEX | +117,257 | -11,615 | **51.20L** (-39%) | **31.46L** (-32%) | **241.26L** (-39%) | **3.31L** (-56%) | **2.36L** (-75%) | **41.54L** (-56%) | no (6/6, fall deeper) |
| linreg trend r2>0.7 15m [trail 1.5R, 1/day] SENSEX | +93,896 | -64,980 | 26.12L (-38%) | 16.45L (-33%) | 160.80L (-38%) | **2.95L** (-55%) | 0.05L (-93%) | 16.44L (-55%) | no (1/6, fall deeper) |
| avg-price band 2.0 trend 15m [trail 1.5R, 1/day] SENSEX | +84,174 | +20,791 | 22.56L (-39%) | **32.64L** (-31%) | **193.33L** (-39%) | **2.29L** (-62%) | 1.35L (-76%) | **31.53L** (-62%) | no (4/6, fall deeper) |
| linreg trend r2>0.7 5m [3R target, 1/day] NIFTY | +162,857 | -74,884 | **32.99L** (-26%) | **24.42L** (-33%) | **192.85L** (-44%) | **4.26L** (-47%) | 0.06L (-91%) | **31.88L** (-47%) | no (5/6, fall deeper) |
| linreg trend r2>0.7 5m [3R target, 1/day] BANKNIFTY | +35,728 | -97,754 | 10.84L (-36%) | 10.96L (-39%) | 81.65L (-36%) | **3.87L** (-56%) | 1.69L (-83%) | 12.52L (-56%) | no (1/6, fall deeper) |
| linreg trend r2>0.7 5m [3R target, 1/day] SENSEX | +134,936 | -87,982 | **40.80L** (-36%) | 13.95L (-37%) | 168.76L (-51%) | 0.04L (-89%) | 0.05L (-90%) | 0.04L (-89%) | no (1/6, fall deeper) |
| **Search result (chosen on 2023-24):** linreg trend r2>0.7 15m [trail 1.5R, 1/day] SENSEX (half size) | | | **44.69L** (-26%) | 17.40L (-29%) | **199.59L** (-28%) | **2.79L** (-56%) | 1.92L (-81%) | 17.42L (-56%) | no (3/6, fall deeper) |

### I. Keltner breakout: sizes, neighbours, random control

| Keltner 2.0, 3R target, stop 2 ATR, 3/day, Sensex | ₹2L 2023-24 | ₹2L 2025-26 | ₹2L full | ₹20k 2023-24 | ₹20k 2025-26 | ₹20k full | Verdict |
|---|---|---|---|---|---|---|---|
| full size | **51.20L** (-39%) | **31.46L** (-32%) | **241.26L** (-39%) | **3.31L** (-56%) | **2.36L** (-75%) | **41.54L** (-56%) | no (6/6, fall deeper) |
| half size | 29.04L (-27%) | **26.84L** (-32%) | **212.18L** (-27%) | **3.90L** (-50%) | **2.36L** (-79%) | **44.18L** (-50%) | no (5/6, fall deeper) |
| third size | **32.11L** (-28%) | **25.64L** (-32%) | **207.88L** (-28%) | **3.93L** (-50%) | **2.44L** (-76%) | **48.47L** (-50%) | no (6/6, fall deeper) |
| quarter size | **31.77L** (-28%) | **25.01L** (-32%) | **200.49L** (-29%) | **3.28L** (-46%) | **2.38L** (-77%) | **27.19L** (-46%) | no (6/6, fall deeper) |

| Group | Versions | Better in all six | Better in 5+ | Fall rule kept | Pass | Avg cells better (of 6) |
|---|---|---|---|---|---|---|
| Keltner on NIFTY, full size | 30 | 5 | 13 | 0 | 0 | 3.53 |
| Keltner on NIFTY, half size | 30 | 0 | 12 | 2 | 0 | 3.93 |
| Keltner on SENSEX, full size | 30 | 3 | 10 | 0 | 0 | 3.90 |
| Keltner on SENSEX, half size | 30 | 0 | 10 | 0 | 0 | 3.73 |
| Keltner on BANKNIFTY, full size | 30 | 0 | 0 | 0 | 0 | 1.87 |
| Keltner on BANKNIFTY, half size | 30 | 0 | 0 | 0 | 0 | 2.00 |
| Random entries on SENSEX (control), full size | 40 | 0 | 0 | 2 | 0 | 0.97 |
| Random entries on SENSEX (control), half size | 40 | 0 | 1 | 3 | 0 | 1.15 |

Keltner alone (1 lot, ₹ per lot), 30 versions per index:

| Index | + in 2023-24 | + in 2025-26 | + in both | Median 2023 | Median 2024 | Median 2025 | Median 2026 |
|---|---|---|---|---|---|---|---|
| NIFTY | 26 | 22 | 20 | +6,172 | +52,078 | +24,566 | +408 |
| SENSEX | 25 | 7 | 6 | +5,749 | +35,264 | +1,937 | -9,298 |

### J. Keltner with the whole account's risk lowered to keep the worst fall (chosen on 2023-24 only)

Today at other risk settings (shows how much the final money depends on the path):

| Risk per trade | ₹2L 2023-24 | ₹2L 2025-26 | ₹2L full | ₹20k 2023-24 | ₹20k 2025-26 | ₹20k full |
|---|---|---|---|---|---|---|
| 0.6% | 7.50L (-26%) | 7.44L (-27%) | 51.95L (-26%) | 1.93L (-68%) | 2.20L (-77%) | 7.16L (-68%) |
| 0.8% | 11.80L (-26%) | 8.37L (-27%) | 111.24L (-27%) | 1.95L (-68%) | 2.21L (-77%) | 8.06L (-68%) |
| 1.0% | 30.70L (-26%) | 21.27L (-28%) | 183.02L (-26%) | 1.94L (-66%) | 2.21L (-77%) | 19.96L (-66%) |
| 1.2% | 22.28L (-41%) | 34.43L (-28%) | 165.56L (-41%) | 1.92L (-66%) | 2.36L (-77%) | 29.06L (-66%) |
| 1.4% | 31.02L (-43%) | 55.82L (-32%) | 181.16L (-43%) | 1.93L (-66%) | 2.65L (-77%) | 49.54L (-66%) |

Search on 2023-24 (₹2L): 600 versions (60 Keltner versions x full/half size x risk 0.6-1.0%); better than today with the fall inside 2 points: **5**. Those, judged on everything else:

| Chosen on 2023-24 | Size | Risk | ₹2L 2023-24 | ₹2L 2025-26 | ₹2L full | ₹20k 2023-24 | ₹20k 2025-26 | ₹20k full | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| Keltner SENSEX k2.5 stop1.5 tp3R 3/d | full | 0.9% | **40.50L** (-24%) | 20.08L (-30%) | **227.22L** (-24%) | **2.89L** (-55%) | 2.04L (-64%) | **28.04L** (-55%) | no (4/6, fall deeper) |
| Keltner SENSEX k2.5 stop1.5 tp3R 1/d | full | 0.9% | **33.65L** (-24%) | 19.78L (-31%) | **208.18L** (-26%) | **2.40L** (-54%) | 1.37L (-72%) | 19.78L (-54%) | no (3/6, fall deeper) |
| Keltner NIFTY k1.5 stop2.0 tp4R 3/d | half | 1.0% | **32.64L** (-28%) | **32.42L** (-29%) | **186.15L** (-28%) | **2.54L** (-54%) | 1.28L (-79%) | **26.10L** (-54%) | no (5/6, fall deeper) |
| Keltner SENSEX k2.0 stop1.5 tp3R 3/d | half | 0.9% | **31.96L** (-22%) | 14.94L (-32%) | **212.32L** (-24%) | **3.19L** (-52%) | 0.05L (-93%) | **21.41L** (-52%) | no (4/6, fall deeper) |
| Keltner SENSEX k2.5 stop1.5 tp2R 3/d | full | 0.9% | **31.80L** (-24%) | 15.70L (-32%) | **206.73L** (-27%) | **2.10L** (-82%) | **3.03L** (-51%) | 17.30L (-82%) | no (4/6, fall deeper) |

With hindsight (every version x size x risk 0.7-0.9%, all six cells at once): pass **0 of 360**; better in all six with a deeper fall 2; fall rule kept 62.

### K. The three families with several versions that made money alone in both periods, added as a slot (chosen on 2023-24 only)

Average-price band 2.0 trend, Ichimoku cross + cloud, TTM squeeze release (15-min; 4 exits x 1 or 3 a day x 3 indices = 72 pieces) x full / half size x risk 0.6-1.0% = 720 versions. Better than today in 2023-24 at ₹2L with the fall inside 2 points: **13**. The 12 best of those, judged on the other five cells:

| Chosen on 2023-24 | Size | ₹2L 2023-24 | ₹2L 2025-26 | ₹2L full | ₹20k 2023-24 | ₹20k 2025-26 | ₹20k full | Verdict |
|---|---|---|---|---|---|---|---|---|
| NIFTY ttm squeeze release [3R target] 1/d | full | **40.11L** (-26%) | **38.07L** (-29%) | **200.97L** (-31%) | **2.28L** (-53%) | **2.22L** (-64%) | **22.58L** (-53%) | no (6/6, fall deeper) |
| NIFTY ttm squeeze release [3R target] 3/d | full | **38.68L** (-26%) | **24.27L** (-28%) | **195.21L** (-32%) | **2.26L** (-60%) | **2.33L** (-57%) | **24.28L** (-60%) | no (6/6, fall deeper) |
| NIFTY ttm squeeze release [signal exit] 3/d | half | **35.28L** (-27%) | **24.35L** (-28%) | **195.99L** (-28%) | **2.49L** (-61%) | 1.98L (-75%) | **20.68L** (-61%) | no (5/6) |
| NIFTY ttm squeeze release [signal exit] 1/d | half | **35.27L** (-26%) | **28.45L** (-28%) | **194.68L** (-28%) | **2.49L** (-55%) | 2.05L (-71%) | **20.38L** (-55%) | no (5/6) |
| SENSEX ttm squeeze release [3R target] 3/d | half | **34.52L** (-25%) | 19.98L (-28%) | **204.43L** (-25%) | **2.43L** (-53%) | **2.30L** (-79%) | **24.33L** (-53%) | no (5/6, fall deeper) |
| SENSEX ttm squeeze release [signal exit] 3/d | half | **33.52L** (-27%) | **21.83L** (-28%) | **195.67L** (-27%) | **2.44L** (-46%) | **2.48L** (-70%) | **21.18L** (-46%) | PASS (6/6) |
| SENSEX ttm squeeze release [3R target] 1/d | half | **33.36L** (-25%) | 19.98L (-28%) | 182.12L (-28%) | **2.31L** (-52%) | **2.27L** (-79%) | **21.75L** (-52%) | no (4/6, fall deeper) |
| SENSEX avg-price band 2.0 trend [3R target] 3/d | half | **32.98L** (-27%) | **22.35L** (-30%) | **199.31L** (-27%) | **2.85L** (-52%) | **2.40L** (-75%) | **28.87L** (-52%) | no (6/6, fall deeper) |
| SENSEX ttm squeeze release [3R target] 3/d | full | **32.96L** (-26%) | 15.63L (-32%) | **185.04L** (-29%) | **2.43L** (-54%) | **2.42L** (-75%) | **24.90L** (-54%) | no (5/6, fall deeper) |
| SENSEX avg-price band 2.0 trend [3R target] 1/d | half | **32.92L** (-27%) | **22.26L** (-30%) | **196.27L** (-27%) | **2.76L** (-51%) | **2.45L** (-75%) | **26.58L** (-51%) | no (6/6, fall deeper) |
| SENSEX ttm squeeze release [signal exit] 1/d | half | **32.19L** (-27%) | **23.26L** (-28%) | **191.91L** (-27%) | **2.32L** (-46%) | **2.45L** (-56%) | **21.83L** (-46%) | PASS (6/6) |
| SENSEX ttm squeeze release [3R target] 1/d | full | **31.38L** (-26%) | **24.90L** (-28%) | **194.94L** (-26%) | **2.32L** (-53%) | **2.40L** (-73%) | 15.63L (-53%) | no (5/6) |

### L. Squeeze release - deep check

Rule: on 15-min bars the Bollinger band (n bars, 2 sd) sits inside the Keltner channel (EMA n +/- 1.5 x ATR 10) = squeeze. When the band comes back out, buy a call if momentum (close minus the average of the n-bar mid-range and the n-bar mean) is up, a put if it is down, at the next bar's open. Stop 1.5 x ATR 14; exit when momentum changes sign; square-off 15:15. Entries 09:30-14:45.

Two versions: **n = 20** (the one the design-only search of part K found) and **n = 14** (the best of the neighbour grid on 2023-24).

| | n = 20, 3 a day | n = 14, 1 a day |
|---|---|---|
| Trades (Sensex, 2023 - Oct 2026) | 339 | 569 |
| Alone, 1 lot: 2023-24 / 2025-26 | +3,150 / +25,605 | +110,086 / +84,856 |
| Alone by year 2023 / 24 / 25 / 26 | +5,647 / -2,497 / +17,755 / +7,850 | +35,844 / +74,242 / +60,620 / +24,236 |
| Wins, average R | 35%, 0.16 | 38%, 0.26 |

Added to today's Autopilot at half size:

| Version | ₹2L 2023-24 | ₹2L 2025-26 | ₹2L full | ₹20k 2023-24 | ₹20k 2025-26 | ₹20k full | Verdict |
|---|---|---|---|---|---|---|---|
| Today | 30.70L (-26%) | 21.27L (-28%) | 183.02L (-26%) | 1.94L (-66%) | 2.21L (-77%) | 19.96L (-66%) | |
| Squeeze n = 20, 3 a day | **33.52L** (-27%) | **21.83L** (-28%) | **195.67L** (-27%) | **2.44L** (-46%) | **2.48L** (-70%) | **21.18L** (-46%) | PASS (6/6) |
| Squeeze n = 14, 1 a day | **45.77L** (-27%) | **29.11L** (-28%) | **210.15L** (-28%) | **3.08L** (-56%) | **2.68L** (-71%) | **28.61L** (-56%) | PASS (6/6) |

Execution stress (half size):

| Stress | Version | ₹2L 2023-24 | ₹2L 2025-26 | ₹2L full | ₹20k 2023-24 | ₹20k 2025-26 | ₹20k full | Verdict |
|---|---|---|---|---|---|---|---|---|
| enter one 15-min bar later | n = 20 | 27.18L (-29%) | 19.75L (-28%) | **183.31L** (-29%) | **2.08L** (-60%) | **2.31L** (-76%) | 9.98L (-60%) | no (3/6, fall deeper) |
| +0.5% worse fill each side | n = 20 | **31.29L** (-28%) | **21.88L** (-28%) | **189.34L** (-28%) | **2.26L** (-50%) | **2.38L** (-70%) | **21.07L** (-50%) | PASS (6/6) |
| +1% worse fill each side | n = 20 | 26.74L (-31%) | 19.94L (-28%) | 180.41L (-31%) | **2.28L** (-62%) | **2.34L** (-72%) | **21.76L** (-62%) | no (3/6, fall deeper) |
| one bar later and +0.5% | n = 20 | 26.91L (-29%) | 16.63L (-29%) | 179.49L (-29%) | **2.06L** (-64%) | **2.25L** (-75%) | 10.03L (-64%) | no (2/6, fall deeper) |
| enter one 15-min bar later | n = 14 | **35.72L** (-27%) | **29.00L** (-28%) | **197.39L** (-28%) | **2.69L** (-51%) | **2.48L** (-79%) | **25.02L** (-51%) | no (6/6, fall deeper) |
| +0.5% worse fill each side | n = 14 | **33.47L** (-27%) | **25.43L** (-28%) | **196.19L** (-28%) | **2.96L** (-58%) | **2.64L** (-62%) | **29.12L** (-58%) | no (6/6, fall deeper) |
| +1% worse fill each side | n = 14 | 25.53L (-27%) | **22.82L** (-28%) | 175.89L (-27%) | **2.78L** (-58%) | **2.47L** (-61%) | **25.31L** (-58%) | no (4/6) |
| one bar later and +0.5% | n = 14 | **33.57L** (-27%) | **24.97L** (-28%) | **191.78L** (-28%) | **2.62L** (-51%) | 1.88L (-79%) | **24.02L** (-51%) | no (5/6, fall deeper) |
| exit one bar later | n = 14 | **33.53L** (-38%) | **25.37L** (-28%) | **205.15L** (-38%) | **2.99L** (-56%) | **4.36L** (-64%) | **31.88L** (-56%) | no (6/6, fall deeper) |
| entry and exit one bar later | n = 14 | **36.38L** (-27%) | **28.76L** (-28%) | **201.69L** (-27%) | **2.82L** (-50%) | **2.44L** (-78%) | **28.25L** (-50%) | PASS (6/6) |
| entry one bar later, +0.5% fills, exit one bar later | n = 14 | 26.61L (-28%) | **24.13L** (-28%) | 181.54L (-30%) | **2.65L** (-51%) | 2.20L (-78%) | **27.18L** (-51%) | no (3/6, fall deeper) |

Each year alone (restart at the capital) and other starting capitals, n = 14:

| | Today | With squeeze | |
|---|---|---|---|
| 2023, ₹200,000 | 3.11L (-23%) | 4.22L (-17%) | better |
| 2023, ₹20,000 | 0.33L (-58%) | 0.55L (-56%) | better |
| 2024, ₹200,000 | 8.40L (-22%) | 12.42L (-28%) | better |
| 2024, ₹20,000 | 2.03L (-27%) | 2.21L (-27%) | better |
| 2025, ₹200,000 | 3.58L (-28%) | 4.43L (-25%) | better |
| 2025, ₹20,000 | 0.70L (-77%) | 1.18L (-71%) | better |
| 2026, ₹200,000 | 6.80L (-21%) | 6.39L (-24%) | **worse** |
| 2026, ₹20,000 | 1.20L (-42%) | 0.60L (-61%) | **worse** |
| ₹25,000, 2023-24 | 1.97L (-60%) | 3.31L (-51%) | better |
| ₹25,000, 2025-26 | 2.05L (-79%) | 3.54L (-71%) | better |
| ₹25,000, full | 21.30L (-60%) | 29.24L (-51%) | better |
| ₹50,000, 2023-24 | 3.95L (-24%) | 7.90L (-27%) | better, fall deeper |
| ₹50,000, 2025-26 | 2.88L (-55%) | 4.33L (-40%) | better |
| ₹50,000, full | 71.52L (-30%) | 117.34L (-29%) | better |
| ₹100,000, 2023-24 | 6.17L (-22%) | 15.53L (-21%) | better |
| ₹100,000, 2025-26 | 4.57L (-34%) | 10.19L (-29%) | better |
| ₹100,000, full | 91.25L (-30%) | 159.47L (-27%) | better |
| ₹500,000, 2023-24 | 66.00L (-31%) | 69.46L (-38%) | better, fall deeper |
| ₹500,000, 2025-26 | 107.71L (-28%) | 115.36L (-29%) | better |
| ₹500,000, full | 204.32L (-31%) | 223.93L (-38%) | better, fall deeper |
| ₹1,000,000, 2023-24 | 121.36L (-33%) | 127.91L (-37%) | better, fall deeper |
| ₹1,000,000, 2025-26 | 154.47L (-27%) | 169.63L (-27%) | better |
| ₹1,000,000, full | 243.48L (-33%) | 279.17L (-37%) | better, fall deeper |

One continuous run, money at each year end (n = 14):

| Start | | 2023 | 2024 | 2025 | Oct 2026 |
|---|---|---|---|---|---|
| ₹200,000 | today | 3.11L | 30.70L | 79.12L | 183.02L |
| ₹200,000 | with squeeze | 4.22L | 45.77L | 96.87L | 210.15L |
| ₹20,000 | today | 0.33L | 1.94L | 3.18L | 19.96L |
| ₹20,000 | with squeeze | 0.55L | 3.08L | 4.46L | 28.61L |
| ₹25,000 | today | 0.50L | 1.97L | 3.56L | 21.30L |
| ₹25,000 | with squeeze | 0.62L | 3.31L | 4.71L | 29.24L |

Neighbours (8 band settings x 4 exits x 1 or 3 a day, each at half and full size, added to today):

| Group | Versions | Pass | Better in all six | Better in 5+ | Fall rule kept | Alone + in both periods |
|---|---|---|---|---|---|---|
| Squeeze on SENSEX, half size | 60 | 4 | 6 | 22 | 27 | 35 |
| Squeeze on SENSEX, full size | 60 | 2 | 3 | 12 | 5 | 35 |
| Squeeze on NIFTY, half size | 56 | 3 | 7 | 17 | 17 | 34 |
| Squeeze on NIFTY, full size | 56 | 1 | 3 | 5 | 4 | 34 |
| Squeeze on BANKNIFTY, half size | 64 | 0 | 0 | 0 | 2 | 1 |
| Squeeze on BANKNIFTY, full size | 64 | 0 | 0 | 0 | 2 | 1 |
| Random entries, same exit and count as n = 20 (control), half size | 100 | 0 | 0 | 3 | | |
| Random entries, same exit and count as n = 14 (control), half size | 60 | 0 | 0 | 0 | | |

Forward selection on the grid: 27 of 360 versions beat today in 2023-24 at ₹2L inside the fall rule; of those **10 pass all six cells** and 22 are better in five or more. The n = 14 candidate beats 100% of its random controls in every cell; n = 20 beats 72-100%.

Block bootstrap (200 resampled histories, 20-day blocks, same resample for both):

| Version | Capital | Squeeze ahead | Final money ratio 10th / median / 90th pct | Fall deeper by > 2 pts | Fall shallower by > 2 pts |
|---|---|---|---|---|---|
| n = 20 | ₹200,000 | 78% | 0.90 / 1.18 / 2.02 | 25% | 22% |
| n = 20 | ₹20,000 | 62% | 0.43 / 1.08 / 4.23 | 37% | 31% |
| n = 14 | ₹200,000 | 84% | 0.92 / 1.30 / 3.26 | 26% | 26% |
| n = 14 | ₹20,000 | 74% | 0.72 / 1.35 / 5.52 | 39% | 34% |

Overlap with today's trades (n = 14, 569 trades): an existing trade is open on the same side during 85% of them (82% on Sensex itself), on the opposite side 13%, nothing else open 10%. Daily P&L correlation with today's rules 0.58. On today's worst 10% of days the squeeze slot lost another ₹72,051 per lot in total. Quarters with a profit (alone): 14 of 16.

Sizes and a Nifty twin (n = 14, exit on momentum turn, 1 a day):

| Version | ₹2L 2023-24 | ₹2L 2025-26 | ₹2L full | ₹20k 2023-24 | ₹20k 2025-26 | ₹20k full | Verdict |
|---|---|---|---|---|---|---|---|
| Sensex full size | 25.16L (-39%) | **38.41L** (-28%) | **195.96L** (-39%) | **2.50L** (-67%) | **4.88L** (-51%) | **30.61L** (-67%) | no (5/6, fall deeper) |
| Sensex half size (candidate) | **45.77L** (-27%) | **29.11L** (-28%) | **210.15L** (-28%) | **3.08L** (-56%) | **2.68L** (-71%) | **28.61L** (-56%) | PASS (6/6) |
| Sensex third size | 29.74L (-28%) | **28.87L** (-28%) | **190.86L** (-28%) | **3.30L** (-50%) | **2.24L** (-72%) | **25.40L** (-50%) | no (5/6, fall deeper) |
| Sensex quarter size | **30.94L** (-27%) | **28.68L** (-28%) | **190.09L** (-28%) | **2.45L** (-52%) | **2.22L** (-76%) | **26.24L** (-52%) | PASS (6/6) |
| Nifty twin, half size | 26.11L (-28%) | **25.84L** (-30%) | 170.69L (-28%) | **2.28L** (-48%) | 2.03L (-75%) | **21.11L** (-48%) | no (3/6, fall deeper) |
| Sensex + Nifty twin, both half | **50.42L** (-39%) | **43.96L** (-29%) | **214.59L** (-39%) | 0.04L (-81%) | **2.48L** (-65%) | 0.04L (-81%) | no (4/6, fall deeper) |
| Sensex + Nifty twin, both quarter | **37.09L** (-27%) | **22.67L** (-28%) | **193.69L** (-27%) | **2.36L** (-57%) | **2.66L** (-76%) | **27.51L** (-57%) | PASS (6/6) |


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
