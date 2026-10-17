"""Does the app's stock rule hold outside the Nifty 500? (runs after fetch_all_6y.py, on GitHub Actions)
Rule pick: close > 200-day average, 3-close drop >= 10%, liquidity floor; buy next open, +5% target (filled at max(open, target)),
else sell at the open after the 5th close. Realistic circuit handling: a stock frozen all day at its lower circuit cannot be sold
(the exit waits) and is not bought; frozen at the upper circuit is not bought. Rs 1L, 10 slots, compounding, 0.15% per side.
Groups (today's index membership): Nifty 50, Nifty 500, outside Nifty 500, all. Train to 30 Jun 2025, unseen after.
Also: the opening-dip test (gap down >= 3% at the open, above the 200-day average, not frozen; buy open, sell close) by group.
Writes research/data/all_stocks_research.json.
"""
import json, os
import numpy as np, pandas as pd

W = "research/work"; COST = 0.0015; CAP = 1e5; SPLIT = pd.Timestamp("2025-07-01")
d = pd.read_csv(f"{W}/all_6y.csv.gz", parse_dates=["date"])
lst = pd.read_csv(f"{W}/all_list.csv").fillna("")
f = (d["adj close"] / d["close"]).replace([np.inf, -np.inf], np.nan).fillna(1.0)
for c in ("open", "high", "low", "close"):
    d[c] = d[c] * f
P = {c: d.pivot(index="date", columns="sym", values=c) for c in ("open", "high", "low", "close", "volume")}
cnt = P["close"].notna().sum(axis=1); keep = cnt >= 0.8 * cnt.rolling(20, min_periods=1).max()
P = {k: v[keep] for k, v in P.items()}
O, H, L, C, V = (P[k] for k in ("open", "high", "low", "close", "volume"))
SMA = C.rolling(200, min_periods=200).mean(); R3 = C / C.shift(3) - 1
TURN = (C * V).rolling(50, min_periods=20).median()
r1 = C / C.shift(1) - 1
dn = pd.DataFrame(False, index=C.index, columns=C.columns); up = dn.copy()
for b in (0.02, 0.05, 0.10, 0.20):
    dn |= (r1 <= -(b - 0.0015)) & (r1 >= -(b + 0.0015)); up |= (r1 >= (b - 0.0015)) & (r1 <= (b + 0.0015))
flat = (H - L) <= 0.0005 * C
SB, BB = flat & dn, flat & up
LC = (C <= L * 1.0005) & dn
LC3 = LC | LC.shift(1, fill_value=False) | LC.shift(2, fill_value=False)
mem = dict(zip(lst["Symbol"].astype(str), lst["Indices"].astype(str)))
groups = {"nifty50": [s for s in C.columns if "N50" in mem.get(s, "").split(",")],
          "nifty500": [s for s in C.columns if "N500" in mem.get(s, "")],
          "outside_nifty500": [s for s in C.columns if "N500" not in mem.get(s, "")],
          "all": list(C.columns)}


def st(eq, tr):
    if len(eq) < 2: return {}
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25; ret = eq.iloc[-1] / eq.iloc[0] - 1
    o = dict(ret=round(ret * 100, 1), cagr=round(((1 + ret) ** (1 / yrs) - 1) * 100, 1), fall=round(float((eq / eq.cummax() - 1).min()) * 100, 1), trades=len(tr))
    if len(tr):
        o.update(win=round(float((tr.ret > 0).mean()) * 100), avg=round(float(tr.ret.mean()) * 100, 2),
                 pf=round(float(tr.pnl[tr.pnl > 0].sum() / max(-tr.pnl[tr.pnl < 0].sum(), 1)), 2), worst=round(float(tr.ret.min()) * 100, 1))
    return o


def win(eq, tr, a, b):
    e = eq[(eq.index >= a) & (eq.index <= b)]
    t = tr[(tr.exit >= a) & (tr.exit <= b)] if len(tr) else tr
    return st(e, t)


def sim(cols, min_turn, slots=10, skip_lc3=False):
    sig = (C[cols] > SMA[cols]) & (TURN[cols] >= min_turn) & (R3[cols] <= -0.10)
    if skip_lc3: sig &= ~LC3[cols]
    CF = C[cols].ffill(); dates = C.index[210:]
    cash = CAP; pos = {}; tr = []; eq = []; to_buy = []
    for i, dd in enumerate(dates):
        o, h = O.loc[dd], H.loc[dd]
        for s in [s for s, p in pos.items() if p.get("sell")]:
            if SB.at[dd, s]: continue
            p = pos.pop(s); px = o[s] if not np.isnan(o[s]) else CF.at[dd, s]
            cash += p["sh"] * px * (1 - COST); tr.append(dict(ret=px * (1 - COST) / (p["px"] * (1 + COST)) - 1, pnl=p["sh"] * (px * (1 - COST) - p["px"] * (1 + COST)), exit=dd))
        if to_buy:
            eqv = cash + sum(p["sh"] * CF.at[dates[i - 1], s] for s, p in pos.items())
            for s in to_buy:
                if len(pos) >= slots or s in pos: continue
                px = o[s]
                if np.isnan(px) or SB.at[dd, s] or BB.at[dd, s]: continue
                sh = int(min(eqv / slots, cash) // (px * (1 + COST)))
                if sh <= 0: continue
                cash -= sh * px * (1 + COST); pos[s] = dict(sh=sh, px=px, d=dd, n=0, tgt=px * 1.05)
            to_buy = []
        for s in list(pos):
            p = pos[s]
            if not np.isnan(h[s]) and h[s] >= p["tgt"]:
                px = p["tgt"] if p["d"] == dd else max(p["tgt"], o[s]); pos.pop(s); cash += p["sh"] * px * (1 - COST)
                tr.append(dict(ret=px * (1 - COST) / (p["px"] * (1 + COST)) - 1, pnl=p["sh"] * (px * (1 - COST) - p["px"] * (1 + COST)), exit=dd))
        for s, p in pos.items():
            p["n"] += 1
            if p["n"] >= 5: p["sell"] = True
        eq.append((dd, cash + sum(p["sh"] * CF.at[dd, s] for s, p in pos.items())))
        free = slots - sum(1 for p in pos.values() if not p.get("sell"))
        row = sig.loc[dd]; c1 = R3.loc[dd, cols][row].sort_values()
        to_buy = [s for s in c1.index if s not in pos][:max(free, 0)]
    e = pd.Series(dict(eq)); t = pd.DataFrame(tr)
    yrs = {int(y): win(e, t, pd.Timestamp(f"{y}-01-01"), pd.Timestamp(f"{y}-12-31")).get("ret") for y in sorted(set(e.index.year))}
    return dict(full=st(e, t), train=win(e, t, e.index[0], SPLIT - pd.Timedelta(days=1)), unseen=win(e, t, SPLIT, e.index[-1]),
                last1y=win(e, t, e.index[-1] - pd.Timedelta(days=365), e.index[-1]), years=yrs, signals=int(sig.loc[dates].values.sum()))


def per_signal(cols, min_turn):
    sig = ((C[cols] > SMA[cols]) & (TURN[cols] >= min_turn) & (R3[cols] <= -0.10)).iloc[210:-7]
    rets = []
    Ov, Hv = O[cols].values, H[cols].values; idx = {dd: i for i, dd in enumerate(C.index)}
    for (dd, s) in sig.stack()[lambda x: x].index:
        i = idx[dd]; j = cols.index(s); e = Ov[i + 1, j]
        if np.isnan(e) or SB.iat[i + 1, C.columns.get_loc(s)]: continue
        ret = None
        for k in range(i + 1, i + 6):
            if Hv[k, j] >= e * 1.05: ret = (max(e * 1.05, Ov[k, j]) if k > i + 1 else e * 1.05) / e - 1; break
        if ret is None: ret = Ov[i + 6, j] / e - 1
        rets.append(ret - 2 * COST)
    r = np.array(rets)
    return dict(n=len(r), win=round(float((r > 0).mean()) * 100, 1) if len(r) else None, avg=round(float(r.mean()) * 100, 2) if len(r) else None)


def opening_dip(cols, min_turn):
    gap = O[cols] / C[cols].shift(1) - 1; oc = C[cols] / O[cols] - 1
    m = (gap <= -0.03) & (gap > -0.08) & (TURN[cols].shift(1) >= min_turn) & (C[cols].shift(1) > SMA[cols].shift(1)) & ~SB[cols]
    x = (oc[m].stack().dropna() - 0.001)
    t = x[x.index.get_level_values(0) < SPLIT]; u = x[x.index.get_level_values(0) >= SPLIT]
    by = x.groupby(x.index.get_level_values(0).year).mean()
    return dict(n=len(x), per_month=round(len(x) / 70, 1), avg=round(float(x.mean()) * 100, 2), win=round(float((x > 0).mean()) * 100, 1),
                train_avg=round(float(t.mean()) * 100, 2), unseen_avg=round(float(u.mean()) * 100, 2), unseen_n=len(u),
                by_year={int(k): round(float(v) * 100, 2) for k, v in by.items()})


res = {"data": dict(stocks=int(C.shape[1]), days=int(C.shape[0]), first=str(C.index[0].date()), last=str(C.index[-1].date()),
                    groups={k: len(v) for k, v in groups.items()})}
for g, cols in groups.items():
    for mt in (1e7, 5e7):
        k = f"{g}|turnover>={int(mt/1e7)}cr"
        res[k] = dict(rule=sim(cols, mt), rule_skip_lc3=sim(cols, mt, skip_lc3=True), per_signal=per_signal(cols, mt), opening_dip=opening_dip(cols, mt))
        print(k, json.dumps(res[k]["rule"]["full"]), json.dumps(res[k]["rule"]["unseen"]), flush=True)
json.dump(res, open("research/data/all_stocks_research.json", "w"), indent=1, default=str)
print("done")
