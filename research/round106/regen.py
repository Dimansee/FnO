"""Round 106 (23 Nov 2026): regenerate today's Autopilot trade sets (1 ITM / 1 OTM / 2 OTM, three indices) with ONE rule change
that needs new trades (band width, look-back, VIX caps, entry cutoff, square-off, Noise time stop, channel catastrophic stop).
Usage: python regen.py NAME   (variants in VARIANTS). Output: /tmp/r106/NAME.pkl = (T, A, H), same columns as round 101's base_sets.
'base' must reproduce round 101's base_sets exactly (sanity check)."""
import os, sys, pickle, inspect, textwrap, time
from datetime import time as dtime
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT + "/research"); sys.path.insert(0, ROOT)
os.chdir(ROOT + "/research")
import warnings; warnings.filterwarnings("ignore")
import pandas as pd
import outcomes as O                                   # sets lot sizes, loads candles
from fno import backtest as B, portfolio_bt as PB, config as C, strategy as S

OUT = "/tmp/r106"; os.makedirs(OUT, exist_ok=True)

# ---- source patches (behaviour identical unless the new EXIT keys / _HN are set) --------------------------------------------
def _patch(fn, pairs):
    src = textwrap.dedent(inspect.getsource(fn))
    for a, b in pairs:
        assert src.count(a) == 1, (fn.__name__, a, src.count(a))
        src = src.replace(a, b)
    exec(compile(src, B.__file__, "exec"), B.__dict__)

B._HN = 15
_patch(B.run, [("sigma = S.noise_sigma(hist)", "sigma = S.noise_sigma(pd.concat([g for _, g in frames[max(0, n - _HN):n]]) if _HN != 15 else hist)")])
_patch(B._one_trade, [("            elif S._is_check(ts):\n",
                       "            elif strat == \"noise\" and _ex(\"noise_tstop\", 0) and best_r < 0.5 and (end - t_in).total_seconds() / 60 >= _ex(\"noise_tstop\", 0):\n"
                       "                reason, x_spot = \"Time stop (noise)\", float(c[\"close\"])\n"
                       "            elif S._is_check(ts):\n")])
_patch(B._channel_trades, [("if pos is not None and C.CHANNEL_HARD_STOP:", "if pos is not None and (C.CHANNEL_HARD_STOP or _ex(\"ch_cat_atr\", 0)):"),
                           ("pos = (sign, i, f, lo_[j] if sign > 0 else hi_[j], a / C.CHANNEL_SIZE)",
                            "pos = (sign, i, f, (f - sign * _ex(\"ch_cat_atr\", 0) * a) if _ex(\"ch_cat_atr\", 0) else (lo_[j] if sign > 0 else hi_[j]), a / C.CHANNEL_SIZE)")])

# extra EXIT keys survive PB._trades (it does EXIT.clear(); EXIT.update(ov)) because they go in through ov
VARIANTS = {
    "base": {},
    "band150": {"mult": 1.50}, "band200": {"mult": 2.00},
    "lb10": {"lookback": 10}, "lb20": {"lookback": 20},
    "nvix16": {"C": {"NOISE_VIX_MAX": 16.0}}, "nvix20": {"C": {"NOISE_VIX_MAX": 20.0}},
    "cvix18": {"C": {"CAM_VIX_MAX": 18.0}}, "cvix22": {"C": {"CAM_VIX_MAX": 22.0}},
    "ncut1330": {"C": {"LAST_ENTRY": dtime(13, 30)}}, "ncut1400": {"C": {"LAST_ENTRY": dtime(14, 0)}},
    "sq1500": {"C": {"SQUARE_OFF": dtime(15, 0)}},
    "ntstop45": {"ov": {"noise_tstop": 45}}, "ntstop60": {"ov": {"noise_tstop": 60}},
    "chcat25": {"ov": {"ch_cat_atr": 2.5}}, "chcat30": {"ov": {"ch_cat_atr": 3.0}}, "chcat40": {"ov": {"ch_cat_atr": 4.0}}, "chcat50": {"ov": {"ch_cat_atr": 5.0}},
}


def build(name):
    v = VARIANTS[name]
    for k, x in v.get("C", {}).items():
        setattr(C, k, x)
    if "lookback" in v:
        S.noise_sigma.__defaults__ = (v["lookback"],)
        B._HN = max(15, v["lookback"] + 1)
    if "mult" in v:
        orig = B.run
        B.run = lambda *a, _o=orig, **k: _o(*a, mult=v["mult"], **k)
    ov = dict(PB.EXITS["current"]["ov"], **v.get("ov", {}))
    vix = O.H.load("INDIAVIX")["close"]
    q = O.B0
    sets = []
    for otm in (int(q["otm"]), C.SMALL_OTM_STRIKES, C.BIG_OTM_STRIKES):
        parts = []
        for s in O.SYMS:
            c = O.candles(s)
            T = PB._trades(s, c, vix, otm, ov, q["big_run_skip"], int(q["max_loss_lot"] or 0))
            if not T.empty:
                T["score"] = PB._scores(T, c)
            parts.append(T)
        sets.append(pd.concat(parts, ignore_index=True))
    return tuple(sets)


if __name__ == "__main__":
    for name in sys.argv[1:]:
        f = f"{OUT}/{name}.pkl"
        if os.path.exists(f):
            continue
        t0 = time.time()
        R = build(name)
        pickle.dump(R, open(f, "wb"))
        print(f"{name}: {len(R[0])} trades in {time.time() - t0:.0f}s", flush=True)
