"""Round 65b (29 Oct 2026): Camarilla entry variants inside the app's backtester (live Autopilot rules, big-run skip,
Nifty / Bank Nifty / Sensex, 1 lot 1-ITM, 2023-01 -> 2026-10). Only the Camarilla entry changes; noise / trend / fib
untouched. Variants (fixed before running):
  live        5-min close crosses H4/L4 -> buy at that close (today)
  ext4 / ext6 skip the cross if price is already > 4.3 / 6.2 ATR from the day's low (high for puts) - wait for a later cross
  rt30 / rt60 after the cross, wait up to 30 / 60 min for a pullback that touches H4 (L4); fill at H4 (or the open if it
              gaps through); cancelled if a bar closes back below H3 (above L3); stop in the fill bar = full loss
  rt30x       retest, but if no pullback in 30 min take nothing (same as rt30) - kept for symmetry with rt30f
  rt30f       retest 30 min, and if no pullback come, buy at the close of the 30th minute anyway (never miss a runner)
Judged like every round: per-lot net in 2023-25 and 2026 separately, Rs 2L account replay, shuffle p for removals.
Run: python round65b.py -> round65b_results.json"""
import os, sys, json, inspect, pickle, warnings, textwrap
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
import round25_harness as H, round43 as R43
from fno import backtest as B, config as C

CAMX = {"mode": "live"}
src = textwrap.dedent(inspect.getsource(B._one_trade))
old_entry = '''                if c0 <= lv["h4"] < c1:
                    entry_i, opt = i, "CE"
                    break
                if c0 >= lv["l4"] > c1:
                    entry_i, opt = i, "PE"
                    break
                continue'''
assert old_entry in src
new_entry = '''                hk = _CAMHOOK(day, idx, i, lv, c0, c1)
                if hk is None:
                    continue
                entry_i, opt, _fill = hk
                break'''
src = src.replace(old_entry, new_entry)
src = src.replace('        entry_i = None\n', '        entry_i = None\n        _fill = None\n', 1)
src = src.replace('        spot = float(bar["close"])\n', '        spot = float(bar["close"]) if _fill is None else float(_fill)\n', 1)
old_loop = '        for ts in idx[entry_i + 1:]:\n'
assert old_loop in src
src = src.replace(old_loop, '''        _pre = _fill is not None and sign * (float((day["low"] if sign > 0 else day["high"]).iloc[entry_i]) - sl_c) <= 0
        if _pre:
            reason, x_spot, x_ts, x_bar = "Stop-loss (H3/L3)", sl_c, ts_in + timedelta(minutes=C.CANDLE_MIN), ts_in
        for ts in ([] if _pre else idx[entry_i + 1:]):
''', 1)
assert "_pre" in src and "_CAMHOOK" in src


def _CAMHOOK(day, idx, i, lv, c0, c1):
    up, dn = c0 <= lv["h4"] < c1, c0 >= lv["l4"] > c1
    if not (up or dn):
        return None
    opt, sg = ("CE", 1) if up else ("PE", -1)
    m = CAMX["mode"]
    if m == "live" or lv.get("_fib"):                # the Fibonacci rule shares this entry code: leave it as live
        return i, opt, None
    a = float(day["atr"].iloc[i])
    if m.startswith("ext"):
        ext = sg * (float(day["close"].iloc[i]) - (float(day["low"].iloc[:i + 1].min()) if sg > 0 else float(day["high"].iloc[:i + 1].max()))) / a
        return None if ext > CAMX["k"] else (i, opt, None)
    lvl, inv = (lv["h4"], lv["h3"]) if sg > 0 else (lv["l4"], lv["l3"])
    n = CAMX["bars"]
    for j in range(i + 1, min(i + 1 + n, len(idx))):
        if (idx[j] + timedelta(minutes=C.CANDLE_MIN)).time() > C.CAM_LAST_ENTRY:
            return None
        o, lo, hi, cl = (float(day[x].iloc[j]) for x in ("open", "low", "high", "close"))
        if sg * (o - lvl) <= 0:                      # opened at / through the level
            return j, opt, o
        if (lo <= lvl) if sg > 0 else (hi >= lvl):
            return j, opt, lvl
        if sg * (cl - inv) < 0:
            return None
    if m == "rt_fall":                               # no pullback: buy at the close of the last waiting bar
        j = min(i + n, len(idx) - 1)
        return j, opt, None
    return None


from datetime import timedelta
from fno import strategy as S
_fib0 = S.fib_levels
S.fib_levels = lambda hist: (lambda d: {**d, "_fib": True} if d else d)(_fib0(hist))   # tag Fibonacci levels
ns = dict(vars(B)); ns["_CAMHOOK"] = _CAMHOOK; ns["timedelta"] = timedelta
exec(compile(src, "patched_one_trade", "exec"), ns)
B._one_trade = ns["_one_trade"]

SYMS = ["NIFTY", "BANKNIFTY", "SENSEX"]


def gen(mode, **kw):
    CAMX.clear(); CAMX.update(mode=mode, **kw)
    v = H.load("INDIAVIX")["close"]; rows = []
    for s in SYMS:
        c = H.load(s)
        B.EXIT.clear(); B.EXIT["big_run_skip"] = True
        r = B.run(s, capital=1e9, candles=c, vix_intraday=v, sizing="one_lot", strategy="autopilot", otm=-1, live_rules=False)
        B.EXIT.clear()
        T = pd.DataFrame(r["trades"]); T["sym"] = s; rows.append(T)
    T = pd.concat(rows, ignore_index=True)
    T["per"] = np.where(T.date < "2026", "2023-25", "2026")
    T["sgn"] = np.where(T.side == "CALL", 1, -1)
    return T


if __name__ == "__main__":
    V = {"live": ("live", {}), "ext4": ("ext", {"k": 4.3}), "ext6": ("ext", {"k": 6.2}),
         "rt30": ("rt", {"bars": 6}), "rt60": ("rt", {"bars": 12}), "rt30f": ("rt_fall", {"bars": 6})}
    out = {}
    base = None
    for nm, (mode, kw) in V.items():
        T = gen(mode, **kw)
        cam = T[T.strategy == "Camarilla"]
        allp = T.groupby("per").pnl.sum()
        rec = {"cam_n": len(cam), "cam_avg": round(float(cam.pnl.mean())), "cam_win": round(float((cam.pnl > 0).mean() * 100), 1),
               "cam_net_2325": round(float(cam[cam.per == "2023-25"].pnl.sum())), "cam_net_2026": round(float(cam[cam.per == "2026"].pnl.sum())),
               "all_net_2325": round(float(allp.get("2023-25", 0))), "all_net_2026": round(float(allp.get("2026", 0))),
               "cam_by_sym": {s: round(float(g.pnl.sum())) for s, g in cam.groupby("sym")}}
        try:
            rec["acct_2324_25"] = R43.replay(T[T.per == "2023-25"]); rec["acct_2026"] = R43.replay(T[T.per == "2026"])
        except Exception as e:
            rec["acct_err"] = str(e)[:120]
        out[nm] = rec
        if nm == "live":
            base = T
        print(nm, json.dumps({k: v for k, v in rec.items() if not k.startswith("acct")}), flush=True)
        print("   account 2023-25", rec.get("acct_2324_25"), " 2026", rec.get("acct_2026"), flush=True)
        T.to_pickle(f"/tmp/round65_{nm}.pkl")
    json.dump(out, open(os.path.join(HERE, "round65b_results.json"), "w"), indent=1, default=str)
