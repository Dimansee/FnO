"""Portfolio backtest (Backtest tab): Nifty + Bank Nifty + Sensex together from one capital, with the choices researched in
rounds 40-42 - compounding or fixed lots, confidence sizing, the big-run skip, and exit / target variants side by side.

How it works
  1. Trades: the app's own backtester (fno.backtest.run, strategy "autopilot" = the rules Autopilot trades on each index),
     one lot, on 5-minute history from Upstox's public API, under the chosen exit profile (fno.backtest.EXIT).
  2. Portfolio replay in time order across the indices (the same replay as research round 42):
       * lots = account x risk% x confidence / risk per lot (1-lot floor only while one lot risks <= 15% of the account),
         or a fixed number of lots; cut to what the cash can pay beside the trades already open
       * Autopilot gates: trend-day slot from Rs 40k, Fibonacci from Rs 2 lakh; weekly loss limit 4% (15% below Rs 1 lakh)
       * size costs: extra slippage per fill = 6% x sqrt(quantity / typical 5-minute volume near the money), at most 10% of
         that volume per trade, brokerage per order with orders split at the exchange freeze quantity
  3. Output: account curve, years, months, per index / strategy / exit reason, every trade; plus the current exits on the
     same settings when another exit is chosen, for a side-by-side.
Runs in a background thread (a full 3-index, multi-year run takes a few minutes on the server); poll job(id).
"""
from __future__ import annotations

import math
import secrets
import threading
import time
import traceback
from datetime import date, timedelta

import pandas as pd

from . import backtest as B
from . import config as C
from . import indicators as I
from . import market as D
from . import strategy as S

SYMS = ("NIFTY", "BANKNIFTY", "SENSEX")
VOL5 = {"NIFTY": 1_565_850, "BANKNIFTY": 17_588, "SENSEX": 432_875}      # typical 5-min option volume near the money (qty, Oct 2026)
FREEZE = {"NIFTY": 3510, "BANKNIFTY": 1440, "SENSEX": 1000}               # exchange freeze quantity per order (NSE from 5 Oct 2026; BSE assumed)
IMPACT_K = 0.06
PART = 0.10
EARLIEST = date(2022, 1, 3)

T3 = 1 / 3
EXITS = {
    "current": {"label": "Current exits (the rules)", "ov": {}},
    "t123": {"label": "Book a third at T1 1R / T2 2R / T3 3R", "ov": {"partials": [(1, T3), (2, T3), (3, T3)]}},
    "t123_253050": {"label": "Book 20% / 30% / 50% at 1R / 2R / 3R", "ov": {"partials": [(1, 0.2), (2, 0.3), (3, 0.5)]}},
    "t2_t4": {"label": "A third at 2R, a third at 4R, rest runs", "ov": {"partials": [(2, T3), (4, T3)]}},
    "half_2r": {"label": "Half at 2R, half runs", "ov": {"partials": [(2, 0.5)]}},
    "t123_ema9": {"label": "20/30/50% at 1R/2R/3R + exit rest on 5-min close across 9-EMA", "ov": {"partials": [(1, 0.2), (2, 0.3), (3, 0.5)], "rev_exit": "ema9"}},
    "t123_vwap": {"label": "20/30/50% at 1R/2R/3R + exit rest on 5-min close across VWAP", "ov": {"partials": [(1, 0.2), (2, 0.3), (3, 0.5)], "rev_exit": "vwap"}},
    "ema9_2r": {"label": "No targets; exit on 9-EMA cross once +2R", "ov": {"rev_exit": "ema9", "rev_after_R": 2.0}},
    "candle_2r": {"label": "No targets; exit on a strong opposite candle once +2R", "ov": {"rev_exit": "candle", "rev_after_R": 2.0}},
    "lock_2r": {"label": "Lock +1R once +2R", "ov": {"lock_steps": [(2.0, 1.0)]}},
    "trail_25": {"label": "Trail 1.5R behind the best move once +2.5R", "ov": {"giveback": (2.5, 1.5)}},
    "trail_2": {"label": "Trail 1R behind the best move once +2R", "ov": {"giveback": (2.0, 1.0)}},
    "be_025": {"label": "Breakeven stop at +0.25R instead of entry", "ov": {"be_lock": 0.25}},
    "noise_t2": {"label": "Noise target 2R (instead of 4R)", "ov": {"noise_rr": 2.0}},
    "noise_be": {"label": "Noise: stop to entry at +1R", "ov": {"noise_be_r": 1.0}},
}
CONF = {"none": "No confidence sizing", "standard": "Standard: 1.33x / 0.67x on Nifty and Sensex (live rule)",
        "strong": "Strong: 2x / 1x / 0.5x on all indices"}

_jobs: dict = {}
_lock = threading.Lock()          # one backtest at a time: fno.backtest.EXIT is module-wide


def options() -> dict:
    return {"exits": {k: v["label"] for k, v in EXITS.items()}, "confidence": CONF, "earliest": EARLIEST.isoformat(),
            "indices": list(SYMS)}


# ------------------------------------------------------------------ data + trades
def _history(sym: str, start: date):
    cal = (C.today_ist() - start).days + 45
    meta = D.instrument(sym)
    key = meta.get("upstox_key") or meta.get("upstox")
    c = D.cached(f"pbt:hist:{sym}:{cal}", 6 * 3600, lambda: D.upstox_public_history(key, cal))
    v = D.cached(f"pbt:hist:VIX:{cal}", 6 * 3600, lambda: D.upstox_public_history(C.INDIA_VIX_UPSTOX, cal))
    if c is None or c.empty:
        raise RuntimeError(f"No 5-minute history for {sym} from Upstox right now.")
    return c, (v["close"] if v is not None and len(v) else None)


def _trades(sym, candles, vix, otm, ov, big_run):
    B.EXIT.clear()
    B.EXIT.update(ov)
    B.EXIT["big_run_skip"] = bool(big_run)
    try:
        r = B.run(sym, capital=1e9, candles=candles, vix_intraday=vix, lot=D.lot_size(sym), sizing="one_lot",
                  strategy="autopilot", otm=otm, live_rules=False)
    finally:
        B.EXIT.clear()
    T = pd.DataFrame(r.get("trades") if not isinstance(r.get("trades"), pd.DataFrame) else r["trades"])
    if T.empty:
        return T
    T["sym"] = sym
    T["lot"] = D.lot_size(sym)
    return T


def _scores(T, candles):
    """The 0-6 multi-timeframe confidence score at each entry (round 33)."""
    c5 = candles[["open", "high", "low", "close"]].copy()
    if getattr(c5.index, "tz", None) is not None:
        c5.index = c5.index.tz_localize(None)
    mtf = S.mtf_frames(c5)
    ends = pd.to_datetime(T.date + " " + T.entry_time)
    return [S.mtf_score_at(mtf, e, 1 if sd == "CALL" else -1) for e, sd in zip(ends, T.side)]


# ------------------------------------------------------------------ replay
def _mult(conf, sym, score):
    if conf == "standard":
        return S.conviction_mult(score) if sym in C.CONVICTION_SYMS else 1.0
    if conf == "strong":
        return next(m for lo, m in C.STRONG_CONF if score >= lo)
    return 1.0


def replay(T: pd.DataFrame, capital: float, sizing: str, risk_pct: float, lots_fixed: int, conf: str, gates: bool = True, max_lots: int = 0,
           dd_half: bool = False) -> pd.DataFrame:
    T = T.sort_values(["date", "entry_time"]).reset_index(drop=True)
    eq = peak = float(capital)
    open_pos, rows = [], []
    week, week_start, week_pnl, blocked = None, eq, 0.0, False
    for r in T.itertuples(index=False):
        s, lot = r.sym, int(r.lot)
        key_in, key_out = r.date + r.entry_time, r.date + r.exit_time
        open_pos = [p for p in open_pos if p[0] > key_in]
        wk = date.fromisoformat(r.date).isocalendar()[:2]
        if wk != week:
            week, week_start, week_pnl, blocked = wk, eq, 0.0, False
        why = None
        acct = eq if sizing == "compound" else float(capital)
        if blocked:
            why = "weekly loss limit"
        elif gates and r.strategy == "Trend-day" and eq < C.TREND_MIN_EQUITY:
            why = "trend slot below Rs 40k"
        elif gates and str(r.strategy).startswith("Fib") and eq < C.FIB_MIN_EQUITY:
            why = "Fibonacci slot below Rs 2 lakh"
        lots, m = 0, _mult(conf, s, int(r.score))
        if dd_half and eq < (1 - C.DD_HALF_AT) * peak:
            m *= 0.5
        if why is None:
            if sizing == "fixed":
                lots = int(lots_fixed)
            else:
                rl = max(float(r.risk_lot), 1.0)
                lots = int(acct * risk_pct / 100 * m // rl)
                if lots == 0:
                    if rl <= C.AUTOPILOT_MAX_TRADE_RISK * max(eq, 1):
                        lots = 1
                    else:
                        why = "1 lot risks > 15% of the account"
        if why is None:
            lots = min(lots, max(1, int(PART * VOL5[s] / lot)))
            if max_lots:
                lots = min(lots, int(max_lots))
            free = eq - sum(p[1] for p in open_pos)
            lots = min(lots, int(free // max(r.prem_in * lot, 1e-9)))
            if lots <= 0:
                why = "not enough free cash"
        if why:
            rows.append({"date": r.date, "entry_time": r.entry_time, "sym": s, "strategy": r.strategy, "skipped": why})
            continue
        qty = lots * lot
        imp = IMPACT_K * math.sqrt(qty / VOL5[s])
        p_in, p_out = r.prem_in * (1 + imp), max(r.prem_out * (1 - imp), 0.05)
        orders = (1 + int(getattr(r, "pieces", 1) or 1)) * math.ceil(qty / FREEZE[s])
        pnl = (p_out - p_in) * qty - I.charges(p_in, p_out, qty, orders=orders, exch_pct=I.exch_rate(s))
        eq += pnl
        peak = max(peak, eq)
        week_pnl += pnl
        cap = 0.04 if week_start >= C.AUTOPILOT_SMALL_ACCOUNT else C.AUTOPILOT_WEEKLY_SMALL
        if week_pnl <= -cap * week_start:
            blocked = True
        open_pos.append((key_out, p_in * qty))
        rows.append({"date": r.date, "entry_time": r.entry_time, "exit_time": r.exit_time, "sym": s, "strategy": r.strategy, "side": r.side,
                     "score": int(r.score), "conf_mult": m, "lots": lots, "spot_in": r.spot_in, "spot_out": r.spot_out, "R": r.R,
                     "prem_in": round(p_in, 2), "prem_out": round(p_out, 2), "pnl": round(pnl, 0), "equity": round(eq, 0),
                     "reason": r.reason, "slip_extra_pct": round(imp * 100, 3)})
    return pd.DataFrame(rows)


def _summary(R: pd.DataFrame, capital: float) -> dict:
    if "skipped" not in R:
        R = R.assign(skipped=None)
    taken, skipped = R[R["skipped"].isna()], R[R["skipped"].notna()]
    if taken.empty:
        return {"trades": 0, "skipped": skipped["skipped"].value_counts().to_dict() if len(skipped) else {}}
    eq = pd.concat([pd.Series([float(capital)]), taken["equity"].astype(float)], ignore_index=True)
    peak = eq.cummax()
    dd = eq / peak - 1
    pnl = taken["pnl"].astype(float)
    w, l = pnl[pnl > 0].sum(), -pnl[pnl <= 0].sum()
    first, last = date.fromisoformat(taken["date"].iloc[0]), date.fromisoformat(taken["date"].iloc[-1])
    yrs = max((last - first).days / 365.25, 1 / 12)
    final = float(eq.iloc[-1])
    years, prev = [], float(capital)
    for y, g in taken.groupby(taken["date"].str[:4]):
        end = float(g["equity"].iloc[-1]); gp = g["pnl"].astype(float)
        e2 = pd.concat([pd.Series([prev]), g["equity"].astype(float)], ignore_index=True)
        years.append({"year": y, "start": round(prev), "end": round(end), "net": round(end - prev), "ret_pct": round((end / prev - 1) * 100, 1),
                      "trades": int(len(g)), "win_rate": round(float((gp > 0).mean() * 100), 1), "max_lots": int(g["lots"].max()),
                      "max_dd_pct": round(float((e2 / e2.cummax() - 1).min() * 100), 1)})
        prev = end
    months = [{"month": m_, "trades": int(len(g)), "net": round(float(g["pnl"].sum())), "win_rate": round(float((g["pnl"] > 0).mean() * 100), 1),
               "end": round(float(g["equity"].iloc[-1]))} for m_, g in taken.groupby(taken["date"].str[:7])]

    def grp(col):
        out = []
        for k, g in taken.groupby(col):
            gp = g["pnl"].astype(float); lo = -gp[gp <= 0].sum()
            out.append({"key": str(k), "trades": int(len(g)), "net": round(float(gp.sum())), "win_rate": round(float((gp > 0).mean() * 100), 1),
                        "profit_factor": round(float(gp[gp > 0].sum() / lo), 2) if lo > 0 else None, "avg_R": round(float(g["R"].mean()), 2)})
        return sorted(out, key=lambda x: -x["trades"])
    i_dd = int(dd.idxmin())
    curve = taken.groupby("date")["equity"].last()
    return {"trades": int(len(taken)), "start_capital": round(capital), "final": round(final), "net": round(final - capital),
            "multiple": round(final / capital, 2), "cagr_pct": round(((final / capital) ** (1 / yrs) - 1) * 100, 1) if final > 0 else -100.0,
            "max_dd_pct": round(float(dd.min() * 100), 1), "max_dd_rs": round(float((eq - peak).min())),
            "max_dd_when": taken["date"].iloc[max(i_dd - 1, 0)] if i_dd > 0 else None,
            "win_rate": round(float((pnl > 0).mean() * 100), 1), "profit_factor": round(float(w / l), 2) if l > 0 else None,
            "avg_R": round(float(taken["R"].mean()), 3), "avg_win": round(float(pnl[pnl > 0].mean())) if (pnl > 0).any() else 0,
            "avg_loss": round(float(pnl[pnl <= 0].mean())) if (pnl <= 0).any() else 0, "best_trade": round(float(pnl.max())), "worst_trade": round(float(pnl.min())),
            "max_lots": int(taken["lots"].max()), "avg_lots": round(float(taken["lots"].mean()), 1), "avg_slip_extra_pct": round(float(taken["slip_extra_pct"].mean()), 3),
            "losing_months": int(sum(1 for m_ in months if m_["net"] < 0)), "months": len(months), "worst_month": min((m_["net"] for m_ in months), default=0),
            "best_month": max((m_["net"] for m_ in months), default=0),
            "period": [taken["date"].iloc[0], taken["date"].iloc[-1]], "years": years, "months_table": months,
            "by_index": grp("sym"), "by_strategy": grp("strategy"), "by_reason": grp("reason"),
            "by_confidence": grp(pd.cut(taken["score"], [-1, 2, 4, 6], labels=["score 0-2", "score 3-4", "score 5-6"]).astype(str)),
            "curve": [{"date": d_, "equity": round(float(v))} for d_, v in curve.items()],
            "skipped": skipped["skipped"].value_counts().to_dict() if len(skipped) else {}}


# ------------------------------------------------------------------ jobs
def _validate(p: dict) -> dict:
    out = {}
    try:
        out["start"] = date.fromisoformat(str(p.get("start") or "2023-01-02"))
        out["end"] = date.fromisoformat(str(p.get("end") or C.today_ist().isoformat()))
    except ValueError:
        raise ValueError("Dates must look like 2023-01-02.")
    out["start"] = max(out["start"], EARLIEST)
    if out["end"] <= out["start"] + timedelta(days=20):
        raise ValueError("Pick a period of at least a month.")
    syms = [s for s in (p.get("indices") or SYMS) if s in SYMS]
    if not syms:
        raise ValueError("Pick at least one index.")
    out["indices"] = syms
    out["capital"] = float(p.get("capital") or 200_000)
    if not 10_000 <= out["capital"] <= 1e9:
        raise ValueError("Capital must be between Rs 10,000 and Rs 100 crore.")
    out["sizing"] = p.get("sizing") if p.get("sizing") in ("compound", "fixed_risk", "fixed") else "compound"
    out["risk_pct"] = float(p.get("risk_pct") or 1.0)
    if not 0.25 <= out["risk_pct"] <= 5:
        raise ValueError("Risk per trade must be 0.25% - 5%.")
    out["lots"] = int(p.get("lots") or 1)
    if not 1 <= out["lots"] <= 500:
        raise ValueError("Fixed lots must be 1 - 500.")
    out["confidence"] = p.get("confidence") if p.get("confidence") in CONF else "standard"
    out["big_run_skip"] = bool(p.get("big_run_skip", True))
    out["otm"] = int(p.get("otm") if p.get("otm") is not None else -1)
    if out["otm"] not in (-1, 0, 1, 2):
        raise ValueError("Strike must be 1 ITM, ATM, 1 OTM or 2 OTM.")
    out["exit"] = p.get("exit") if p.get("exit") in EXITS else "current"
    out["compare"] = bool(p.get("compare", True)) and out["exit"] != "current"
    out["gates"] = bool(p.get("gates", True))
    out["dd_half"] = bool(p.get("dd_half", True))
    out["max_lots"] = int(p.get("max_lots") or 0)
    if not 0 <= out["max_lots"] <= 5000:
        raise ValueError("Max lots must be 0 (no cap) - 5000.")
    return out


def market_busy(now=None) -> bool:
    """09:10-15:40 IST on trading days: the server is running the live Indian engine, so heavy backtests wait."""
    n = now or C.now_ist()
    return n.weekday() < 5 and n.date() not in C.HOLIDAYS and (9 * 60 + 10) <= n.hour * 60 + n.minute <= (15 * 60 + 40)


# ------------------------------------------------------------------ saved setups ("my strategies") + Pine Script
SETUPS_KEY = "pbt_setups"


def setups() -> list[dict]:
    from . import store
    return store.get(SETUPS_KEY) or []


def save_setup(name: str, params: dict, result: dict | None = None) -> dict:
    from . import store
    name = str(name or "").strip()[:60]
    if not name:
        raise ValueError("Give the strategy a name.")
    p = _validate(params)
    p = {**p, "start": p["start"].isoformat(), "end": p["end"].isoformat()}
    cur = [s for s in setups() if s["name"].lower() != name.lower()]
    if len(cur) >= 30:
        raise ValueError("Up to 30 saved strategies; delete one first.")
    item = {"id": secrets.token_hex(4), "name": name, "params": p, "exit_label": EXITS[p["exit"]]["label"],
            "saved": C.now_ist().isoformat(timespec="minutes"),
            "result": {k: (result or {}).get(k) for k in ("final", "multiple", "cagr_pct", "max_dd_pct", "trades", "win_rate", "profit_factor")} if result else None}
    store.put(SETUPS_KEY, cur + [item])
    return item


def delete_setup(sid: str) -> bool:
    from . import store
    cur = setups()
    new = [s for s in cur if s["id"] != sid]
    store.put(SETUPS_KEY, new)
    return len(new) < len(cur)


def pine_for(params: dict, index: str) -> dict:
    from . import pine
    p = _validate(params)
    lot = None
    try:
        lot = D.lot_size(index)
    except Exception:
        pass
    return pine.generate(p, index, EXITS[p["exit"]]["ov"], EXITS[p["exit"]]["label"], lot)


def start(params: dict) -> str:
    p = _validate(params)
    if market_busy() and not params.get("_test"):
        raise ValueError("Portfolio backtests run outside market hours (after 15:40 IST or before 09:10), so they never slow down live trading. The single-index backtest below works any time.")
    jid = secrets.token_hex(5)
    _jobs[jid] = {"id": jid, "status": "queued", "progress": "waiting for the previous backtest", "params": {**p, "start": p["start"].isoformat(), "end": p["end"].isoformat()},
                  "t0": time.time()}
    for k in [k for k, v in _jobs.items() if time.time() - v["t0"] > 3 * 3600]:      # keep memory small
        _jobs.pop(k, None)
    threading.Thread(target=_run, args=(jid, p), daemon=True).start()
    return jid


def job(jid: str) -> dict:
    j = _jobs.get(jid)
    if not j:
        raise ValueError("That backtest has expired; run it again.")
    out = {k: v for k, v in j.items() if k != "t0"}
    out["elapsed_s"] = round(time.time() - j["t0"])
    return out


def _set(jid, **kw):
    _jobs[jid].update(kw)


def stop(jid: str) -> dict:
    """Ask a queued or running backtest to stop (it stops within a few seconds, at the next trading day it simulates)."""
    j = _jobs.get(jid)
    if not j:
        raise ValueError("That backtest has expired.")
    if j["status"] in ("queued", "running"):
        j["cancel"] = True
        j["progress"] = "stopping…"
    return {"ok": True, "status": j["status"]}


def _check(jid):
    if _jobs.get(jid, {}).get("cancel"):
        raise B.Stopped()


def _run(jid, p):
    with _lock:
        if _jobs[jid].get("cancel"):
            _set(jid, status="stopped", progress="stopped before it started")
            return
        B.STOP_CHECK = lambda: _check(jid)
        try:
            _set(jid, status="running")
            runs = [p["exit"]] + (["current"] if p["compare"] else [])
            trades = {e: [] for e in runs}
            for sym in p["indices"]:
                _set(jid, progress=f"loading {sym} history")
                c, v = _history(sym, p["start"])
                c = c[(c.index.date >= p["start"] - timedelta(days=40)) & (c.index.date <= p["end"])]
                sc = None
                for e in runs:
                    _check(jid)
                    _set(jid, progress=f"simulating {sym} · {EXITS[e]['label']}")
                    T = _trades(sym, c, v, p["otm"], EXITS[e]["ov"], p["big_run_skip"])
                    if T.empty:
                        continue
                    T = T[(T.date >= p["start"].isoformat()) & (T.date <= p["end"].isoformat())]
                    if T.empty:
                        continue
                    T["score"] = _scores(T, c)
                    trades[e].append(T)
            _check(jid)
            _set(jid, progress="replaying the account")
            res = {}
            for e in runs:
                if not trades[e]:
                    res[e] = {"trades": 0}
                    continue
                T = pd.concat(trades[e], ignore_index=True)
                R = replay(T, p["capital"], p["sizing"], p["risk_pct"], p["lots"], p["confidence"], p["gates"], p["max_lots"], p["dd_half"] and p["sizing"] != "fixed")
                sm = _summary(R, p["capital"])
                sm["label"] = EXITS[e]["label"]
                if e == p["exit"]:
                    taken = R[R["skipped"].isna()] if "skipped" in R else R
                    sm["trades_list"] = taken.drop(columns=[c_ for c_ in ("skipped",) if c_ in taken]).tail(3000).to_dict("records")
                res[e] = sm
            _set(jid, status="done", progress="done", result={"main": res[p["exit"]], "compare": res.get("current") if p["compare"] else None})
        except B.Stopped:
            _set(jid, status="stopped", progress="stopped by you")
        except Exception as ex:
            traceback.print_exc()
            _set(jid, status="error", error=str(ex)[:300])
        finally:
            B.STOP_CHECK = None
            B.EXIT.clear()
