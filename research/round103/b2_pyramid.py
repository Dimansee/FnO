"""Round 103 B: the user's 21 Nov idea - lots up to 500, and after entry add more lots once the trade is +0.95R in profit, then
take 10-25 index points. Built on the round-96 re-marking (option re-priced from the 1-MINUTE index path with the app's own
pricing, implied vol backed out of the entry price) with intrabar target / stop checks (the p-addon watch item asked for these),
the app's own market-impact model on every extra order (IMPACT_K * sqrt(qty / 5-min volume)), freeze-quantity order slicing in
the charges, a 10% 5-min-volume participation cap, and free cash. Add-on profit is not re-invested in sizing."""
import sys, json, math, time, itertools
sys.path.insert(0, __file__.rsplit("/", 1)[0])
import r103lib as R3
import numpy as np, pandas as pd
from datetime import datetime, timedelta
from scipy.optimize import brentq
from fno import portfolio_bt as PB, indicators as I, pricing as PR, market as D, config as C
L = R3.L
R34 = L.R34
SLIP = 0.005
IMP_MULT = float(sys.argv[1]) if len(sys.argv) > 1 else 1.0
M1 = {s: R34.m1(s) for s in ("NIFTY", "BANKNIFTY", "SENSEX")}
M1A = {s: (m.index.values, m.open.values, m.high.values, m.low.values, m.close.values) for s, m in M1.items()}
META = {s: D.instrument(s) for s in M1}


def otm_of(t):
    t = str(t); n = int(t.split()[0]) if t[0].isdigit() else 0
    return -n if "ITM" in t else (n if "OTM" in t else 0)


def attach(R, frames):
    look = {}
    for F in frames:
        if F is None: continue
        for r in F.itertuples(index=False):
            look.setdefault((r.sym, r.date, r.entry_time, r.strategy), []).append(r)
    rows = []
    for r in R.itertuples(index=False):
        c = look.get((r.sym, r.date, r.entry_time, r.strategy), [])
        if not c: rows.append(None); continue
        rows.append(min(c, key=lambda x: abs(x.prem_in * (1 + 0) - r.prem_in / (1 + r.slip_extra_pct / 100))))
    R = R.copy(); R["_src"] = rows
    R = R[R["_src"].notna()].reset_index(drop=True)
    for col in ("type", "lot", "risk_lot", "prem_in", "prem_out"):
        R["src_" + col] = [getattr(x, col) for x in R["_src"]]
    return R.drop(columns="_src")


_PATH = {}


def path(r):
    """Minute marks between entry and exit: arrays (when, spot, high, low, mark_at_close) + a function to price the option at a spot."""
    key = (r.sym, r.date, r.entry_time, r.strategy, r.src_type)
    if key in _PATH: return _PATH[key]
    m = META[r.sym]; step = m["step"]
    d = datetime.strptime(r.date, "%Y-%m-%d").date()
    exp = I.pick_trading_expiry(I.demo_expiries(m["expiry"], d, 3, m.get("exchange", "NSE")), m["expiry"], d)
    if exp is None: _PATH[key] = None; return None
    opt = "CE" if r.side == "CALL" else "PE"; sign = 1 if opt == "CE" else -1
    k = round(r.spot_in / step) * step + sign * otm_of(r.src_type) * step
    t_in = datetime.strptime(f"{r.date} {r.entry_time}", "%Y-%m-%d %H:%M")
    t_out = datetime.strptime(f"{r.date} {r.exit_time}", "%Y-%m-%d %H:%M")
    mid_in = r.src_prem_in / (1 + SLIP)
    T0 = max(I.years_to_expiry(exp, t_in), 1 / (365 * 24 * 12))
    try:
        iv = brentq(lambda v: I.bs_price(r.spot_in, k, T0, v, opt) - mid_in, 0.01, 3.0)
    except Exception:
        _PATH[key] = None; return None
    ts, o, h, l, c = M1A[r.sym]
    a = ts.searchsorted(np.datetime64(t_in)); b = ts.searchsorted(np.datetime64(t_out))
    dte = (exp - d).days

    def price(spot, when):
        raw = I.bs_price(float(spot), k, max(I.years_to_expiry(exp, when), 1 / (365 * 24 * 12)), iv, opt)
        return PR.real_adjust(mid_in, raw, dte, max((when - t_in).total_seconds() / 3600, 0))
    whens = [pd.Timestamp(ts[j]).to_pydatetime() + timedelta(minutes=1) for j in range(a, b)]
    marks = np.array([price(c[j], w) for j, w in zip(range(a, b), whens)]) if b > a else np.array([])
    out = {"whens": whens, "high": h[a:b], "low": l[a:b], "close": c[a:b], "mark": marks, "price": price, "sign": sign, "t_out": t_out,
           "k": k, "dte": dte, "mid_in": mid_in}
    _PATH[key] = out
    return out


def impact(sym, qty):
    return IMP_MULT * PB.IMPACT_K * math.sqrt(max(qty, 1) / PB.VOL5[sym])


def overlay(R, cap_total, trig, X, rule, stop, mode):
    rows = []
    for i, r in enumerate(R.itertuples(index=False)):
        P = path(r)
        if P is None or len(P["mark"]) < 2: continue
        lot, lots, sym, sign = int(r.src_lot), int(r.lots), r.sym, P["sign"]
        eq_before = float(r.equity - r.pnl)
        rl = max(float(r.src_risk_lot), 1.0)
        p0 = r.src_prem_in
        base_cost = r.prem_in * lot * lots
        added = None
        for j in range(len(P["mark"]) - 1):
            mk = P["mark"][j]
            if added is None:
                rr = (mk * (1 - SLIP) - p0) * lot * lots / (rl * lots)
                if rr >= trig:
                    want = {"+100%": lots, "+300%": 3 * lots, "to cap": cap_total - lots, "+50 lots": 50}[rule]
                    room = max(int(PB.PART * PB.VOL5[sym] / lot) - lots, 0)
                    q = min(want, cap_total - lots, room)
                    p_add = mk * (1 + SLIP)
                    q = min(q, int(max(eq_before - base_cost, 0) // max(p_add * lot, 1e-9)))
                    if q <= 0: break
                    qa = q * lot; imp = impact(sym, qa)
                    added = dict(j=j, p=p_add * (1 + imp), q=q, spot=P["close"][j], imp=imp)
                continue
            # after the add: check target / stop on the NEXT bars (intrabar high/low for the target, close for the stops)
            tgt_spot = added["spot"] + sign * X
            hit = (P["high"][j] >= tgt_spot) if sign > 0 else (P["low"][j] <= tgt_spot)
            exit_px, why = None, None
            ep_mark = P["mark"][j]
            if stop == "own" and ep_mark * (1 - SLIP) <= added["p"] / (1 + added["imp"]):
                exit_px, why = ep_mark * (1 - SLIP), "stop"
            elif stop == "be" and ((ep_mark * (1 - SLIP) - p0) * lot * lots + (ep_mark * (1 - SLIP) - added["p"] / (1 + added["imp"])) * lot * added["q"]) <= 0:
                exit_px, why = ep_mark * (1 - SLIP), "stop"
            if hit and why is None:
                exit_px, why = P["price"](tgt_spot, P["whens"][j]) * (1 - SLIP), "target"
            if exit_px is not None:
                added["x"], added["why"], added["xj"] = exit_px, why, j
                break
        if added is None: continue
        if "x" not in added:
            added["x"], added["why"], added["xj"] = P["mark"][-1] * (1 - SLIP), "with trade", len(P["mark"]) - 1
        qa = added["q"] * lot
        x = added["x"] * (1 - added["imp"])
        orders = 2 * math.ceil(qa / PB.FREEZE[sym])
        pnl = (x - added["p"]) * qa - I.charges(added["p"], x, qa, orders=orders, exch_pct=I.exch_rate(sym))
        delta_base = 0.0
        if mode == "all-out" and added["why"] in ("target", "stop"):
            q0 = lots * lot; imp0 = impact(sym, q0)
            xb = added["x"] * (1 - imp0)
            alt = (xb - r.prem_in) * q0 - I.charges(r.prem_in, xb, q0, orders=2 * math.ceil(q0 / PB.FREEZE[sym]), exch_pct=I.exch_rate(sym))
            delta_base = alt - float(r.pnl)
        rows.append({"date": r.date, "sym": sym, "q": added["q"], "pnl": pnl + delta_base, "add_pnl": pnl, "why": added["why"], "R": r.R})
    return pd.DataFrame(rows)


def curve_dd(R, X, cap):
    eq = R.groupby("date").equity.last()
    add = X.groupby("date").pnl.sum().reindex(eq.index).fillna(0).cumsum() if len(X) else 0
    cur = pd.concat([pd.Series([float(cap)]), eq + add], ignore_index=True)
    return float((cur / cur.cummax() - 1).min() * 100), float(cur.iloc[-1])


def main():
    out = {}
    CAPS = [200_000, 1_000_000, 5_000_000]
    GRID = list(itertools.product((0.95,), (10, 15, 20, 25), ("+100%", "+300%", "to cap"), ("own", "be", "none"), ("add-only", "all-out")))
    for cap in CAPS:
        for per in ("2023-24", "2025-26", "full", "fwd"):
            a, b = R3.PER[per]
            R0 = L.replay(cap, a, b, T=R3.T0, A=R3.A0, H=R3.H0, raw=True, max_lots=500)
            s0 = PB._summary(R0, cap)
            Rb = R0[R0["skipped"].isna()].reset_index(drop=True) if "skipped" in R0 else R0
            Rb = attach(Rb, (R3.T0, R3.A0, R3.H0))
            t = time.time()
            res = {"cap500_no_add": {"final": s0["final"], "dd": s0["max_dd_pct"], "trades": s0["trades"], "max_lots": s0["max_lots"]}}
            s50 = PB._summary(L.replay(cap, a, b, T=R3.T0, A=R3.A0, H=R3.H0, raw=True, max_lots=50), cap)
            res["today_cap50"] = {"final": s50["final"], "dd": s50["max_dd_pct"], "trades": s50["trades"], "max_lots": s50["max_lots"]}
            for trig, X, rule, stop, mode in GRID:
                O_ = overlay(Rb, 500, trig, X, rule, stop, mode)
                dd, fin = curve_dd(Rb, O_, cap) if len(O_) else (s0["max_dd_pct"], s0["final"])
                res[f"{trig}|{X}|{rule}|{stop}|{mode}"] = {"final": round(fin), "dd": round(dd, 1), "n": int(len(O_)), "win": round(float((O_.add_pnl > 0).mean()), 3) if len(O_) else None,
                                                           "avg_q": round(float(O_.q.mean()), 1) if len(O_) else 0, "add_total": round(float(O_.pnl.sum())) if len(O_) else 0,
                                                           "target_share": round(float((O_.why == "target").mean()), 3) if len(O_) else None}
            out[f"{cap}|{per}"] = res
            best = sorted(((k, v) for k, v in res.items() if "|" in k), key=lambda kv: -kv[1]["final"])[:3]
            print(f"cap {cap/1e5:.0f}L {per}: today(cap50) {res['today_cap50']['final']/1e5:.1f}L ({res['today_cap50']['dd']}%) | cap500 {res['cap500_no_add']['final']/1e5:.1f}L ({res['cap500_no_add']['dd']}%) | best add-on " +
                  " ; ".join(f"{k} {v['final']/1e5:.1f}L ({v['dd']}%) n={v['n']} win {v['win']}" for k, v in best) + f" [{time.time()-t:.0f}s]", flush=True)
    json.dump(out, open(f"/tmp/r103_b2_imp{IMP_MULT:g}.json", "w"), indent=1)


if __name__ == "__main__":
    main()
