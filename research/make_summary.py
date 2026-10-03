"""Writes fno/research_summary.json - the numbers the app shows in its Backtest tab."""
import json, sys
sys.path.insert(0, "..")
import numpy as np
import engine as E
from fno import config as C

W = dict(mult=1.75, stop_atr=2.0, rr=4.0, vwap=1, max_trades=1, vix_min=11, last_entry=870, otm=0, be=0, ctx=-9, rmin=1.0, rmax=2.5)
OLD = dict(or_min=15, rr=2.0, stop="mid", vwap=1, trend=1, be=1.0, tstop=45, ctx=-9, max_trades=2)
for s, m in C.STOCKS.items():
    E.META[s] = {"lot": m["lot"], "step": m["step"], "kind": "monthly", "iv_mult": 1.4}
D = {s: E.load_days(s) for s in ["NIFTY", "BANKNIFTY", "FINNIFTY"] + list(C.STOCKS)}
dates = [d.d for d in D["NIFTY"]]


def win(tr, w):
    t = [x for x in tr if x["date"] >= dates[-w]]
    pos, neg = sum(x["pnl"] for x in t if x["pnl"] > 0), -sum(x["pnl"] for x in t if x["pnl"] <= 0)
    return {"net": round(sum(x["pnl"] for x in t)), "pf": round(pos / neg, 2) if neg else None, "trades": len(t),
            "win_rate": round(sum(x["pnl"] > 0 for x in t) / len(t) * 100) if t else None}


def block(sym, strat, p):
    tr = E.run(D[sym], sym, strat, p)
    return {"windows": {w: win(tr, w) for w in (30, 60, 90, 120)},
            "years": {y: round(sum(x["pnl"] for x in tr if x["date"].year == y)) for y in (2023, 2024, 2025, 2026)},
            "all": win(tr, len(dates))}


def small_account(cap=15000):
    out = {}
    for sym in ("NIFTY", "BANKNIFTY"):
        days = D[sym]
        for otm in (0, 1, 2):
            tr = E.run(days, sym, "noise", dict(W, one_lot=1, otm=otm), capital=cap)
            pnl = np.array([t["pnl"] for t in tr])
            eq = cap + np.cumsum(pnl)
            peak = np.maximum.accumulate(np.concatenate([[cap], eq]))[1:]
            out[f"{sym}_{otm}"] = {"symbol": sym, "otm": otm, "trades": len(tr),
                                   "years": {y: round(sum(t["pnl"] for t in tr if t["date"].year == y)) for y in (2023, 2024, 2025, 2026)},
                                   "last120": round(sum(t["pnl"] for t in tr if t["date"] >= dates[-120])),
                                   "max_dd_pct": round(float(((eq - peak) / peak).min() * 100)) if len(tr) else 0,
                                   "worst_trade": round(float(pnl.min())) if len(tr) else 0,
                                   "cost_per_lot": round(float(np.median([t["prem_in"] for t in tr]) * E.META[sym]["lot"])) if tr else None}
    return {"capital": cap, "sizing": "1 lot per signal", "rows": list(out.values())}


wf = json.load(open("wf_report_250.json"))
res = json.load(open("results.json"))
fam = {}
for r in res:
    f = fam.setdefault(r["strategy"], {"tested": 0, "robust": 0, "robust_oos_profitable": 0})
    f["tested"] += 1
    if r["score"] > -50:
        f["robust"] += 1
        f["robust_oos_profitable"] += sum(r["test"][s]["last120"]["net"] for s in ("NIFTY", "BANKNIFTY")) > 0
for r in json.load(open("results_creators.json")):
    f = fam.setdefault(r["strategy"], {"tested": 0, "robust": 0, "robust_oos_profitable": 0})
    f["tested"] += 1
    if r["score"] > -50:
        f["robust"] += 1
        f["robust_oos_profitable"] += sum(r["test"][s]["last120"]["net"] for s in ("NIFTY", "BANKNIFTY")) > 0
wf.update({k: {"all": v} for k, v in json.load(open("wf_creators.json")).items()})
best = {}
for r in res + json.load(open("results_creators.json")):
    if r["strategy"] not in best or r["score"] > best[r["strategy"]]["score"]:
        best[r["strategy"]] = r
LABEL = {"ema5": "5 EMA (Power of Stocks / Subasish Pani)", "inside": "Inside-bar breakout (Bank Nifty creators, e.g. Ghanshyam Tech)",
         "ma44": "44 moving average (Siddharth Bhanushali)", "rsi6040": "RSI 60/40 momentum (Vishal Malkan style)",
         "mtf": "15-min bias + 5-min breakout (Booming Bulls style)", "fib": "Fibonacci 50-61.8% pullback (Magicfibs style)",
"orb": "Opening-range breakout + VWAP/trend (the old rules' family)", "orb_candle": "5-min ORB (Zarattini/Aziz)",
         "noise": "Noise-area momentum (Zarattini/Aziz/Barbon)", "vwap_pull": "VWAP / EMA pullback",
         "supertrend": "Supertrend flip + ADX", "pdhl": "Previous-day high/low + narrow CPR", "ema_adx": "EMA 9/21 cross + ADX",
         "gap": "Gap-and-go / gap-fade", "ironfly": "9:20 iron fly (option selling)"}
families = []
for k, f in fam.items():
    w = wf.get(k, {}).get("all", {})
    top = best.get(k)
    t120 = round(sum(top["test"][x]["last120"]["net"] for x in ("NIFTY", "BANKNIFTY"))) if top else None
    if k == "noise":   # the setting actually chosen (refined grid, best on training)
        t120 = sum(win(E.run(D[x], x, "noise", W), 120)["net"] for x in ("NIFTY", "BANKNIFTY"))
    families.append({"key": k, "name": LABEL[k], **f, "best_train_setting_last120": t120,
                     "walk_forward_net": round(w.get("net", 0)) if w else None,
                     "walk_forward_sharpe": round(w.get("sharpe", 0), 2) if w else None})
families.sort(key=lambda x: -(x["best_train_setting_last120"] if x["best_train_setting_last120"] is not None else -1e9))
out = {
    "period": f"{dates[0]} to {dates[-1]} ({len(dates)} sessions of 5-minute data)",
    "train": f"{dates[0]} to {dates[-121]}", "test": f"{dates[-120]} to {dates[-1]} (never used for tuning)",
    "capital": 200000, "risk_per_trade": "1%", "costs": "0.5% slippage per fill + brokerage, STT, exchange, GST, stamp",
    "configs_tested": len(res) + 3072 + 2400,
    "small_account": small_account(), "families": families,
    "chosen": {"name": "Noise-area momentum", "band": 1.75, "stop": "2 x ATR", "target": "4R", "checks": "every 30 min from 09:45",
               "filters": "close beyond band and VWAP; India VIX >= 11", "trades_per_day": 1, "option": "ATM, nearest expiry"},
    "new": {s: block(s, "noise", W) for s in ("NIFTY", "BANKNIFTY", "FINNIFTY")},
    "old": {s: block(s, "orb", OLD) for s in ("NIFTY", "BANKNIFTY")},
    "stocks": {s: block(s, "noise", W) for s in C.STOCKS},
    "notes": [
        "Option prices are modelled (Black-Scholes with India VIX); real option quotes for past days are not freely available.",
        "Picking the best settings every month (walk-forward) did WORSE than holding one robust setting - chasing recent results overfits.",
        "Market-context filters (global cues, gap, VIX change) reduced profit in testing, so they are shown for information only.",
        "Nifty was profitable in every year tested; Bank Nifty lost money in 2025; most single stocks lost money - prefer the indices.",
        "Creator setups (5 EMA, inside bar, 44 MA, RSI 60/40, multi-timeframe breakout, Fibonacci) were coded as written and tested the same way; none beat the noise-area rules on the unseen days.",
        "Option-selling styles (straddles/strangles, iron condors, adjustments) and OI/PCR-based methods need historical option quotes and OI, which are not freely available, so they could not be tested honestly.",
        "Option selling (9:20 iron fly) could not be judged fairly: its edge comes from IV crush near expiry, which a VIX-based model can't reproduce.",
    ],
}
json.dump(out, open("../fno/research_summary.json", "w"), indent=1, default=str)
print(json.dumps({k: out["new"][k]["windows"] for k in out["new"]}, indent=0, default=str)[:900])
print("families:", [(f["key"], f["walk_forward_net"], f["robust"], f["robust_oos_profitable"]) for f in families])
