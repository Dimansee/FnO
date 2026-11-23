"""Round 106 helpers (23 Nov 2026): the robustness / risk / filter / exit / sizing checklist.
xreplay() = fno.portfolio_bt.replay (today's Autopilot portfolio replay) with extra hooks, all OFF by default - with defaults it
reproduces PB.replay to the rupee (checked in a_robust.py). Features per trade (gap, VIX, expiry day, ADX, confluence) come from
the 5-minute candles and the VIX file, known before the entry."""
import os, sys, math, pickle
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE.replace("round106", "round103"))
import r103lib as R3
import numpy as np, pandas as pd
from datetime import date
from fno import portfolio_bt as PB, config as C, indicators as I
L = R3.L; O = L.O
SYMS = L.SYMS
PER = dict(R3.PER)
YEARS = {"2023": ("2023-01-02", "2023-12-31"), "2024": ("2024-01-01", "2024-12-31"), "2025": ("2025-01-01", "2025-12-31"),
         "2026": ("2026-01-01", "2026-11-20")}
SIX = R3.SIX
B0 = L.B0
STRICT = os.environ.get("R106_STRICT", "1") == "1"   # judge on closed trades only (the live app's view); 0 = house replay


def load_set(name):
    if name == "base":
        return R3.T0, R3.A0, R3.H0
    return pickle.load(open(f"/tmp/r106/{name}.pkl", "rb"))


# ------------------------------------------------------------------ features
def _daily(sym):
    c = O.candles(sym)
    g = c.groupby(c.index.date)
    d = pd.DataFrame({"open": g.open.first(), "high": g.high.max(), "low": g.low.min(), "close": g.close.last()})
    d.index = [x.isoformat() for x in d.index]
    pc = d.close.shift(1)
    tr = pd.concat([d.high - d.low, (d.high - pc).abs(), (d.low - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    d["atr_prev"] = atr.shift(1)
    d["gap_atr"] = (d.open - pc) / d.atr_prev
    d["prev_range_atr"] = ((d.high - d.low) / atr).shift(1)
    up, dn = d.high.diff(), -d.low.diff()
    pdm = np.where((up > dn) & (up > 0), up, 0.0); mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    trs = tr.ewm(alpha=1 / 14, adjust=False).mean()
    pdi = 100 * pd.Series(pdm, index=d.index).ewm(alpha=1 / 14, adjust=False).mean() / trs
    mdi = 100 * pd.Series(mdm, index=d.index).ewm(alpha=1 / 14, adjust=False).mean() / trs
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi)
    d["adx_prev"] = dx.ewm(alpha=1 / 14, adjust=False).mean().shift(1)
    meta = C.INDICES[sym]
    d["expiry_day"] = [I.demo_expiries(meta["expiry"], date.fromisoformat(x), 1, meta.get("exchange", "NSE"))[0].isoformat() == x for x in d.index]
    return d


_F = {}


def features(T):
    """Adds per-trade columns: gap_atr, prev_range_atr, adx_prev, expiry_day, vix (at entry), vix_chg (% vs previous close),
    vix_open, dow, agree / conflict (another Autopilot strategy on the same index already signalled the same / opposite way today)."""
    if not _F:
        for s in SYMS:
            _F[s] = _daily(s)
        v = O.H.load("INDIAVIX")
        vd = v.groupby(v.index.date).close.last(); vd.index = [x.isoformat() for x in vd.index]
        _F["vix5"] = v.close; _F["vix_pc"] = vd.shift(1)
        vo = v.groupby(v.index.date).open.first(); vo.index = [x.isoformat() for x in vo.index]; _F["vix_open"] = vo
    T = T.copy()
    for col in ("gap_atr", "prev_range_atr", "adx_prev", "expiry_day"):
        T[col] = [_F[s][col].get(d, np.nan) for s, d in zip(T.sym, T.date)]
    ts = pd.to_datetime(T.date + " " + T.entry_time) - pd.Timedelta(minutes=5)       # signal bar start (entry is at its close)
    v5 = _F["vix5"]
    ix = v5.index.searchsorted(ts.values, side="right") - 1
    T["vix"] = np.where(ix >= 0, v5.values[np.maximum(ix, 0)], np.nan)
    T["vix_pc"] = T.date.map(_F["vix_pc"]).astype(float)
    T["vix_open"] = T.date.map(_F["vix_open"]).astype(float)
    T["vix_chg"] = (T.vix / T.vix_pc - 1) * 100
    T["dow"] = pd.to_datetime(T.date).dt.dayofweek
    # within (sym, date): earlier entries by other strategies
    T["agree"] = 0; T["conflict"] = 0
    for (s, d), g in T.groupby(["sym", "date"]):
        if len(g) < 2:
            continue
        for i, r in g.iterrows():
            prev = g[(g.entry_time < r.entry_time) & (g.strategy != r.strategy)]
            openp = prev[prev.exit_time > r.entry_time]
            T.at[i, "agree"] = int((prev.side == r.side).any())
            T.at[i, "conflict"] = int((openp.side != r.side).any())
    return T


def tag_sets(T, A, H):
    f = features(T)
    cols = ["gap_atr", "prev_range_atr", "adx_prev", "expiry_day", "vix", "vix_chg", "vix_open", "dow", "agree", "conflict"]
    key = ["sym", "date", "entry_time", "strategy"]
    m = f[key + cols]
    add = lambda X: None if X is None else X.drop(columns=[c for c in cols if c in X], errors="ignore").merge(m, on=key, how="left")
    return f, add(A), add(H)


# ------------------------------------------------------------------ extended replay
def xreplay(T, capital, A=None, H=None, slip_add=0.0, impact_k=None, dd_levels=((C.DD_HALF_AT, 0.5),), weekly=0.04,
            weekly_small=None, monthly=None, streak=None, open_risk=None, same_dir=None, cooldown=None, cool_score=None,
            mult=None, skip=None, q=None, strict=None, strict_eq=None):
    """PB.replay with hooks (Autopilot defaults: compounding 1%, standard confidence, gates, cpr sizing, 50-lot cap, small/big OTM).
    slip_add: extra slippage per fill on top of the 0.5% in the trades (0.005 = doubled); impact_k: size-impact constant (0.06 now).
    dd_levels: [(fall from peak, size multiplier)], deepest matching level applies. weekly / weekly_small: weekly loss limits.
    monthly: monthly loss limit (fraction). streak: (n, mult) - after n losses in a row, size x mult until a win.
    open_risk: cap on the sum of open trades' risk (fraction of equity). same_dir: max open positions one way across the indices.
    cooldown: minutes after a stop-loss exit on an index with no new entry there (cool_score: unless the score is >= this).
    mult / skip: per-trade arrays (aligned with T after the sort below) or callables r -> value.
    strict_eq: sizing, the drawdown brake, the weekly / monthly limits and the cash check use only CLOSED trades' results
    (the live app's view); the plain replay adds each trade's result at its entry. Reported equity is the same either way.
    strict: the loss streak counts only trades already closed at this entry, in exit order (the plain replay books a trade's
    result at its entry, so a streak rule there could see trades still open)."""
    if strict_eq is None:
        strict_eq = STRICT
    if strict is None:
        strict = STRICT
    q = {**B0, **(q or {}), "capital": capital}
    sizing, risk_pct, conf, gates, max_lots = q["sizing"], q["risk_pct"], q["confidence"], q["gates"], q["max_lots"]
    cpr = q.get("cpr_size", False) and sizing != "fixed"
    dd_on = q["dd_half"] and sizing != "fixed"
    ik = PB.IMPACT_K if impact_k is None else impact_k
    wsmall = C.AUTOPILOT_WEEKLY_SMALL if weekly_small is None else weekly_small
    T = T.sort_values(["date", "entry_time", "sym", "strategy", "side"], kind="mergesort").reset_index(drop=True)
    alt_by = {(a.sym, a.date, a.entry_time, a.strategy): a for a in A.itertuples(index=False)} if A is not None and len(A) else {}
    hi_by = {(a.sym, a.date, a.entry_time, a.strategy): a for a in H.itertuples(index=False)} if H is not None and len(H) else {}
    eq = peak = float(capital)
    open_pos, rows = [], []
    week, week_start, week_pnl, blocked = None, eq, 0.0, False
    month, month_start, month_pnl, mblocked = None, eq, 0.0, False
    losses_row = 0
    last_stop = {}
    closed = []      # (exit key, pnl) for strict streaks
    pend, real, rpeak, tot = [], float(capital), float(capital), float(capital)
    from fno import strategy as S
    for idx, r0 in enumerate(T.itertuples(index=False)):
        r = r0
        if strict_eq:
            k0 = r0.date + r0.entry_time
            for p in sorted((p for p in pend if p[0] <= k0), key=lambda p: p[0]):
                real += p[1]; rpeak = max(rpeak, real)
                if p[2] == week:
                    week_pnl += p[1]
                if p[3] == month:
                    month_pnl += p[1]
            pend = [p for p in pend if p[0] > k0]
            eq, peak = real, rpeak
        if alt_by and eq < C.SMALL_OTM_BELOW:
            r = alt_by.get((r.sym, r.date, r.entry_time, r.strategy), r)
        elif hi_by and eq >= C.BIG_OTM_FROM:
            r = hi_by.get((r.sym, r.date, r.entry_time, r.strategy), r)
        s, lot = r.sym, int(r.lot)
        key_in, key_out = r.date + r.entry_time, r.date + r.exit_time
        open_pos = [p for p in open_pos if p[0] > key_in]
        wk = date.fromisoformat(r.date).isocalendar()[:2]
        if wk != week:
            week, week_start, week_pnl, blocked = wk, eq, 0.0, False
        if r.date[:7] != month:
            month, month_start, month_pnl, mblocked = r.date[:7], eq, 0.0, False
        if strict_eq:
            cap_ = weekly if week_start >= C.AUTOPILOT_SMALL_ACCOUNT else wsmall
            if week_pnl <= -cap_ * week_start:
                blocked = True
            if monthly and month_pnl <= -monthly * month_start:
                mblocked = True
        why = None
        acct = eq if sizing == "compound" else float(capital)
        if blocked:
            why = "weekly loss limit"
        elif mblocked:
            why = "monthly loss limit"
        elif gates and r.strategy == "Trend-day" and eq < C.TREND_MIN_EQUITY:
            why = "trend gate"
        elif gates and str(r.strategy).startswith("Fib") and eq < C.FIB_MIN_EQUITY:
            why = "fib gate"
        elif str(r.strategy).startswith("Channel") and (not q.get("channel", True) or (gates and eq < C.CHANNEL_MIN_EQUITY)):
            why = "channel gate"
        elif str(r.strategy).startswith("Round-level") and (not q.get("rlevel", True) or (gates and eq < C.RLEVEL_MIN_EQUITY)):
            why = "rlevel gate"
        if why is None and skip is not None and (skip[idx] if not callable(skip) else skip(r0)):
            why = "filter"
        if why is None and cooldown and s in last_stop:
            t_stop = last_stop[s]
            if t_stop <= key_in and (pd.Timestamp(key_in[:10] + " " + key_in[10:]) - pd.Timestamp(t_stop[:10] + " " + t_stop[10:])).total_seconds() / 60 < cooldown:
                if cool_score is None or int(r.score) < cool_score:
                    why = "cooldown"
        if why is None and same_dir and sum(1 for p in open_pos if p[3] == r.side) >= same_dir:
            why = "same-direction cap"
        lots, m = 0, PB._mult(conf, s, int(r.score))
        if cpr and why is None:
            ct = S.cpr_tilt(getattr(r, "cpr_w", None))
            if ct == 0:
                why = "wide CPR"
            elif ct:
                m *= ct
        if dd_on:
            fall = 1 - eq / peak
            dm = 1.0
            for lv, mm in dd_levels:
                if fall > lv:
                    dm = mm
            m *= dm
        if streak and strict:
            done = sorted((c for c in closed if c[0] <= key_in), key=lambda c: c[0])
            losses_row = 0
            for c in reversed(done):
                if c[1] < 0:
                    losses_row += 1
                else:
                    break
            if len(closed) > 400:
                closed = [c for c in closed if c[0] > key_in] + done[-50:]
        if streak and losses_row >= streak[0]:
            m *= streak[1]
        if mult is not None:
            m *= (mult[idx] if not callable(mult) else mult(r0))
        if why is None and m <= 0:
            why = "size 0"
        rl = max(float(getattr(r, "risk_lot_capped", r.risk_lot) or r.risk_lot), 1.0)
        if why is None:
            if sizing == "fixed":
                lots = int(q["lots"])
            else:
                lots = int(acct * risk_pct / 100 * m // rl)
                if lots == 0:
                    if rl <= C.AUTOPILOT_MAX_TRADE_RISK * max(eq, 1):
                        lots = 1
                    else:
                        why = "1 lot > 15%"
        if why is None:
            lots = min(lots, max(1, int(PB.PART * PB.VOL5[s] / lot)))
            if max_lots:
                lots = min(lots, int(max_lots))
            free = eq - sum(p[1] for p in open_pos)
            lots = min(lots, int(free // max(r.prem_in * lot, 1e-9)))
            if open_risk is not None:
                room = open_risk * eq - sum(p[2] for p in open_pos)
                lots = min(lots, int(room // rl))
            if lots <= 0:
                why = "no room"
        if why:
            rows.append({"date": r.date, "entry_time": r.entry_time, "sym": s, "strategy": r.strategy, "skipped": why})
            continue
        qty = lots * lot
        imp = ik * math.sqrt(qty / PB.VOL5[s])
        p_in = r.prem_in * (1 + slip_add) * (1 + imp)
        p_out = max(r.prem_out * (1 - slip_add) * (1 - imp), 0.05)
        orders = (1 + int(getattr(r, "pieces", 1) or 1)) * math.ceil(qty / PB.FREEZE[s])
        pnl = (p_out - p_in) * qty - I.charges(p_in, p_out, qty, orders=orders, exch_pct=I.exch_rate(s))
        tot += pnl
        if strict_eq:
            pend.append((key_out, pnl, week, month))
        else:
          eq += pnl
          peak = max(peak, eq)
          week_pnl += pnl; month_pnl += pnl
          cap = weekly if week_start >= C.AUTOPILOT_SMALL_ACCOUNT else wsmall
          if week_pnl <= -cap * week_start:
              blocked = True
          if monthly and month_pnl <= -monthly * month_start:
              mblocked = True
        if not strict:
            losses_row = losses_row + 1 if pnl < 0 else 0
        closed.append((key_out, pnl))
        if cooldown and "stop" in str(r.reason).lower():
            last_stop[s] = key_out
        open_pos.append((key_out, p_in * qty, rl * lots, r.side))
        rows.append({"date": r.date, "entry_time": r.entry_time, "exit_time": r.exit_time, "sym": s, "strategy": r.strategy, "side": r.side,
                     "score": int(r.score), "conf_mult": m, "lots": lots, "spot_in": r.spot_in, "spot_out": r.spot_out, "R": r.R,
                     "prem_in": round(p_in, 2), "prem_out": round(p_out, 2), "pnl": round(pnl, 0), "equity": round(tot, 0),
                     "reason": r.reason, "slip_extra_pct": round(imp * 100, 3)})
    return pd.DataFrame(rows)


def cell(T, A, H, cap, a, b, raw=False, **kw):
    f = lambda X: None if X is None else X[(X.date >= a) & (X.date <= b)]
    Tf = f(T)
    for k in ("mult", "skip"):
        if k in kw and kw[k] is not None and not callable(kw[k]):
            raise ValueError("pass callables for mult / skip")
    R = xreplay(Tf, cap, f(A), f(H), **kw)
    if raw:
        return R
    s = PB._summary(R, cap)
    if not s.get("trades"):
        return {"final": float(cap), "dd": 0.0, "trades": 0}
    return {"final": float(s["final"]), "dd": float(s["max_dd_pct"]), "trades": int(s["trades"])}


def grid(T, A, H, pers=("2023-24", "2025-26", "full"), caps=(200_000, 20_000), **kw):
    out = {}
    for cap in caps:
        for p in pers:
            a, b = PER[p] if p in PER else YEARS[p]
            out[f"{cap}|{p}"] = cell(T, A, H, cap, a, b, **kw)
    return out


def verdict(g, base):
    ks = [k for k in base if k in g]
    better = sum(1 for k in ks if g[k]["final"] > base[k]["final"])
    deeper = max(base[k]["dd"] - g[k]["dd"] for k in ks)
    return better, len(ks), round(deeper, 1)


def fmt(c):
    return f"{c['final'] / 1e5:.2f}L ({c['dd']:.0f}%)"


_BASE = {}


def base_grid():
    if not _BASE:
        T, A, H = load_set("base")
        _BASE.update(grid(T, A, H))
    return _BASE


def report(name, T, A, H, out, **kw):
    g = grid(T, A, H, **kw)
    b, n, d = verdict(g, base_grid())
    out[name] = {"cells": g, "better": b, "of": n, "deeper": d}
    flag = "PASS" if b == n and d <= 2.0 else ""
    print(f"  {name:44s} better {b}/{n} fall {d:+5.1f} {flag:4s} | " + " | ".join(f"{k.replace('200000', '2L').replace('20000', '20k')} {fmt(v)}" for k, v in g.items()), flush=True)
    return g
