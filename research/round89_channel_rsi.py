"""Round 89 (5 Nov 2026): TradingView Channel Breakout + RSI filter (user request).

Channel breakout as in round 87 (buy-stop at the last N bars' high, sell-stop at their low, stop-and-reverse, bought ATM
calls / puts, entries 09:20-14:45, out at 15:15), N = 5 (TradingView default) and 20 (Donchian). An order is only placed
when RSI(14) (Wilder, as TradingView's ta.rsi) at the bar's close agrees:
  none    no filter (round 87 baseline)
  50      long only if RSI > 50, short only if RSI < 50          (trend side)
  60/40   long only if RSI > 60, short only if RSI < 40          (momentum, the creators' rule)
  not_x   long only if RSI < 70, short only if RSI > 30          (skip overbought / oversold breakouts)
  band    long only if 50 < RSI < 70, short only if 30 < RSI < 50
5m and 15m, Nifty / Bank Nifty / Sensex, Jan 2023 - Oct 2026, app option pricing, 0.5% slippage, real charges, 1 lot,
random-direction twin. Usage: python research/round89_channel_rsi.py [data_dir]
"""
import os, sys, json, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
import round87_tradingview as M

FILTERS = {"none": None, "50": (50, 100, 0, 50), "60/40": (60, 100, 0, 40), "not_x": (0, 70, 30, 100), "band": (50, 70, 30, 50)}
_orig = M.orders


def rsi(c, n=14):
    d = np.diff(c, prepend=np.nan)
    up = pd.Series(np.where(d > 0, d, 0.0)).ewm(alpha=1 / n, adjust=False).mean()
    dn = pd.Series(np.where(d < 0, -d, 0.0)).ewm(alpha=1 / n, adjust=False).mean()
    return (100 - 100 / (1 + up / dn.replace(0, np.nan))).values


def orders(name, o, h, l, c):
    if not name.startswith("ch"):
        return _orig(name, o, h, l, c)
    _, n, f = name.split("|")
    n = int(n)
    L = pd.Series(h).rolling(n).max().values.copy(); S = pd.Series(l).rolling(n).min().values.copy()
    lim = FILTERS[f]
    if lim:
        r = rsi(c)
        L[~((r > lim[0]) & (r < lim[1]))] = np.nan
        S[~((r > lim[2]) & (r < lim[3]))] = np.nan
    return L, S


M.orders = orders


def main():
    res, keep = {}, {}
    for tf in ("5m", "15m"):
        for n in (5, 20):
            for f in FILTERS:
                name = f"ch|{n}|{f}"
                tot, tw = [], []
                for sym in M.SYMS:
                    T = M.run(sym, tf, name)
                    if len(T):
                        tot.append(T.assign(sym=sym)); tw.append(M.price(sym, M.bars(sym, tf), T, twin=7).assign(sym=sym))
                A, W = pd.concat(tot), pd.concat(tw)
                r = {"all": M.summ(A), "twin": M.summ(W), "by_sym": {s: M.summ(A[A.sym == s]) for s in M.SYMS}}
                res[f"{tf}|{name}"] = r
                keep[f"{tf}|{name}"] = A
                s = r["all"]
                print(f"{tf:>3} N={n:<2} RSI {f:<6} n={s['n']:>6} ({s['per_day']}/day) win {s['win']:.0%} pts {s['pts']:+5.1f} "
                      f"Rs/trade {s['rs_trade']:+5.0f}  23-24 {s['a']:>+10,.0f}  25-26 {s['b']:>+10,.0f} | twin {r['twin']['rs_trade']:+.0f} | "
                      + "  ".join(f"{k[:2]} {v['a']:+,.0f}/{v['b']:+,.0f}" for k, v in r["by_sym"].items()), flush=True)
    json.dump(res, open(os.path.join(HERE, "round89_results.json"), "w"), indent=1)
    return keep


if __name__ == "__main__":
    main()
