"""Round 69: breakouts of market structure (higher high / lower low), round numbers and major multi-day points (1 Nov 2026).

User: "what if we add breakout logic also of higher high, lower low, major points breaks like 23000, 22700, 27550".
Same grid, pricing and judging as round 67 (research/round67_levels_grid.py), with new level families:
  round_minor  Nifty every 100, Bank Nifty / Sensex every 500 (within 1.5% of the previous close)
  round_major  Nifty every 500, Bank Nifty / Sensex every 1,000
  swing        the last two confirmed 15-min swing highs and lows (3 bars each side; usable only after confirmation)
  major        5-session and 20-session high and low (excluding today)
and two direction filters, run separately:
  run 'cpr'        the day's CPR bias as in round 67
  run 'structure'  longs only after a higher high + higher low (last two confirmed 15-min swings), shorts only after a lower
                   high + lower low - i.e. a break in the structure's direction (BOS); without the filter, breaks against it
                   (CHoCH) are included too
Behaviours break / retest / bounce / false break; stops level / bar; targets next level, 1.5R, 2R, end of day; CPR width;
confluence; morning window. Nifty, Bank Nifty, Sensex 5m / 15m, Jan 2023 - Oct 2026. Index R vs a random-direction twin; money as
ATM options (decay, charges, 0.25% slippage, Rs 2,000 risk). Usage: python research/round69_structure_round_numbers.py <data_dir> <cpr|structure>
"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import numpy as np
import pandas as pd
import round67_levels_grid as G

DATA = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "data")
RUN = sys.argv[2] if len(sys.argv) > 2 else "cpr"
G.DATA = G.S.DATA = DATA
G.FAMS = ["round_minor", "round_major", "swing", "major"]
G.OUT = os.path.join(HERE, f"round69_{RUN}_results.json")
STEP = {"NIFTY": (100, 500), "BANKNIFTY": (500, 1000), "SENSEX": (500, 1000)}
_CUR = {"sym": None}
_load = G.load


def load(sym, tf):
    _CUR["sym"] = sym
    return _load(sym, tf)


def swings15(df):
    """Per 5m/15m bar: last two confirmed 15-min swing highs and lows (known only k bars after the swing)."""
    h15 = df.resample("15min").agg({"high": "max", "low": "min"}).dropna()
    hs, ls, t = h15["high"].values, h15["low"].values, h15.index
    k = 3
    ev = []
    for p in range(k, len(hs) - k):
        known = t[p + k] + pd.Timedelta(minutes=15)
        if hs[p] >= hs[p - k:p + k + 1].max():
            ev.append((known, "H", hs[p]))
        if ls[p] <= ls[p - k:p + k + 1].min():
            ev.append((known, "L", ls[p]))
    ev.sort(key=lambda x: x[0])
    n = len(df)
    out = np.full((n, 4), np.nan)          # last high, previous high, last low, previous low
    H, L = [np.nan, np.nan], [np.nan, np.nan]
    j = 0
    idx = df.index
    for i in range(n):
        while j < len(ev) and ev[j][0] <= idx[i]:
            if ev[j][1] == "H":
                H = [ev[j][2], H[0]]
            else:
                L = [ev[j][2], L[0]]
            j += 1
        out[i] = (H[0], H[1], L[0], L[1])
    return out


def day_levels(df):
    sym = _CUR["sym"]
    LV0, fam0, width, bias = _levels_orig(df)
    d = df.index.normalize()
    g = df.groupby(d).agg(H=("high", "max"), L=("low", "min"), C=("close", "last"))
    pc = g.C.shift(1).reindex(d).values
    cols, fams = [], []
    for fam, step in (("round_minor", STEP[sym][0]), ("round_major", STEP[sym][1])):
        base = np.floor(pc / step) * step
        span = int(np.ceil(0.015 * np.nanmax(pc) / step)) + 1
        for q in range(-span, span + 1):
            lv = base + q * step
            lv = np.where(np.abs(lv / pc - 1) <= 0.015, lv, np.nan)
            cols.append(lv); fams.append(fam)
    sw = swings15(df)
    for q in range(4):
        cols.append(sw[:, q]); fams.append("swing")
    for nd in (5, 20):
        hh = g.H.shift(1).rolling(nd, min_periods=nd).max().reindex(d).values
        ll = g.L.shift(1).rolling(nd, min_periods=nd).min().reindex(d).values
        cols += [hh, ll]; fams += ["major", "major"]
    LV = np.column_stack(cols)
    if RUN == "structure":
        up = (sw[:, 0] > sw[:, 1]) & (sw[:, 2] > sw[:, 3])
        dn = (sw[:, 0] < sw[:, 1]) & (sw[:, 2] < sw[:, 3])
        bias = np.where(up, 1, np.where(dn, -1, 0))
    return LV, np.array(fams), width, bias


_levels_orig = G.day_levels
G.load, G.day_levels = load, day_levels

if __name__ == "__main__":
    G.main()
