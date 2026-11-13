"""Round 101 helpers: today's Autopilot replay (exactly outcomes.run_cfg) with optional extra strategy pieces, a bar simulator
and the round-34 option pricing. Design period 2023-24, unseen 2025-26 (house convention)."""
import os, sys, pickle, warnings, math
warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.environ.get("R101_OUT") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(OUT, exist_ok=True); sys.path.insert(0, ROOT + "/research"); sys.path.insert(0, ROOT)
os.chdir(ROOT + "/research")
import numpy as np, pandas as pd
import outcomes as O
import round87_tradingview as M
from fno import portfolio_bt as PB, config as C
R34 = M.R34
M.DATA = R34.DATA = ROOT + "/research/data"
SYMS = ["NIFTY", "BANKNIFTY", "SENSEX"]
END = "2026-10-16"
PER = {"2023-24": ("2023-01-02", "2024-12-31"), "2025-26": ("2025-01-01", END), "full": ("2023-01-02", END)}
YEARS = {"2023": ("2023-01-02", "2023-12-31"), "2024": ("2024-01-01", "2024-12-31"), "2025": ("2025-01-01", "2025-12-31"), "2026": ("2026-01-01", END)}
B0 = dict(O.B0)
_F = OUT + "/base3.pkl"


def base_sets():
    if os.path.exists(_F):
        return pickle.load(open(_F, "rb"))
    k, ak, hk = PB._trade_key(B0), PB._alt_key(B0), PB._hi_key(B0)
    T = pd.concat([O.trades(k, s) for s in SYMS], ignore_index=True)
    A = pd.concat([O.trades(ak, s) for s in SYMS], ignore_index=True) if ak else None
    H = pd.concat([O.trades(hk, s) for s in SYMS], ignore_index=True) if hk else None
    pickle.dump((T, A, H), open(_F, "wb"))
    return T, A, H


def replay(cap, a, b, extra=None, T=None, A=None, H=None, raw=False, **over):
    """Today's Autopilot (or with `over` changes that do not need new trades) from `cap` over [a, b]; `extra` = list of piece frames."""
    if T is None:
        T, A, H = base_sets()
    q = {**B0, **over, "capital": cap}
    if extra:
        T = pd.concat([T] + list(extra), ignore_index=True)
        A = pd.concat([A] + list(extra), ignore_index=True) if A is not None else None
        H = pd.concat([H] + list(extra), ignore_index=True) if H is not None else None
    f = lambda X: None if X is None else X[(X.date >= a) & (X.date <= b)]
    R = PB.replay(f(T), q["capital"], q["sizing"], q["risk_pct"], q["lots"], q["confidence"], q["gates"], q["max_lots"],
                  q["dd_half"] and q["sizing"] != "fixed", f(A), cpr=q.get("cpr_size", False) and q["sizing"] != "fixed", hi=f(H),
                  channel=q.get("channel", True))
    if raw:
        return R
    s = PB._summary(R, q["capital"])
    if not s.get("trades"):
        return {"final": float(cap), "dd": 0.0, "trades": 0}
    return {"final": float(s["final"]), "dd": float(s["max_dd_pct"]), "trades": int(s["trades"])}


def bar_of(sym, tf):
    df = M.bars(sym, tf)
    o, h, l, c = (df[k].values.astype(float) for k in ("open", "high", "low", "close"))
    hm = np.asarray(df.index.hour * 60 + df.index.minute)
    day = np.asarray(df.index.normalize().astype("int64"))
    return df, o, h, l, c, hm, day


def atr(h, l, c, n=14):
    pc = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.vstack([h - l, np.abs(h - pc), np.abs(l - pc)]), axis=0)
    return pd.Series(tr).ewm(alpha=1 / n, adjust=False).mean().values


def ema(x, n):
    return pd.Series(x).ewm(span=n, adjust=False).mean().values


def simulate(o, h, l, c, hm, day, L, S, risk, tp_R=0.0, xL=None, xS=None, first=9 * 60 + 20, last=14 * 60 + 45, sq=15 * 60 + 15,
             per_day=1, step=5, trail_R=0.0, be_R=0.0, tstop=0):
    """Signals L / S are decided at a bar's close; entry at the next bar's open (same day, entry bar starts within first..last).
    risk = index points to the stop (array, at the signal bar). Stop is checked before target inside a bar (conservative).
    xL / xS: exit a long / short at that bar's close. trail_R: stop follows the best close by trail_R x risk once ahead.
    be_R: move the stop to entry once +be_R x risk. tstop: exit after that many bars. Everything is closed in the bar that
    contains `sq`. Returns rows (i, j, ep, xs, side, sl0)."""
    n = len(c); rows = []; i = 1; taken = 0; cur_day = -1
    last_start = sq - step
    while i < n:
        if day[i] != cur_day:
            cur_day = day[i]; taken = 0
        j0 = i - 1
        if day[j0] != day[i] or taken >= per_day or not (first <= hm[i] <= last):
            i += 1; continue
        s = 1 if L[j0] else (-1 if S[j0] else 0)
        r = risk[j0]
        if s == 0 or not (r > 0):
            i += 1; continue
        ep = o[i]; sl0 = sl = ep - s * r; tp = ep + s * tp_R * r if tp_R else None
        best = ep; j = i; xs = None
        while True:
            adv, fav = (l[j], h[j]) if s > 0 else (h[j], l[j])
            if s * (adv - sl) <= 0:
                xs = o[j] if (j > i and s * (o[j] - sl) < 0) else sl; break
            if tp is not None and s * (fav - tp) >= 0:
                xs = o[j] if (j > i and s * (o[j] - tp) > 0) else tp; break
            if (s > 0 and xL is not None and xL[j]) or (s < 0 and xS is not None and xS[j]):
                xs = c[j]; break
            if hm[j] >= last_start or j + 1 >= n or day[j + 1] != day[i]:
                xs = c[j]; break
            if tstop and j - i + 1 >= tstop:
                xs = c[j]; break
            if s * (c[j] - best) > 0:
                best = c[j]
            if be_R and s * (best - ep) >= be_R * r:
                sl = ep if s * (ep - sl) > 0 else sl
            if trail_R and s * (best - ep) > 0:
                t = best - s * trail_R * r
                if s * (t - sl) > 0:
                    sl = t
            j += 1
        rows.append((i, j, ep, xs, s, sl0)); taken += 1
        i = j + 1
    return rows


def price(sym, df, rows, step=5):
    """rows from simulate -> priced trades (1 lot ATM, 0.5% slippage a fill, charges): columns of round 87 + rs (Rs per trade)."""
    if not rows:
        return pd.DataFrame()
    T = pd.DataFrame(rows, columns=["i", "j", "ep", "xs", "side", "sl"]).astype({"i": int, "j": int})
    T["date"] = df.index[T.i.values].strftime("%Y-%m-%d"); T["t_in"] = df.index[T.i.values].strftime("%H:%M")
    T["t_out"] = (df.index[T.j.values] + pd.Timedelta(minutes=step)).strftime("%H:%M")
    T["pts"] = T.side * (T["xs"] - T.ep)
    return M.price(sym, df, T)


def to_piece(sym, T, label):
    """Priced trades -> rows the Autopilot replay understands (as rounds 87b / 89b)."""
    lot = T["lot"].values
    pi = T.prem_in.values * 1.005; po = np.maximum(T.prem_out.values * 0.995, 0.05)
    risk = np.abs(T.ep.values - T.sl.values)
    out = pd.DataFrame({"date": T.date, "entry_time": T.t_in, "exit_time": [min(x, "15:20") for x in T.t_out], "sym": sym,
                        "strategy": f"{label} r101", "side": np.where(T.side > 0, "CALL", "PUT"), "prem_in": pi, "prem_out": po, "lot": lot,
                        "risk_lot": np.maximum(T.risk.values * 1.005, pi * 0.05) * lot, "R": T.pts.values / np.maximum(risk, 1e-9),
                        "spot_in": T.ep.values, "spot_out": T["xs"].values, "reason": "r101", "pieces": 1})
    out["risk_lot_capped"] = out["risk_lot"]
    out["cpr_w"] = PB.cpr_widths(O.candles(sym)).reindex(out.date).values
    out["score"] = PB._scores(out, O.candles(sym))
    return out


def summ(T):
    if T is None or len(T) == 0:
        return {"n": 0, "a": 0.0, "b": 0.0, "a_n": 0, "b_n": 0, "a_avg": 0.0, "b_avg": 0.0, "win": 0.0}
    a, b = T[T.date < "2025"], T[T.date >= "2025"]
    return {"n": int(len(T)), "a_n": int(len(a)), "b_n": int(len(b)), "a": float(a.rs.sum()), "b": float(b.rs.sum()),
            "a_avg": float(a.rs.mean()) if len(a) else 0.0, "b_avg": float(b.rs.mean()) if len(b) else 0.0, "win": float((T.rs > 0).mean()),
            "pts": float(T.pts.mean()), "years": T.groupby(T.date.str[:4]).rs.sum().round(0).to_dict()}
