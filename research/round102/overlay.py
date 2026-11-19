"""Round 102e: the round-number counts as filters / size tilts on today's trades (no new trades).
Per Autopilot trade, known at its entry minute (5-min bars closed by then, zone +/-25 on Nifty = z0.5):
  rej_today   rejections at any round level today so far (a 'range' meter: many rejections = two-way day)
  brk_with    a round level that had been rejected >= 1 time this week broke today in the trade's direction (last break today)
  brk_against the last such break today was against the trade
Overlays: skip / half size when rej_today >= k; 1.25x when brk_with, 0.75x when brk_against; skip brk_against.
Controls: the same number of trades skipped / tilted at random (20 draws); 'beats' = share of random draws the real one beats."""
import sys, json
sys.path.insert(0, "/home/claude/dimansee/fno/research/round101"); sys.path.insert(0, "/home/claude/r102")
import lib as L
import numpy as np, pandas as pd
import zones as Z
T0, A0, H0 = L.base_sets()
CAPS = (20000, 200000, 500000)


def feats():
    out = []
    for sym in L.SYMS:
        df, o, h, l, c, hm, day = L.bar_of(sym, "5m")
        E = Z.events(df, o, h, l, c, day, sym, 0.5)
        rej = (np.isfinite(E["rej_r"]) | np.isfinite(E["rej_s"])).astype(int)
        cum = pd.Series(rej).groupby(day).cumsum().values
        bu = np.isfinite(E["brk_u"]) & (np.nan_to_num(E["buw"]) >= 1)
        bd = np.isfinite(E["brk_d"]) & (np.nan_to_num(E["bdw"]) >= 1)
        last = np.zeros(len(c)); cur = 0; dd = None
        for t in range(len(c)):
            if day[t] != dd:
                dd = day[t]; cur = 0
            if bu[t]: cur = 1
            if bd[t]: cur = -1
            last[t] = cur
        close_t = df.index + pd.Timedelta(minutes=5)
        F = pd.DataFrame({"ts": close_t, "rej_today": cum, "brk": last})
        F["date"] = F.ts.dt.strftime("%Y-%m-%d"); F["sym"] = sym
        out.append(F)
    return pd.concat(out)


def attach(T, F):
    T = T.copy(); T["_ix"] = T.index
    T["ts"] = pd.to_datetime(T.date + " " + T.entry_time)
    res = []
    for sym, g in T.groupby("sym"):
        f = F[F.sym == sym].sort_values("ts")
        m = pd.merge_asof(g.sort_values("ts"), f[["ts", "rej_today", "brk", "date"]].rename(columns={"date": "fdate"}), on="ts", direction="backward")
        m.loc[m.fdate != m.date, ["rej_today", "brk"]] = 0
        res.append(m)
    T = pd.concat(res).set_index("_ix").sort_index(); T.index.name = None
    side = np.where(T.side == "CALL", 1, -1)
    T["brk_with"] = T.brk.values * side > 0; T["brk_against"] = T.brk.values * side < 0
    return T


def apply(X, mult):
    """mult: Series aligned to X.index; 0 = skip."""
    keep = mult > 0
    X = X[keep].copy(); m = mult[keep]
    X["risk_lot"] = X.risk_lot / m; X["risk_lot_capped"] = X.risk_lot_capped / m
    return X


def cells(T, A, H):
    return {f"{cap}|{p}": L.replay(cap, a, b, T=T, A=A, H=H) for cap in CAPS for p, (a, b) in L.PER.items()}


def run(rule, Tf, Af, Hf):
    mT, mA, mH = rule(Tf), rule(Af), rule(Hf)
    return cells(apply(T0, mT), apply(A0, mA), apply(H0, mH))


if __name__ == "__main__":
    F = feats()
    Tf, Af, Hf = attach(T0, F), attach(A0, F), attach(H0, F)
    print("trades", len(Tf), "rej_today pct", Tf.rej_today.describe().round(1).to_dict(), "brk_with", Tf.brk_with.mean().round(3), "against", Tf.brk_against.mean().round(3))
    # per-trade R by bucket (1 lot R, base set)
    Tf["rb"] = pd.cut(Tf.rej_today, [-1, 0, 2, 4, 7, 100])
    print(Tf.groupby("rb").R.agg(["size", "mean"]).round(3).to_string())
    print(Tf.groupby(["brk_with", "brk_against"]).R.agg(["size", "mean"]).round(3).to_string())
    for st, g in Tf.groupby("strategy"):
        print(st, g.groupby("rb").R.mean().round(2).to_dict())
    base = cells(T0, A0, H0)
    one = lambda X: pd.Series(1.0, index=X.index)
    rules = {}
    for k in (3, 5, 8):
        rules[f"skip if rejections today >= {k}"] = (lambda kk: lambda X: pd.Series(np.where(X.rej_today >= kk, 0.0, 1.0), index=X.index))(k)
        rules[f"half if rejections today >= {k}"] = (lambda kk: lambda X: pd.Series(np.where(X.rej_today >= kk, 0.5, 1.0), index=X.index))(k)
    rules["1.25x with a round-level break, 0.75x against"] = lambda X: pd.Series(np.where(X.brk_with, 1.25, np.where(X.brk_against, 0.75, 1.0)), index=X.index)
    rules["skip against a round-level break"] = lambda X: pd.Series(np.where(X.brk_against, 0.0, 1.0), index=X.index)
    res = {"base": base, "rules": {}}
    rng = np.random.default_rng(1)
    for name, rule in rules.items():
        r = run(rule, Tf, Af, Hf)
        six = [k for k in r if not k.startswith("500000")]
        better = sum(r[k]["final"] > base[k]["final"] for k in six); deeper = any(r[k]["dd"] < base[k]["dd"] - 2 for k in six)
        # random control: same multipliers shuffled across trades (same trades in T/A/H share the draw via their key)
        m0 = rule(Tf).values; beats = {k: 0 for k in r}
        keyv = (Tf.sym + Tf.date + Tf.entry_time + Tf.strategy).values
        for s in range(10):
            perm = rng.permutation(m0); mp = dict(zip(keyv, perm))
            rr = lambda X: pd.Series([mp.get(a, 1.0) for a in (X.sym + X.date + X.entry_time + X.strategy).values], index=X.index)
            rc = run(rr, Tf, Af, Hf)
            for k in r:
                beats[k] += r[k]["final"] > rc[k]["final"]
        res["rules"][name] = {"cells": r, "better6": better, "fall_ok": not deeper, "beats_random": {k: v / 10 for k, v in beats.items()}}
        print(name, better, not deeper, {k: (round(v["final"] / 1e5, 2), v["dd"]) for k, v in r.items()}, {k: v / 10 for k, v in beats.items()}, flush=True)
    json.dump(res, open("/home/claude/r102/out/overlay.json", "w"), indent=1, default=str)
