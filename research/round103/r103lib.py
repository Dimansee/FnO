"""Round 103 helpers (20-21 Nov 2026). Built on the round-101 harness (today's Autopilot replay) with two additions:
the data now runs to 20 Nov 2026 (live recorder days appended), so 'fwd' = 19 Oct - 20 Nov 2026 is a true forward window for
everything decided on data to 16 Oct; and a verdict() that applies the house bar (more money in the six cells, worst fall no
more than 2 points deeper)."""
import os, sys, pickle
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
os.environ.setdefault("R101_OUT", "/tmp/r103_out")
import importlib.util as _iu
_spec = _iu.spec_from_file_location("r101lib", ROOT + "/research/round101/lib.py")
L = _iu.module_from_spec(_spec); _spec.loader.exec_module(L)      # round-101 harness
import numpy as np, pandas as pd
from fno import portfolio_bt as PB, config as C

PER = dict(L.PER)
PER["fwd"] = ("2026-10-19", "2026-11-20")
PER["full+"] = ("2023-01-02", "2026-11-20")
SIX = [(cap, p) for cap in (200_000, 20_000) for p in ("2023-24", "2025-26", "full")]
T0, A0, H0 = L.base_sets()


def run(cap, per, T=None, A=None, H=None, **over):
    a, b = PER[per]
    return L.replay(cap, a, b, T=T0 if T is None else T, A=A0 if T is None else A, H=H0 if T is None else H, **over)


def six(T=None, A=None, H=None, **over):
    return {f"{cap}|{p}": run(cap, p, T, A, H, **over) for cap, p in SIX}


def verdict(cells, base, tol=2.0):
    """cells/base: {'cap|per': {'final','dd'}}. Better = more money; fall rule = dd no more than tol points deeper."""
    better = sum(1 for k in base if cells[k]["final"] > base[k]["final"])
    deeper = max(base[k]["dd"] - cells[k]["dd"] for k in base)      # dd are negative numbers: deeper = more negative
    return better, round(deeper, 1)


def fmt(c):
    return f"{c['final'] / 1e5:.2f}L ({c['dd']:.0f}%)"
