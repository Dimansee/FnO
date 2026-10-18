"""Variant harness built on the app's own backtester (fno/backtest.py) so the baseline = current live rules."""
import sys, math
from pathlib import Path
from datetime import timedelta
import pandas as pd, numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fno import backtest as B, strategy as S, indicators as I, config as C, pricing as PR
SLIP = B.SLIP

def one(day, idx, bands, start, exp, iv_of, step, lot, capital, d, strat, lv, P, exch_pct, want_side=None):
    entry_i = None
    for i, ts in enumerate(idx):
        if i < start: continue
        end = ts + timedelta(minutes=C.CANDLE_MIN)
        if strat == "camarilla":
            if end.time() > C.CAM_LAST_ENTRY: break
            if i < 1: continue
            c0, c1 = float(day["close"].iloc[i-1]), float(day["close"].iloc[i])
            if c0 <= lv["h4"] < c1 and want_side in (None, "CE"): entry_i, opt = i, "CE"; break
            if c0 >= lv["l4"] > c1 and want_side in (None, "PE"): entry_i, opt = i, "PE"; break
            continue
        if end.time() > C.LAST_ENTRY: break
        if end.time() < C.FIRST_CHECK or not S._is_check(ts): continue
        b, up, lo = day.loc[ts], bands.loc[ts, "up"], bands.loc[ts, "lo"]
        if pd.isna(up): continue
        if b["close"] > up and b["close"] > b["vwap"] and want_side in (None, "CE"): entry_i, opt = i, "CE"; break
        if b["close"] < lo and b["close"] < b["vwap"] and want_side in (None, "PE"): entry_i, opt = i, "PE"; break
    if entry_i is None: return None
    ts_in = idx[entry_i]; bar = day.loc[ts_in]; sign = 1 if opt == "CE" else -1
    spot = float(bar["close"]); a = float(bar["atr"])
    if strat == "camarilla":
        risk = min(max(abs(spot - (lv["h3"] if sign > 0 else lv["l3"])), C.CAM_RMIN*a), C.CAM_RMAX*a); sl, tgt = spot - sign*risk, None
    else:
        risk = min(max(C.STOP_ATR*a, a), 2.5*a); sl, tgt = spot - sign*risk, spot + sign*C.RR_NOISE*risk
    t_in = ts_in + timedelta(minutes=C.CANDLE_MIN)
    otm = -C.STRIKE_ITM
    k = round(spot/step)*step + sign*otm*step
    def prem(s, when, ts):
        t = I.years_to_expiry(exp, when.to_pydatetime().replace(tzinfo=None))
        return I.bs_price(s, k, max(t, 1/(365*24*12)), iv_of(ts, s, k, opt), opt)
    p_in = prem(spot, t_in, ts_in)*(1+SLIP)
    risk_unit = max(p_in - prem(sl, t_in, ts_in), p_in*0.05)
    cost = p_in*lot
    if P.get('fixed_lots'):
        lots = P['fixed_lots']
    elif P['sizing'] == 'one_lot':
        if cost > capital: return ("skip", entry_i, "cash")
        lots = 1
    else:
        budget = capital*C.RISK_PER_TRADE
        lots = int(budget//(risk_unit*lot))
        if lots == 0 and risk_unit*lot <= 1.5*budget: lots = 1
        if lots == 0: return ("skip", entry_i, "risk")
        lots = min(lots, int(capital//cost))
    if P.get('exit') == 'fixed':
        return fixed_exit(day, idx, entry_i, spot, sign, risk, sl, prem, p_in, t_in, exp, d, lots, lot, exch_pct, P, strat, opt, risk_unit)
    reason = x_spot = x_ts = None; live_sl, reached = sl, False
    best = 0.0
    for ts in idx[entry_i+1:]:
        c = day.loc[ts]; end = ts + timedelta(minutes=C.CANDLE_MIN)
        adverse, favour = (c["low"], c["high"]) if sign > 0 else (c["high"], c["low"])
        best = max(best, sign*(favour-spot)/risk)
        if sign*(adverse-live_sl) <= 0:
            reason = "stop_be" if abs(live_sl-spot) < 1e-9 else ("stop_trail" if sign*(live_sl-spot) > 0 else "stop")
            x_spot = c["open"] if sign*(c["open"]-live_sl) < 0 else live_sl
        elif tgt is not None and sign*(favour-tgt) >= 0:
            reason, x_spot = "target", (c["open"] if sign*(c["open"]-tgt) > 0 else tgt)
        elif strat == "camarilla":
            if sign*(favour-(spot+sign*C.CAM_BE_R*risk)) >= 0 and sign*(live_sl-spot) < 0: live_sl = spot
            if sign*(favour-(spot+sign*0.5*risk)) >= 0: reached = True
            if P['cam_tstop'] and not reached and (end-t_in).total_seconds()/60 >= P['cam_tstop']:
                reason, x_spot = "time", float(c["close"])
        else:
            # noise exits
            if P.get('noise_be') and best >= P['noise_be'] and sign*(live_sl-spot) < 0: live_sl = spot
            if S._is_check(ts) and P['noise_trail'] != 'none':
                held = (end - t_in).total_seconds()/60
                if P['noise_trail'] == 'band_vwap' or (P['noise_trail'] == 'band_vwap_after1r' and best >= 1):
                    lvl = max(bands.loc[ts,"up"], c["vwap"]) if sign > 0 else min(bands.loc[ts,"lo"], c["vwap"])
                elif P['noise_trail'] in ('band', 'band_vwap_after1r'):
                    lvl = bands.loc[ts,"up"] if sign > 0 else bands.loc[ts,"lo"]
                elif P['noise_trail'] == 'vwap':
                    lvl = c["vwap"]
                if not pd.isna(lvl) and sign*(c["close"]-lvl) < 0:
                    reason, x_spot = "trail", float(c["close"])
        if reason is None and end.time() >= C.SQUARE_OFF: reason, x_spot = "squareoff", float(c["close"])
        if reason: x_ts, x_bar = end, ts; xi = idx.index(ts); break
    if reason is None:
        x_bar = idx[-1]; xi = len(idx)-1
        reason, x_spot, x_ts = "squareoff", float(day["close"].iloc[-1]), idx[-1]+timedelta(minutes=5)
    p_raw = prem(x_spot, x_ts, x_bar)
    p_out = PR.real_adjust(p_in/(1+SLIP), p_raw, (exp-d).days, (x_ts-t_in).total_seconds()/3600)*(1-SLIP)
    qty = lots*lot
    pnl = (p_out-p_in)*qty - I.charges(p_in, p_out, qty, exch_pct=exch_pct)
    return {"risk_lot": risk_unit*lot, "cost_lot": p_in*lot, "date": d.isoformat(), "entry_time": t_in.strftime("%H:%M"), "exit_time": x_ts.strftime("%H:%M"),
            "side": opt, "spot_in": spot, "spot_out": float(x_spot), "R": sign*(x_spot-spot)/risk, "lots": lots,
            "pnl": pnl, "reason": reason, "strategy": strat, "xi": xi, "best_R": best}

def _targets(P, day, entry_i):
    """List of (R multiple, share of lots). 'situational': 2R when price held one side of VWAP on >= 80% of today's bars, else 0.75R."""
    if P.get('targets') == 'situational':
        sub = day.iloc[:entry_i + 1]
        share = float((sub["close"] > sub["vwap"]).mean())
        return [(2.0, 1.0)] if max(share, 1 - share) >= 0.8 else [(0.75, 1.0)]
    return P['targets']


def fixed_exit(day, idx, entry_i, spot, sign, risk, sl, prem, p_in, t_in, exp, d, lots, lot, exch_pct, P, strat, opt, risk_unit):
    """Fixed stop + fixed target(s): no trailing, no time stop. Lots are split across the targets in whole lots (fewer lots than
    targets -> only the first targets are used). Optional: stop to entry after T1 (P['be_t1']). Anything still open exits at 15:15."""
    tg = _targets(P, day, entry_i)
    n = len(tg)
    use = tg[:min(n, lots)]
    w = [f for _, f in use]; tot = sum(w)
    alloc = [int(lots * f / tot) for f in w]
    alloc[0] += lots - sum(alloc)
    legs = [{"R": r, "lots": a, "done": False} for (r, _), a in zip(use, alloc) if a > 0]
    live_sl = sl
    fills = []                                   # (lots, x_spot, x_ts, x_bar, reason)
    xi = len(idx) - 1
    for ts in idx[entry_i + 1:]:
        c = day.loc[ts]; end = ts + timedelta(minutes=C.CANDLE_MIN)
        adverse, favour = (c["low"], c["high"]) if sign > 0 else (c["high"], c["low"])
        open_legs = [g for g in legs if not g["done"]]
        if not open_legs:
            break
        if sign * (adverse - live_sl) <= 0:      # stop first (same-bar ties go to the stop, as in every backtest here)
            px = c["open"] if sign * (c["open"] - live_sl) < 0 else live_sl
            why = "stop_be" if abs(live_sl - spot) < 1e-9 else "stop"
            for g in open_legs:
                g["done"] = True; fills.append((g["lots"], px, end, ts, why))
            xi = idx.index(ts); break
        for g in open_legs:
            tgt = spot + sign * g["R"] * risk
            if sign * (favour - tgt) >= 0:
                px = c["open"] if sign * (c["open"] - tgt) > 0 else tgt
                g["done"] = True; fills.append((g["lots"], px, end, ts, f"T{g['R']:g}R"))
                if P.get('be_t1') and sign * (live_sl - spot) < 0:
                    live_sl = spot
        if all(g["done"] for g in legs):
            xi = idx.index(ts); break
        if end.time() >= C.SQUARE_OFF:
            for g in legs:
                if not g["done"]:
                    g["done"] = True; fills.append((g["lots"], float(c["close"]), end, ts, "squareoff"))
            xi = idx.index(ts); break
    for g in legs:
        if not g["done"]:
            fills.append((g["lots"], float(day["close"].iloc[-1]), idx[-1] + timedelta(minutes=5), idx[-1], "squareoff"))
    pnl, R_w = 0.0, 0.0
    for lt, px, x_ts, x_bar, why in fills:
        p_out = PR.real_adjust(p_in / (1 + SLIP), prem(px, x_ts, x_bar), (exp - d).days, (x_ts - t_in).total_seconds() / 3600) * (1 - SLIP)
        q = lt * lot
        pnl += (p_out - p_in) * q - I.charges(p_in, p_out, q, exch_pct=exch_pct)
        R_w += lt * sign * (px - spot) / risk
    last = max(fills, key=lambda f: f[2])
    reasons = sorted({f[4] for f in fills})
    reason = "target" if all(r.startswith("T") for r in reasons) else ("stop" if "stop" in reasons else reasons[-1])
    return {"risk_lot": risk_unit * lot, "cost_lot": p_in * lot, "date": d.isoformat(), "entry_time": t_in.strftime("%H:%M"),
            "exit_time": last[2].strftime("%H:%M"), "side": opt, "spot_in": spot, "spot_out": float(last[1]), "R": R_w / lots,
            "lots": lots, "pnl": pnl, "reason": reason, "strategy": strat, "xi": xi, "best_R": None, "fills": "+".join(r for *_, r in fills)}


def run(symbol, candles, vix, P, capital=200000, start='2023-01-01'):
    meta = B.D.instrument(symbol); lot = meta['lot']; step = meta['step']
    df = I.add_indicators(candles)
    frames = [(d, g) for d, g in df.groupby(df.index.date)]
    vx = vix.sort_index()
    trades = []
    for n, (d, day) in enumerate(frames):
        if n == 0 or len(day) < 60 or str(d) < start: continue
        hist = pd.concat([g for _, g in frames[max(0, n-15):n]])
        sigma = S.noise_sigma(hist)
        if not sigma: continue
        bands = S.noise_bands(day, float(hist["close"].iloc[-1]), sigma, C.NOISE_MULT)
        def vix_at(ts):
            i = vx.index.searchsorted(ts, side="right")-1
            return float(vx.iloc[i]) if i >= 0 else 14.0
        v0 = vix_at(day.index[0])
        if v0 < C.VIX_MIN: continue
        day_exps = I.demo_expiries(meta["expiry"], d, 3, meta.get("exchange", "NSE"))
        if d in C.BUDGET_DAYS: continue
        exp = I.pick_trading_expiry(day_exps, meta["expiry"], d)
        iv_of = lambda ts, s=None, k=None, opt=None: PR.iv(symbol, vix_at(ts), s, k, opt, (exp-d).days, meta["step"], meta["iv_mult"]) if s else min(max(vix_at(ts)/100*meta["iv_mult"], 0.06), 0.9)
        idx = list(day.index); lv = S.camarilla_levels(hist)
        day_trades = []
        for strat in ("noise", "camarilla"):
            if strat == "camarilla" and (not lv or v0 > P.get('cam_vix_max', C.CAM_VIX_MAX)): continue
            if strat == "noise" and v0 >= P.get('noise_vix_max', C.NOISE_VIX_MAX): continue
            start_i, ntr, side = 0, 0, None
            while True:
                res = one(day, idx, bands, start_i, exp, iv_of, step, lot, capital, d, strat, lv, P, I.exch_rate(symbol), side)
                if res is None: break
                if isinstance(res, tuple): start_i = res[1]+1; continue
                day_trades.append(res); ntr += 1
                # re-entry rule: only after a non-stop exit (trail/time), same direction, max P['reentry'] extra
                if ntr > P.get('reentry', 0): break
                if P.get('reentry_after'):
                    if res['reason'] not in P['reentry_after']: break
                else:
                    if res['reason'] in ('stop', 'target', 'squareoff', 'stop_be'): break
                    if P.get('reentry_only_if_trail') and res['reason'] not in ('trail', 'time'): break
                start_i = res['xi']+1; side = res['side']
        day_trades.sort(key=lambda t: t["entry_time"])
        busy = ""
        for t in day_trades:
            if not P.get('overlap') and t["entry_time"] < busy: continue
            trades.append(t); busy = max(busy, t["exit_time"])
    for t in trades: t['sym'] = symbol
    return trades

DATA = Path(__file__).resolve().parent / "data"


def m5(sym):
    """5-min index candles 2023-01 -> latest: the 1-minute research file plus the recorder's live/ days."""
    import glob
    a = pd.read_csv(DATA / f"{sym}_1m_upstox.csv.gz", parse_dates=["ts"]).set_index("ts")
    parts = [a]
    for f in sorted(glob.glob(str(DATA / "live" / "*" / "candles_1m.csv.gz"))):
        d = pd.read_csv(f)
        d = d[(d.sym == sym) & (d.kind == "IDX")]
        if d.empty:
            continue
        d["ts"] = pd.to_datetime(d.ts)
        parts.append(d.set_index("ts")[["open", "high", "low", "close", "volume"]])
    x = pd.concat(parts)
    x = x[~x.index.duplicated(keep="first")].sort_index().between_time("09:15", "15:29")
    return x.resample("5min", label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()


_C = {}
def load(sym):
    if sym not in _C:
        _C[sym] = m5(sym)
    return _C[sym]
