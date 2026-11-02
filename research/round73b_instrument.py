"""Round 73b: same Autopilot signals, cheaper instrument? (2 Nov 2026)

Round 72 and most failed rounds lost to option time decay + spread, while Autopilot's money comes from holding trend-day
moves for hours. So: keep every entry and exit exactly as today and only change what is bought.
  A. expiry: today rolls weekly contracts when <= 1 day is left (round 6). Try rolling when < 3 / < 5 days are left
     (i.e. buy next week's contract more often: less decay per hour, but less delta per rupee).
  B. strike: 1 ITM (today) vs 2 ITM (more delta, less decay as a share of premium, costs more cash).
  C. index futures instead of options: no decay, ~0.02% cost, but margin (~13% of notional) and the full loss on a gap.
     P&L = side x (exit spot - entry spot) x lot - futures charges - 0.02% slippage a side; stop distance from the trade's R.
Every variant goes through today's Outcomes replay (compounding 1%, conviction, CPR sizing, brakes, gates) at Rs 2L and
Rs 20k, by year. Pass bar: better in every year and the full period at Rs 2L, worst fall not deeper.
Usage: python research/round73b_instrument.py [data_dir]
"""
import json, os, sys, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
DATA = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "data")
import numpy as np, pandas as pd
import round73_regime_tilts as R73                 # replay_x + offline lot sizes
import outcomes as O
from fno import portfolio_bt as PB, indicators as I

PERIODS = R73.PERIODS
_orig_pick = I.pick_trading_expiry


def patch_roll(min_weekly):
    def pick(expiries, kind, today=None):
        today = today or I.C.today_ist()
        fut = sorted(e for e in expiries if e >= today)
        if not fut:
            return None
        md = min_weekly if kind == "weekly" else 3
        for e in fut:
            if (e - today).days >= md:
                return e
        return fut[-1]
    I.pick_trading_expiry = pick
    import fno.backtest as B
    B.I.pick_trading_expiry = pick


def trades_variant(otm, roll):
    patch_roll(roll)
    try:
        B0 = dict(O.B0)
        ex, _, brs, mll = PB._trade_key(B0)
        vix = O.H.load("INDIAVIX")["close"]
        out, alt = [], []
        for s in O.SYMS:
            c = O.candles(s)
            T = PB._trades(s, c, vix, otm, PB.EXITS[ex]["ov"], brs, mll)
            T["score"] = PB._scores(T, c)
            out.append(T)
            A = PB._trades(s, c, vix, 1, PB.EXITS[ex]["ov"], brs, mll)   # the 1-OTM twin used below Rs 60k
            A["score"] = PB._scores(A, c)
            alt.append(A)
        return pd.concat(out, ignore_index=True), pd.concat(alt, ignore_index=True)
    finally:
        I.pick_trading_expiry = _orig_pick
        import fno.backtest as B
        B.I.pick_trading_expiry = _orig_pick


def futures(base):
    T = base.copy()
    side = np.where(T.side == "CALL", 1.0, -1.0)
    slip = 0.0002
    move = side * (T.spot_out - T.spot_in) - slip * (T.spot_in + T.spot_out)
    margin = 0.13 * T.spot_in
    # encode as a 'premium' so the replay's cash and P&L maths work: cash used = margin, P&L = move (charges below)
    T["prem_in"] = margin
    T["prem_out"] = np.maximum(margin + move - (40 / T.lot + 0.0002 * T.spot_out + 0.00002 * (T.spot_in + T.spot_out)), 0.05)
    risk_pts = np.where(T.R.abs() > 0.05, (T.spot_out - T.spot_in).abs() / T.R.abs(), 0.004 * T.spot_in)
    risk_pts = np.clip(risk_pts, 0.001 * T.spot_in, 0.02 * T.spot_in)
    T["risk_lot"] = risk_pts * T.lot
    T["risk_lot_capped"] = T["risk_lot"]
    T["xm"] = 1.0
    return T


def run_all(T, A):
    T = T.copy(); T["xm"] = 1.0
    if A is not None:
        A = A.copy(); A["xm"] = 1.0
    return {cap: {p: R73.run(T, A, cap, a, b) for p, (a, b) in PERIODS.items()} for cap in (200_000, 20_000)}


def show(lab, r):
    print(f"{lab:<34} " + " ".join(f"{p}:{r[200_000][p]['final']/1e5:6.2f}L" for p in PERIODS) +
          f" fall {r[200_000]['full']['dd']:+.1f}% | 20k full {r[20_000]['full']['final']/1e5:.2f}L fall {r[20_000]['full']['dd']:+.1f}%", flush=True)


def main():
    B0 = dict(O.B0)
    k, ak = PB._trade_key(B0), PB._alt_key(B0)
    base = pd.concat([O.trades(k, s) for s in O.SYMS], ignore_index=True)
    alt = pd.concat([O.trades(ak, s) for s in O.SYMS], ignore_index=True) if ak else None
    res = {}
    res["Autopilot today (1 ITM, roll at <=1 day)"] = r = run_all(base, alt); show("Autopilot today", r)
    # per-lot comparison of the same signals
    print("per lot, 1 ITM today:", base.groupby(base.date.str[:4]).pnl.sum().round(0).to_dict())
    for otm, roll, lab in ((-1, 3, "1 ITM, roll at <3 days"), (-1, 5, "1 ITM, roll at <5 days"), (-2, 2, "2 ITM, roll as today"),
                           (-2, 3, "2 ITM, roll at <3 days")):
        T, A = trades_variant(otm, roll)
        print(f"  {lab}: {len(T)} trades, per lot", T.groupby(T.date.str[:4]).pnl.sum().round(0).to_dict(), flush=True)
        res[lab] = r = run_all(T, A); show(lab, r)
    # futures: per-lot comparison with realistic costs (0.005% slippage a side, STT 0.02% on the sell, exchange, stamp,
    # Rs 40 brokerage + GST). Not replayed: one Nifty futures lot needs ~Rs 2L margin, so a Rs 2L account holds one position.
    side = np.where(base.side == "CALL", 1, -1)
    pts = side * (base.spot_out - base.spot_in)
    cost = 40 * 1.18 + 0.0002 * base.spot_out * base.lot + 0.0000173 * 1.18 * (base.spot_in + base.spot_out) * base.lot \
        + 0.00002 * base.spot_in * base.lot + 0.00005 * (base.spot_in + base.spot_out) * base.lot
    fut = pts * base.lot - cost
    y = base.date.str[:4]
    res["Index futures per lot (same signals)"] = {"futures": fut.groupby(y).sum().round(0).to_dict(), "options": base.groupby(y).pnl.sum().round(0).to_dict(),
                                                   "fut_avg_loss": round(float(fut[fut <= 0].mean())), "opt_avg_loss": round(float(base.pnl[base.pnl <= 0].mean()))}
    print("futures vs options per lot:", res["Index futures per lot (same signals)"])
    json.dump(res, open(os.path.join(HERE, "round73b_results.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
