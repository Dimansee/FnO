"""Round 51 (28 Oct 2026): ICT / SMC concepts as filters on TODAY's Autopilot trades (round 43 generator + round 49).

Each Autopilot trade gets, from 5m bars closed before its entry (today only, engine.py days):
  in_fvg_with     entry sits inside a still-open FVG of its own direction (gap support for a CALL)
  fvg_against     an open opposite FVG lies within 1 ATR ahead (gap resistance)
  ob_with         entry within 0.5 ATR of a fresh order block of its own direction
  sd_against      a fresh opposite supply/demand zone lies within 1 ATR ahead
  discount        CALL below / PUT above the midpoint of the day's range so far (premium-discount)
  killzone        entry 09:15-10:45 or 13:30-15:00
  smt_with        SMT divergence in its favour in the last 30 min (partner index)
  eq_ahead        equal highs (CALL) / lows (PUT) within 1 ATR ahead (a liquidity magnet)
  displacement    a >= 1 ATR body candle in its direction in the last 3 bars
Judged like round 43: per-lot 2023-25 and 2026 both better, shuffle p < 0.05, Rs 2L account replay.
Run: python round51_smc_filters.py -> round51_results.json
"""
import os, sys, json, pickle, itertools, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
os.environ["FNO_CALIB"] = "1"
import numpy as np, pandas as pd
import round43 as R43
import round50_smc as M
import engine as E

SYMS = ["NIFTY", "BANKNIFTY", "SENSEX"]


def feats(T):
    M.ensure_sensex_5m()
    D = {s: E.load_days(s) for s in SYMS}
    rows = []
    for sym in SYMS:
        days = D[sym]; pdays = D[M.PARTNER[sym]]
        di = {d.d: i for i, d in enumerate(days)}; pi = {d.d: i for i, d in enumerate(pdays)}
        cache = {}
        for idx, r in T[T.sym == sym].iterrows():
            dd = pd.Timestamp(r.date).date()
            if dd not in di or di[dd] == 0:
                continue
            nd = di[dd]
            if nd not in cache:
                cx = M.Ctx(days[nd], days[nd - 1])
                pc = M.Ctx(pdays[pi[dd]], pdays[pi[dd] - 1]) if dd in pi and pi[dd] > 0 else None
                smt = M.gen("smt", cx, pc) if pc else []
                cache[nd] = (cx, M.fvgs(cx, 0.5), smt)
            cx, G, smt = cache[nd]
            hm = int(r.entry_time[:2]) * 60 + int(r.entry_time[3:])
            i = max([k for k, t in enumerate(cx.d.t) if t + 5 <= hm], default=None)
            if i is None or i < 2:
                continue
            s, px, a = r.sgn, cx.c[i], cx.a[i]
            h, l, c, o = cx.h, cx.l, cx.c, cx.o

            def still_open(k, gs, edge, far):
                seg = range(k + 1, i + 1)
                return not any(((l[j] <= far) if gs > 0 else (h[j] >= far)) for j in seg)
            in_fvg = any(gs == s and k <= i and still_open(k, gs, edge, far) and min(edge, far) <= px <= max(edge, far) + 0.25 * a
                         for k, gs, edge, far, _ in G)
            fvg_ag = any(gs == -s and k <= i and still_open(k, gs, edge, far) and 0 <= s * (min(edge, far) if s > 0 else max(edge, far)) - s * px <= a
                         for k, gs, edge, far, _ in G)
            # order blocks / s-d zones formed before i
            ob_with = sd_ag = False
            for j in range(3, i + 1):
                body = c[j] - o[j]
                if abs(body) >= a and np.sign(body) == s:
                    ob = next((k for k in range(j - 1, max(j - 6, -1), -1) if s * (c[k] - o[k]) < 0), None)
                    if ob is not None:
                        edge = h[ob] if s > 0 else l[ob]
                        if abs(px - edge) <= 0.5 * a and s * (px - (l[ob] if s > 0 else h[ob])) > 0:
                            ob_with = True
                if abs(body) >= 1.2 * a and np.sign(body) == -s and j >= 2:
                    b = j - 1
                    if abs(c[b] - o[b]) <= 0.5 * (h[b] - l[b] + 1e-9):
                        prox = l[b] if s > 0 else h[b]           # supply zone bottom for a CALL
                        if 0 <= s * (prox - px) <= a and not any(((h[k] >= prox) if s > 0 else (l[k] <= prox)) for k in range(j + 1, i + 1)):
                            sd_ag = True
            mid = (max(h[:i + 1]) + min(l[:i + 1])) / 2
            disc = s * (px - mid) <= 0
            kz = hm <= 10 * 60 + 45 or hm >= 13 * 60 + 30
            smt_w = any(x[1] == s and i - 6 < x[0] <= i for x in smt)
            sw = [(cf, p) for (_, cf, p) in (cx.sh if s > 0 else cx.sl) if cf <= i]
            eq_ahead = any(abs(p1 - p2) <= 0.0004 * p1 and 0 <= s * (p1 - px) <= a and
                           not any(((h[k] > max(p1, p2)) if s > 0 else (l[k] < min(p1, p2))) for k in range(max(c1, c2), i + 1))
                           for (c1, p1), (c2, p2) in itertools.combinations(sw, 2))
            disp = any(s * (c[j] - o[j]) >= a for j in range(max(0, i - 2), i + 1))
            rows.append({"i": idx, "in_fvg_with": in_fvg, "fvg_against": fvg_ag, "ob_with": ob_with, "sd_against": sd_ag,
                         "discount": disc, "killzone": kz, "smt_with": smt_w, "eq_ahead": eq_ahead, "displacement": disp})
    return T.join(pd.DataFrame(rows).set_index("i"), how="left")


if __name__ == "__main__":
    T = pickle.load(open(R43.PKL, "rb")) if os.path.exists(R43.PKL) else R43.gen()
    T = feats(T)
    base_pl, base_acct = R43.per_lot(T), R43.replay(T)
    print("BASE", base_pl["train"], base_pl["y2026"], base_acct, "| feature coverage", int(T.discount.notna().sum()), "of", len(T), flush=True)
    f = T
    yes = lambda col: f[col].fillna(False).astype(bool)
    FILT = {
        "FVG: keep only entries inside an open FVG our way": yes("in_fvg_with") | f.in_fvg_with.isna(),
        "FVG: skip if an opposite open FVG is within 1 ATR ahead": ~yes("fvg_against"),
        "OB: keep only entries at a fresh order block our way": yes("ob_with") | f.ob_with.isna(),
        "S/D: skip if a fresh opposite zone is within 1 ATR ahead": ~yes("sd_against"),
        "Premium/discount: keep only CALLs in discount / PUTs in premium": yes("discount") | f.discount.isna(),
        "Premium/discount: skip CALLs in discount / PUTs in premium": ~yes("discount"),
        "Killzones: keep only 09:15-10:45 and 13:30-15:00": yes("killzone") | f.killzone.isna(),
        "SMT: keep only with SMT divergence our way (30 min)": yes("smt_with") | f.smt_with.isna(),
        "EQH/EQL: skip if equal highs/lows within 1 ATR ahead": ~yes("eq_ahead"),
        "EQH/EQL: keep only if equal highs/lows within 1 ATR ahead": yes("eq_ahead") | f.eq_ahead.isna(),
        "Displacement: keep only after a >= 1 ATR candle our way": yes("displacement") | f.displacement.isna(),
        "Displacement: skip after a >= 1 ATR candle our way": ~yes("displacement"),
    }
    out = {"baseline": {"per_lot": base_pl, "account": base_acct}, "filters": {}}
    for nm, keep in FILT.items():
        keep = keep.astype(bool)
        X = T[keep]; pl = R43.per_lot(X); acct = R43.replay(X); p = R43.shuffle_p(T, keep)
        both = pl["train"] > base_pl["train"] and pl["y2026"] > base_pl["y2026"]
        out["filters"][nm] = {"removed": int((~keep).sum()), "per_lot": pl, "account": acct, "shuffle_p": p, "both_better": both}
        print(f"{nm:66s} rm {int((~keep).sum()):4d} | {pl['train']:8d} {pl['y2026']:8d} | acct {acct['final']:>9} {acct['dd']:6} | p {p} {'BOTH' if both else ''}", flush=True)
    json.dump(out, open(os.path.join(HERE, "round51_results.json"), "w"), indent=1, default=str)
    print("saved")
