# Round 71: sizing tilts (CPR width, dealer gamma, OI wall), 1 Nov 2026

**Question (user):** "Is there any combination that needs to be tried out, or anything left to backtest? Please do the needful."

## Why sizing instead of more rules
Rounds 59 to 70 tested about 30 new entry ideas: OB/FVG, chart patterns, levels, scalping, OI and round numbers. A few had a small standalone edge. Every one of them lowered Autopilot when added, because its trades took cash from Autopilot's bigger trend trades. So the untested combination was the opposite approach: keep Autopilot's trades as they are and size each one up or down using the readings the research had found useful.

## What was tested
Same replay as the Outcomes tab: today's rules, compounding 1%, conviction, big-run skip, drawdown brake, gates, 1 OTM below Rs 60k. Capital Rs 2L and Rs 20k.

| Tilt | Reading | Up / down |
|---|---|---|
| CPR | Today's CPR width from yesterday's high / low / close. Narrow < 0.25%, wide > 0.5% | narrow up, wide down |
| Gamma | Dealer gamma from the previous day's option OI (Nifty, Bank Nifty), nearest 2 expiries, Black-Scholes gamma at India VIX | short gamma up, long gamma down |
| OI wall | Trade goes through yesterday's OI wall, or runs into it within 0.3% | through up, into down |
| Combined | Sum of the three | 1 + 0.15 × score |

Strengths tested: ×1.3 / ×0.7 and ×1.5 / ×0.5, then a grid for CPR. Control: the same multipliers shuffled across the same trades.

## Result
- **CPR width works.**
  - Per lot, narrow-CPR days earn more every year: +Rs 96 / 682 / 370 / 489 (2023 to 2026).
  - Middle and wide days lose. t-test p = 0.0004.
  - A whole zone of settings beats today's Autopilot in every period (up 1.2 to 1.3, middle 0.7 to 0.85).
- **Gamma, OI-wall and combined tilts** were not better in every period. Not used.

**Chosen setting:** narrow ×1.2, middle ×0.85, wide days skipped.

### Rs 2L account (app replay)

| Period | Today's Autopilot | With CPR sizing |
|---|---|---|
| 2023 | 2.61L | 2.88L |
| 2024 | 4.63L | 6.15L |
| 2025 | 3.56L | 3.85L |
| 2026 (to 23 Oct) | 4.69L | 5.22L |
| Jan 2023 to Oct 2026 | 33.36L | 44.18L |
| Worst fall (full) | −28.6% | −31.3% |

### Rs 20k account (app replay)
- Full period: 6.54L vs 5.06L.
- Worse in 2024 (1.61L vs 1.73L) and about equal in 2026 (1.01L vs 1.02L).
- Small accounts often fall back to 1 lot, so sizing changes little there.

## Decision
Adopted on 1 Nov 2026 as a switch: Research → Recent changes → "CPR-width sizing (round 71)". It applies to Autopilot entries on Nifty, Bank Nifty and Sensex:
- **Narrow CPR:** risk × 1.2.
- **Middle CPR:** risk × 0.85.
- **Wide CPR:** no entry, logged as "Skipped (round 71): wide CPR today".

It is applied after conviction and the AI judge, and before the drawdown brake. It is also part of the Outcomes and portfolio-backtest baseline (`cpr_size`), with a "CPR-width sizing OFF" scenario added.

Code:
- `fno/strategy.py`: `cpr_width`, `cpr_tilt`
- `fno/service.py`: `_cpr_size`
- `fno/portfolio_bt.py`: `cpr_widths` and `replay(cpr=...)`
- `fno/config.py`: `CPR_*`
- `tests/test_r71.py`

Scripts: `research/round71_sizing_tilts.py` and `research/round71_results.json`.

## What is left
- **OI-wall filter (p-oi-wall):** waiting for Dec 2026 / Mar 2027 data.
- **Basket trade:** waiting for the user's details.
- Nothing else from the user's list is untested.
