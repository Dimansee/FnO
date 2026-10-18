"""Round 23 (Oct 2026): why the rules lost on the 13 Oct 2026 rally day, exit / re-entry / VIX-ceiling variants, and a
trend-day catcher in its own slot. Train = 2023-2025, judged on 2026 (Jan - 16 Oct), both indices, ₹2 lakh / 1% risk and
₹20,000 / 1 lot. Same pricing as the app's backtester (calibrated premiums, 0.5% slippage, 2026 charges).
Run: python round23.py [grid]   (grid = also re-run the 32-setting trend grid, ~10 min)  -> round23_results.json"""
import itertools, json, sys, os
from datetime import time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
import round23_harness as H, round23_trend as TB

VIX = H.load("INDIAVIX")["close"]
SYMS = ["NIFTY", "BANKNIFTY"]
BASE = dict(cam_tstop=45, noise_trail="band_vwap", overlap=False, reentry=0)
VARIANTS = {
    "V0 current rules": {},
    "V1 no Camarilla 45-min time stop": dict(cam_tstop=0),
    "V1b Camarilla time stop 90 min": dict(cam_tstop=90),
    "V2 Noise trail on band only": dict(noise_trail="band"),
    "V2b Noise VWAP trail only after +1R": dict(noise_trail="band_vwap_after1r"),
    "V3 Noise and Camarilla may hold together": dict(overlap=True),
    "V4 one re-entry after a trail/time exit": dict(reentry=1, reentry_only_if_trail=True),
    "V5 no VIX ceilings": dict(noise_vix_max=99, cam_vix_max=99),
    "V5b VIX ceilings 22 / 25": dict(noise_vix_max=22, cam_vix_max=25),
    "V6 Noise without the trailing exit": dict(noise_trail="none"),
    "V7 Noise VWAP-only trail": dict(noise_trail="vwap"),
}
TREND = dict(t0=time(10, 45), share=0.8, stop="vwap", mult=1.75, be=1.0)   # picked on 2023-25 only (best training net, PF >= 1.2)


def stats(df):
    if df.empty:
        return {"n": 0, "net": 0, "pf": None, "max_fall": 0, "win_pct": 0}
    df = df.sort_values(["date", "entry_time"])
    w, l = df.pnl[df.pnl > 0].sum(), -df.pnl[df.pnl <= 0].sum()
    eq = df.pnl.cumsum()
    return {"n": int(len(df)), "net": round(float(df.pnl.sum())), "pf": round(float(w / l), 2) if l else None,
            "max_fall": round(float((eq - eq.cummax().clip(lower=0)).min())), "win_pct": round(float((df.pnl > 0).mean() * 100))}


def split(df):
    df = df.copy(); df["y"] = df.date.str[:4]
    return {"train_2023_25": stats(df[df.y < "2026"]), "y2026": stats(df[df.y == "2026"]),
            "by_year": {k: round(float(v)) for k, v in df.groupby("y").pnl.sum().items()},
            "by_index_year": {f"{s}:{y}": round(float(v)) for (s, y), v in df.groupby(["sym", "y"]).pnl.sum().items()}}


out = {"variants": {}, "trend_grid": [], "final": {}}
for name, ov in VARIANTS.items():
    rows = []
    for s in SYMS:
        rows += H.run(s, H.load(s), VIX, {**BASE, "sizing": "risk", **ov})
    df = pd.DataFrame(rows)
    out["variants"][name] = {**split(df), "oct13": df[df.date == "2026-10-13"][["sym", "strategy", "entry_time", "exit_time", "reason"]].assign(pnl=df.pnl.round()).to_dict("records")}
    print(name, out["variants"][name]["train_2023_25"], out["variants"][name]["y2026"], flush=True)

if "grid" in sys.argv:
    for t0, share, stop, mult, be in itertools.product([time(10, 45), time(11, 45)], [0.8, 0.9], ["vwap", 2.0], [1.0, 1.75], [0, 1.0]):
        P = dict(t0=t0, share=share, stop=stop, mult=mult, be=be, sizing="risk")
        rows = []
        for s in SYMS:
            rows += TB.run(s, H.load(s), VIX, P)
        out["trend_grid"].append({"setting": f"{t0:%H:%M} share {share} stop {stop} band {mult} be {be}", **split(pd.DataFrame(rows))})
        print(out["trend_grid"][-1]["setting"], out["trend_grid"][-1]["train_2023_25"]["net"], flush=True)

for sizing, cap in [("risk", 200_000), ("one_lot", 20_000)]:
    cur, trd = [], []
    for s in SYMS:
        cur += H.run(s, H.load(s), VIX, {**BASE, "sizing": sizing}, capital=cap)
        trd += TB.run(s, H.load(s), VIX, {**TREND, "sizing": sizing}, capital=cap)
    cur, trd = pd.DataFrame(cur), pd.DataFrame(trd)
    imp = pd.concat([cur, trd])
    months = {}
    for name, df in [("current", cur), ("trend", trd), ("improved", imp)]:
        d = df[df.date >= "2026-01-01"]
        for m, g in d.groupby(d.date.str[:7]):
            months.setdefault(m, {})[name] = {"n": int(len(g)), "net": round(float(g.pnl.sum()))}
    same = trd.merge(cur[["sym", "date", "side"]], on=["sym", "date"], how="left", suffixes=("", "_cur"))
    out["final"][sizing] = {"capital": cap, "current": split(cur), "trend_only": split(trd), "improved": split(imp),
                            "months_2026": months, "trend_trades_on_days_current_had_none": int(same.side_cur.isna().sum()),
                            "trend_trades": int(len(trd))}
    print(sizing, out["final"][sizing]["current"]["y2026"], out["final"][sizing]["improved"]["y2026"], flush=True)

json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "round23_results.json"), "w"), indent=1, default=str)
print("saved round23_results.json")
