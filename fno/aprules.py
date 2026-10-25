"""Today's Autopilot rules in one place, written from config.py and the live switches, so the page can never drift from the code.

Used by the "Autopilot rules" tab (/api/autopilot/rules) and by the Outcomes PDF.
"""
from __future__ import annotations

from . import config as C


def _flag(name: str) -> bool:
    try:
        from . import store as st
        return bool((st.get("change_flags") or {}).get(name, True))
    except Exception:
        return True


def _t(x) -> str:
    return x.strftime("%H:%M")


def _rs(x) -> str:
    return f"₹{x:,.0f}"


def _idx_rules() -> dict:
    out = {}
    for s in C.AUTOPILOT_SYMS:
        out[s] = C.AUTOPILOT_SYM_STRATEGIES.get(s, C.AUTOPILOT_STRATEGIES)
    return out


NAMES = {"noise": "Noise band", "camarilla": "Camarilla H4/L4", "trend": "Trend-day catcher", "fib": "Fibonacci 0.618"}
LABEL = {"NIFTY": "Nifty", "BANKNIFTY": "Bank Nifty", "SENSEX": "Sensex"}


def rulebook() -> dict:
    """Sections of {title, rows: [[name, value]]} plus the live switch states."""
    per = _idx_rules()
    which = {r: ", ".join(LABEL[s] for s, rr in per.items() if r in rr) for r in NAMES}
    sw = {k: _flag(k) for k in ("conviction", "big_run_skip", "dd_half", "small_otm", "ai_judge", "limits_system_only")}
    on = lambda k: "ON" if sw[k] else "OFF (switched off in Research → Changes)"
    sections = [
        {"title": "What Autopilot trades", "rows": [
            ["Indices", ", ".join(LABEL[s] for s in C.AUTOPILOT_SYMS)],
            *[[LABEL[s], " + ".join(NAMES[r] for r in rr)] for s, rr in per.items()],
            ["Instrument", f"Buys an index option (CALL for up, PUT for down). Strike {C.STRIKE_ITM} step in the money"
                           + (f"; 1 step out of the money while equity is below {_rs(C.SMALL_OTM_BELOW)}" if sw["small_otm"] else "")],
            ["Never sells options", "Only buys - the most you can lose on a trade is the premium paid; the stop normally exits well before that"],
        ]},
        {"title": "Entry rules", "rows": [
            ["Noise band (" + which["noise"] + ")",
             f"A 5-min close beyond open × (1 ± {C.NOISE_MULT}× the typical move-since-open at that time, last {C.NOISE_LOOKBACK} sessions). "
             f"Checked on the half hour {_t(C.FIRST_CHECK)}-{_t(C.LAST_ENTRY)} (:15 / :45). Signal valid {C.SIGNAL_VALID_MIN} min. VIX {C.VIX_MIN:g}-{C.NOISE_VIX_MAX:g}"],
            ["Camarilla H4/L4 (" + which["camarilla"] + ")",
             f"5-min close above yesterday's H4 (CALL) or below L4 (PUT), {_t(C.FIRST_ENTRY_ANY)}-{_t(C.CAM_LAST_ENTRY)}. VIX {C.VIX_MIN:g}-{C.CAM_VIX_MAX:g}"],
            ["Trend-day catcher (" + which["trend"] + ")",
             f"Half-hour checks {_t(C.TREND_FIRST_CHECK)}-{_t(C.TREND_LAST_CHECK)}: close beyond the noise band AND VWAP, price on that side of VWAP for "
             f"≥{C.TREND_VWAP_SHARE:.0%} of today's bars. Own slot. Only once equity ≥ {_rs(C.TREND_MIN_EQUITY)}. VIX ≥ {C.VIX_MIN:g}"],
            ["Fibonacci 0.618 (" + which["fib"] + ")",
             f"5-min close crossing yesterday's pivot ± 0.618 × range, {_t(C.FIRST_ENTRY_ANY)}-{_t(C.CAM_LAST_ENTRY)}. Own slot. Only once equity ≥ {_rs(C.FIB_MIN_EQUITY)}"],
            ["Slots", "One Noise/Camarilla position per index; trend-day and Fibonacci each have their own slot"],
        ]},
        {"title": "Stops, targets and exits", "rows": [
            ["Noise", f"Stop {C.STOP_ATR:g} × ATR(14, 5-min) on the index. Target {C.RR_NOISE:g}R. Trails on the band / VWAP. No breakeven move"],
            ["Camarilla", f"Stop at H3/L3, kept between {C.CAM_RMIN:g} and {C.CAM_RMAX:g} ATR. Stop to entry at +{C.CAM_BE_R:g}R. "
                          f"Exit after {C.CAM_TIME_STOP} min if +0.5R not reached. No target"],
            ["Trend-day", f"Stop = distance to VWAP + 0.5 ATR (1-3 ATR). Stop to entry at +{C.TREND_BE_R:g}R. No target"],
            ["Fibonacci", f"Stop at the 0.382 level (1-3 ATR). Stop to entry at +1R. {C.CAM_TIME_STOP}-min time stop. No target"],
            ["Square-off", f"Every open trade closes at {_t(C.SQUARE_OFF)}. Nothing is held overnight"],
            ["Partial booking", "None - the whole position exits together (rounds 40-41: T1/T2/T3 booking earned less)"],
        ]},
        {"title": "Days and conditions it sits out", "rows": [
            ["India VIX", f"No trades below {C.VIX_MIN:g}. Noise needs VIX < {C.NOISE_VIX_MAX:g}; Camarilla ≤ {C.CAM_VIX_MAX:g}"],
            ["Union Budget day", "No new trades"],
            ["NSE holidays", f"Closed (list covers {C.HOLIDAYS_YEAR})"],
            ["Big 3-day run", f"{on('big_run_skip')}: no CALL after a 3-session fall, no PUT after a 3-session rally, of ≥ {C.BIG_RUN_ATR:g}× the daily ATR"],
        ]},
        {"title": "Position size", "rows": [
            ["Risk per trade", f"{C.AUTOPILOT_RISK_PCT:g}% of CURRENT equity (compounds as the account grows) ÷ the option's risk per lot"],
            ["Confidence", f"{on('conviction')}: Nifty & Sensex risk {C.CONVICTION_HIGH}× when 5-min/15-min/1-hour charts agree (score ≥ {C.CONVICTION_MIN_SCORE}), "
                           f"{C.CONVICTION_LOW}× otherwise. Bank Nifty 1×. 'Strong' (2×/1×/0.5×) is opt-in in Settings"],
            ["AI judge", f"{on('ai_judge')}: Nifty only, 1.33× / 0.67× on top of confidence (total 0.5×-1.6×). Not in the backtest"],
            ["Drawdown brake", f"{on('dd_half')}: half the risk while equity is more than {C.DD_HALF_AT:.0%} below its peak"],
            ["1-lot floor", f"If 1% buys less than a lot, 1 lot is still taken while one lot risks ≤ {C.AUTOPILOT_MAX_TRADE_RISK:.0%} of equity; otherwise skipped"],
            ["Cash check", "Skipped if the account can't pay the premium"],
            ["Big orders", "Split into exchange freeze-size pieces (Nifty 3,510 qty, Bank Nifty 1,440, Sensex 1,000). The portfolio backtest also caps at 50 lots by default"],
        ]},
        {"title": "Loss limits", "rows": [
            ["Weekly", f"No new trades for the rest of the week after −{C.MAX_WEEKLY_LOSS:.0%} of equity (−{C.AUTOPILOT_WEEKLY_SMALL:.0%} below {_rs(C.AUTOPILOT_SMALL_ACCOUNT)}). Your choice in Settings can override"],
            ["Daily", f"Two losers, −{C.MAX_DAILY_LOSS:.0%} or {C.MAX_TRADES_PER_DAY} trades: advisory on the demo account unless 'Enforce daily limits' is on"],
            ["Whose trades count", f"{on('limits_system_only')}: only the system's own trades count toward the limits"],
            ["Per-trade loss cap", "None (round 44: a ₹2,200-per-lot cap cut profit; it is a backtest option only)"],
        ]},
    ]
    return {"sections": sections, "switches": sw,
            "note": "Written live from the app's settings file and your switches - if a rule changes in the code, this page changes with it."}
