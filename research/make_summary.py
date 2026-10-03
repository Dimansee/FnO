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


wf = json.load(open("wf_report_250.json"))
res = json.load(open("results.json"))
fam = {}
for r in res:
    f = fam.setdefault(r["strategy"], {"tested": 0, "robust": 0, "robust_oos_profitable": 0})
    f["tested"] += 1
    if r["score"] > -50:
        f["robust"] += 1
        f["robust_oos_profitable"] += sum(r["test"][s]["last120"]["net"] for s in ("NIFTY", "BANKNIFTY")) > 0
LABEL = {"orb": "Opening-range breakout + VWAP/trend (the old rules' family)", "orb_candle": "5-min ORB (Zarattini/Aziz)",
         "noise": "Noise-area momentum (Zarattini/Aziz/Barbon)", "vwap_pull": "VWAP / EMA pullback",
         "supertrend": "Supertrend flip + ADX", "pdhl": "Previous-day high/low + narrow CPR", "ema_adx": "EMA 9/21 cross + ADX",
         "gap": "Gap-and-go / gap-fade", "ironfly": "9:20 iron fly (option selling)"}
families = []
for k, f in fam.items():
    w = wf.get(k, {}).get("all", {})
    families.append({"key": k, "name": LABEL[k], **f, "walk_forward_net": round(w.get("net", 0)) if w else None,
                     "walk_forward_sharpe": round(w.get("sharpe", 0), 2) if w else None})
families.sort(key=lambda x: -(x["walk_forward_net"] if x["walk_forward_net"] is not None else -1e9))
out = {
    "period": f"{dates[0]} to {dates[-1]} ({len(dates)} sessions of 5-minute data)",
    "train": f"{dates[0]} to {dates[-121]}", "test": f"{dates[-120]} to {dates[-1]} (never used for tuning)",
    "capital": 200000, "risk_per_trade": "1%", "costs": "0.5% slippage per fill + brokerage, STT, exchange, GST, stamp",
    "configs_tested": len(res) + 3072, "families": families,
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
        "Option selling (9:20 iron fly) could not be judged fairly: its edge comes from IV crush near expiry, which a VIX-based model can't reproduce.",
    ],
}
json.dump(out, open("../fno/research_summary.json", "w"), indent=1, default=str)
print(json.dumps({k: out["new"][k]["windows"] for k in out["new"]}, indent=0, default=str)[:900])
print("families:", [(f["key"], f["walk_forward_net"], f["robust"], f["robust_oos_profitable"]) for f in families])
