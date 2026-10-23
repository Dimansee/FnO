"""Gold by trading session, and which gold rules survive the app's costs (written 23 Oct 2026).

Part A - session behaviour (descriptive). For each session (Asia, London, overlap, New York; fno/sessions.py,
daylight-saving aware) on COMEX gold (GC=F 1h ~2 years, 15m ~59 days) and PAXG (Binance 1h since 2020):
average range, share of the COMEX day's range, how often the day's high / low is made there, average move and
up-rate, with a t-stat, split by year so a one-year effect is visible. Also the hour-of-day range strip in IST.

Part B - rules (same protocol as global_rules_search.py, fixed before looking):
  Engine fno.custom.simulate (signal at bar close, entry next open, stop wins ties, GOLD costs 2+3 bps a side).
  Candidates = the six gold autopilot rules in fno/gautopilot.py plus a few textbook variants.
  Data: XAUUSD (GC=F). 1h/4h: ~2 years, design = first 60%, holdout = last 40%. Daily: from 2010, design to
  2021-12-31, holdout 2022 on. PAXG 1h (2020 on) is a second, independent check for the 1h/4h rules.
  Design pass: >= 60 trades, avg >= +0.05R, PF >= 1.10. Holdout pass: avg > 0, PF >= 1.05.
  (One symbol, so the trade-count bar is lower than the pooled crypto test; read t-stats, not just signs.)
Usage: python research/gold_sessions.py <data_dir>    (data from research/fetch_global.py <dir> gold)
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fno import custom as CU, gautopilot as AP, gold as G, sessions as SS  # noqa: E402

DATA = sys.argv[1] if len(sys.argv) > 1 else "research/data/global"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gold_sessions_results.json")
LON = SS.LON


def load(name):
    f = os.path.join(DATA, name)
    if not os.path.exists(f):
        return pd.DataFrame()
    df = pd.read_csv(f, parse_dates=["ts"]).set_index("ts")
    df.index = pd.to_datetime(df.index, utc=True).tz_convert(LON)
    return df[["open", "high", "low", "close", "volume"]].astype(float).sort_index()


def daily_from_yahoo(df):
    """Yahoo daily futures bars are labelled by trade date (midnight New York); relabel at midnight London like the app."""
    if df.empty:
        return df
    ny = df.index.tz_convert(SS.NY)
    df = df.copy()
    df.index = pd.DatetimeIndex([pd.Timestamp(x.date(), tz=LON) for x in ny])
    return df[~df.index.duplicated(keep="last")]


def stats(r):
    r = np.asarray(r, float)
    if not len(r):
        return {"n": 0}
    lo = -r[r <= 0].sum()
    sd = r.std(ddof=1) if len(r) > 1 else 0
    return {"n": int(len(r)), "avg_R": round(float(r.mean()), 3), "pf": round(float(r[r > 0].sum() / lo), 2) if lo > 0 else None,
            "win": round(float((r > 0).mean() * 100), 1), "total_R": round(float(r.sum()), 1),
            "t": round(float(r.mean() / (sd / np.sqrt(len(r)))), 2) if sd > 0 else None}


# ------------------------------------------------------------------ part A
def part_a(h1, m15, paxg):
    out = {}
    for name, df in (("XAUUSD_1h", h1), ("XAUUSD_15m", m15), ("PAXG_1h", paxg)):
        if df.empty:
            continue
        if name.startswith("PAXG"):           # follow real gold's hours so weekend PAXG trading doesn't dilute sessions
            df = df[[__import__("fno.markets", fromlist=["x"]).gold_open(t.to_pydatetime(), pad_after=0) for t in df.index]]
        full = G.stats_from_bars(df)
        by_year = {}
        for y, part in df.groupby(df.index.year):
            if len(part) > 500:
                by_year[int(y)] = [{k: r[k] for k in ("session", "avg_range", "share_of_day", "avg_move_pct", "up_pct", "t_move")} for r in G.stats_from_bars(part)["rows"]]
        out[name] = {"all": full, "by_year": by_year}
    return out


# ------------------------------------------------------------------ part B
def c(l, op, r):
    return {"l": l, "op": op, "r": r}


P = {"price": "close"}


def variants():
    cands = [dict(c_, src="autopilot") for c_ in AP.CANDIDATES["GOLD"]]
    extra = [
        {"id": "x1", "name": "London breakout of Asian range (15m)", "timeframe": "15m", "direction": "both",
         "long": {"all": [c(P, "crosses_above", {"level": "asia_high"})]}, "short": {"all": [c(P, "crosses_below", {"level": "asia_low"})]},
         "exit": {**CU.DEFAULT_EXIT, "stop_atr": 1.5, "target_r": 2.0}, "hold": "intraday", "filters": {"max_trades_day": 1, "session": "london", "end": "11:00"}},
        {"id": "x2", "name": "London breakout of Asian range, 1R target (1h)", "timeframe": "1h", "direction": "both",
         "long": {"all": [c(P, "crosses_above", {"level": "asia_high"})]}, "short": {"all": [c(P, "crosses_below", {"level": "asia_low"})]},
         "exit": {**CU.DEFAULT_EXIT, "stop_atr": 1.0, "target_r": 1.0}, "hold": "intraday", "filters": {"max_trades_day": 1, "session": "london", "end": "11:00"}},
        {"id": "x3", "name": "Fade the Asian range break in London (1h)", "timeframe": "1h", "direction": "both",
         "long": {"all": [c(P, "crosses_above", {"level": "asia_low"})]}, "short": {"all": [c(P, "crosses_below", {"level": "asia_high"})]},
         "exit": {**CU.DEFAULT_EXIT, "stop_atr": 1.0, "target_r": 1.5}, "hold": "intraday", "filters": {"max_trades_day": 1, "session": "london"}},
        {"id": "x4", "name": "New York opening-range breakout (15m)", "timeframe": "15m", "direction": "both",
         "long": {"all": [c(P, "crosses_above", {"level": "ny_or_high"})]}, "short": {"all": [c(P, "crosses_below", {"level": "ny_or_low"})]},
         "exit": {**CU.DEFAULT_EXIT, "stop_atr": 1.5, "target_r": 2.0}, "hold": "intraday", "filters": {"max_trades_day": 1, "session": "overlap"}},
        {"id": "x5", "name": "Supertrend 10/3 (1h), overlap entries only", "timeframe": "1h", "direction": "both",
         "long": {"all": [c({"ind": "supertrend", "n": 10, "mult": 3.0, "out": "dir"}, "crosses_above", {"value": 0})]},
         "short": {"all": [c({"ind": "supertrend", "n": 10, "mult": 3.0, "out": "dir"}, "crosses_below", {"value": 0})]},
         "exit": {**CU.DEFAULT_EXIT, "target_r": None, "stop_atr": 2.0, "exit_on_opposite": True}, "hold": "swing", "filters": {"max_trades_day": 2, "session": "overlap"}},
        {"id": "x6", "name": "Western-hours fade, short (1h)", "timeframe": "1h", "direction": "short",
         "short": {"all": [c({"price": "hour"}, "ge", {"value": 13}), c({"price": "hour"}, "le", {"value": 13})]},
         "exit": {**CU.DEFAULT_EXIT, "target_r": None, "stop_atr": 2.0, "time_stop_bars": 8}, "hold": "intraday", "filters": {"max_trades_day": 1}},
    ]
    return cands + [dict(x, src="variant") for x in extra]


def spec_of(c_):
    return AP._spec("GOLD", c_, ["XAUUSD"])


def run_split(spec, df, split):
    tr = CU.simulate(spec, df)
    d = [t["R"] for t in tr if t["entry_ts"] < split]
    h = [t["R"] for t in tr if t["entry_ts"] >= split]
    return stats(d), stats(h), tr


def part_b(h1, m15, d1, paxg):
    rows = []
    h4 = CU.resample(h1, "4h", "GOLD") if not h1.empty else h1
    p4 = CU.resample(paxg, "4h", "GOLD") if not paxg.empty else paxg
    for c_ in variants():
        spec = spec_of(c_)
        tf = spec["timeframe"]
        df = {"15m": m15, "1h": h1, "4h": h4, "1d": d1}[tf]
        if df.empty or len(df) < 200:
            rows.append({"id": c_["id"], "name": c_["name"], "tf": tf, "src": c_["src"], "note": "not enough data"})
            continue
        split = pd.Timestamp("2022-01-01", tz=LON) if tf == "1d" else df.index[int(len(df) * 0.6)]
        des, hold, tr = run_split(spec, df, split)
        ok_d = des["n"] >= 60 and des.get("avg_R", -1) >= 0.05 and (des.get("pf") or 0) >= 1.10
        ok_h = hold["n"] > 0 and hold.get("avg_R", -1) > 0 and (hold.get("pf") or 0) >= 1.05
        row = {"id": c_["id"], "name": c_["name"], "tf": tf, "src": c_["src"], "split": split.isoformat()[:10], "design": des, "holdout": hold,
               "pass_design": ok_d, "pass_holdout": ok_h, "both": ok_d and ok_h}
        if tf in ("1h", "4h") and not paxg.empty:                                # independent second series
            pdf = paxg if tf == "1h" else p4
            pdf = pdf[[__import__("fno.markets", fromlist=["x"]).gold_open(t.to_pydatetime(), pad_after=0) for t in pdf.index]] if tf == "1h" else pdf
            row["paxg_all"] = stats([t["R"] for t in CU.simulate(spec, pdf)])
        yrs = {}
        for t in tr:
            yrs.setdefault(t["entry_ts"].year, []).append(t["R"])
        row["by_year"] = {int(y): stats(v) for y, v in sorted(yrs.items())}
        rows.append(row)
        print(f"{c_['name']:<52} {tf:>3}  design {des.get('n', 0):>4} {des.get('avg_R', 0):+.3f}R  holdout {hold.get('n', 0):>4} {hold.get('avg_R', 0):+.3f}R  "
              f"{'PASS' if row['both'] else '-'}  paxg {row.get('paxg_all', {}).get('avg_R', '')}", flush=True)
    return rows


if __name__ == "__main__":
    h1 = load("gold_XAUUSD_1h.csv.gz")
    m15 = load("gold_XAUUSD_15m.csv.gz")
    d1 = daily_from_yahoo(load("gold_XAUUSD_1d.csv.gz"))
    d1 = d1[d1.index >= pd.Timestamp("2010-01-01", tz=LON)] if not d1.empty else d1
    paxg = load("crypto_PAXGUSDT_1h.csv.gz")
    print("bars:", {k: len(v) for k, v in (("1h", h1), ("15m", m15), ("1d", d1), ("paxg", paxg))})
    a = part_a(h1, m15, paxg)
    for k, v in a.items():
        print("\n==", k, v["all"].get("days"), "days; busiest IST", v["all"].get("busiest_ist"))
        for r in v["all"]["rows"]:
            print(f"  {r['session']:<8} range ${r['avg_range']:>7} ({r['avg_range_pct']}%)  share {r['share_of_day']:>5}%  hi {r['made_day_high_pct']:>5}%  lo {r['made_day_low_pct']:>5}%  "
                  f"move {r['avg_move_pct']:+.3f}% (t {r['t_move']})  up {r['up_pct']}%")
    print()
    b = part_b(h1, m15, d1, paxg)
    json.dump({"generated": pd.Timestamp.utcnow().isoformat(timespec="minutes"), "sessions": a, "rules": b}, open(OUT, "w"), indent=1, default=str)
    print("wrote", OUT)
