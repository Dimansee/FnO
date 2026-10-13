"""Round 16 — cash-equity (long-only, delivery, no leverage) stock rules on the Nifty 500 list, as a possible second,
uncorrelated income stream next to the intraday option rules.

Data: data/stocks_1d.csv.gz (499 NSE stocks, TODAY's Nifty 500 list -> survivorship bias), data/NIFTY_1d.csv.gz (real index
daily from 2023-10-09 only; before that an equal-weight proxy of today's Nifty 50 names, spliced, is used for the benchmark /
regime filter / RS — see nifty_series()). Capital ₹2 lakh, equal-weight slots, 0.1 % per side all-in cost on every fill.
Train = 2020-10 .. 2025-06, unseen = 2025-07 .. 2026-10 (nothing tuned on unseen; the parameter variants were fixed up front).
Writes round16_stocks.json and prints a readable table.  Runtime ~1-2 min (+2 min if the option daily P&L cache is missing).
"""
import json, os, sys, time
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CAP = 200_000.0
COST = 0.001                      # per side
TRAIN_END = pd.Timestamp("2025-06-30")
SCRATCH = "/tmp/claude-0/-home-claude-fno/bd78bd3c-d191-5480-8e56-6fe861f828d6/scratchpad"
OPT_CACHE = os.path.join(SCRATCH, "option_trades.csv")
N50 = """ADANIENT ADANIPORTS APOLLOHOSP ASIANPAINT AXISBANK BAJAJ-AUTO BAJFINANCE BAJAJFINSV BEL BHARTIARTL CIPLA COALINDIA
DRREDDY EICHERMOT ETERNAL GRASIM HCLTECH HDFCBANK HDFCLIFE HEROMOTOCO HINDALCO HINDUNILVR ICICIBANK INDUSINDBK INFY ITC JIOFIN
JSWSTEEL KOTAKBANK LT M&M MARUTI NESTLEIND NTPC ONGC POWERGRID RELIANCE SBILIFE SBIN SHRIRAMFIN SUNPHARMA TATACONSUM
TATAMOTORS TATASTEEL TCS TECHM TITAN TRENT ULTRACEMCO WIPRO""".split()

t0 = time.time()


# ----------------------------------------------------------------------------------------------------------------- data
def load():
    d = pd.read_csv(os.path.join(HERE, "data/stocks_1d.csv.gz"), parse_dates=["date"])
    f = d["adj close"] / d["close"]                    # split/dividend adjustment applied to the whole bar
    for c in ("open", "high", "low", "close"):
        d[c] = d[c] * f
    W = {c: d.pivot(index="date", columns="sym", values=c) for c in ("open", "high", "low", "close", "volume")}
    return W


W = load()
DATES = W["close"].index
SYMS = W["close"].columns
T, S = W["close"].shape
O, H, L, C, V = (W[c].values for c in ("open", "high", "low", "close", "volume"))
Cff = W["close"].ffill().values                     # for mark-to-market only
TURN = (W["close"] * W["volume"]).rolling(50, min_periods=20).median().values   # ₹ turnover / day


ANCHOR = 11934.0                                   # Nifty close on 2020-10-13 (from memory, not from data) — used only to de-drift the proxy


def nifty_series():
    """Real Nifty from 2023-10-09; before that an equal-weight daily-return proxy of today's Nifty 50 names, chained backwards
    from the first real level. The raw equal-weight proxy drifts up vs the real index (survivorship + equal weight: +41 % vs
    +15 % on the 2023-10..2026-10 overlap), so a constant daily drift is removed to pin the 2020-10-13 level to ANCHOR.
    Returns the spliced close series plus the overlap check."""
    real = pd.read_csv(os.path.join(HERE, "data/NIFTY_1d.csv.gz"), parse_dates=["ts"]).set_index("ts")["close"]
    real = real.reindex(DATES).ffill()
    cols = [s for s in N50 if s in SYMS]
    r = W["close"][cols].pct_change().mean(axis=1).fillna(0)
    first = real.first_valid_index()
    pre = r.loc[:first]

    def chain(rr):
        lvl = real.loc[first] / np.cumprod(1 + rr.values[::-1][:-1])[::-1]
        return pd.Series(np.r_[lvl, real.loc[first]], index=rr.index)

    raw = chain(pre)
    n = len(pre) - 1
    drift = (ANCHOR / raw.iloc[0]) ** (1 / n) - 1           # per-day excess drift of the proxy (positive = proxy too strong)
    adj = chain((1 + pre) / (1 + drift) - 1)
    full = pd.concat([adj.iloc[:-1], real.loc[first:]])
    pr = (1 + r.loc[first:]).cumprod()
    chk = {"n50_names_used": len(cols),
           "overlap_daily_corr": round(float(np.corrcoef(r.loc[first:].values[1:], real.loc[first:].pct_change().values[1:])[0, 1]), 3),
           "overlap_real_total_return_pct": round(float(real.iloc[-1] / real.loc[first] - 1) * 100, 1),
           "overlap_proxy_total_return_pct": round(float(pr.iloc[-1] - 1) * 100, 1),
           "raw_proxy_level_2020_10_13": round(float(raw.iloc[0])), "anchor_2020_10_13": ANCHOR,
           "drift_removed_pct_per_year": round(float(drift * 252 * 100), 1),
           "note": "proxy used for 2020-10..2023-10 benchmark, regime filter and RS; de-drifted so 2020-10-13 = 11,934 (memory)"}
    return full, chk


NIFTY, NIFTY_CHK = nifty_series()
NIFTY_SMA200 = NIFTY.rolling(200, min_periods=120).mean()
REGIME_OK = (NIFTY > NIFTY_SMA200).values


# ----------------------------------------------------------------------------------------------------------- indicators
def sma(a, n):
    return pd.DataFrame(a).rolling(n, min_periods=n).mean().values


def rsi(a, n):
    d = np.diff(a, axis=0, prepend=np.nan)
    up = np.where(d > 0, d, 0.0); dn = np.where(d < 0, -d, 0.0)
    if n <= 2:
        au = pd.DataFrame(up).rolling(n, min_periods=n).mean().values
        ad = pd.DataFrame(dn).rolling(n, min_periods=n).mean().values
    else:
        au = pd.DataFrame(up).ewm(alpha=1 / n, min_periods=n, adjust=False).mean().values
        ad = pd.DataFrame(dn).ewm(alpha=1 / n, min_periods=n, adjust=False).mean().values
    with np.errstate(divide="ignore", invalid="ignore"):
        r = 100 - 100 / (1 + au / ad)
    r = np.where(ad == 0, 100.0, r)
    return r


def atr(n):
    pc = np.roll(Cff, 1, axis=0); pc[0] = np.nan
    tr = np.nanmax(np.stack([H - L, np.abs(H - pc), np.abs(L - pc)]), axis=0)
    return pd.DataFrame(tr).rolling(n, min_periods=n).mean().values


def ret(n):
    return C / np.roll(C, n, axis=0) - 1


LIQ = TURN > 1e7                                     # ≥ ₹1 cr median daily turnover
SMA200 = sma(C, 200); SMA10 = sma(C, 10)
VOL50 = pd.DataFrame(V).rolling(50, min_periods=50).mean().values
HI252 = pd.DataFrame(H).rolling(252, min_periods=252).max().shift(1).values     # prior 52-week high
HI20 = pd.DataFrame(H).rolling(20, min_periods=20).max().shift(1).values
ATR20 = atr(20)
RSI2 = rsi(C, 2); RSI14 = rsi(C, 14)
R63 = ret(63); R126 = ret(126); R252_21 = np.roll(C, 21, axis=0) / np.roll(C, 252, axis=0) - 1; R3 = ret(3)
for a in (R63, R126, R252_21, R3):
    a[:260] = np.nan
NR63 = (NIFTY / NIFTY.shift(63) - 1).values
RS63 = R63 - NR63[:, None]


# ------------------------------------------------------------------------------------------------------------- engine
def run_signal_strategy(entry, score, maxpos, stop_pct=None, trail_pct=None, trail_atr=None, max_hold=None,
                        exit_sig=None, target_pct=None, name=""):
    """Generic daily loop. entry[i,s] computed at close i -> buy at open i+1 (best `score` first while slots free).
    Exits: stop (fixed % of entry, or trailing % off the highest close, or chandelier k*ATR20 off the highest close) filled
    at the stop level, or at the open if the day gaps through; target filled at the target level; exit_sig[i,s] at close i
    -> sell at open i+1; max_hold trading days -> sell at the open after the N-th close. Equal slots = equity / maxpos."""
    cash = CAP; pos = {}                              # sym_idx -> dict
    eq = np.full(T, np.nan); trades = []; pend_buy = []; pend_sell = set()
    for i in range(T):
        # ---- exits first
        for s in list(pos):
            p = pos[s]
            if np.isnan(O[i, s]):
                continue
            px = None; why = None
            if s in pend_sell:
                px, why = O[i, s], p["why"]
            elif p["stop"] is not None and L[i, s] <= p["stop"]:
                px, why = min(O[i, s], p["stop"]), "stop"
            elif target_pct and H[i, s] >= p["entry"] * (1 + target_pct):
                px, why = max(O[i, s], p["entry"] * (1 + target_pct)), "target"
            if px is not None:
                cash += p["qty"] * px * (1 - COST)
                trades.append(dict(sym=SYMS[s], entry_date=str(DATES[p["i"]].date()), exit_date=str(DATES[i].date()),
                                   entry=round(p["entry"], 2), exit=round(px, 2), qty=p["qty"], hold=i - p["i"], why=why,
                                   pnl=round(p["qty"] * (px * (1 - COST) - p["entry"] * (1 + COST)), 2)))
                del pos[s]
        pend_sell = set()
        # ---- entries
        if pend_buy:
            equity = cash + sum(p["qty"] * Cff[i - 1, s] for s, p in pos.items())
            slot = equity / maxpos
            for s in pend_buy:
                if len(pos) >= maxpos or s in pos or np.isnan(O[i, s]):
                    continue
                qty = int(slot // (O[i, s] * (1 + COST)))
                if qty <= 0 or qty * O[i, s] * (1 + COST) > cash:
                    continue
                cash -= qty * O[i, s] * (1 + COST)
                st = O[i, s] * (1 - stop_pct) if stop_pct else None
                pos[s] = dict(i=i, entry=O[i, s], qty=qty, stop=st, hi=O[i, s], why=None)
            pend_buy = []
        # ---- update trails, exit signals, time exits (on close i)
        for s, p in pos.items():
            if not np.isnan(C[i, s]):
                p["hi"] = max(p["hi"], C[i, s])
            if trail_pct:
                p["stop"] = max(p["stop"] or 0, p["hi"] * (1 - trail_pct))
            if trail_atr and not np.isnan(ATR20[i, s]):
                p["stop"] = max(p["stop"] or 0, p["hi"] - trail_atr * ATR20[i, s])
            if max_hold and i - p["i"] >= max_hold:
                pend_sell.add(s); p["why"] = "time"
            elif exit_sig is not None and exit_sig[i, s]:
                pend_sell.add(s); p["why"] = "signal"
        # ---- new signals at close i
        if i >= 260 and len(pos) < maxpos:
            cand = np.where(entry[i] & LIQ[i])[0]
            cand = [s for s in cand if s not in pos]
            cand.sort(key=lambda s: -score[i, s])
            pend_buy = cand[: maxpos - len(pos) + 2]
        eq[i] = cash + sum(p["qty"] * Cff[i, s] for s, p in pos.items())
    return pd.Series(eq, index=DATES), trades


def run_momentum(lookback, top, regime, name=""):
    """Month-end ranking (at the last close of the month) -> trade at the next session's open. Equal weight over `top` names
    (slot = equity/top at each rebalance). regime: go to cash when Nifty < 200-day SMA at the month-end close."""
    cash = CAP; pos = {}; eq = np.full(T, np.nan); trades = []; target = None
    month = pd.Series(DATES.month, index=DATES)
    is_me = (month.shift(-1) != month).values
    for i in range(T):
        if target is not None:                          # rebalance at open i
            eqv = cash + sum(p["qty"] * Cff[i - 1, s] for s, p in pos.items())
            for s in list(pos):
                if s not in target and not np.isnan(O[i, s]):
                    p = pos.pop(s); px = O[i, s]
                    cash += p["qty"] * px * (1 - COST)
                    trades.append(dict(sym=SYMS[s], entry_date=str(DATES[p["i"]].date()), exit_date=str(DATES[i].date()),
                                       entry=round(p["entry"], 2), exit=round(px, 2), qty=p["qty"], hold=i - p["i"], why="rebal",
                                       pnl=round(p["qty"] * (px * (1 - COST) - p["entry"] * (1 + COST)), 2)))
            slot = eqv / top
            for s in target:
                if s in pos or np.isnan(O[i, s]):
                    continue
                qty = int(slot // (O[i, s] * (1 + COST)))
                if qty <= 0 or qty * O[i, s] * (1 + COST) > cash:
                    continue
                cash -= qty * O[i, s] * (1 + COST)
                pos[s] = dict(i=i, entry=O[i, s], qty=qty)
            target = None
        if is_me[i] and i >= 260:
            if regime and not REGIME_OK[i]:
                target = set()
            else:
                sc = np.where(LIQ[i] & ~np.isnan(lookback[i]), lookback[i], -np.inf)
                order = np.argsort(-sc)[:top]
                target = set(int(s) for s in order if sc[s] > -np.inf)
        eq[i] = cash + sum(p["qty"] * Cff[i, s] for s, p in pos.items())
    return pd.Series(eq, index=DATES), trades


# ------------------------------------------------------------------------------------------------------------ metrics
def curve_stats(eq, a, b):
    e = eq.loc[a:b].dropna()
    if len(e) < 2:
        return {}
    yrs = (e.index[-1] - e.index[0]).days / 365.25
    tot = e.iloc[-1] / e.iloc[0] - 1
    dd = (e / e.cummax() - 1).min()
    return {"return_pct": round(tot * 100, 1), "cagr_pct": round(((1 + tot) ** (1 / yrs) - 1) * 100, 1),
            "maxdd_pct": round(dd * 100, 1)}


def yearly(eq):
    e = eq.dropna().loc[START:]
    y = e.groupby(e.index.year).agg(["first", "last"])
    prev = e.iloc[0]
    out = {}
    for yr, row in y.iterrows():
            out[str(yr)] = round(float(row["last"] / prev - 1) * 100, 1); prev = row["last"]
    return out


def trade_stats(trades, a, b):
    tr = [t for t in trades if a <= pd.Timestamp(t["entry_date"]) <= b]
    if not tr:
        return {"trades": 0}
    p = np.array([t["pnl"] for t in tr])
    return {"trades": len(tr), "win_pct": round((p > 0).mean() * 100, 1), "avg_hold_days": round(np.mean([t["hold"] for t in tr]), 1),
            "net": round(p.sum()), "avg_pnl": round(p.mean()), "pf": round(p[p > 0].sum() / max(1e-9, -p[p <= 0].sum()), 2)}


def turnover(trades, eq, a, b):
    tr = [t for t in trades if a <= pd.Timestamp(t["entry_date"]) <= b]
    traded = sum(t["qty"] * (t["entry"] + t["exit"]) for t in tr)
    e = eq.loc[a:b].dropna(); yrs = (e.index[-1] - e.index[0]).days / 365.25
    return round(traded / e.mean() / yrs / 2, 1)          # round trips per year as a multiple of equity


START = DATES[260]                                   # first tradeable day after the 252-day warm-up (2021-11); Nifty measured from here too
HALVES = {"train": (START, TRAIN_END), "unseen": (TRAIN_END + pd.Timedelta(days=1), DATES[-1]), "full": (START, DATES[-1])}


def summarize(name, eq, trades):
    out = {"name": name, "yearly_pct": yearly(eq)}
    if trades:
        p = sorted((t["pnl"] for t in trades), reverse=True); tot = sum(p)
        out["top5_trades_share_of_net_pct"] = round(sum(p[:5]) / tot * 100, 1) if tot > 0 else None
    for h, (a, b) in HALVES.items():
        out[h] = {**curve_stats(eq, a, b), **trade_stats(trades, a, b)}
        if out[h].get("maxdd_pct"):
            out[h]["calmar"] = round(out[h]["cagr_pct"] / -out[h]["maxdd_pct"], 2)
        if trades:
            out[h]["turnover_x"] = turnover(trades, eq, a, b)
        out[h]["trades_per_month"] = round(out[h]["trades"] / max(1, (b - a).days / 30.44), 1)
    out["beats_nifty"] = bool(all(out[h]["cagr_pct"] > NB[h]["cagr_pct"] and out[h]["maxdd_pct"] > NB[h]["maxdd_pct"]
                                  for h in ("train", "unseen")))
    return out


NIFTY_EQ = NIFTY / NIFTY.loc[START] * CAP
NB = {h: curve_stats(NIFTY_EQ, a, b) for h, (a, b) in HALVES.items()}

# ------------------------------------------------------------------------------------------------------- strategies
RES = {}; CURVES = {}; TRADES = {}

def add(key, eq, trades):
    RES[key] = summarize(key, eq, trades); CURVES[key] = eq; TRADES[key] = trades
    r = RES[key]
    print(f"{key:34s} train {r['train'].get('cagr_pct', 0):6.1f}% / DD {r['train'].get('maxdd_pct', 0):6.1f}%   "
          f"unseen {r['unseen'].get('cagr_pct', 0):6.1f}% / DD {r['unseen'].get('maxdd_pct', 0):6.1f}%   "
          f"trades {r['full'].get('trades', 0):4d} win {r['full'].get('win_pct', 0):5.1f}% hold {r['full'].get('avg_hold_days', 0):5.1f}d "
          f"turn {r['full'].get('turnover_x', 0):4.1f}x  {'BEATS' if r['beats_nifty'] else ''}  ({time.time() - t0:.0f}s)")


print("Nifty B&H:", {h: NB[h] for h in HALVES}, "\nproxy check:", NIFTY_CHK)

# 1. cross-sectional momentum
for lb_name, lb in (("6m", R126), ("12-1", R252_21)):
    for regime in (False, True):
        eq, tr = run_momentum(lb, 10, regime)
        add(f"mom_{lb_name}_top10{'_regime' if regime else ''}", eq, tr)

# 2. 52-week-high breakout with volume
E2 = (C > HI252) & (V > 1.5 * VOL50)
SC2 = V / VOL50
eq, tr = run_signal_strategy(E2, SC2, 10, stop_pct=0.10, max_hold=60); add("brk52_stop10_t60", eq, tr)
eq, tr = run_signal_strategy(E2, SC2, 10, stop_pct=0.10, trail_pct=0.15, max_hold=60); add("brk52_stop10_trail15_t60", eq, tr)
eq, tr = run_signal_strategy(E2, SC2, 10, trail_pct=0.15, max_hold=60); add("brk52_trail15_t60", eq, tr)
eq, tr = run_signal_strategy(E2, SC2, 10, stop_pct=0.10, trail_pct=0.15, max_hold=60, exit_sig=None, name="x");
# 3. pullback in uptrend
X3 = C > SMA10
E3a = (C > SMA200) & (RSI2 < 10); E3b = (C > SMA200) & (RSI14 < 35)
eq, tr = run_signal_strategy(E3a, -RSI2, 10, stop_pct=0.07, max_hold=10, exit_sig=X3); add("pull_rsi2<10_sma10_t10", eq, tr)
eq, tr = run_signal_strategy(E3b, -RSI14, 10, stop_pct=0.07, max_hold=10, exit_sig=X3); add("pull_rsi14<35_sma10_t10", eq, tr)
eq, tr = run_signal_strategy(E3a & REGIME_OK[:, None], -RSI2, 10, stop_pct=0.07, max_hold=10, exit_sig=X3); add("pull_rsi2<10_regime", eq, tr)

# 4. relative-strength leaders, 20-day breakout, chandelier trail
def decile_mask(q):
    m = np.zeros_like(C, dtype=bool)
    x = np.where(LIQ & ~np.isnan(RS63), RS63, np.nan)
    thr = np.nanpercentile(x, q, axis=1)
    return x >= thr[:, None]
TOP10 = decile_mask(90); TOP20 = decile_mask(80)
E4a = TOP10 & (C > HI20); E4b = TOP20 & (C > HI20)
eq, tr = run_signal_strategy(E4a, RS63, 8, trail_atr=2.0); add("rs_top10%_brk20_2atr", eq, tr)
eq, tr = run_signal_strategy(E4a, RS63, 8, trail_atr=3.0); add("rs_top10%_brk20_3atr", eq, tr)
eq, tr = run_signal_strategy(E4b, RS63, 8, trail_atr=2.0); add("rs_top20%_brk20_2atr", eq, tr)
eq, tr = run_signal_strategy(E4a & REGIME_OK[:, None], RS63, 8, trail_atr=2.0); add("rs_top10%_brk20_2atr_regime", eq, tr)

# 5. short-term mean reversion
E5a = (R3 < -0.07) & (C > SMA200); E5b = (R3 < -0.10) & (C > SMA200)
eq, tr = run_signal_strategy(E5a, -R3, 10, max_hold=5, target_pct=0.05); add("mr_drop7_t5_tgt5", eq, tr)
eq, tr = run_signal_strategy(E5a, -R3, 10, max_hold=5); add("mr_drop7_t5", eq, tr)
eq, tr = run_signal_strategy(E5b, -R3, 10, max_hold=5, target_pct=0.05); add("mr_drop10_t5_tgt5", eq, tr)
eq, tr = run_signal_strategy(E5a, -R3, 10, max_hold=5, target_pct=0.05, stop_pct=0.07); add("mr_drop7_t5_tgt5_stop7", eq, tr)

# survivorship check: restrict to the 150 most liquid names each day (large caps rarely drop out of the list, so this universe
# is closer to what was investable at the time)
RANK = pd.DataFrame(TURN).rank(axis=1, ascending=False).values
LIQ150 = LIQ & (RANK <= 150)
LIQ_ALL = LIQ
LIQ = LIQ150
eq, tr = run_momentum(R126, 10, True); add("CHK150 mom_6m_top10_regime", eq, tr)
eq, tr = run_momentum(R252_21, 10, True); add("CHK150 mom_12-1_top10_regime", eq, tr)
eq, tr = run_signal_strategy(E5b, -R3, 10, max_hold=5, target_pct=0.05); add("CHK150 mr_drop10_t5_tgt5", eq, tr)
eq, tr = run_signal_strategy(E4a, RS63, 8, trail_atr=3.0); add("CHK150 rs_top10%_brk20_3atr", eq, tr)
eq, tr = run_signal_strategy(E2, SC2, 10, trail_pct=0.15, max_hold=60); add("CHK150 brk52_trail15_t60", eq, tr)
LIQ = LIQ_ALL

# cost sensitivity for the two survivors (delivery STT alone is 0.1 % a side; realistic all-in is 0.13-0.2 %)
for c in (0.0015, 0.002):
    COST = c
    eq, tr = run_signal_strategy(E5b, -R3, 10, max_hold=5, target_pct=0.05); add(f"COST{c*100:.2f}% mr_drop10_t5_tgt5", eq, tr)
    LIQ = LIQ150
    eq, tr = run_signal_strategy(E5b, -R3, 10, max_hold=5, target_pct=0.05); add(f"COST{c*100:.2f}% CHK150 mr_drop10_t5_tgt5", eq, tr)
    LIQ = LIQ_ALL
COST = 0.001

# ---------------------------------------------------------------------------------------- correlation with the option rules
def option_daily():
    if not os.path.exists(OPT_CACHE):
        os.environ["FNO_CALIB"] = "1"; sys.path.insert(0, HERE)
        import round6 as R6
        tr = R6.book()
        pd.DataFrame([{"date": str(t["date"]), "pnl": t["pnl"]} for t in tr]).to_csv(OPT_CACHE, index=False)
    o = pd.read_csv(OPT_CACHE, parse_dates=["date"])
    return o.groupby("date")["pnl"].sum()


OPT = option_daily()
opt_days = DATES[(DATES >= OPT.index.min()) & (DATES <= OPT.index.max())]
OPTD = OPT.reindex(opt_days, fill_value=0.0)
opt_eq = CAP + OPTD.cumsum()
CORR = {"option_rules": {"period": f"{opt_days[0].date()}..{opt_days[-1].date()}", "net": round(float(OPTD.sum())),
                         "maxdd": round(float((opt_eq - opt_eq.cummax()).min()))}}
for k, eq in CURVES.items():
    pnl = eq.diff().reindex(opt_days).fillna(0)
    comb = opt_eq + (eq.reindex(opt_days) - eq.reindex(opt_days).iloc[0])
    nif = NIFTY_EQ.diff().reindex(opt_days).fillna(0)
    m = pnl.resample("ME").sum(); om = OPTD.resample("ME").sum()
    CORR[k] = {"daily_corr": round(float(np.corrcoef(pnl, OPTD)[0, 1]), 3), "monthly_corr": round(float(np.corrcoef(m, om)[0, 1]), 3),
               "stock_net": round(float(pnl.sum())), "stock_maxdd": round(float((eq.reindex(opt_days) - eq.reindex(opt_days).cummax()).min())),
               "combined_net": round(float(comb.iloc[-1] - CAP)), "combined_maxdd_rs": round(float((comb - comb.cummax()).min())),
               "combined_maxdd_pct_of_4L": round(float((comb - comb.cummax()).min()) / (2 * CAP) * 100, 1),
               "sum_of_separate_maxdd": round(float((eq.reindex(opt_days) - eq.reindex(opt_days).cummax()).min() + (opt_eq - opt_eq.cummax()).min()))}
CORR["nifty_bh_vs_options_daily_corr"] = round(float(np.corrcoef(NIFTY_EQ.diff().reindex(opt_days).fillna(0), OPTD)[0, 1]), 3)

# ------------------------------------------------------------------------------------------------------------- output
print("\n== yearly % (strategy vs Nifty) ==")
print("nifty".ljust(34), yearly(NIFTY_EQ))
print("nifty B&H by half:", {h: NB[h] for h in HALVES})
for k, r in RES.items():
    print(k.ljust(34), r["yearly_pct"], "top5 trades =", r.get("top5_trades_share_of_net_pct"), "% of net")
print("\n== correlation with the option rules' daily P&L (overlap", CORR["option_rules"]["period"], ") ==")
for k in CURVES:
    c = CORR[k]
    print(f"{k:34s} daily r {c['daily_corr']:+.2f} monthly r {c['monthly_corr']:+.2f}  stock net {c['stock_net']:>8,} DD {c['stock_maxdd']:>8,}  "
          f"combined net {c['combined_net']:>8,} DD {c['combined_maxdd_rs']:>8,} ({c['combined_maxdd_pct_of_4L']}% of 4L; separate DDs sum {c['sum_of_separate_maxdd']:,})")

# detail on the one survivor, for the app recommendation
_df = pd.DataFrame(TRADES["mr_drop10_t5_tgt5"]); _df["ret"] = _df["exit"] / _df["entry"] - 1
_sig = pd.Series((E5b & LIQ_ALL).sum(axis=1), index=DATES).loc[START:]
_m = CURVES["mr_drop10_t5_tgt5"].diff().loc[START:].resample("ME").sum()
DETAIL = {"exit_reason": {w: {"n": int(len(g)), "avg_ret_pct": round(float(g["ret"].mean()) * 100, 2)} for w, g in _df.groupby("why")},
          "avg_ret_pct": round(float(_df["ret"].mean()) * 100, 2), "median_ret_pct": round(float(_df["ret"].median()) * 100, 2),
          "worst_trade_pct": round(float(_df["ret"].min()) * 100, 1), "signal_days": int((_sig > 0).sum()),
          "mean_signals_on_signal_day": round(float(_sig[_sig > 0].mean()), 1), "days_with_more_than_10_signals": int((_sig > 10).sum()),
          "months": int(len(_m)), "months_positive": int((_m > 0).sum()), "worst_month_rs": round(float(_m.min())), "best_month_rs": round(float(_m.max())),
          "unseen_months_rs": {str(k.date()): round(float(v)) for k, v in _m.loc["2025-07":].items()}}
print("\nmr_drop10_t5_tgt5 detail:", DETAIL)

best = sorted(RES, key=lambda k: -(RES[k]["unseen"]["cagr_pct"] + RES[k]["train"]["cagr_pct"]))[:5]
out = {"notes": {"universe": "499 of today's Nifty 500 list -> survivorship bias (dead/demoted names missing); results are an upper bound",
                 "costs": "0.1 % per side on every fill, integer shares, slot = equity / max positions at entry",
                 "fills": "signals on the close -> buy at the next open; stops/targets filled at the level (or the open if gapped through); "
                          "exit signals -> next open",
                 "nifty": NIFTY_CHK, "train": "2020-10-13..2025-06-30", "unseen": "2025-07-01..2026-10-12"},
       "nifty_bh": {**{h: NB[h] for h in HALVES}, "yearly_pct": yearly(NIFTY_EQ)},
       "strategies": RES, "correlation_with_options": CORR, "survivor_detail": DETAIL, "top5_by_cagr_sum": best,
       "sample_trades": {k: TRADES[k][-5:] for k in best}}
json.dump(out, open(os.path.join(HERE, "round16_stocks.json"), "w"), indent=1, default=str)
print(f"\nwrote round16_stocks.json in {time.time() - t0:.0f}s")
