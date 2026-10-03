"""Runs the AI strategy's gradient-boosted trees with numpy only (no LightGBM on the server).

fno/ai_model.npz holds the trees exported by research/ai_export.py:
  feat   split feature index per node (-1 = leaf)
  thr    split threshold ("go left if x <= thr"; NaN goes to the default side)
  left / right   child node indices (absolute)
  dleft  1 if a missing value goes left
  value  leaf value
  roots  index of each tree's root, and `kind` tells which model each tree belongs to
Prediction = sum of leaf values (sigmoid for the win-probability model).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
_M = {}


def load():
    if not _M:
        z = np.load(HERE / "ai_model.npz")
        _M.update({k: z[k] for k in z.files})
        _M["meta"] = json.loads(str(z["meta"]))
    return _M


def _predict_trees(x: np.ndarray, roots: np.ndarray) -> np.ndarray:
    m = load()
    feat, thr, left, right, dleft, value = m["feat"], m["thr"], m["left"], m["right"], m["dleft"], m["value"]
    out = np.zeros(len(x))
    rows = np.arange(len(x))
    for r in roots:
        node = np.full(len(x), int(r))
        while True:
            f = feat[node]
            active = f >= 0
            if not active.any():
                break
            xv = x[rows[active], f[active]]
            go_left = np.where(np.isnan(xv), dleft[node[active]] == 1, xv <= thr[node[active]])
            node[active] = np.where(go_left, left[node[active]], right[node[active]])
        out += value[node]
    return out


def features_order() -> list[str]:
    return load()["meta"]["cols"]


def expected_r(x: np.ndarray) -> np.ndarray:
    """x: (n, len(cols)) in the order of features_order(), including side and stop_k columns."""
    m = load()
    return _predict_trees(x, m["roots"][m["kind"] == 0])


def win_prob(x: np.ndarray) -> np.ndarray:
    m = load()
    z = _predict_trees(x, m["roots"][m["kind"] == 1])
    return 1 / (1 + np.exp(-z))


def meta() -> dict:
    return load()["meta"]
