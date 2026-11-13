"""Part N: does the live shadow code (fno/squeeze.py) take the same trades as the backtest? Drives the live entry / exit
functions through history tick by tick (one tick a minute after every 5-minute boundary) and compares with simulate()."""
import sys, json; sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from lib import *
from datetime import timedelta
sys.path.insert(0, ROOT)
from fno import squeeze as SQ, config as C
import b_families as bf

df, o, h, l, c, hm, day = bar_of("SENSEX", "15m")
# backtest trades
e = ema(c, 14); a10 = atr(h, l, c, 10); m = bf.roll(c, 14, "mean"); s = bf.roll(c, 14, "std")
on = (m + 2.0 * s < e + 1.5 * a10) & (m - 2.0 * s > e - 1.5 * a10)
prev = np.roll(on.astype(float), 1); prev[0] = np.nan
rel = (~on) & (prev > 0); mom = c - (bf.roll(h, 14, "max") + bf.roll(l, 14, "min") + 2 * m) / 4
rows = simulate(o, h, l, c, hm, day, rel & (mom > 0), rel & (mom < 0), 1.5 * atr(h, l, c, 14), xL=mom < 0, xS=mom > 0, per_day=1, step=15)
bt = {df.index[i]: (side, round(ep, 2), round(xs, 2), df.index[j]) for i, j, ep, xs, side, sl0 in rows}
print("backtest trades", len(bt), flush=True)
# live code on 5-minute candles built from the same 1-minute data
m1 = M.bars("SENSEX", "5m")[["open", "high", "low", "close"]]
if m1.index.tz is None:
    m1.index = m1.index.tz_localize(C.IST)
B = SQ.frame(m1)                                              # full history, every bar complete
idx15 = B.index; ends = idx15 + timedelta(minutes=15)
live = {}; pos = None; taken_day = None
t5 = m1.index; opens = m1["open"].values
import bisect
for k in range(len(t5)):
    now = (t5[k] + timedelta(minutes=1)).to_pydatetime()      # one minute into this 5-minute candle
    if not (9 * 60 + 15 <= now.hour * 60 + now.minute <= 15 * 60 + 29):
        continue
    nb = bisect.bisect_right(ends.values, np.datetime64(t5[k].tz_convert("UTC").tz_localize(None))) if False else int((ends <= t5[k]).sum()) if k % 3000 == 0 else None
    # completed bars: those ending at or before this candle's start
    if nb is None:
        lo, hi = 0, len(ends)
        while lo < hi:
            mid = (lo + hi) // 2
            if ends[mid] <= t5[k]:
                lo = mid + 1
            else:
                hi = mid
        nb = lo
    b = B.iloc[max(0, nb - 40):nb]
    c5 = m1.iloc[max(0, k - 120):k]                           # completed 5-minute candles only
    spot = float(opens[k])
    if pos is not None:
        chk = SQ.exit_check(b, c5, pos, now, spot)
        if chk:
            live[pos["_i"]] = (pos["side"], pos["entry_spot"], round(chk[1], 2), chk[0]); pos = None
    if pos is None and taken_day != now.date() and (now.minute - 1) % 15 == 0:
        sg = SQ.entry_check(b, now)
        if sg:
            pos = {"side": sg["side"], "sl": spot - sg["side"] * sg["risk_pts"], "entry_bar": sg["entry_bar"], "entry_spot": round(spot, 2), "_i": pd.Timestamp(sg["entry_bar"])}
            taken_day = now.date()
tz = lambda t: t if t.tzinfo is not None else t.tz_localize(C.IST)
btk = {tz(k): v for k, v in bt.items()}
same_entry = [k for k in btk if k in live and live[k][0] == btk[k][0]]
same_all = [k for k in same_entry if abs(live[k][1] - btk[k][1]) < 0.06 and abs(live[k][2] - btk[k][2]) < 0.06]
only_bt = [k for k in btk if k not in live]; only_live = [k for k in live if k not in btk]
print(f"live-code trades {len(live)}; same entry bar and side {len(same_entry)}; same entry and exit price too {len(same_all)}; only in backtest {len(only_bt)}; only in live code {len(only_live)}")
diff = [k for k in same_entry if k not in same_all]
sq_ = [k for k in diff if live[k][3].startswith("15:15")]
gap = [abs(live[k][2] - btk[k][2]) for k in sq_]
print(f"   exits that differ: {len(diff)}, of which 15:15 square-offs {len(sq_)} (live = first price at 15:15, backtest = the 15:00 bar's close; median gap {np.median(gap) if gap else 0:.1f} index points, largest {max(gap) if gap else 0:.1f})")
other = [k for k in diff if k not in sq_]
for k in other[:6]:
    print("   differs", k, "bt", btk[k], "live", live[k])
print("   only bt", only_bt[:4], "only live", only_live[:4])
json.dump({"backtest": len(bt), "live_code": len(live), "same_entry": len(same_entry), "same_entry_exit": len(same_all), "only_backtest": len(only_bt), "only_live": len(only_live), "exit_differs": len(diff), "of_which_square_off": len(sq_), "square_off_median_gap_pts": float(np.median(gap)) if gap else 0.0, "square_off_max_gap_pts": float(max(gap)) if gap else 0.0},
          open(OUT + "/n_parity.json", "w"))
