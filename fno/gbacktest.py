"""Backtest for the crypto / US / gold books, in money, the way the paper account trades (28 Oct 2026).

One coin, or every coin in the book at once ("ALL"). Each coin's trades come from the app's own simulator
(fno/custom.simulate: signal on the closed bar, entry next bar open, costs in R). Then one account replays them in time
order with the live sizing rules of fno/gbook.open_position:
  - risk per trade = risk % of the account (compounding: the account grows and shrinks with results),
  - position value capped at account x leverage / max open positions x 1.5,
  - the margin must fit in the free cash, and no more than `max_open` positions at once (later signals are skipped).
A trade's money result = its R (after fees and slippage) x the money actually risked.
Not modelled: the learning engine's vetoes / half-size, the weekly rule check and per-coin pausing, manual trades.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import custom as CU
from . import gbook as GB
from . import markets as MK
from . import sessions as SS

# history limits (calendar days) so one request stays a few seconds: fast bars need many more candles per day
MAX_DAYS = {"single": {"fast": 365, "mid": 1460, "slow": 1500}, "all": {"fast": 120, "mid": 365, "slow": 1460}}


def speed(tf: str) -> str:
    m = MK.MINUTES[tf]
    return "fast" if m < 15 else "mid" if m < 60 else "slow"


def symbols_for(market: str, symbol: str) -> list[str]:
    if symbol in ("ALL", "", None):
        return MK.catalog_ids(market)
    if not MK.valid_symbol(market, symbol):
        raise ValueError(f"{symbol} is not in the {market} list.")
    return [symbol]


def money_sim(trades: list[dict], capital: float, risk_pct: float, leverage: float, max_open: int) -> dict:
    """Replay trades (all coins) through one account. trades need entry_ts, exit_ts, entry, sl0, R, symbol."""
    order = sorted(range(len(trades)), key=lambda i: trades[i]["entry_ts"])
    eq = float(capital)
    open_: list[tuple] = []              # (exit_ts, margin, pnl, idx)
    taken, skipped_full, skipped_cash = [], 0, 0
    max_conc = 0
    curve = [(None, eq)]

    def settle(until):
        nonlocal eq
        open_.sort(key=lambda x: x[0])
        while open_ and (until is None or open_[0][0] <= until):
            ts, _, pnl, i = open_.pop(0)
            eq += pnl
            curve.append((ts, eq))
            taken.append((i, pnl))

    for i in order:
        t = trades[i]
        settle(t["entry_ts"])
        if len(open_) >= max_open:
            skipped_full += 1
            continue
        stop_dist = abs(t["entry"] - t["sl0"])
        if eq <= 0 or stop_dist <= 0:
            continue
        risk_amt = eq * risk_pct / 100
        notional = risk_amt / stop_dist * t["entry"]
        notional = min(notional, eq * leverage / max(max_open, 1) * 1.5)
        margin = notional / leverage
        free = eq - sum(m for _, m, _, _ in open_)
        if margin > free:
            skipped_cash += 1
            continue
        risked = notional * stop_dist / t["entry"]
        pnl = t["R"] * risked
        open_.append((t["exit_ts"], margin, pnl, i))
        max_conc = max(max_conc, len(open_))
    settle(None)

    vals = np.array([v for _, v in curve], dtype=float)
    peak = np.maximum.accumulate(vals)
    dd = float(((vals - peak) / peak).min() * 100) if len(vals) else 0.0
    pnl_by = {}
    for i, p in taken:
        pnl_by.setdefault(trades[i]["symbol"], []).append(p)
    pnls = np.array([p for _, p in taken], dtype=float)
    w, lo = pnls[pnls > 0], pnls[pnls <= 0]
    months = {}
    for (ts, v), (_, prev) in zip(curve[1:], curve[:-1]):
        months.setdefault(ts.strftime("%Y-%m"), 0.0)
        months[ts.strftime("%Y-%m")] += v - prev
    return {"capital": round(capital, 2), "final": round(eq, 2), "net": round(eq - capital, 2), "net_pct": round((eq / capital - 1) * 100, 1),
            "max_dd_pct": round(dd, 1), "trades": len(taken), "skipped_full": skipped_full, "skipped_cash": skipped_cash, "max_open_seen": max_conc,
            "win_rate": round(float(len(w) / len(pnls) * 100), 1) if len(pnls) else None,
            "profit_factor": round(float(w.sum() / -lo.sum()), 2) if lo.sum() < 0 else None,
            "best": round(float(pnls.max()), 2) if len(pnls) else None, "worst": round(float(pnls.min()), 2) if len(pnls) else None,
            "best_share_pct": round(float(pnls.max() / (eq - capital) * 100), 0) if len(pnls) and eq > capital and pnls.max() > 0 else None,
            "top5_share_pct": round(float(np.sort(pnls)[-5:].sum() / (eq - capital) * 100), 0) if len(pnls) >= 5 and eq > capital else None,
            "by_symbol": {k: round(float(sum(v)), 2) for k, v in pnl_by.items()},
            "by_month": [{"m": k, "pnl": round(v, 2)} for k, v in sorted(months.items())][-36:],
            "pnl_by_trade": {i: round(p, 2) for i, p in taken},
            "curve": [{"t": ts.isoformat(), "v": round(v, 2)} for ts, v in curve[1:]][-500:]}


STEADY_R = 0.05          # "steady" = at least +0.05R a trade in both the earlier 70% and the latest 30% (after costs)
SESSIONS = ["all", "asia", "london", "newyork", "overlap", "late"]
SESSION_NAMES = {"all": "All day (24 h)", **SS.NAMES}


def _with_session(spec: dict, session: str) -> dict:
    sp = copy.deepcopy(spec)
    sp["filters"]["session"] = None if session in (None, "", "all") else session
    return sp


def _simulate(spec: dict, data: dict) -> tuple[list, dict]:
    trades, per = [], {}
    for s, bars in data.items():
        tr = CU.simulate(spec, bars, None)
        for t in tr:
            t["symbol"] = s
        trades += tr
        per[s] = CU.stats_R([t["R"] for t in tr])
    trades.sort(key=lambda t: t["entry_ts"])
    return trades, per


def session_profile(market: str, syms: list[str], days: int = 90) -> dict:
    """How much each session moves and trades, from 1-hour candles of the tested symbols (last `days` days).
    move = average hourly high-low range in %; volume = volume per hour in that session vs the 24-hour average."""
    rows = {k: {"move": [], "vol": []} for k in SESSIONS[1:]}
    used = 0
    for s in syms:
        try:
            df = MK.candles(market, s, "1h", days, ttl=1800)
        except Exception:
            continue
        if len(df) < 200:
            continue
        used += 1
        idx = df.index.tz_convert("UTC")
        rng = ((df["high"] - df["low"]) / df["open"] * 100).values
        vol = df["volume"].values
        avg_v = float(np.nanmean(vol)) or 1.0
        for k in rows:
            mk = SS.mask(idx, k)
            if mk.sum() < 20:
                continue
            rows[k]["move"].append(float(np.nanmean(rng[mk])))
            rows[k]["vol"].append(float(np.nanmean(vol[mk])) / avg_v)
    day_move = [float(np.nanmean(v["move"])) for v in rows.values() if v["move"]]
    base = float(np.mean(day_move)) if day_move else None
    wins = {w["id"]: w for w in SS.windows_ist(datetime.now(timezone.utc).astimezone(SS.IST).date())}
    out = []
    for k, v in rows.items():
        out.append({"id": k, "name": SESSION_NAMES[k], "ist": f"{wins[k]['start_ist']}-{wins[k]['end_ist']}" if k in wins else "",
                    "move_pct": round(float(np.mean(v["move"])), 3) if v["move"] else None,
                    "volume_x": round(float(np.mean(v["vol"])), 2) if v["vol"] else None})
    return {"days": days, "symbols": used, "rows": out}


def run(raw: dict, market: str, symbol: str, days: int, capital: float, risk_pct: float | None = None,
        leverage: float | None = None, max_open: int | None = None, busy: bool = False, session: str = "all") -> dict:
    """session: "all", one of SESSIONS (only signals from bars that start in that session), or "compare"
    (every session run separately; the main result is the 24-hour one)."""
    spec = CU.validate(raw, existing_id=raw.get("id") if raw.get("id") else None)
    if spec["market"] != market:
        raise ValueError(f"This strategy is for {spec['market']}, not {market}.")
    session = (session or "all").lower()
    if session not in SESSIONS + ["compare"]:
        raise ValueError("Session must be all, asia, london, newyork, overlap, late or compare.")
    pf = GB.prefs(market)
    risk_pct = float(risk_pct or pf["risk_pct"])
    leverage = float(leverage or pf["max_leverage"])
    max_open = int(max_open or pf["max_open"])
    if not 0.1 <= risk_pct <= 5:
        raise ValueError("Risk per trade must be between 0.1% and 5%.")
    if not 1 <= leverage <= GB.MAX_LEVERAGE[market]:
        raise ValueError(f"Leverage must be between 1 and {GB.MAX_LEVERAGE[market]:g}x.")
    if not 1 <= max_open <= 10:
        raise ValueError("Max open positions must be between 1 and 10.")
    if not 50 <= capital <= 1e9:
        raise ValueError("Starting amount must be between 50 and 1,000,000,000.")
    daily = MK.MINUTES[spec["timeframe"]] >= 1440
    if daily and session not in ("all", "compare"):
        raise ValueError("This rule uses daily bars: a day covers every session, so it can't be limited to one. Pick 'All day' or 'Compare sessions'.")
    syms = symbols_for(market, symbol)
    notes = []
    sp = speed(spec["timeframe"])
    cap_days = MAX_DAYS["all" if len(syms) > 1 else "single"][sp]
    if busy and sp != "slow":
        cap_days = min(cap_days, 250)
    if days > cap_days:
        notes.append(f"History shortened from {days} to {cap_days} days for {spec['timeframe']} bars"
                     + (" on all coins" if len(syms) > 1 else "") + (" while the Indian market is open" if busy and sp != "slow" else "") + ".")
        days = cap_days

    data, errors, n_bars, dates, t_from, t_to = {}, {}, 0, set(), None, None
    for s in syms:
        try:
            df = MK.candles(market, s, spec["timeframe"], int(days), ttl=1800)
            bars = CU.resample(df, spec["timeframe"], market)
            if len(bars) < 80:
                errors[s] = f"only {len(bars)} bars of history"
                continue
        except Exception as e:                       # one coin's data failing must not sink the whole test
            errors[s] = str(e)[:120]
            continue
        data[s] = bars
        n_bars += len(bars)
        dates |= set(df.index.date)
        t_from = min(t_from, df.index[0]) if t_from is not None else df.index[0]
        t_to = max(t_to, df.index[-1]) if t_to is not None else df.index[-1]
    if not data:
        raise ValueError("No price history could be loaded: " + "; ".join(f"{k}: {v}" for k, v in errors.items()))

    main = "all" if session == "compare" else session
    trades, per = _simulate(_with_session(spec, main), data)
    label = "all coins" if len(syms) > 1 and market == "CRYPTO" else "all symbols" if len(syms) > 1 else syms[0]
    res = CU.summarize(spec, trades, label, n_bars, len(dates), str(t_from.date()), str(t_to.date()))
    m = money_sim(trades, float(capital), risk_pct, leverage, max_open)
    pbt = m.pop("pnl_by_trade")
    first = len(trades) - len(res["trades"])
    bar = pd.Timedelta(minutes=MK.MINUTES[spec["timeframe"]])
    sig = lambda t: SS.label(t["entry_ts"] - bar)        # session of the bar that gave the signal (entry = next bar's open)
    for k, row in enumerate(res["trades"]):
        row["pnl"] = pbt.get(first + k)          # None = skipped (account full or not enough free cash)
        row["session"] = sig(trades[first + k])
    res["by_symbol"] = [{"symbol": s, **st, "pnl": m["by_symbol"].get(s, 0.0)} for s, st in sorted(per.items(), key=lambda x: -(x[1].get("total_R") or 0))]
    res["money"] = {**m, "currency": MK.CURRENCY[market], "risk_pct": risk_pct, "leverage": leverage, "max_open": max_open}
    res["session"] = {"id": main, "name": SESSION_NAMES[main]}
    # result by the session the signal came from (inside this run), for every run
    by_s = {}
    for t in trades:
        by_s.setdefault(sig(t), []).append(t["R"])
    res["by_session_R"] = {SESSION_NAMES.get(k, k): CU.stats_R(v) for k, v in by_s.items()}

    if session == "compare":
        rows = []
        for k in SESSIONS:
            if daily and k != "all":
                continue
            tr_k, _ = (trades, per) if k == "all" else _simulate(_with_session(spec, k), data)
            rk = [t["R"] for t in tr_k]
            st = CU.stats_R(rk)
            cut = int(len(rk) * 0.7)
            early, late = CU.stats_R(rk[:cut]), CU.stats_R(rk[cut:])
            mk = money_sim(tr_k, float(capital), risk_pct, leverage, max_open) if tr_k else None
            if mk:
                mk.pop("pnl_by_trade")
            rows.append({"id": k, "name": SESSION_NAMES[k], **{f: st.get(f) for f in ("n", "win_rate", "avg_R", "profit_factor", "total_R", "max_dd_R")},
                         "net": mk["net"] if mk else 0.0, "net_pct": mk["net_pct"] if mk else 0.0, "max_dd_pct": mk["max_dd_pct"] if mk else 0.0,
                         "taken": mk["trades"] if mk else 0, "avg_R_early": early.get("avg_R"), "avg_R_late": late.get("avg_R"),
                         "n_late": late.get("n", 0), "steady": bool(early.get("avg_R") is not None and late.get("avg_R") is not None
                                                                    and early["avg_R"] >= STEADY_R and late["avg_R"] >= STEADY_R), "curve": [x["v"] for x in mk["curve"]][-200:] if mk else []})
        res["sessions"] = rows
        if daily:
            notes.append("Daily-bar rule: each daily bar covers all sessions, so only the 24-hour result is shown; the session profile below still applies.")
        elif MK.MINUTES[spec["timeframe"]] >= 240:
            notes.append(f"{spec['timeframe']} bars start at 00, 04, 08, 12, 16 and 20 UTC, so a session only gets the bars that start inside it "
                         "(e.g. late hours get none in winter). For a sharper split test a 1h or 15m strategy.")
        try:
            res["profile"] = session_profile(market, list(data))
        except Exception as e:
            res["profile"] = {"error": str(e)[:120]}
    res["errors"] = errors
    res["notes"] = notes + ([f"{len(errors)} symbol(s) skipped: " + ", ".join(errors)] if errors else [])
    res["symbols"] = syms
    res["assumptions"] = res["assumptions"] + [
        f"Money: {risk_pct:g}% of the account risked per trade, compounding; position capped at account x {leverage:g}x / {max_open} x 1.5; "
        f"at most {max_open} open at once (later signals skipped), as the paper account does.",
        "Sessions are judged on the bar that gave the signal (its start time), in each city's own clock, so daylight-saving changes are included.",
        "Not included: the learning engine's skips and half-size trades, and the weekly check that pauses a rule or a coin."]
    return res
