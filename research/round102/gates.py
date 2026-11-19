"""Round 102c: the equity gates (Improvements tab: 'Rule waits for a bigger account') and the Rs 5 lakh rules.
Today: trend-day from Rs 40k, Fibonacci from Rs 2L, channel 15m (half size) from Rs 5L, 2-OTM strike from Rs 5L, 1-OTM below Rs 60k.
Each variant moves one or more thresholds; everything else as live. Cells: Rs 20k / 2L / 5L x 2023-24 / 2025-26 / full."""
import sys, json, itertools
sys.path.insert(0, "/home/claude/dimansee/fno/research/round101")
import lib as L
from fno import config as C

CAPS = (20000, 200000, 500000)
ORIG = {k: getattr(C, k) for k in ("TREND_MIN_EQUITY", "FIB_MIN_EQUITY", "CHANNEL_MIN_EQUITY", "BIG_OTM_FROM", "SMALL_OTM_BELOW")}


def cells(**over):
    out = {}
    for cap in CAPS:
        for p, (a, b) in L.PER.items():
            out[f"{cap}|{p}"] = L.replay(cap, a, b, **over)
    return out


def with_consts(consts, **over):
    for k, v in consts.items():
        setattr(C, k, v)
    try:
        return cells(**over)
    finally:
        for k, v in ORIG.items():
            setattr(C, k, v)


V = {
    "today (all Rs 5L rules on)": ({}, {}),
    "without the Rs 5L rules (no channel slot, no 2-OTM)": ({}, {"channel": False, "big_otm": False}),
    "without the channel slot only": ({}, {"channel": False}),
    "without the 2-OTM switch only": ({}, {"big_otm": False}),
    "no equity gates at all": ({}, {"gates": False}),
    "trend-day from Rs 20k": ({"TREND_MIN_EQUITY": 20000}, {}),
    "trend-day from Rs 0": ({"TREND_MIN_EQUITY": 0}, {}),
    "Fibonacci from Rs 1L": ({"FIB_MIN_EQUITY": 100000}, {}),
    "Fibonacci from Rs 50k": ({"FIB_MIN_EQUITY": 50000}, {}),
    "Fibonacci from Rs 3L": ({"FIB_MIN_EQUITY": 300000}, {}),
    "channel from Rs 2L": ({"CHANNEL_MIN_EQUITY": 200000}, {}),
    "channel from Rs 3L": ({"CHANNEL_MIN_EQUITY": 300000}, {}),
    "channel from Rs 10L": ({"CHANNEL_MIN_EQUITY": 1000000}, {}),
    "2-OTM from Rs 3L": ({"BIG_OTM_FROM": 300000}, {}),
    "2-OTM from Rs 10L": ({"BIG_OTM_FROM": 1000000}, {}),
    "channel + 2-OTM both from Rs 3L": ({"CHANNEL_MIN_EQUITY": 300000, "BIG_OTM_FROM": 300000}, {}),
}

if __name__ == "__main__":
    res = {}
    for name, (consts, over) in V.items():
        res[name] = with_consts(consts, **over)
        print(name, {k: (round(v["final"] / 1e5, 2), v["dd"]) for k, v in res[name].items()}, flush=True)
    json.dump(res, open("/home/claude/r102/out/gates.json", "w"), indent=1)
