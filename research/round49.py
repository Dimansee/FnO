"""Round 49 (28 Oct 2026): do the three reel ideas help TODAY's Autopilot rules?

Reels tested stand-alone the same night (all rejected as stand-alone systems):
  liquidity / SMC (swing H/L, sweeps, BOS, CHoCH)   -> round 46
  trend -> consolidation -> POC -> manipulation      -> round 47
  put-call parity arbitrage                          -> round 48 (no usable signal)
Here every usable piece is attached to the live rule set instead.

Trades: round 43's generator (fno.backtest.run "autopilot", big-run skip on, 1 lot, 1 ITM, Nifty + Bank Nifty + Sensex,
Jan 2023 -> latest). Every feature uses only 5m bars that closed before the entry time.

A. Filters (keep / skip trades), judged like round 43: per-lot 2023-25 and 2026 both better, shuffle p < 0.05,
   plus the Rs 2 lakh compounding account replay.
B. Exit: book at the first untouched liquidity pool in the trade's direction (>= 0.5 ATR away) if the index reaches it
   before the trade's own exit (option P&L re-priced with the trade's own realised delta; approximation).
C. Add-on setups from the stand-alone tests' best leads, run on research/engine.py (1 lot, 1 ITM, calibrated
   premiums, costs): PDH/PDL + opening-range sweep with CHoCH (3R), and a 60-min box breakout in an HH/HL trend (3R).
Run: python round49.py -> round49_results.json
"""
import os, sys, json, pickle, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
import round25_harness as H
import round43 as R43

SYMS = ["NIFTY", "BANKNIFTY", "SENSEX"]
LOT = {"NIFTY": 65, "BANKNIFTY": 30, "SENSEX": 20}


def fractals(h, l, N):
    sh, sl = [], []
    for i in range(N, len(h) - N):
        if h[i] > h[i - N:i].max() and h[i] >= h[i + 1:i + N + 1].max():
            sh.append((i, i + N, h[i]))
        if l[i] < l[i - N:i].min() and l[i] <= l[i + 1:i + N + 1].min():
            sl.append((i, i + N, l[i]))
    return sh, sl


def tpo_poc(h, l, bins=30):
    lo, hi = l.min(), h.max()
    if hi <= lo:
        return (hi + lo) / 2
    e = np.linspace(lo, hi, bins + 1); w = np.zeros(bins)
    for a, b in zip(h, l):
        i0 = max(0, np.searchsorted(e, b, "right") - 1); i1 = max(i0, min(bins - 1, np.searchsorted(e, a, "left") - 1))
        w[i0:i1 + 1] += 1 / (i1 - i0 + 1)
    k = int(np.argmax(w)); return (e[k] + e[k + 1]) / 2


def features(T):
    out = []
    for sym in SYMS:
        c = H.load(sym).copy()
        tr = pd.concat([c.high - c.low, (c.high - c.close.shift()).abs(), (c.low - c.close.shift()).abs()], axis=1).max(axis=1)
        c["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
        days = {d: g for d, g in c.groupby(c.index.date)}
        dkeys = sorted(days)
        prev = {dkeys[i]: dkeys[i - 1] for i in range(1, len(dkeys))}
        for idx, r in T[T.sym == sym].iterrows():
            d = pd.Timestamp(r.date).date()
            if d not in days or d not in prev:
                continue
            g, y = days[d], days[prev[d]]
            et = pd.Timestamp(f"{r.date} {r.entry_time}")
            today = g[g.index + pd.Timedelta(minutes=5) <= et]
            if len(today) < 3:
                continue
            s = r.sgn
            buf = pd.concat([y, today])
            h, l, cl = buf.high.values, buf.low.values, buf.close.values
            n0 = len(y); n = len(buf); a = buf.atr.values[-1]; px = cl[-1]
            # pools (unbroken up to each bar): PDH/PDL, opening range, swings today + yesterday (N=2)
            sh, sl = fractals(h, l, 2)
            pools = [(y.high.max(), 1, n0), (y.low.min(), -1, n0)]
            if len(today) >= 3:
                pools += [(today.high.values[:3].max(), 1, n0 + 3), (today.low.values[:3].min(), -1, n0 + 3)]
            pools += [(p, 1, cf + 1) for (_, cf, p) in sh] + [(p, -1, cf + 1) for (_, cf, p) in sl]
            sweep_with = sweep_against = False
            room = np.inf
            for (p, side, av) in pools:
                if av >= n:
                    continue
                seg_h, seg_l = h[av:], l[av:]
                hit = np.where(seg_h > p)[0] if side > 0 else np.where(seg_l < p)[0]
                if len(hit):
                    k = av + hit[0]
                    back = cl[k] < p if side > 0 else cl[k] > p
                    if back and k >= n - 6 and k >= n0:
                        # buy-side pool swept and rejected = bearish; sell-side = bullish
                        if -side == s:
                            sweep_with = True
                        else:
                            sweep_against = True
                elif side == s and s * (p - px) > 0:
                    room = min(room, s * (p - px) / a)
            # structure (N=3) on the 2-day buffer
            sh3, sl3 = fractals(h, l, 3)
            hs = [p for (_, cf, p) in sh3 if cf < n][-2:]; ls = [p for (_, cf, p) in sl3 if cf < n][-2:]
            st = 0
            if len(hs) == 2 and len(ls) == 2:
                st = 1 if (hs[1] > hs[0] and ls[1] > ls[0]) else (-1 if (hs[1] < hs[0] and ls[1] < ls[0]) else 0)
            # BOS with the trade in the last 12 bars; CHoCH against in the last 6 (internal N=1)
            bos = False
            ref = [(cf, p) for (_, cf, p) in (sh if s > 0 else sl)]
            for k in range(max(n0, n - 12), n):
                prior = [p for (cf, p) in ref if cf < k]
                if prior and s * (cl[k] - prior[-1]) > 0 and s * (cl[k - 1] - prior[-1]) <= 0:
                    bos = True
            ih, il = fractals(h, l, 1)
            choch_against = False
            iref = [(cf, p) for (_, cf, p) in (il if s > 0 else ih)]
            for k in range(max(n0, n - 6), n):
                prior = [p for (cf, p) in iref if cf < k]
                if prior and s * (cl[k] - prior[-1]) < 0 and s * (cl[k - 1] - prior[-1]) >= 0:
                    choch_against = True
            # consolidation: last 6 bars inside 1.5 ATR; box breakout / manipulation in the last 12 bars
            last6 = today.tail(6)
            in_box = len(last6) == 6 and (last6.high.max() - last6.low.min()) <= 1.5 * a
            box_brk = manip = False
            tv = today.reset_index(drop=True)
            for b in range(5, len(tv) - 1):
                if b < len(tv) - 13:
                    continue
                bx = tv.iloc[b - 5:b + 1]
                hi_, lo_ = bx.high.max(), bx.low.min()
                if hi_ - lo_ > 1.5 * tv.atr.iloc[b]:
                    continue
                after = tv.iloc[b + 1:]
                go = (after.close > hi_) if s > 0 else (after.close < lo_)
                out_ = (after.low < lo_) if s > 0 else (after.high > hi_)
                if go.any():
                    box_brk = True
                    first_go = go.idxmax()
                    if out_.loc[:first_go].any():
                        manip = True
            poc_today = tpo_poc(today.high.values, today.low.values)
            poc_y = tpo_poc(y.high.values, y.low.values)
            out.append({"i": idx, "sweep_with": sweep_with, "sweep_against": sweep_against, "room_atr": room,
                        "struct": st * s, "bos_with": bos, "choch_against": choch_against, "in_box": in_box,
                        "box_brk": box_brk, "manip": manip, "poc_side": int(np.sign(px - poc_today) * s),
                        "ypoc_side": int(np.sign(px - poc_y) * s), "atr": a})
    F = pd.DataFrame(out).set_index("i")
    return T.join(F, how="left")


def pool_exit(T):
    """B: exit at the first untouched pool in the trade's direction (>= 0.5 ATR) if touched before the trade's own exit."""
    pnl_new = T.pnl.copy(); hit_n = 0
    for sym in SYMS:
        c = H.load(sym)
        for idx, r in T[T.sym == sym].iterrows():
            if not np.isfinite(r.room_atr) or r.room_atr < 0.5:
                continue
            target = r.spot_in + r.sgn * r.room_atr * r.atr
            et, xt = pd.Timestamp(f"{r.date} {r.entry_time}"), pd.Timestamp(f"{r.date} {r.exit_time}")
            path = c[(c.index >= et) & (c.index < xt)]
            if path.empty:
                continue
            touched = (path.high >= target).any() if r.sgn > 0 else (path.low <= target).any()
            if not touched:
                continue
            mv = r.spot_out - r.spot_in
            delta = (r.prem_out - r.prem_in) / mv if abs(mv) > 0.3 * r.atr else 0.6
            delta = float(np.clip(abs(delta), 0.3, 0.9))
            lot = LOT[sym]
            charges = (r.prem_out - r.prem_in) * lot - r.pnl
            pnl_new[idx] = delta * abs(target - r.spot_in) * lot - charges
            hit_n += 1
    return pnl_new, hit_n


def addon_trades():
    """C: stand-alone leads from the earlier tests, as extra trades (engine.py harness, 1 lot, 1 ITM)."""
    import random
    os.environ["FNO_CALIB"] = "1"
    import round46_liquidity as LQ, round47_amd_poc as AP, engine as E
    rows = []
    for sym in ("NIFTY", "BANKNIFTY"):
        days = E.load_days(sym); meta = E.META[sym]
        # C1 liquidity: key pools, CHoCH, 3R, full day
        cfg = {"setup": "A", "N": 2, "pools": "key", "confirm": "choch", "htf": "none", "last": LQ.LAST_DAY, "tgt": 3}
        for nd in range(1, len(days)):
            d = days[nd]; busy = -1; taken = 0
            for sg in LQ.signals_A(d, days[nd - 1], cfg):
                if sg.i <= busy or taken >= 2:
                    continue
                k, px, _ = E.simulate(d, sg, meta, {})
                tr = E.trade_pnl(d, sg, k, px, meta, {"otm": -1, "one_lot": True})
                busy, taken = k, taken + 1
                if tr:
                    rows.append(_row(d, sg, tr, sym, "Liquidity sweep + CHoCH", meta))
        # C2 box breakout in HH/HL trend, 60-min box, 3R (Nifty lead; Bank Nifty run too)
        adays = [AP.D(d.t, d.o, d.h, d.l, d.c, [1] * len(d.c), d.atr) for d in days]
        for nd in range(1, len(days)):
            y, d = adays[nd - 1], adays[nd]; ed = days[nd]
            Hh, Ll, Cc, Oo = list(y.h) + list(d.h), list(y.l) + list(d.l), list(y.c) + list(d.c), list(y.o) + list(d.o)
            A_, T_ = list(y.atr) + list(d.atr), [-1] * len(y.c) + list(d.t)
            s0 = len(y.c); busy = -1; taken = 0
            for s in AP.setups(Hh, Ll, Cc, Oo, [1] * len(Cc), A_, T_, s0, {"K": 12, "m": 2.5, "trend": True, "mode": "plain"}, False):
                if s["i"] <= busy or taken >= 2:
                    continue
                risk = abs(s["px"] - s["stop"])
                if risk < 0.3 * A_[s["i"]] or risk > 4 * A_[s["i"]]:
                    continue
                tgt = s["px"] + s["side"] * 3 * risk
                k, ex = AP.walk(Hh, Ll, Cc, Oo, T_, s["i"], s["side"], s["px"], s["stop"], tgt)
                sg = E.Sig(s["i"] - s0, s["side"], s["stop"], tgt, "", 0, 0, "box", s["px"])
                tr = E.trade_pnl(ed, sg, k - s0, ex, meta, {"otm": -1, "one_lot": True})
                busy, taken = k, taken + 1
                if tr:
                    rows.append(_row(ed, sg, tr, sym, "Box breakout in trend", meta))
    return pd.DataFrame(rows)


def _row(d, sg, tr, sym, name, meta):
    hm = lambda m: f"{m // 60:02d}:{m % 60:02d}"
    risk_lot = max(0.6 * abs((sg.px if sg.px is not None else d.c[sg.i]) - sg.stop) * meta["lot"], 1.0)
    return {"date": str(d.d), "entry_time": hm(tr["in"]), "exit_time": hm(tr["out"]), "side": "CALL" if sg.side > 0 else "PUT",
            "sgn": sg.side, "prem_in": tr["prem_in"], "prem_out": tr["prem_out"], "pnl": tr["pnl"], "risk_lot": risk_lot,
            "strategy": name, "sym": sym, "score": 3}


if __name__ == "__main__":
    T = pickle.load(open(R43.PKL, "rb")) if os.path.exists(R43.PKL) else R43.gen()
    T = features(T)
    base_pl, base_acct = R43.per_lot(T), R43.replay(T)
    print("BASE", base_pl["train"], base_pl["y2026"], base_acct, flush=True)
    f = T.fillna({"room_atr": np.inf})
    FILT = {
        "liq: keep only after a sweep in our favour (last 30 min)": f.sweep_with == True,
        "liq: skip after a sweep against us (last 30 min)": f.sweep_against != True,
        "liq: skip if a pool is < 0.5 ATR ahead": ~(f.room_atr < 0.5),
        "liq: skip if a pool is < 1 ATR ahead": ~(f.room_atr < 1.0),
        "structure: keep only HH/HL agreeing": f.struct == 1,
        "structure: skip when HH/HL is against": f.struct != -1,
        "BOS: keep only after a BOS our way (last 60 min)": f.bos_with == True,
        "CHoCH: skip after a CHoCH against (last 30 min)": f.choch_against != True,
        "box: skip entries inside a 30-min consolidation": f.in_box != True,
        "box: keep only after a box breakout our way (last 60 min)": f.box_brk == True,
        "manipulation: keep only after sweep-then-breakout": f.manip == True,
        "POC: keep only on our side of today's POC": f.poc_side == 1,
        "POC: keep only on our side of yesterday's POC": f.ypoc_side == 1,
        "combo: structure not against + no sweep against": (f.struct != -1) & (f.sweep_against != True),
    }
    out = {"baseline": {"per_lot": base_pl, "account": base_acct}, "filters": {}, "exit": {}, "addons": {}}
    for nm, keep in FILT.items():
        keep = keep.fillna(True).astype(bool)
        X = T[keep]; pl = R43.per_lot(X); acct = R43.replay(X); p = R43.shuffle_p(T, keep)
        both = pl["train"] > base_pl["train"] and pl["y2026"] > base_pl["y2026"]
        out["filters"][nm] = {"removed": int((~keep).sum()), "per_lot": pl, "account": acct, "shuffle_p": p, "both_better": both}
        print(f"{nm:62s} rm {int((~keep).sum()):4d} | {pl['train']:8d} {pl['y2026']:8d} | acct {acct['final']:>9} {acct['dd']:6} | p {p} {'BOTH' if both else ''}", flush=True)
    pn, hits = pool_exit(T)
    X = T.copy(); X["pnl"] = pn
    pl, acct = R43.per_lot(X), R43.replay(X)
    out["exit"]["book at next liquidity pool"] = {"trades_changed": hits, "per_lot": pl, "account": acct}
    print("EXIT pool", hits, pl["train"], pl["y2026"], acct, flush=True)
    A = addon_trades()
    A["wd"] = pd.to_datetime(A.date).dt.weekday
    for nm, g in A.groupby("strategy"):
        X = pd.concat([T[list(T.columns.intersection(g.columns))], g], ignore_index=True)
        cor = pd.concat([T.groupby("date").pnl.sum(), g.groupby("date").pnl.sum()], axis=1).fillna(0).corr().iloc[0, 1]
        pl_add, pl_all, acct = R43.per_lot(g), R43.per_lot(X), R43.replay(X)
        out["addons"][nm] = {"alone_per_lot": pl_add, "with_autopilot_per_lot": pl_all, "account": acct, "daily_corr": round(float(cor), 3)}
        print("ADDON", nm, pl_add["n"], pl_add["train"], pl_add["y2026"], "| combined", pl_all["train"], pl_all["y2026"], acct, "corr", round(cor, 3), flush=True)
    json.dump(out, open(os.path.join(HERE, "round49_results.json"), "w"), indent=1, default=str)
    pickle.dump(T, open("/tmp/round49_trades.pkl", "wb"))
    print("saved")
