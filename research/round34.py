"""Round 34 (Oct 2026): scalping as specified - 1-minute and 5-minute charts only, EMA 9 / EMA 15 with volume and liquidity,
small fixed stop and target, many trades, many lots.

Part A - 2023-01 to 2026-10-16, Nifty / Bank Nifty / Sensex 1-minute index candles, ATM weekly (Bank Nifty monthly) option priced
like the app (calibrated Black-Scholes + the real-price decay adjustment), one position per index at a time, re-entry allowed at once.
  entries  cross   EMA 9 crosses EMA 15 on the chart's close
           pull    trend (EMA 9 above EMA 15), the candle dips to EMA 9 and closes back above it as a green candle (mirror for puts)
           +5m     (1-minute entries only) the 5-minute EMA 9 / 15 must point the same way
  volume   none, or heavyweight-stock turnover >= 1.3x its usual level for that time of day (index has no volume; round 9 proxy,
           data to 9 Oct 2026)
  exits    stop = 1 or 2 ATR of the chart; target = 1 or 1.5 x the stop; exit on the opposite EMA cross; optional time stop
           (10 minutes on the 1-minute chart, 30 on the 5-minute); 15:15
  size     "many lots": Rs 2 lakh account, lots = Rs 2,000 risk / the option's risk per lot (cash-capped, max 40 lots); brokerage is per
           order, so more lots dilute it. Slippage per fill tested at 0.5% (app default), 0.25% and 0.1% (best case for liquid ATM).
Part B - the 15 recorded sessions (28 Sep - 19 Oct 2026): the same rules on REAL 1-minute option prices, REAL futures volume
  (>= 1.5x the last 20 minutes) and a REAL liquidity check from the option-chain snapshots (bid-ask spread of the strike <= 0.5% of
  its price), entries / exits at the next minute's open plus half the real spread.
Run: python round34.py -> round34_results.json"""
import os, sys, json, glob, gzip, itertools, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
from datetime import datetime, timedelta, time, date
import numpy as np, pandas as pd
from numba import njit
from scipy.stats import norm
from fno import indicators as I, config as C, pricing as PR, market as D

DATA = os.path.join(HERE, "data"); SYMS = ["NIFTY", "BANKNIFTY", "SENSEX"]
RISK = 2000.0; CAPITAL = 200_000.0; MAX_LOTS = 40
RES = {}


def m1(sym):
    a = pd.read_csv(f"{DATA}/{sym}_1m_upstox.csv.gz", parse_dates=["ts"]).set_index("ts")[["open", "high", "low", "close"]]
    a = a[~a.index.duplicated()].sort_index().between_time("09:15", "15:29")
    return a


def ema(x, n):
    return pd.Series(x).ewm(span=n, adjust=False).mean().values


def atr(h, l, c, n=14):
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc)))
    return pd.Series(tr).ewm(alpha=1 / n, adjust=False).mean().values


def features(sym, rel):
    """1-minute arrays, 1-minute and 5-minute EMA 9/15 states and signals, ATRs, volume proxy - all as known at each 1-min close."""
    a = m1(sym); idx = a.index
    o, h, l, c = (a[k].values for k in ("open", "high", "low", "close"))
    mins = idx.hour.values * 60 + idx.minute.values
    day = (idx.normalize().values.astype("datetime64[D]").astype(np.int64))
    e9, e15 = ema(c, 9), ema(c, 15); at1 = atr(h, l, c)
    up1 = e9 > e15
    # 5-minute bars inside each day; a 5-min bar's values are known at the close of its last 1-minute bar
    b5 = a.resample("5min", label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    b5 = b5[(b5.index.time >= time(9, 15)) & (b5.index.time <= time(15, 25))]
    f9, f15 = ema(b5.close.values, 9), ema(b5.close.values, 15); at5 = atr(b5.high.values, b5.low.values, b5.close.values)
    pos = pd.Series(np.arange(len(idx)), index=idx)
    last1 = pos.reindex(b5.index + pd.Timedelta(minutes=4)).values          # 1-min bar that closes the 5-min bar
    ok = ~np.isnan(last1); last1 = last1[ok].astype(int)
    b5o, b5c = b5.open.values[ok], b5.close.values[ok]; b5l, b5h = b5.low.values[ok], b5.high.values[ok]
    f9, f15, at5 = f9[ok], f15[ok], at5[ok]
    def onto1(v, fill=np.nan):
        out = np.full(len(idx), fill); out[last1] = v
        return pd.Series(out).ffill().values
    up5 = onto1((f9 > f15).astype(float), 0.0) > 0.5
    dn5 = onto1((f9 < f15).astype(float), 0.0) > 0.5
    atr5 = onto1(at5)
    # signals on the 1-minute chart
    cr1 = np.zeros(len(idx), np.int8)
    cr1[1:][(e9[1:] > e15[1:]) & (e9[:-1] <= e15[:-1])] = 1
    cr1[1:][(e9[1:] < e15[1:]) & (e9[:-1] >= e15[:-1])] = -1
    pl1 = np.zeros(len(idx), np.int8)
    pl1[(e9 > e15) & (l <= e9) & (c > e9) & (c > o)] = 1
    pl1[(e9 < e15) & (h >= e9) & (c < e9) & (c < o)] = -1
    # signals on the 5-minute chart, placed on the 1-minute bar where the 5-minute candle closes
    cr5v = np.zeros(len(f9), np.int8)
    cr5v[1:][(f9[1:] > f15[1:]) & (f9[:-1] <= f15[:-1])] = 1
    cr5v[1:][(f9[1:] < f15[1:]) & (f9[:-1] >= f15[:-1])] = -1
    pl5v = np.zeros(len(f9), np.int8)
    pl5v[(f9 > f15) & (b5l <= f9) & (b5c > f9) & (b5c > b5o)] = 1
    pl5v[(f9 < f15) & (b5h >= f9) & (b5c < f9) & (b5c < b5o)] = -1
    cr5 = np.zeros(len(idx), np.int8); cr5[last1] = cr5v
    pl5 = np.zeros(len(idx), np.int8); pl5[last1] = pl5v
    # volume proxy (5-min heavyweight turnover vs the same time of day), known at the 5-min close
    rv = rel.reindex(b5.index[ok]).values if rel is not None else np.full(ok.sum(), np.nan)
    rel1 = onto1(rv)
    return dict(idx=idx, o=o, h=h, l=l, c=c, mins=mins, day=day, at1=at1, atr5=atr5, up1=up1, up5=up5, dn5=dn5,
                cr1=cr1, pl1=pl1, cr5=cr5, pl5=pl5, rel=rel1)


@njit(cache=True)
def sim(o, h, l, c, mins, day, sig, atrs, upx, dnx, k_sl, r_tp, tstop, first_m, last_m, sq_m):
    """One position at a time. Returns (i_in, i_out, x_spot, side, sl_spot, reason) rows.
    reason: 0 stop, 1 target, 2 opposite EMA, 3 time, 4 square-off."""
    n = len(c); out = np.zeros((n // 2 + 1, 6)); m = 0; i = 0
    while i < n - 1:
        s = sig[i]
        if s == 0 or mins[i] + 1 < first_m or mins[i] + 1 > last_m or np.isnan(atrs[i]) or atrs[i] <= 0:
            i += 1; continue
        c0 = c[i]; risk = k_sl * atrs[i]; sl = c0 - s * risk; tp = c0 + s * r_tp * risk
        j = i + 1; xs = c[i]; why = 4
        while j < n:
            if day[j] != day[i]:
                j -= 1; xs = c[j]; why = 4; break
            adv = l[j] if s > 0 else h[j]; fav = h[j] if s > 0 else l[j]
            if s * (adv - sl) <= 0:
                xs = o[j] if s * (o[j] - sl) < 0 else sl; why = 0; break
            if s * (fav - tp) >= 0:
                xs = o[j] if s * (o[j] - tp) > 0 else tp; why = 1; break
            if (s > 0 and dnx[j]) or (s < 0 and upx[j]):
                xs = c[j]; why = 2; break
            if tstop > 0 and j - i >= tstop:
                xs = c[j]; why = 3; break
            if mins[j] + 1 >= sq_m:
                xs = c[j]; why = 4; break
            j += 1
        if j >= n:
            j = n - 1; xs = c[j]
        out[m, 0] = i; out[m, 1] = j; out[m, 2] = xs; out[m, 3] = s; out[m, 4] = sl; out[m, 5] = why; m += 1
        i = j + 1
    return out[:m]


# ---------------- option pricing, vectorised (same formulas as the app)
_EXP = {}
def expiry_of(sym, d):
    key = (sym, d)
    if key not in _EXP:
        meta = D.instrument(sym)
        _EXP[key] = I.pick_trading_expiry(I.demo_expiries(meta["expiry"], d, 3, meta.get("exchange", "NSE")), meta["expiry"], d)
    return _EXP[key]


def bs(s, k, t, iv, call):
    t = np.maximum(t, 1 / (365 * 24 * 4)); iv = np.maximum(iv, 0.01); r = C.RISK_FREE
    d1 = (np.log(s / k) + (r + 0.5 * iv * iv) * t) / (iv * np.sqrt(t)); d2 = d1 - iv * np.sqrt(t)
    ce = s * norm.cdf(d1) - k * np.exp(-r * t) * norm.cdf(d2)
    pe = k * np.exp(-r * t) * norm.cdf(-d2) - s * norm.cdf(-d1)
    return np.where(call, ce, pe)


def ivv(sym, vix, s, k, call, dte, step, mult):
    m = np.clip(np.round((k - s) / step).astype(int) * np.where(call, 1, -1), -4, 4)
    tab = PR._table()
    db = [PR._dteb(x) for x in dte]
    r = np.array([tab.get((sym, b, int(mm))) or tab.get((sym, b, 0)) or mult for b, mm in zip(db, m)])
    return np.clip(vix / 100 * r, 0.06, 0.9)


def price(sym, F, T, vix1):
    """Model premiums (no slippage) for trades T (DataFrame with i, j, xs, side, sl)."""
    meta = D.instrument(sym); step = meta["step"]; mult = meta["iv_mult"]
    idx = F["idx"]; t_in = idx[T["i"].values] + pd.Timedelta(minutes=1); t_out = idx[T["j"].values] + pd.Timedelta(minutes=1)
    dates = [x.date() for x in idx[T["i"].values]]
    exps = np.array([expiry_of(sym, d) for d in dates])
    exp_dt = pd.to_datetime([datetime.combine(e, time(15, 30)) for e in exps])
    yin = np.maximum((exp_dt - t_in).total_seconds().values, 0) / (365 * 24 * 3600)
    yout = np.maximum((exp_dt - t_out).total_seconds().values, 0) / (365 * 24 * 3600)
    dte = np.array([(e - d).days for e, d in zip(exps, dates)])
    vx = vix1.reindex(idx[T["i"].values], method="ffill").fillna(14.0).values
    s_in = F["c"][T["i"].values]; call = T["side"].values > 0
    k = np.round(s_in / step) * step                                   # ATM
    iv_in = ivv(sym, vx, s_in, k, call, dte, step, mult)
    p_in = bs(s_in, k, yin, iv_in, call)
    p_sl = bs(T["sl"].values, k, yin, ivv(sym, vx, T["sl"].values, k, call, dte, step, mult), call)
    p_out = bs(T["xs"].values, k, yout, ivv(sym, vx, T["xs"].values, k, call, dte, step, mult), call)
    hours = (t_out - t_in).total_seconds().values / 3600
    rate = np.array([next(r for lim, r in PR.EXTRA_DECAY if x <= lim) for x in dte])
    p_out = np.maximum(p_in + PR.CHG_SCALE * (p_out - p_in) - p_in * rate * hours, 0.05)
    risk = np.maximum(p_in - p_sl, p_in * 0.05)
    return p_in, p_out, risk, dte


def money(sym, p_in, p_out, risk, slip, lots_mode="risk", fixed_lots=10):
    """Rs P&L per trade with the lots rule, slippage per fill and real charges (brokerage per order)."""
    meta = D.instrument(sym); lot = meta["lot"]; ex = I.exch_rate(sym)
    if lots_mode == "risk":
        lots = np.floor(RISK / (risk * lot)); lots = np.minimum(lots, np.floor(CAPITAL / (p_in * (1 + slip) * lot)))
        lots = np.clip(lots, 1, MAX_LOTS)
    else:
        lots = np.full(len(p_in), float(fixed_lots))
    qty = lots * lot; pi, po = p_in * (1 + slip), p_out * (1 - slip)
    bv, sv = pi * qty, po * qty; br = 40.0; exch = ex * (bv + sv); sebi = 1e-6 * (bv + sv)
    chg = br + 0.0015 * sv + exch + sebi + 0.00003 * bv + 0.18 * (br + exch + sebi)
    return (po - pi) * qty - chg, lots, chg


def stats(df, col="pnl"):
    if len(df) == 0: return dict(n=0)
    s = df.groupby("date")[col].sum(); eq = s.cumsum(); fall = float((eq - eq.cummax().clip(lower=0)).min())
    w = df[col][df[col] > 0].sum(); ls = -df[col][df[col] <= 0].sum()
    return dict(n=int(len(df)), per_day=round(len(df) / max(df.date.nunique(), 1), 1), net=round(float(df[col].sum())),
                win=round(float((df[col] > 0).mean() * 100), 1), pf=round(float(w / ls), 2) if ls else None, fall=round(fall))


GRID = []
for chart, ent, f5 in (("1m", "cross", False), ("1m", "pull", False), ("1m", "cross", True), ("1m", "pull", True),
                       ("5m", "cross", False), ("5m", "pull", False)):
    for vol, k, r, ts in itertools.product((None, 1.3), (1.0, 2.0), (1.0, 1.5), (False, True)):
        GRID.append(dict(chart=chart, entry=ent, f5=f5, vol=vol, k=k, r=r, tstop=ts))


def label(P):
    return f"{P['chart']} {P['entry']}{' +5m trend' if P['f5'] else ''}{' vol>=1.3' if P['vol'] else ''} | stop {P['k']:g} ATR, target {P['r']:g}x{' , time stop' if P['tstop'] else ''}"


def part_a():
    rel = pd.read_pickle("/tmp/round30_rel.pkl")
    vix1 = pd.read_csv(f"{DATA}/INDIAVIX_1m_upstox.csv.gz", parse_dates=["ts"]).set_index("ts").close
    rows = []
    for sym in SYMS:
        F = features(sym, rel)
        for kk, P in enumerate(GRID):
            if P["chart"] == "1m":
                sig = (F["cr1"] if P["entry"] == "cross" else F["pl1"]).copy()
                if P["f5"]:
                    sig[(sig > 0) & ~F["up5"]] = 0; sig[(sig < 0) & ~F["dn5"]] = 0
                atrs, upx, dnx, ts = F["at1"], F["up1"], ~F["up1"], (10 if P["tstop"] else 0)
            else:
                sig = (F["cr5"] if P["entry"] == "cross" else F["pl5"]).copy()
                atrs, upx, dnx, ts = F["atr5"], F["up5"], F["dn5"], (30 if P["tstop"] else 0)
            if P["vol"]:
                sig[~(F["rel"] >= P["vol"])] = 0
            out = sim(F["o"], F["h"], F["l"], F["c"], F["mins"], F["day"], sig.astype(np.int8), atrs.astype(np.float64),
                      upx.astype(np.bool_), dnx.astype(np.bool_), P["k"], P["r"], ts, 9 * 60 + 20, 15 * 60, 15 * 60 + 15)
            if len(out) == 0: continue
            T = pd.DataFrame(out, columns=["i", "j", "xs", "side", "sl", "why"]).astype({"i": int, "j": int, "side": int, "why": int})
            p_in, p_out, risk, dte = price(sym, F, T, vix1)
            T["date"] = [str(x.date()) for x in F["idx"][T["i"].values]]; T["sym"] = sym; T["k_grid"] = kk
            T["pts"] = T["side"] * (T["xs"] - F["c"][T["i"].values]); T["hold"] = T.j - T.i
            T["p_in"], T["p_out"], T["risk"] = p_in, p_out, risk
            for s in (0.005, 0.0025, 0.001):
                T[f"pnl_{s}"], T["lots"], T[f"chg_{s}"] = money(sym, p_in, p_out, risk, s)
            T["pnl10_0.0025"] = money(sym, p_in, p_out, risk, 0.0025, "fixed", 10)[0]
            rows.append(T.drop(columns=["xs", "sl"]))
            print(sym, kk, label(P), len(T), "trades", round(T["pnl_0.0025"].sum()), flush=True)
    A = pd.concat(rows, ignore_index=True); A["y"] = A.date.str[:4]
    A.to_pickle("/tmp/round34_A.pkl")
    return A


def summarise_a(A):
    out = []
    for kk, g in A.groupby("k_grid"):
        P = GRID[kk]
        if P["vol"]: g = g[g.date <= "2026-10-09"]
        r = dict(k=kk, setting=label(P), per_day=round(len(g) / max(g.date.nunique(), 1) / 3, 1),
                 avg_pts=round(float(g.pts.mean()), 2), win_pts=round(float((g.pts > 0).mean() * 100), 1),
                 avg_lots=round(float(g.lots.mean()), 1))
        for s in ("0.005", "0.0025", "0.001"):
            col = f"pnl_{s}"
            r[f"net_{s}"] = round(float(g[col].sum()))
            r[f"years_{s}"] = {y: round(float(v)) for y, v in g.groupby("y")[col].sum().items()}
        r["win_0.0025"] = round(float((g["pnl_0.0025"] > 0).mean() * 100), 1)
        r["fees_share_0.0025"] = round(float(g["chg_0.0025"].sum() / max(abs(g["pnl_0.0025"] + g["chg_0.0025"]).sum(), 1) * 100), 1)
        r["by_index_0.001"] = {s: round(float(v)) for s, v in g.groupby("sym")["pnl_0.001"].sum().items()}
        out.append(r)
    R = pd.DataFrame(out).sort_values("net_0.001", ascending=False)
    pd.set_option("display.width", 250); pd.set_option("display.max_colwidth", 70)
    print(R[["setting", "per_day", "avg_pts", "win_pts", "avg_lots", "net_0.005", "net_0.0025", "net_0.001", "win_0.0025"]].head(25).to_string())
    print("positive at 0.5% slippage:", int((R["net_0.005"] > 0).sum()), "| at 0.25%:", int((R["net_0.0025"] > 0).sum()),
          "| at 0.1%:", int((R["net_0.001"] > 0).sum()), "of", len(R))
    pos_all_years = [r for r in out if all(v > 0 for v in r["years_0.001"].values())]
    print("positive in every year at 0.1% slippage:", len(pos_all_years))
    RES["part_a"] = dict(settings=len(R), positive_05=int((R["net_0.005"] > 0).sum()), positive_025=int((R["net_0.0025"] > 0).sum()),
                         positive_01=int((R["net_0.001"] > 0).sum()), positive_every_year_01=len(pos_all_years),
                         top=R.head(15).to_dict("records"), all=R.to_dict("records"))
    # where the money goes (all settings pooled, 0.25% slippage)
    g = A
    RES["part_a"]["pooled"] = dict(trades=int(len(g)), avg_index_pts=round(float(g.pts.mean()), 2),
                                   gross_option_rs_per_trade_before_costs=round(float(((g.p_out - g.p_in) * g.lots * g.sym.map(lambda s: D.instrument(s)['lot'])).mean())),
                                   fees_rs_per_trade=round(float(g["chg_0.0025"].mean())),
                                   slippage_rs_per_trade_025=round(float(((g.p_in * 0.0025 + g.p_out * 0.0025) * g.lots * g.sym.map(lambda s: D.instrument(s)['lot'])).mean())))
    print("pooled:", RES["part_a"]["pooled"])


# ---------------- Part B: the recorded sessions on real option prices, real futures volume and real bid-ask spreads
LIVE = os.path.join(DATA, "live")


def live_days():
    return sorted(d for d in os.listdir(LIVE) if d[:2] == "20" and os.path.exists(f"{LIVE}/{d}/candles_1m.csv.gz"))


def m1_with_live(sym):
    a = m1(sym); parts = [a]
    for d in live_days():
        x = pd.read_csv(f"{LIVE}/{d}/candles_1m.csv.gz"); x = x[(x.sym == sym) & (x.kind == "IDX")]
        if len(x): parts.append(x.assign(ts=pd.to_datetime(x.ts)).set_index("ts")[["open", "high", "low", "close"]])
    a = pd.concat(parts); return a[~a.index.duplicated(keep="first")].sort_index().between_time("09:15", "15:29")


def part_b():
    days = live_days(); out = []
    vix1 = pd.read_csv(f"{DATA}/INDIAVIX_1m_upstox.csv.gz", parse_dates=["ts"]).set_index("ts").close
    vx_live = []
    for d in days:
        x = pd.read_csv(f"{LIVE}/{d}/candles_1m.csv.gz"); x = x[x.sym == "INDIAVIX"]
        vx_live.append(x.assign(ts=pd.to_datetime(x.ts)).set_index("ts").close)
    vix1 = pd.concat([vix1] + vx_live); vix1 = vix1[~vix1.index.duplicated()].sort_index()
    global m1
    base_m1 = m1
    for sym in ("NIFTY", "BANKNIFTY"):
        meta = D.instrument(sym); lot = meta["lot"]; step = meta["step"]
        A = m1_with_live(sym)
        m1 = lambda s, _A=A: _A
        F = features(sym, None)
        m1 = base_m1
        idx = F["idx"]; pos = pd.Series(np.arange(len(idx)), index=idx)
        # futures volume ratio: this minute vs the previous 20 (1-min chart); 5-min sum vs the previous four 5-min sums
        fv = pd.Series(np.nan, index=idx); opt_px = {}; spreads = {}
        for d in days:
            x = pd.read_csv(f"{LIVE}/{d}/candles_1m.csv.gz"); x["ts"] = pd.to_datetime(x.ts)
            f = x[(x.sym == sym) & (x.kind == "FUT")]
            if len(f):
                f = f[f.expiry == f.expiry.min()].set_index("ts").volume
                fv.loc[f.index.intersection(idx)] = f.loc[f.index.intersection(idx)]
            o = x[(x.sym == sym) & x.kind.isin(["CE", "PE"])]
            if len(o):
                e0 = o.expiry.min(); o = o[o.expiry == e0]
                for (k, kind), g in o.groupby(["strike", "kind"]):
                    opt_px[(d, float(k), kind)] = g.set_index("ts")[["open", "close", "volume"]]
            sp = f"{LIVE}/{d}/snapshots.json.gz"
            if os.path.exists(sp):
                S = json.load(gzip.open(sp))
                for snap in S["snapshots"]:
                    if snap.get("sym") != sym: continue
                    for r in snap["rows"]:
                        k, cb, ca, pb, pa = r[0], r[2], r[3], r[8], r[9]
                        spreads.setdefault((d, float(k), "CE", snap["exp"]), []).append((snap["t"], cb, ca))
                        spreads.setdefault((d, float(k), "PE", snap["exp"]), []).append((snap["t"], pb, pa))
        r1 = (fv / fv.rolling(20, min_periods=10).mean().shift(1)).values
        f5 = fv.groupby(fv.index.floor("5min")).transform("sum")
        r5 = (f5 / fv.resample("5min").sum().rolling(4, min_periods=2).mean().shift(1).reindex(fv.index.floor("5min")).values).values
        live_mask = np.isin(idx.normalize().strftime("%Y-%m-%d"), days)
        for kk, P in enumerate(GRID):
            if P["chart"] == "1m":
                sig = (F["cr1"] if P["entry"] == "cross" else F["pl1"]).copy()
                if P["f5"]:
                    sig[(sig > 0) & ~F["up5"]] = 0; sig[(sig < 0) & ~F["dn5"]] = 0
                atrs, upx, dnx, ts = F["at1"], F["up1"], ~F["up1"], (10 if P["tstop"] else 0); vr = r1
            else:
                sig = (F["cr5"] if P["entry"] == "cross" else F["pl5"]).copy()
                atrs, upx, dnx, ts = F["atr5"], F["up5"], F["dn5"], (30 if P["tstop"] else 0); vr = r5
            sig[~live_mask] = 0
            if P["vol"]:
                sig[~(vr >= P["vol"])] = 0
            res = sim(F["o"], F["h"], F["l"], F["c"], F["mins"], F["day"], sig.astype(np.int8), atrs.astype(np.float64),
                      upx.astype(np.bool_), dnx.astype(np.bool_), P["k"], P["r"], ts, 9 * 60 + 20, 15 * 60, 15 * 60 + 15)
            if len(res) == 0: continue
            T = pd.DataFrame(res, columns=["i", "j", "xs", "side", "sl", "why"]).astype({"i": int, "j": int, "side": int, "why": int})
            p_in_m, p_out_m, risk_m, _ = price(sym, F, T, vix1)
            for q, r in enumerate(T.itertuples()):
                t_in, t_out = idx[r.i] + pd.Timedelta(minutes=1), idx[r.j] + pd.Timedelta(minutes=1)
                d = str(t_in.date()); kind = "CE" if r.side > 0 else "PE"
                k = float(np.round(F["c"][r.i] / step) * step)
                px = opt_px.get((d, k, kind))
                if px is None: continue
                a_in = px.open.reindex([t_in]).values[0] if t_in in px.index else np.nan
                a_out = px.open.reindex([t_out]).values[0] if t_out in px.index else np.nan
                if np.isnan(a_in) or np.isnan(a_out) or a_in <= 0: continue
                def spr(t):
                    best = None
                    for key, rows in spreads.items():
                        if key[0] == d and key[1] == k and key[2] == kind:
                            for (tt, b, a) in rows:
                                if tt <= t.strftime("%H:%M") and b > 0 and a > 0:
                                    best = (a - b)
                    return best
                s_in, s_out = spr(t_in), spr(t_out)
                if s_in is None or s_out is None: continue
                fill_in, fill_out = a_in + s_in / 2, max(a_out - s_out / 2, 0.05)
                lots = float(np.clip(np.floor(RISK / (risk_m[q] * lot)), 1, MAX_LOTS)); qty = lots * lot
                bv, sv = fill_in * qty, fill_out * qty; ex = I.exch_rate(sym) * (bv + sv); se = 1e-6 * (bv + sv)
                chg = 40 + 0.0015 * sv + ex + se + 0.00003 * bv + 0.18 * (40 + ex + se)
                mod = money(sym, np.array([p_in_m[q]]), np.array([p_out_m[q]]), np.array([risk_m[q]]), 0.0025)[0][0]
                out.append(dict(k_grid=kk, sym=sym, date=d, side=r.side, why=r.why, pts=r.side * (r.xs - F["c"][r.i]),
                                spread_pct=round(s_in / a_in * 100, 3), real=(fill_out - fill_in) * qty - chg, model=mod, lots=lots,
                                liquid=s_in / a_in <= 0.005, spread_rs=(s_in / 2 + s_out / 2) * qty, fees=chg))
    B = pd.DataFrame(out); B.to_pickle("/tmp/round34_B.pkl")
    return B


def summarise_b(B):
    if B.empty:
        print("part B: no trades"); return
    rows = []
    for kk, g in B.groupby("k_grid"):
        for liq in (False, True):
            h = g[g.liquid] if liq else g
            if len(h) == 0: continue
            rows.append(dict(setting=label(GRID[kk]) + (" + spread <= 0.5%" if liq else ""), n=len(h), real=round(h.real.sum()),
                             model=round(h.model.sum()), win=round((h.real > 0).mean() * 100, 1), avg_pts=round(h.pts.mean(), 2)))
    R = pd.DataFrame(rows).sort_values("real", ascending=False)
    print(R.head(15).to_string()); print("positive on real prices:", int((R.real > 0).sum()), "of", len(R))
    RES["part_b"] = dict(days=B.date.nunique(), settings=len(R), positive=int((R.real > 0).sum()), top=R.head(15).to_dict("records"),
                         real_total=round(float(B.real.sum())), model_total=round(float(B.model.sum())),
                         median_spread_pct=round(float(B.spread_pct.median()), 3), liquid_share=round(float(B.liquid.mean() * 100), 1),
                         spread_rs_per_trade=round(float(B.spread_rs.mean())), fees_rs_per_trade=round(float(B.fees.mean())),
                         corr_real_model=round(float(B.real.corr(B.model)), 2))
    print({k: v for k, v in RES["part_b"].items() if k != "top"})


if __name__ == "__main__":
    A = part_a() if not os.path.exists("/tmp/round34_A.pkl") or "--fresh" in sys.argv else pd.read_pickle("/tmp/round34_A.pkl")
    summarise_a(A)
    B = part_b() if not os.path.exists("/tmp/round34_B.pkl") or "--fresh" in sys.argv else pd.read_pickle("/tmp/round34_B.pkl")
    summarise_b(B)
    json.dump(RES, open(os.path.join(HERE, "round34_results.json"), "w"), indent=1, default=str)
    print("saved")
