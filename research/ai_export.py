"""Exports the trained LightGBM models to fno/ai_model.npz for numpy-only inference, and checks the
numpy predictions against LightGBM's on a sample.   usage: python ai_export.py _v3 [threshold]"""
import json, sys
import numpy as np
import pandas as pd
import lightgbm as lgb

sys.path.insert(0, "..")
TAGS = sys.argv[1].split(",") if len(sys.argv) > 1 else [""]
THR = float(sys.argv[2]) if len(sys.argv) > 2 else 0.2
SCALE = 1.0 / len(TAGS)              # an ensemble: average of the seeds' predictions


def flatten(booster, kind, arrays, roots, kinds):
    dump = booster.dump_model()
    for t in dump["tree_info"]:
        base = len(arrays["feat"])
        nodes = []

        def walk(nd):
            idx = len(nodes)
            nodes.append(None)
            if "leaf_value" in nd:
                nodes[idx] = (-1, 0.0, -1, -1, 0, float(nd["leaf_value"]) * SCALE)
            else:
                l = walk(nd["left_child"])
                r = walk(nd["right_child"])
                nodes[idx] = (int(nd["split_feature"]), float(nd["threshold"]), base + l, base + r,
                              1 if nd.get("default_left", True) else 0, 0.0)
            return idx
        walk(t["tree_structure"])
        for f, th, l, r, dl, v in nodes:
            arrays["feat"].append(f); arrays["thr"].append(th); arrays["left"].append(l); arrays["right"].append(r)
            arrays["dleft"].append(dl); arrays["value"].append(v)
        roots.append(base)
        kinds.append(kind)


meta = json.load(open(f"ai_meta{TAGS[0]}.json"))
regs = [lgb.Booster(model_file=f"ai_reg{t}.txt") for t in TAGS]
clss = [lgb.Booster(model_file=f"ai_cls{t}.txt") for t in TAGS]
arrays = {k: [] for k in ("feat", "thr", "left", "right", "dleft", "value")}
roots, kinds = [], []
for b in regs:
    flatten(b, 0, arrays, roots, kinds)
for b in clss:
    flatten(b, 1, arrays, roots, kinds)
meta["threshold"] = THR
meta["seeds"] = len(TAGS)
np.savez_compressed("../fno/ai_model.npz", feat=np.array(arrays["feat"], np.int32), thr=np.array(arrays["thr"]),
                    left=np.array(arrays["left"], np.int32), right=np.array(arrays["right"], np.int32),
                    dleft=np.array(arrays["dleft"], np.int8), value=np.array(arrays["value"]),
                    roots=np.array(roots, np.int32), kind=np.array(kinds, np.int8), meta=json.dumps(meta))
# check
from fno import ai_model as AM
df = pd.read_parquet("ai_rows.parquet").sample(3000, random_state=1)
cols = meta["cols"]
X = df.reindex(columns=cols).copy()
X["side"] = np.where(np.arange(len(X)) % 2 == 0, 1, -1)
X["stop_k"] = meta["stops"][0]
x = X[cols].values.astype(float)
a, b = np.mean([r.predict(X[cols]) for r in regs], axis=0), AM.expected_r(x)
z = np.mean([np.log(p / (1 - p)) for p in (c_.predict(X[cols]) for c_ in clss)], axis=0)
c, d = 1 / (1 + np.exp(-z)), AM.win_prob(x)
print("max |diff| expected R:", np.abs(a - b).max(), " win prob:", np.abs(c - d).max())
import os
print("model file", round(os.path.getsize("../fno/ai_model.npz") / 1e6, 2), "MB, trees", len(roots), "nodes", len(arrays["feat"]))
