"""Part L: deep check of the one candidate that met the bar in part K - 'TTM squeeze release' (Bollinger band inside the Keltner
channel, then out; direction = momentum) on 15-min bars as an extra half-size slot.
  1 what it is: trades, Rs per lot by year, what the replay actually takes, year by year, other starting capitals
  2 neighbours: band settings, exits, trades a day, all three indices, full and half size
  3 execution stress: enter one bar later; +0.5% worse fill on each side of the new trades
  4 random-entry control with the same exit and the same number of signals
  5 block bootstrap of trading days (20-day blocks): how often is 'today + squeeze' ahead of 'today' on the same resampled history
Usage: python research/round101/l_squeeze.py [stage ...]   (stages 1-5, default all)"""
import sys, json, time, pickle; sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from lib import *
STAGES = [int(x) for x in sys.argv[1:] if x.isdigit()] or [1, 2, 3, 4, 5]
sys.argv = [sys.argv[0], "15m"]
import b_families as bf
bf.TF, bf.STEP = "15m", 15
CELLS = [(cap, p) for cap in (200_000, 20_000) for p in PER]
T0, A0, H0 = base_sets()
ref = {x: replay(x[0], *PER[x[1]]) for x in CELLS}
D = {s: bar_of(s, "15m") for s in SYMS}
RES_F = OUT + f"/l_squeeze{os.environ.get('L_TAG', '')}.json"
res = json.load(open(RES_F)) if os.path.exists(RES_F) else {}
res["today"] = {f"{c}|{p}": v for (c, p), v in ref.items()}
EX = {"signal": dict(k=1.5, tp=0.0, sig=True), "3R": dict(k=2.0, tp=3.0, sig=False), "2R": dict(k=1.5, tp=2.0, sig=False), "trail": dict(k=1.0, tp=0.0, sig=False, trail=1.5)}


def sh(x, n=1):
    y = np.roll(np.asarray(x, float), n); y[:n] = np.nan; return y


def squeeze(sym, n=20, bb=2.0, kc=1.5):
    df, o, h, l, c, hm, day = D[sym]
    e = ema(c, n); a10 = atr(h, l, c, 10); m = bf.roll(c, n, "mean"); s = bf.roll(c, n, "std")
    on = (m + bb * s < e + kc * a10) & (m - bb * s > e - kc * a10)
    rel = (~on) & (sh(on.astype(float)) > 0); mom = c - (bf.roll(h, n, "max") + bf.roll(l, n, "min") + 2 * m) / 4
    return rel & (mom > 0), rel & (mom < 0), mom < 0, mom > 0


def piece(sym, sig, ex="signal", pdy=3, delay=0, slip=0.0, label="sq"):
    df, o, h, l, c, hm, day = D[sym]; L, S, xL, xS = sig; e = EX[ex]; a14 = atr(h, l, c, 14)
    if delay:
        L = np.nan_to_num(sh(L.astype(float), delay)) > 0; S = np.nan_to_num(sh(S.astype(float), delay)) > 0
    rows = simulate(o, h, l, c, hm, day, L, S, e["k"] * a14, tp_R=e["tp"], xL=xL if e["sig"] else None, xS=xS if e["sig"] else None, per_day=pdy, step=15, trail_R=e.get("trail", 0))
    T = price(sym, df, rows, 15); P = to_piece(sym, T, "r101 squeeze " + label)
    if slip:
        P = P.assign(prem_in=P.prem_in * (1 + slip), prem_out=np.maximum(P.prem_out * (1 - slip), 0.05))
    own = {"n": len(T), "design": float(T[T.date < "2025"].rs.sum()), "unseen": float(T[T.date >= "2025"].rs.sum()), "years": T.groupby(T.date.str[:4]).rs.sum().round(0).to_dict(),
           "n_years": T.groupby(T.date.str[:4]).size().to_dict(), "win": float((T.rs > 0).mean()), "avgR": float(P.R.mean())}
    return P, own


def half(P, m=2):
    return [P.assign(risk_lot=P.risk_lot * m, risk_lot_capped=P.risk_lot_capped * m)]


def judge(X, cells=CELLS, **over):
    row = {x: replay(x[0], *PER[x[1]], extra=X, **over) for x in cells}
    return row, sum(row[x]["final"] > ref[x]["final"] for x in cells), all(row[x]["dd"] >= ref[x]["dd"] - 2 for x in cells)


def line(tag, row, b, f, own=None):
    print(f"   {tag:<50}" + "".join(f"{row[x]['final'] / 1e5:>8.2f}{'*' if row[x]['final'] > ref[x]['final'] else ' '}({row[x]['dd']:>4.0f})" for x in CELLS) + f"  {b}/6 fall {'ok' if f else 'deeper'}"
          + (f"  own {own['design']:+,.0f}/{own['unseen']:+,.0f} n={own['n']}" if own else "") + ("  PASS" if b == 6 and f else ""), flush=True)


def pack(row):
    return {f"{c}|{p}": v for (c, p), v in row.items()}


def save():
    json.dump(res, open(RES_F, "w"), default=float)


print("today" + " " * 48 + "".join(f"{ref[x]['final'] / 1e5:>8.2f} ({ref[x]['dd']:>4.0f})" for x in CELLS), flush=True)
_c = (os.environ.get("L_CAND") or "SENSEX,20,2.0,1.5,signal,3").split(",")
CSYM, CN, CBB, CKC, CEX, CPDY = _c[0], int(_c[1]), float(_c[2]), float(_c[3]), _c[4], int(_c[5])
CSIG = squeeze(CSYM, CN, CBB, CKC)
CP, COWN = piece(CSYM, CSIG, CEX, CPDY)
CNAME = f"{CSYM} squeeze n{CN} bb{CBB} kc{CKC}, exit {CEX}, {CPDY}/day, half size"
print("candidate:", CNAME, flush=True)

if 1 in STAGES:
    print("\n1. the candidate:", CNAME)
    P, own = CP, COWN; print("   alone, 1 lot:", json.dumps(own))
    row, b, f = judge(half(P)); line("candidate, half size", row, b, f, own)
    res["cand"] = {"own": own, "cells": pack(row), "better": b, "fall_ok": f, "years": {}, "caps": {}}
    for y, (a_, b_) in YEARS.items():
        for cap in (200_000, 20_000):
            t = replay(cap, a_, b_); c_ = replay(cap, a_, b_, extra=half(P)); res["cand"]["years"][f"{cap}|{y}"] = [t, c_]
            print(f"   {y} Rs {cap:>7,}: today {t['final'] / 1e5:>7.2f}L ({t['dd']:.0f}%)   with squeeze {c_['final'] / 1e5:>7.2f}L ({c_['dd']:.0f}%)  {'better' if c_['final'] > t['final'] else 'WORSE'}", flush=True)
    for cap in (25_000, 50_000, 100_000, 500_000, 1_000_000):
        for p, (a_, b_) in PER.items():
            t = replay(cap, a_, b_); c_ = replay(cap, a_, b_, extra=half(P)); res["cand"]["caps"][f"{cap}|{p}"] = [t, c_]
            print(f"   Rs {cap:>9,} {p:<8}: today {t['final'] / 1e5:>8.2f}L ({t['dd']:.0f}%)   with squeeze {c_['final'] / 1e5:>8.2f}L ({c_['dd']:.0f}%)  {'better' if c_['final'] > t['final'] else 'WORSE'}{'' if c_['dd'] >= t['dd'] - 2 else '  fall deeper'}", flush=True)
    for cap in (200_000, 20_000):
        R = replay(cap, *PER["full"], extra=half(P), raw=True); tk = R[R.get("skipped").isna()] if "skipped" in R else R
        g = tk[tk.strategy.str.contains("squeeze")]; sk = R[R.strategy.str.contains("squeeze") & R.get("skipped").notna()] if "skipped" in R else R.iloc[:0]
        print(f"   Rs {cap:,} full: squeeze trades taken {len(g)} (skipped {len(sk)}: {sk.skipped.value_counts().to_dict() if len(sk) else {}}), their P&L {g.pnl.sum():+,.0f}, avg lots {g.lots.mean():.1f}; all trades {len(tk)}", flush=True)
        res["cand"][f"taken|{cap}"] = {"n": int(len(g)), "pnl": float(g.pnl.sum()), "skipped": int(len(sk))}
    save()

if 2 in STAGES:
    print("\n2. neighbours")
    res["neigh"] = []
    for sym in SYMS:
        for (n, bb, kc) in ((20, 2.0, 1.5), (20, 2.0, 1.0), (20, 2.0, 2.0), (20, 1.5, 1.5), (20, 2.5, 1.5), (20, 1.5, 1.0), (14, 2.0, 1.5), (30, 2.0, 1.5)):
            sig = squeeze(sym, n, bb, kc)
            for ex in ("signal", "3R", "2R", "trail"):
                for pdy in (1, 3):
                    P, own = piece(sym, sig, ex, pdy)
                    if own["n"] < 60:
                        continue
                    for nm, m in (("half", 2), ("full", 1)):
                        row, b, f = judge(half(P, m)); tag = f"{sym[:6]} n{n} bb{bb} kc{kc} {ex} {pdy}/d {nm}"
                        line(tag, row, b, f, own)
                        res["neigh"].append({"tag": tag, "sym": sym, "n": n, "bb": bb, "kc": kc, "exit": ex, "pdy": pdy, "size": nm, "better": b, "fall_ok": f, "own": own, "cells": pack(row)})
        save()
    for sym in SYMS:
        for nm in ("half", "full"):
            X = [x for x in res["neigh"] if x["sym"] == sym and x["size"] == nm]
            print(f"   {sym} {nm}: versions {len(X)}, pass {sum(x['better'] == 6 and x['fall_ok'] for x in X)}, better in all six {sum(x['better'] == 6 for x in X)}, 5+ {sum(x['better'] >= 5 for x in X)}, "
                  f"fall ok {sum(x['fall_ok'] for x in X)}, avg cells {np.mean([x['better'] for x in X]):.2f}, alone + in both {sum(x['own']['design'] > 0 and x['own']['unseen'] > 0 for x in X)}")

if 3 in STAGES:
    print("\n3. execution stress on the candidate (half size)")
    res["stress"] = {}
    for tag, kw in (("as tested", {}), ("enter one 15-min bar later", {"delay": 1}), ("+0.5% worse fill each side", {"slip": 0.005}), ("+1% worse fill each side", {"slip": 0.01}), ("one bar later and +0.5%", {"delay": 1, "slip": 0.005})):
        P, own = piece(CSYM, CSIG, CEX, CPDY, **kw); row, b, f = judge(half(P)); line(tag, row, b, f, own)
        res["stress"][tag] = {"better": b, "fall_ok": f, "cells": pack(row), "own": own}
    save()

if 4 in STAGES:
    NR = int(os.environ.get("L_NR") or 100)
    print(f"\n4. random-entry control: {NR} pieces, same index, same number of signals, same exit, same trades a day, half size")
    df, o, h, l, c, hm, day = D[CSYM]; L0, S0, xL, xS = CSIG; ok = (hm >= 9 * 60 + 20) & (hm <= 14 * 60 + 45) & np.isfinite(atr(h, l, c, 14))
    n_sig = int((L0 & ok).sum() + (S0 & ok).sum()); idx = np.where(ok)[0]; rng = np.random.default_rng(1011); res["null"] = res.get("null", [])
    for r in range(len(res["null"]), NR):
        pick = rng.choice(idx, n_sig, replace=False); side = rng.random(n_sig) < 0.5
        L = np.zeros(len(c), bool); S = np.zeros(len(c), bool); L[pick[side]] = True; S[pick[~side]] = True
        P, own = piece(CSYM, (L, S, xL, xS), CEX, CPDY, label="rand"); row, b, f = judge(half(P))
        res["null"].append({"better": b, "fall_ok": f, "own": own, "cells": pack(row)})
        if r % 10 == 9:
            X = res["null"]; print(f"   {len(X)} random: pass {sum(x['better'] == 6 and x['fall_ok'] for x in X)}, all six {sum(x['better'] == 6 for x in X)}, 5+ {sum(x['better'] >= 5 for x in X)}, avg cells {np.mean([x['better'] for x in X]):.2f}, "
                               f"avg trades {np.mean([x['own']['n'] for x in X]):.0f}", flush=True); save()
    X = res["null"]; cand = res.get("cand", {}).get("cells")
    if cand:
        for c_ in [f"{a}|{b}" for a, b in CELLS]:
            print(f"   {c_}: candidate {cand[c_]['final'] / 1e5:.2f}L beats {np.mean([cand[c_]['final'] > x['cells'][c_]['final'] for x in X]) * 100:.0f}% of random; random median {np.median([x['cells'][c_]['final'] for x in X]) / 1e5:.2f}L")
    save()

if 5 in STAGES:
    NB, BL = 200, 20
    print(f"\n5. block bootstrap: {NB} resampled histories (blocks of {BL} trading days), today vs today + squeeze (half size), same resample for both")
    P, own = CP, COWN; X = half(P)[0]
    days = sorted(set(T0.date) | set(X.date)); cal = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2023-01-02", periods=len(days) + BL)]
    by = lambda F: {d: g for d, g in F.groupby("date")} if F is not None else None
    G = [by(T0), by(A0), by(H0), by(X)]
    rng = np.random.default_rng(55); out = []
    for b in range(NB):
        starts = rng.integers(0, len(days) - BL + 1, size=len(days) // BL + 1); seq = [days[s + k] for s in starts for k in range(BL)][:len(days)]
        def build(g):
            if g is None:
                return None
            parts = [g[d].assign(date=cal[i]) for i, d in enumerate(seq) if d in g]
            return pd.concat(parts, ignore_index=True) if parts else None
        Tb, Ab, Hb, Xb = [build(g) for g in G]
        r_ = {}
        for cap in (200_000, 20_000):
            t = replay(cap, cal[0], cal[-1], T=Tb, A=Ab, H=Hb); c_ = replay(cap, cal[0], cal[-1], T=Tb, A=Ab, H=Hb, extra=[Xb] if Xb is not None else None)
            r_[cap] = (t["final"], t["dd"], c_["final"], c_["dd"])
        out.append(r_)
        if b % 25 == 24:
            for cap in (200_000, 20_000):
                w = np.mean([o_[cap][2] > o_[cap][0] for o_ in out]); md = np.median([o_[cap][2] / max(o_[cap][0], 1) for o_ in out]); dd = np.median([o_[cap][3] - o_[cap][1] for o_ in out])
                print(f"   {len(out)} runs Rs {cap:>7,}: squeeze ahead in {w * 100:.0f}%; median ratio of final money {md:.3f}; median change in worst fall {dd:+.1f} pts", flush=True)
            res["boot"] = {str(cap): [list(o_[cap]) for o_ in out] for cap in (200_000, 20_000)}; save()
print("done")
