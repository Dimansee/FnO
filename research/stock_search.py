"""Stock strategy search (17 Oct 2026): entry rules x filters x exits (stop-loss, target, trail after target, time cap).

Universe: today's Nifty 500 list (default) or all NSE EQ stocks (--all), 6 years daily, adjusted, >= Rs 1 cr median turnover.
Entry: signal at close t, buy at the open t+1 (skipped if that day is frozen at a circuit). Long only (cash delivery).
Exits, checked every day in this order: stop (gap through -> the open), target (gap over -> the open; 'exit' books it, 'trail'
moves the stop to entry and then trails it under the highest close by the trail distance), time cap -> the next open.
Same-day stop and target -> the stop (conservative). Costs 0.15% per side.
Honest selection: every exit is chosen on TRAIN signals only (to 31 Dec 2024); UNSEEN (1 Jan 2025 - Oct 2026) is looked at
once, for the train winner of each entry rule. Results -> stock_search_results.json.
"""
import json, math, sys, time, itertools
import numpy as np, pandas as pd

DATA = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "/home/claude/data6y"
ALL = "--all" in sys.argv
COST = 0.0015
TRAIN_END = pd.Timestamp("2024-12-31")
T0 = time.time()

d = pd.read_csv(f"{DATA}/all_6y.csv.gz", parse_dates=["date"])
lst = pd.read_csv(f"{DATA}/all_list.csv").fillna("")
mem = dict(zip(lst["Symbol"].astype(str), lst["Indices"].astype(str)))
if not ALL:
    d = d[d["sym"].map(lambda s: "N500" in mem.get(s, ""))]
f = (d["adj close"] / d["close"]).replace([np.inf, -np.inf], np.nan).fillna(1.0)
for c in ("open", "high", "low", "close"):
    d[c] = d[c] * f
P = {c: d.pivot(index="date", columns="sym", values=c) for c in ("open", "high", "low", "close", "volume")}
cnt = P["close"].notna().sum(axis=1); keep = cnt >= 0.8 * cnt.rolling(20, min_periods=1).max()
P = {k: v[keep] for k, v in P.items()}
O, H, L, C, V = (P[k] for k in ("open", "high", "low", "close", "volume"))
DATES = C.index; SYMS = C.columns
print(f"{C.shape[1]} stocks x {C.shape[0]} days {DATES[0].date()}..{DATES[-1].date()} ({time.time()-T0:.0f}s)", flush=True)

# ------------------------------------------------------------------ indicators (all known at the close of the signal day)
r1 = C / C.shift(1) - 1
TURN = (C * V).rolling(50, min_periods=20).median()
LIQ = TURN >= 1e7
SMA = {n: C.rolling(n, min_periods=n).mean() for n in (10, 20, 50, 200)}
EMA = {n: C.ewm(span=n, adjust=False).mean() for n in (9, 20, 21, 50)}
tr_ = pd.concat({"a": H - L, "b": (H - C.shift(1)).abs(), "c": (L - C.shift(1)).abs()}).groupby(level=1).max() if False else np.maximum(H - L, np.maximum((H - C.shift(1)).abs(), (L - C.shift(1)).abs()))
ATR = tr_.ewm(alpha=1 / 14, adjust=False).mean()


def rsi(n):
    ch = C.diff(); up = ch.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean(); dn = (-ch.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


RSI2, RSI14 = rsi(2), rsi(14)
BBL = SMA[20] - 2 * C.rolling(20).std(); BBU = SMA[20] + 2 * C.rolling(20).std()
HI20 = H.rolling(20).max().shift(1); HI55 = H.rolling(55).max().shift(1); HI252 = H.rolling(252, min_periods=200).max().shift(1)
LO20 = L.rolling(20).min()
VX = V / V.rolling(50, min_periods=20).mean().shift(1)
IBS = (C - L) / (H - L).replace(0, np.nan)
DN = (r1 < 0).astype(int); CONS_DN = DN.groupby((DN != DN.shift()).cumsum(), axis=0).cumsum() if False else DN.rolling(5).sum()
UPT = C > SMA[200]
R3, R5 = C / C.shift(3) - 1, C / C.shift(5) - 1
FROM_HI20 = C / H.rolling(20).max() - 1
MOM126 = C / C.shift(126) - 1
NR7 = (H - L) <= (H - L).rolling(7).min()
INSIDE = (H <= H.shift(1)) & (L >= L.shift(1))
MACD = EMA[9] * 0 + (C.ewm(span=12, adjust=False).mean() - C.ewm(span=26, adjust=False).mean()); MSIG = MACD.ewm(span=9, adjust=False).mean()
# Supertrend(10,3) direction
hl2 = (H + L) / 2; atr10 = tr_.ewm(alpha=1 / 10, adjust=False).mean()
ub, lb = (hl2 + 3 * atr10).values, (hl2 - 3 * atr10).values; cv = C.values
st_dir = np.ones_like(cv); fub, flb = ub.copy(), lb.copy()
for i in range(1, len(cv)):
    fub[i] = np.where((ub[i] < fub[i - 1]) | (cv[i - 1] > fub[i - 1]), ub[i], fub[i - 1])
    flb[i] = np.where((lb[i] > flb[i - 1]) | (cv[i - 1] < flb[i - 1]), lb[i], flb[i - 1])
    st_dir[i] = np.where(cv[i] > fub[i - 1], 1, np.where(cv[i] < flb[i - 1], -1, st_dir[i - 1]))
ST = pd.DataFrame(st_dir, index=C.index, columns=C.columns)
# ADX(14)
upm, dnm = H.diff(), -L.diff()
pdm = upm.where((upm > dnm) & (upm > 0), 0.0); ndm = dnm.where((dnm > upm) & (dnm > 0), 0.0)
pdi = 100 * pdm.ewm(alpha=1 / 14, adjust=False).mean() / ATR; ndi = 100 * ndm.ewm(alpha=1 / 14, adjust=False).mean() / ATR
ADX = (100 * (pdi - ndi).abs() / (pdi + ndi)).ewm(alpha=1 / 14, adjust=False).mean()
# market regime: share of stocks above their 200-day average, and an equal-weight index above its 100-day average
BREADTH = UPT.where(SMA[200].notna()).mean(axis=1)
EWI = (1 + r1.mean(axis=1).fillna(0)).cumprod(); MKT_UP = EWI > EWI.rolling(100).mean()
mk = lambda s: pd.DataFrame(np.repeat(s.values[:, None], C.shape[1], axis=1), index=C.index, columns=C.columns)
MKTUP, BRD = mk(MKT_UP), mk(BREADTH)
# circuit freeze on the entry day (whole session at one price after a band-step move): cannot trade
band = pd.DataFrame(False, index=C.index, columns=C.columns)
for b in (0.02, 0.05, 0.10, 0.20):
    band |= ((r1.abs() - b).abs() <= 0.0015)
FROZEN = ((H - L) <= 0.0005 * C) & band
print(f"indicators ({time.time()-T0:.0f}s)", flush=True)

# ------------------------------------------------------------------ entry rules (fixed before looking)
E = {
    # mean reversion in an uptrend
    "drop3>=10% (current rule)": UPT & (R3 <= -0.10),
    "drop3>=7%": UPT & (R3 <= -0.07),
    "drop3>=15%": UPT & (R3 <= -0.15),
    "drop5>=12%": UPT & (R5 <= -0.12),
    "RSI2<5": UPT & (RSI2 < 5),
    "RSI2<10": UPT & (RSI2 < 10),
    "RSI14<30": UPT & (RSI14 < 30),
    "close<lower Bollinger": UPT & (C < BBL),
    "4+ down days of 5": UPT & (CONS_DN >= 4) & (R5 <= -0.05),
    "IBS<0.1 and down >=3%": UPT & (IBS < 0.1) & (r1 <= -0.03),
    "15% below 20-day high": UPT & (FROM_HI20 <= -0.15),
    "capitulation: down >=5% on 3x volume": UPT & (r1 <= -0.05) & (VX >= 3),
    # trend / momentum / breakout
    "52-week-high breakout": UPT & (C > HI252),
    "52-week high on 1.5x volume": UPT & (C > HI252) & (VX >= 1.5),
    "20-day breakout": UPT & (C > HI20),
    "55-day breakout": UPT & (C > HI55),
    "golden cross 50/200": (SMA[50] > SMA[200]) & (SMA[50].shift(1) <= SMA[200].shift(1)),
    "MACD cross up above zero": UPT & (MACD > MSIG) & (MACD.shift(1) <= MSIG.shift(1)) & (MACD > 0),
    "Supertrend flips up": UPT & (ST == 1) & (ST.shift(1) == -1),
    "ADX>25 trend + pullback to EMA20": UPT & (ADX > 25) & (pdi > ndi) & (L <= EMA[20]) & (C > EMA[20]),
    "volume breakout: 20-day high on 2x volume": UPT & (C > HI20) & (VX >= 2),
    "NR7 then breakout": UPT & NR7.shift(1, fill_value=False) & (C > H.shift(1)),
    "inside day then breakout": UPT & INSIDE.shift(1, fill_value=False) & (C > H.shift(1)),
    "strong 6-month momentum + pullback to EMA20": UPT & (MOM126 >= 0.30) & (L <= EMA[20]) & (C > EMA[20]),
    "EMA 9/21 cross up": UPT & (EMA[9] > EMA[21]) & (EMA[9].shift(1) <= EMA[21].shift(1)),
}
FILTERS = {"": None, "+market up": MKTUP, "+breadth>50%": BRD > 0.5}
RANK = {"drop": R3, "rsi2": RSI2}

# ------------------------------------------------------------------ exits
EXITS = []
for sl, tg, mode, hold in itertools.product(
        [("pct", 0.03), ("pct", 0.05), ("pct", 0.08), ("pct", 0.12), ("atr", 1.5), ("atr", 2.0), ("atr", 3.0), ("none", 0)],
        [("pct", 0.05), ("pct", 0.10), ("pct", 0.20), ("R", 1.5), ("R", 2.0), ("R", 3.0), ("none", 0)],
        ["exit", "trail"], [5, 10, 20, 40]):
    if tg[0] == "none" and mode == "trail": continue
    if tg[0] == "R" and sl[0] == "none": continue
    EXITS.append(dict(sl=sl, tg=tg, mode=mode, hold=hold))
HMAX = 41
Ov, Hv, Lv, Cv, ATRv, FRv = (x.values for x in (O, H, L, C, ATR, FROZEN))
NT = len(DATES)


def windows(mask):
    m = (mask & LIQ).values.copy(); m[-HMAX - 1:, :] = False; m[:205, :] = False
    ti, si = np.nonzero(m)
    e = Ov[ti + 1, si]
    ok = np.isfinite(e) & (e > 0) & ~FRv[ti + 1, si]
    ti, si, e = ti[ok], si[ok], e[ok]
    idx = ti[:, None] + 1 + np.arange(HMAX)[None, :]
    return dict(ti=ti, si=si, e=e, atr=ATRv[ti, si], o=Ov[idx, si[:, None]], h=Hv[idx, si[:, None]], l=Lv[idx, si[:, None]], c=Cv[idx, si[:, None]],
                date=DATES[ti])


def simulate(w, x):
    e = w["e"]; n = len(e)
    sd = {"pct": e * x["sl"][1], "atr": w["atr"] * x["sl"][1], "none": e * 0.99}[x["sl"][0]]
    sd = np.where(np.isfinite(sd) & (sd > 0), sd, e * 0.08)
    stop = e - sd
    tgt = {"pct": e * (1 + x["tg"][1]), "R": e + sd * x["tg"][1], "none": np.full(n, np.inf)}[x["tg"][0]]
    trail_d = sd if x["sl"][0] != "none" else e * 0.10
    out = np.full(n, np.nan); live = np.ones(n, bool); armed = np.zeros(n, bool); peak = e.copy()
    o, h, l, c = w["o"], w["h"], w["l"], w["c"]
    for k in range(x["hold"]):
        ok_, hk, lk, ck = o[:, k], h[:, k], l[:, k], c[:, k]
        valid = live & np.isfinite(ck)
        # stop (gap through on later days -> the open)
        gap_s = valid & (k > 0) & (ok_ <= stop)
        hit_s = valid & ~gap_s & (lk <= stop)
        out[gap_s] = ok_[gap_s]; out[hit_s] = stop[hit_s]
        live &= ~(gap_s | hit_s)
        valid = live & np.isfinite(ck)
        # target
        if x["mode"] == "exit":
            gap_t = valid & (k > 0) & (ok_ >= tgt); hit_t = valid & ~gap_t & (hk >= tgt)
            out[gap_t] = ok_[gap_t]; out[hit_t] = tgt[hit_t]; live &= ~(gap_t | hit_t)
        else:
            newly = valid & ~armed & (hk >= tgt)
            armed |= newly; stop = np.where(newly, np.maximum(stop, e), stop)
            peak = np.where(valid, np.maximum(peak, ck), peak)
            stop = np.where(armed & valid, np.maximum(stop, peak - trail_d), stop)
    # time exit at the next open
    k = x["hold"]
    rem = live
    px = np.where(np.isfinite(o[:, k]), o[:, k], c[:, k - 1])
    out[rem] = px[rem]
    out = np.where(np.isfinite(out), out, e)
    return out / e * (1 - COST) / (1 + COST) - 1


CTRL = None


def control_means(x):
    """Average return of a RANDOM entry (any liquid stock above its 200-day average) on each date, same exit rule -> excess
    return removes the market's direction and the universe's survivorship from every rule's result."""
    global CTRL
    if CTRL is None:
        rng = np.random.default_rng(11)
        m = (UPT & LIQ).values.copy(); m[-HMAX - 1:, :] = False; m[:205, :] = False
        keep = rng.random(m.shape) < 0.25
        CTRL = windows(pd.DataFrame(m & keep, index=C.index, columns=C.columns))
        CTRL["cache"] = {}
    k = json.dumps(x, sort_keys=True)
    if k not in CTRL["cache"]:
        r = simulate(CTRL, x)
        CTRL["cache"][k] = pd.Series(r).groupby(CTRL["ti"]).mean()
    return CTRL["cache"][k]


def score(ret, dates, sel):
    r = ret[sel]
    if len(r) < 2: return dict(n=len(r))
    pos, neg = r[r > 0].sum(), -r[r <= 0].sum()
    yrs = pd.Series(r, index=dates[sel]).groupby(dates[sel].year).agg(["mean", "count"])
    return dict(n=int(len(r)), avg=round(float(r.mean()) * 100, 3), win=round(float((r > 0).mean()) * 100, 1), pf=round(float(pos / neg), 2) if neg else None,
                t=round(float(r.mean() / (r.std(ddof=1) / math.sqrt(len(r)))), 2), worst=round(float(r.min()) * 100, 1),
                years={int(y): round(float(m) * 100, 2) for y, (m, n_) in yrs.iterrows()})


def xname(x):
    s = {"pct": f"SL {x['sl'][1]*100:.0f}%", "atr": f"SL {x['sl'][1]}xATR", "none": "no SL"}[x["sl"][0]]
    t = {"pct": f"target {x['tg'][1]*100:.0f}%", "R": f"target {x['tg'][1]}R", "none": "no target"}[x["tg"][0]]
    return f"{s}, {t}{' then trail' if x['mode']=='trail' else ''}, max {x['hold']} days"


if __name__ == "__main__":
    out = {"universe": "all NSE EQ" if ALL else "Nifty 500", "stocks": int(C.shape[1]), "period": f"{DATES[0].date()}..{DATES[-1].date()}",
           "train_end": str(TRAIN_END.date()), "exits_per_rule": len(EXITS), "rules": []}
    for (en, em), (fn, fm) in itertools.product(E.items(), FILTERS.items()):
        mask = em if fm is None else (em & fm)
        w = windows(mask)
        if len(w["e"]) < 200: continue
        tr = w["date"] <= TRAIN_END; un = ~tr
        best = None
        for x in EXITS:
            raw = simulate(w, x)
            cm = control_means(x).reindex(w["ti"]).values
            ret = raw - np.where(np.isfinite(cm), cm, 0.0)            # excess over a random entry the same day
            s = score(ret, w["date"], tr)
            if s.get("n", 0) < 150: continue
            yrs_pos = sum(v > 0 for y, v in s["years"].items() if y in (2021, 2022, 2023, 2024))
            key = (yrs_pos >= 3, s["t"])
            if best is None or key > best[0]: best = (key, x, s, ret)
        if best is None: continue
        _, x, s_tr, ret = best
        raw = simulate(w, x)
        row = dict(rule=en + fn, exit=xname(x), exit_cfg=x, signals=int(len(w["e"])), per_month=round(len(w["e"]) / 70, 1),
                   train=s_tr, unseen=score(ret, w["date"], un), raw_train=score(raw, w["date"], tr), raw_unseen=score(raw, w["date"], un))
        out["rules"].append(row)
        u = row["unseen"]
        print(f"{row['rule'][:48]:48s} | {row['exit'][:52]:52s} | train n={s_tr['n']:5d} excess={s_tr['avg']:+.2f}% t={s_tr['t']:5.1f} | "
              f"unseen n={u.get('n',0):5d} excess={u.get('avg',0):+.2f}% t={u.get('t')} | raw unseen avg={row['raw_unseen'].get('avg')}% win={row['raw_unseen'].get('win')} pf={row['raw_unseen'].get('pf')} ({time.time()-T0:.0f}s)", flush=True)
    json.dump(out, open("stock_search_results" + ("_all" if ALL else "") + ".json", "w"), indent=1, default=str)
    print("done", time.time() - T0)
