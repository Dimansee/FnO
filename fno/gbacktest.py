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

import numpy as np
import pandas as pd

from . import custom as CU
from . import gbook as GB
from . import markets as MK

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


def run(raw: dict, market: str, symbol: str, days: int, capital: float, risk_pct: float | None = None,
        leverage: float | None = None, max_open: int | None = None, busy: bool = False) -> dict:
    spec = CU.validate(raw, existing_id=raw.get("id") if raw.get("id") else None)
    if spec["market"] != market:
        raise ValueError(f"This strategy is for {spec['market']}, not {market}.")
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

    trades, errors, per, n_bars, dates, t_from, t_to = [], {}, {}, 0, set(), None, None
    for s in syms:
        try:
            df = MK.candles(market, s, spec["timeframe"], int(days), ttl=1800)
            bars = CU.resample(df, spec["timeframe"], market)
            if len(bars) < 80:
                errors[s] = f"only {len(bars)} bars of history"
                continue
            tr = CU.simulate(spec, bars, None)
        except Exception as e:                       # one coin's data failing must not sink the whole test
            errors[s] = str(e)[:120]
            continue
        for t in tr:
            t["symbol"] = s
        trades += tr
        per[s] = CU.stats_R([t["R"] for t in tr])
        n_bars += len(bars)
        dates |= set(df.index.date)
        t_from = min(t_from, df.index[0]) if t_from is not None else df.index[0]
        t_to = max(t_to, df.index[-1]) if t_to is not None else df.index[-1]
    if not per:
        raise ValueError("No price history could be loaded: " + "; ".join(f"{k}: {v}" for k, v in errors.items()))
    trades.sort(key=lambda t: t["entry_ts"])
    label = "all coins" if len(syms) > 1 and market == "CRYPTO" else "all symbols" if len(syms) > 1 else syms[0]
    res = CU.summarize(spec, trades, label, n_bars, len(dates), str(t_from.date()), str(t_to.date()))
    m = money_sim(trades, float(capital), risk_pct, leverage, max_open)
    pbt = m.pop("pnl_by_trade")
    first = len(trades) - len(res["trades"])
    for k, row in enumerate(res["trades"]):
        row["pnl"] = pbt.get(first + k)          # None = skipped (account full or not enough free cash)
    res["by_symbol"] = [{"symbol": s, **st, "pnl": m["by_symbol"].get(s, 0.0)} for s, st in sorted(per.items(), key=lambda x: -(x[1].get("total_R") or 0))]
    res["money"] = {**m, "currency": MK.CURRENCY[market], "risk_pct": risk_pct, "leverage": leverage, "max_open": max_open}
    res["errors"] = errors
    res["notes"] = notes + ([f"{len(errors)} symbol(s) skipped: " + ", ".join(errors)] if errors else [])
    res["symbols"] = syms
    res["assumptions"] = res["assumptions"] + [
        f"Money: {risk_pct:g}% of the account risked per trade, compounding; position capped at account x {leverage:g}x / {max_open} x 1.5; "
        f"at most {max_open} open at once (later signals skipped), as the paper account does.",
        "Not included: the learning engine's skips and half-size trades, and the weekly check that pauses a rule or a coin."]
    return res
