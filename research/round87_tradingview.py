"""Round 87 (5 Nov 2026): TradingView's built-in strategy scripts on Nifty / Bank Nifty / Sensex index options.

Coded as TradingView ships them (default inputs), on 5-minute and 15-minute index candles, Jan 2023 - Oct 2026:
  barupdn   BarUpDn Strategy: long when close > open and open > previous close; short mirror (market, next open)
  bb        Bollinger Bands Strategy (20, 2): close crosses up through the lower band -> long; crosses down through the upper -> short
  channel   Channel Breakout Strategy (5): buy-stop at the 5-bar high, sell-stop at the 5-bar low, every bar
  consec    Consecutive Up/Down Strategy (3, 3): 3 higher closes in a row -> long; 3 lower closes -> short
  greedy    Greedy Strategy: bar gaps above the previous high -> buy-stop at the previous high; gap below the low -> sell-stop;
            adds 'Up'/'Dn' re-entries in TradingView (pyramiding) - here one position at a time
  inside    InSide Bar Strategy: inside bar that closes green -> long, red -> short
  momentum  Momentum Strategy (12): momentum > 0 and rising -> buy-stop at the high; < 0 and falling -> sell-stop at the low
  ma2       MovingAvg2Line Cross (SMA 9 / 18): cross up -> long, cross down -> short
  volexp    Volatility Expansion Close Strategy (5, 0.75 ATR): buy-stop at close + 0.75 ATR(5), sell-stop at close - 0.75 ATR(5)
All are stop-and-reverse in TradingView; here the long becomes a bought ATM call and the short a bought ATM put (no option
selling), one position per index, entries 09:20-14:45, everything closed at 15:15 (intraday, like Autopilot).
Indicators run on the continuous series (as on a TradingView chart). Options priced like the app (round 34 model: calibrated
Black-Scholes, time decay, real charges), 1 lot, 0.5% slippage per fill. A random-direction twin (same entry/exit bars, coin-flip
side) shows whether the signal's direction adds anything. 'Liquidity levels / order blocks' is not re-run: rounds 49, 52, 61
and the liquidity research note (rliq) covered it.
Usage: python research/round87_tradingview.py [data_dir]
"""
import os, sys, json, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
DATA = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "data")
import numpy as np, pandas as pd
import round34 as R34
R34.DATA = DATA
R34.D.master = lambda: {}
SYMS = ["NIFTY", "BANKNIFTY", "SENSEX"]
SLIP = 0.005
STRATS = ["barupdn", "bb", "channel", "consec", "greedy", "inside", "momentum", "ma2", "volexp"]
_C = {}


def bars(sym, tf):
    if (sym, tf) not in _C:
        a = R34.m1(sym)
        rule = {"5m": "5min", "15m": "15min"}[tf]
        g = a.groupby(a.index.date, group_keys=False).apply(
            lambda x: x.resample(rule, origin=x.index[0], label="left", closed="left").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna())
        _C[(sym, tf)] = g
    return _C[(sym, tf)]


def vix():
    if "vix" not in _C:
        v = pd.read_csv(os.path.join(DATA, "INDIAVIX_1m_upstox.csv.gz"), parse_dates=["ts"]).set_index("ts").close
        _C["vix"] = v[~v.index.duplicated()].sort_index()
    return _C["vix"]


def sma(x, n):
    return pd.Series(x).rolling(n).mean().values


def atr(h, l, c, n):
    pc = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.vstack([h - l, np.abs(h - pc), np.abs(l - pc)]), axis=0)
    return pd.Series(tr).rolling(n).mean().values      # TradingView ta.atr uses RMA; SMA here is close enough for a 5-bar stop


def orders(name, o, h, l, c):
    """Per bar (decided at that bar's close): long order, short order. Each is NaN (none), 0 (market at next open) or a stop price."""
    n = len(c); L = np.full(n, np.nan); S = np.full(n, np.nan)
    pc = np.r_[np.nan, c[:-1]]
    if name == "barupdn":
        L[(c > o) & (o > pc)] = 0; S[(c < o) & (o < pc)] = 0
    elif name == "bb":
        m = sma(c, 20); sd = pd.Series(c).rolling(20).std(ddof=0).values
        up, lo = m + 2 * sd, m - 2 * sd
        cu = (c > lo) & (pc <= np.r_[np.nan, lo[:-1]]); cd = (c < up) & (pc >= np.r_[np.nan, up[:-1]])
        L[cu] = lo[cu]; S[cd] = up[cd]                   # stop orders at the band: price is already through it -> next open
    elif name == "channel":
        L[:] = pd.Series(h).rolling(5).max().values; S[:] = pd.Series(l).rolling(5).min().values
    elif name == "consec":
        upc = np.zeros(n); dnc = np.zeros(n)
        for i in range(1, n):
            upc[i] = upc[i - 1] + 1 if c[i] > c[i - 1] else 0
            dnc[i] = dnc[i - 1] + 1 if c[i] < c[i - 1] else 0
        L[upc >= 3] = 0; S[dnc >= 3] = 0
    elif name == "greedy":
        ph, pl = np.r_[np.nan, h[:-1]], np.r_[np.nan, l[:-1]]
        g_up, g_dn = o > ph, o < pl
        L[g_up] = ph[g_up]; S[g_dn] = pl[g_dn]
    elif name == "inside":
        ph, pl = np.r_[np.nan, h[:-1]], np.r_[np.nan, l[:-1]]
        ins = (h < ph) & (l > pl)
        L[ins & (c > o)] = 0; S[ins & ~(c > o)] = 0
    elif name == "momentum":
        m0 = c - np.r_[np.full(12, np.nan), c[:-12]]; m1 = m0 - np.r_[np.nan, m0[:-1]]
        L[(m0 > 0) & (m1 > 0)] = h[(m0 > 0) & (m1 > 0)]; S[(m0 < 0) & (m1 < 0)] = l[(m0 < 0) & (m1 < 0)]
    elif name == "ma2":
        f, s = sma(c, 9), sma(c, 18); d = f - s; pd_ = np.r_[np.nan, d[:-1]]
        L[(d > 0) & (pd_ <= 0)] = 0; S[(d < 0) & (pd_ >= 0)] = 0
    elif name == "volexp":
        a = atr(h, l, c, 5) * 0.75
        L[:] = c + a; S[:] = c - a
    return L, S


def run(sym, tf, name):
    df = bars(sym, tf)
    o, h, l, c = (df[k].values for k in ("open", "high", "low", "close"))
    hm = np.asarray(df.index.hour * 60 + df.index.minute)
    day = np.asarray(df.index.normalize().astype("int64"))
    L, S = orders(name, o, h, l, c)
    a14 = atr(h, l, c, 14)
    step = 5 if tf == "5m" else 15
    last_start = 15 * 60 + 15 - step                   # last bar that starts before 15:15
    rows, pos = [], None                              # pos = (side, entry bar, entry px)
    for i in range(1, len(c)):
        new_day = day[i] != day[i - 1]
        if new_day and pos is not None:              # safety: should already be closed at 15:15
            pos = None
        j = i - 1                                     # orders placed at bar j's close work during bar i
        if day[j] != day[i]:
            continue
        fill = None
        if 9 * 60 + 20 <= hm[i] <= 14 * 60 + 45:
            cand = []
            for side, px in ((1, L[j]), (-1, S[j])):
                if np.isnan(px) or (pos is not None and pos[0] == side):
                    continue
                if px == 0:
                    cand.append((0.0, side, o[i]))
                elif side == 1 and h[i] >= px:
                    f = max(o[i], px); cand.append((abs(f - o[i]), side, f))
                elif side == -1 and l[i] <= px:
                    f = min(o[i], px); cand.append((abs(f - o[i]), side, f))
            if cand:
                cand.sort(key=lambda x: x[0]); fill = cand[0]
        if fill is not None:
            _, side, f = fill
            if pos is not None:                       # stop-and-reverse
                rows.append((pos[1], i, pos[2], f, pos[0]))
            pos = (side, i, f)
        if pos is not None and hm[i] >= last_start:
            rows.append((pos[1], i, pos[2], c[i], pos[0])); pos = None
    if not rows:
        return pd.DataFrame()
    T = pd.DataFrame(rows, columns=["i", "j", "ep", "xs", "side"])
    T["sl"] = T.ep - T.side * np.nan_to_num(a14[T.i.values], nan=float(np.nanmean(a14)))
    T["date"] = df.index[T.i.values].strftime("%Y-%m-%d"); T["t_in"] = df.index[T.i.values].strftime("%H:%M")
    T["t_out"] = (df.index[T.j.values] + pd.Timedelta(minutes=step)).strftime("%H:%M")
    T["pts"] = T.side * (T["xs"] - T.ep)
    return price(sym, df, T)


def price(sym, df, T, twin=None):
    if twin is not None:
        T = T.copy(); flip = np.random.default_rng(twin).choice([1, -1], len(T))
        T["side"] = T.side * flip; T["pts"] = T.pts * flip; T["sl"] = T.ep - T.side * (T.ep - T.sl).abs()
    ent = np.full(len(df), np.nan); ent[T.i.values] = T.ep.values
    p_in, p_out, risk, _ = R34.price(sym, {"idx": df.index, "c": ent}, T, vix())
    rs, _, _ = R34.money(sym, p_in, p_out, risk, SLIP, "fixed", 1)
    T = T.copy(); T["prem_in"], T["prem_out"], T["risk"], T["rs"] = p_in, p_out, risk, rs
    T["lot"] = R34.D.lot_size(sym)
    return T


def summ(T):
    if T is None or len(T) == 0:
        return {"n": 0}
    a, b = T[T.date < "2025"], T[T.date >= "2025"]
    return {"n": int(len(T)), "per_day": round(len(T) / T.date.nunique(), 1), "win": round(float((T.rs > 0).mean()), 3),
            "pts": round(float(T.pts.mean()), 1), "rs_trade": round(float(T.rs.mean()), 0),
            "a": round(float(a.rs.sum()), 0), "b": round(float(b.rs.sum()), 0),
            "years": T.groupby(T.date.str[:4]).rs.sum().round(0).to_dict()}


def main():
    res = {}
    for tf in ("5m", "15m"):
        for name in STRATS:
            tot, tw = [], []
            for sym in SYMS:
                T = run(sym, tf, name)
                if len(T):
                    T["sym"] = sym; tot.append(T)
                    tw.append(price(sym, bars(sym, tf), T, twin=7).assign(sym=sym))
            A, W = pd.concat(tot), pd.concat(tw)
            r = {"all": summ(A), "twin": summ(W), "by_sym": {s: summ(A[A.sym == s]) for s in SYMS},
                 "idx_pts_before_costs": round(float(A.pts.mean()), 2)}
            res[f"{tf}|{name}"] = r
            s = r["all"]
            print(f"{tf:>3} {name:<9} n={s['n']:>6} ({s['per_day']}/day) win {s['win']:.0%}  pts/trade {s['pts']:+.1f}  "
                  f"Rs/trade/lot {s['rs_trade']:+.0f}  2023-24 {s['a']:+,.0f}  2025-26 {s['b']:+,.0f}  | twin Rs/trade {r['twin']['rs_trade']:+.0f}  "
                  f"| " + "  ".join(f"{k[:2]} {v['a']:+,.0f}/{v['b']:+,.0f}" for k, v in r["by_sym"].items()), flush=True)
            A.to_csv(os.path.join(HERE, f"out_round87_{tf}_{name}.csv.gz"), index=False)
    json.dump(res, open(os.path.join(HERE, "round87_results.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
