"""Round 2 of the stock search: (a) exit plateau check for the round-1 finalists, (b) Rs 1L portfolio tests (10 slots,
compounding, 0.15%/side, buy next open, realistic circuit freeze on entry) vs the app's current rule, train to 2024 / unseen
2025-26. Run from research/ after stock_search.py: python3 stock_search_r2.py /path/to/data6y
"""
import json, sys, time
import numpy as np, pandas as pd
import stock_search as S

T0 = time.time()
TR_END = S.TRAIN_END


def sim_full(w, x):
    """simulate() plus the exit day offset of every trade."""
    e = w["e"]; n = len(e)
    sd = {"pct": e * x["sl"][1], "atr": w["atr"] * x["sl"][1], "none": e * 0.99}[x["sl"][0]]
    sd = np.where(np.isfinite(sd) & (sd > 0), sd, e * 0.08); stop = e - sd
    tgt = {"pct": e * (1 + x["tg"][1]), "R": e + sd * x["tg"][1], "none": np.full(n, np.inf)}[x["tg"][0]]
    trail_d = sd if x["sl"][0] != "none" else e * 0.10
    out = np.full(n, np.nan); kout = np.full(n, x["hold"]); live = np.ones(n, bool); armed = np.zeros(n, bool); peak = e.copy()
    o, h, l, c = w["o"], w["h"], w["l"], w["c"]
    for k in range(x["hold"]):
        ok_, hk, lk, ck = o[:, k], h[:, k], l[:, k], c[:, k]
        valid = live & np.isfinite(ck)
        gs = valid & (k > 0) & (ok_ <= stop); hs = valid & ~gs & (lk <= stop)
        out[gs] = ok_[gs]; out[hs] = stop[hs]; kout[gs | hs] = k; live &= ~(gs | hs)
        valid = live & np.isfinite(ck)
        if x["mode"] == "exit":
            gt = valid & (k > 0) & (ok_ >= tgt); ht = valid & ~gt & (hk >= tgt)
            out[gt] = ok_[gt]; out[ht] = tgt[ht]; kout[gt | ht] = k; live &= ~(gt | ht)
        else:
            nw = valid & ~armed & (hk >= tgt); armed |= nw; stop = np.where(nw, np.maximum(stop, e), stop)
            peak = np.where(valid, np.maximum(peak, ck), peak); stop = np.where(armed & valid, np.maximum(stop, peak - trail_d), stop)
    k = x["hold"]; px = np.where(np.isfinite(o[:, k]), o[:, k], c[:, k - 1]); out[live] = px[live]
    out = np.where(np.isfinite(out), out, e)
    return out, kout


def portfolio(books, slots=10, start=None):
    """books: list of (name, w, x, rank_values, max_slots); earlier books have priority. Returns equity series + trades."""
    Cf = S.C.ffill().values; NT = len(S.DATES)
    sig_by_t = {}
    for bi, (nm, w, x, rk, cap) in enumerate(books):
        exitpx, kout = sim_full(w, x)
        for j in range(len(w["e"])):
            sig_by_t.setdefault(int(w["ti"][j]), []).append((bi, rk[j], int(w["si"][j]), float(w["e"][j]), float(exitpx[j]), int(w["ti"][j]) + 1 + int(kout[j])))
    cash = 1e5; pos = []; eq = np.full(NT, np.nan); trades = []
    t0 = start if start is not None else 210
    for t in range(t0, NT):
        # exits happening today (exit index == t)
        keep = []
        for p in pos:
            if p["xt"] <= t:
                cash += p["sh"] * p["xp"] * (1 - S.COST); trades.append(dict(book=p["b"], ret=p["xp"] * (1 - S.COST) / (p["e"] * (1 + S.COST)) - 1, exit=S.DATES[min(p["xt"], NT - 1)]))
            else:
                keep.append(p)
        pos = keep
        # entries at today's open from yesterday's signals
        cands = sorted(sig_by_t.get(t - 1, []), key=lambda z: (z[0], z[1]))
        held = {p["s"] for p in pos}
        if cands:
            eqv = cash + sum(p["sh"] * Cf[t - 1, p["s"]] for p in pos)
            for bi, rk, si, e, xp, xt in cands:
                if len(pos) >= slots or si in held: continue
                if sum(1 for p in pos if p["b"] == bi) >= books[bi][4]: continue
                sh = int(min(eqv / slots, cash) // (e * (1 + S.COST)))
                if sh <= 0: continue
                cash -= sh * e * (1 + S.COST); pos.append(dict(b=bi, s=si, e=e, sh=sh, xp=xp, xt=xt)); held.add(si)
        eq[t] = cash + sum(p["sh"] * Cf[t, p["s"]] for p in pos)
    e = pd.Series(eq, index=S.DATES).dropna()
    return e, pd.DataFrame(trades)


def pstats(e, tr, a=None, b=None):
    if a is not None: e = e[(e.index >= a) & (e.index <= b)]
    if len(e) < 2: return {}
    yrs = (e.index[-1] - e.index[0]).days / 365.25; ret = e.iloc[-1] / e.iloc[0] - 1
    t = tr[(tr.exit >= e.index[0]) & (tr.exit <= e.index[-1])] if len(tr) else tr
    return dict(cagr=round(((1 + ret) ** (1 / yrs) - 1) * 100, 1), fall=round(float((e / e.cummax() - 1).min()) * 100, 1), trades=len(t),
                win=round(float((t.ret > 0).mean()) * 100) if len(t) else None, avg=round(float(t.ret.mean()) * 100, 2) if len(t) else None)


def report(name, e, tr):
    out = dict(full=pstats(e, tr), train=pstats(e, tr, e.index[0], TR_END), unseen=pstats(e, tr, TR_END + pd.Timedelta(days=1), e.index[-1]),
               years={int(y): round(float(g.iloc[-1] / g.iloc[0] - 1) * 100, 1) for y, g in e.groupby(e.index.year) if len(g) > 20})
    print(f"{name:62s} full {out['full']} | train {out['train'].get('cagr')}%/{out['train'].get('fall')}% | unseen {out['unseen'].get('cagr')}%/{out['unseen'].get('fall')}% | {out['years']}", flush=True)
    return out


if __name__ == "__main__":
    X = lambda sl, tg, mode, hold: dict(sl=sl, tg=tg, mode=mode, hold=hold)
    APP = X(("none", 0), ("pct", 0.05), "exit", 5)                    # the app's exit: +5% target, out after 5 closes
    res = {"plateau": {}, "portfolios": {}}
    # (a) plateau: neighbours of each finalist's exit, excess return on train / unseen
    fin = {
        "drop3>=10% (current rule)": S.E["drop3>=10% (current rule)"],
        "drop3>=10% + market up": S.E["drop3>=10% (current rule)"] & S.MKTUP,
        "52-week high on 1.5x volume + market up": S.E["52-week high on 1.5x volume"] & S.MKTUP,
        "52-week-high breakout + market up": S.E["52-week-high breakout"] & S.MKTUP,
        "RSI2<5 + breadth>50%": S.E["RSI2<5"] & (S.BRD > 0.5),
        "RSI14<30 + breadth>50%": S.E["RSI14<30"] & (S.BRD > 0.5),
        "4+ down days of 5 + breadth>50%": S.E["4+ down days of 5"] & (S.BRD > 0.5),
    }
    grids = {
        "mr": [X(sl, tg, "exit", h) for sl in [("none", 0), ("atr", 2.0), ("atr", 3.0), ("pct", 0.08)] for tg in [("pct", 0.05), ("pct", 0.10)] for h in (5, 10)],
        "brk": [X(sl, tg, m, h) for sl in [("none", 0), ("pct", 0.08), ("pct", 0.12), ("atr", 3.0)] for tg, m in [(("none", 0), "exit"), (("pct", 0.10), "trail"), (("pct", 0.20), "trail")] for h in (20, 40)],
    }
    W = {}
    for nm, m in fin.items():
        w = S.windows(m); W[nm] = w; tr = w["date"] <= TR_END
        g = grids["brk" if "52-week" in nm else "mr"]; rows = []
        for x in g:
            raw = S.simulate(w, x); cm = S.control_means(x).reindex(w["ti"]).values; ex = raw - np.where(np.isfinite(cm), cm, 0)
            a, b = S.score(ex, w["date"], tr), S.score(ex, w["date"], ~tr)
            rows.append(dict(exit=S.xname(x), train=a.get("avg"), train_t=a.get("t"), unseen=b.get("avg"), unseen_t=b.get("t"), raw_unseen=S.score(raw, w["date"], ~tr).get("avg")))
        df = pd.DataFrame(rows); res["plateau"][nm] = rows
        print(f"\n== plateau: {nm} ({len(w['e'])} signals)\n" + df.to_string(index=False), flush=True)
    # (b) portfolios
    rk_drop = lambda w: S.R3.values[w["ti"], w["si"]]
    rk_rsi = lambda w: S.RSI2.values[w["ti"], w["si"]]
    rk_brk = lambda w: -S.VX.values[w["ti"], w["si"]]
    BRK40 = X(("none", 0), ("none", 0), "exit", 40)
    BRK_T = X(("pct", 0.12), ("pct", 0.20), "trail", 40)
    P = {
        "P0 current app rule (5% target, 5 days)": [("cur", W["drop3>=10% (current rule)"], APP, rk_drop(W["drop3>=10% (current rule)"]), 10)],
        "P1 current rule, SL 3xATR, target 10%, 5 days": [("cur", W["drop3>=10% (current rule)"], X(("atr", 3.0), ("pct", 0.10), "exit", 5), rk_drop(W["drop3>=10% (current rule)"]), 10)],
        "P2 current rule + market up (app exits)": [("cur", W["drop3>=10% + market up"], APP, rk_drop(W["drop3>=10% + market up"]), 10)],
        "P3 52w high on 1.5x vol + market up, hold 40 days": [("brk", W["52-week high on 1.5x volume + market up"], BRK40, rk_brk(W["52-week high on 1.5x volume + market up"]), 10)],
        "P4 52w high on 1.5x vol + mkt up, SL12%, +20% then trail": [("brk", W["52-week high on 1.5x volume + market up"], BRK_T, rk_brk(W["52-week high on 1.5x volume + market up"]), 10)],
        "P5 RSI2<5 + breadth (app exits)": [("rsi", W["RSI2<5 + breadth>50%"], APP, rk_rsi(W["RSI2<5 + breadth>50%"]), 10)],
        "P6 current rule first + 52w breakout fills (max 5)": [("cur", W["drop3>=10% (current rule)"], APP, rk_drop(W["drop3>=10% (current rule)"]), 10),
                                                             ("brk", W["52-week high on 1.5x volume + market up"], BRK40, rk_brk(W["52-week high on 1.5x volume + market up"]), 5)],
        "P7 current rule first + RSI2<5 fills (max 5)": [("cur", W["drop3>=10% (current rule)"], APP, rk_drop(W["drop3>=10% (current rule)"]), 10),
                                                        ("rsi", W["RSI2<5 + breadth>50%"], APP, rk_rsi(W["RSI2<5 + breadth>50%"]), 5)],
        "P8 current + RSI2<5 (max 3) + 52w breakout (max 3)": [("cur", W["drop3>=10% (current rule)"], APP, rk_drop(W["drop3>=10% (current rule)"]), 10),
                                                              ("rsi", W["RSI2<5 + breadth>50%"], APP, rk_rsi(W["RSI2<5 + breadth>50%"]), 3),
                                                              ("brk", W["52-week high on 1.5x volume + market up"], BRK40, rk_brk(W["52-week high on 1.5x volume + market up"]), 3)],
    }
    print("\n== portfolios (Rs 1L, 10 slots)", flush=True)
    for nm, bk in P.items():
        e, tr = portfolio(bk); res["portfolios"][nm] = report(nm, e, tr)
    json.dump(res, open("stock_search_r2" + ("_all" if S.ALL else "") + ".json", "w"), indent=1, default=str)
    print("done", round(time.time() - T0))
