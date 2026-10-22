"""Round 39: train the final 'AI judge' (meta-label model for the rules' own signals) on every Nifty / Bank Nifty trade of the
current set up to the last feature-table day, and export it to fno/ai_judge.npz (numpy-only inference, same tree layout as
fno/ai_model.npz). Usage: python round39_export.py /path/to/fno-package-dir"""
import os, sys, json, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import numpy as np, lightgbm as lgb
import round39 as R

OUT = os.path.join(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "fno"), "ai_judge.npz")


def flatten(booster, arrays, roots):
    for t in booster.dump_model()["tree_info"]:
        base = len(arrays["feat"]); nodes = []

        def walk(nd):
            idx = len(nodes); nodes.append(None)
            if "leaf_value" in nd:
                nodes[idx] = (-1, 0.0, -1, -1, 0, float(nd["leaf_value"]))
            else:
                l = walk(nd["left_child"]); r = walk(nd["right_child"])
                nodes[idx] = (int(nd["split_feature"]), float(nd["threshold"]), base + l, base + r, 1 if nd.get("default_left", True) else 0, 0.0)
            return idx
        walk(t["tree_structure"])
        for f, th, l, r, dl, v in nodes:
            for k, x in zip(("feat", "thr", "left", "right", "dleft", "value"), (f, th, l, r, dl, v)):
                arrays[k].append(x)
        roots.append(base)


if __name__ == "__main__":
    J = R.trades_with_features(); X = R.COLS + ["slot_id", "strat_id"]
    y = np.clip(J.u.values / 2000.0, -1.5, 4.0)
    m = lgb.train({**R.PB, "seed": 11}, lgb.Dataset(J[X], y), 200)
    p = m.predict(J[X])
    arrays = {k: [] for k in ("feat", "thr", "left", "right", "dleft", "value")}; roots = []
    flatten(m, arrays, roots)
    meta = dict(round=39, cols=X, dir_cols=R.DIR, centered_cols=["rsi", "m15_rsi"], cut=float(np.median(p)),
                train_until=str(J.date.max()), trades=int(len(J)), syms=["NIFTY"], high=1.33, low=0.67, clip=[0.5, 1.6],
                slot_id={"main": 0, "trend": 1, "fib": 2}, strat_id={"noise": 0, "camarilla": 1, "trend": 2, "fib": 1})
    np.savez_compressed(OUT, feat=np.array(arrays["feat"], np.int32), thr=np.array(arrays["thr"]),
                        left=np.array(arrays["left"], np.int32), right=np.array(arrays["right"], np.int32),
                        dleft=np.array(arrays["dleft"], np.int8), value=np.array(arrays["value"]),
                        roots=np.array(roots, np.int32), kind=np.zeros(len(roots), np.int8), meta=json.dumps(meta))
    # numpy parity
    sys.path.insert(0, os.path.dirname(os.path.dirname(OUT)))
    from fno import ai_judge as AJ
    AJ._M.clear()
    q = AJ.predict(J[X].values.astype(float))
    print("trades", len(J), "until", meta["train_until"], "cut", round(meta["cut"], 4), "max |diff|", float(np.abs(q - p).max()),
          "trees", len(roots), "size MB", round(os.path.getsize(OUT) / 1e6, 2))
