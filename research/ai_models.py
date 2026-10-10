"""Round 12b - do other model families find patterns LightGBM missed?

Same feature table (ai_rows.parquet), same long table (bar x side x stop), same quarterly walk-forward
from 2024-Q1 and the same trade simulator as ai_train.py, so every model is scored on the same 2¾
unseen years with the same option pricing.  Models: ridge regression, logistic (P(win) ranked),
random forest, extra trees, histogram gradient boosting, a small neural net, and k-nearest
neighbours on the same scaled inputs.  Policies: fixed expected-R threshold 0.2 and "top 5 % of
past scores" (the model's own scale), so a model whose outputs are compressed is not punished.
usage: python ai_models.py [model,model,...]      output: ai_models.json
"""
import json, os, sys, time
from datetime import date
import numpy as np
import pandas as pd

os.environ.setdefault("FNO_CALIB", "1")
WANT = sys.argv[1].split(",") if len(sys.argv) > 1 else None
sys.argv = sys.argv[:1]                     # ai_policy reads argv[1] as a file tag
import engine as E
import ai_train as T
import ai_policy as P
from ai_data import SYMS

from sklearn.linear_model import Ridge, LogisticRegression
from sklearn.ensemble import RandomForestRegressor, ExtraTreesRegressor, HistGradientBoostingRegressor
from sklearn.neural_network import MLPRegressor
from sklearn.neighbors import KNeighborsRegressor
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.impute import SimpleImputer

MODELS = {
    "ridge": lambda: make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), Ridge(alpha=10.0)),
    "logistic": lambda: make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), LogisticRegression(C=0.1, max_iter=300)),
    "random_forest": lambda: make_pipeline(SimpleImputer(strategy="median"),
                                           RandomForestRegressor(n_estimators=120, min_samples_leaf=300, max_features=0.3, n_jobs=2, random_state=11)),
    "extra_trees": lambda: make_pipeline(SimpleImputer(strategy="median"),
                                         ExtraTreesRegressor(n_estimators=150, min_samples_leaf=300, max_features=0.3, n_jobs=2, random_state=11)),
    "hist_gb": lambda: HistGradientBoostingRegressor(loss="squared_error", max_iter=300, learning_rate=0.03, max_leaf_nodes=15,
                                                     min_samples_leaf=300, l2_regularization=5.0, random_state=11),
    "mlp": lambda: make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                                 MLPRegressor(hidden_layer_sizes=(64, 32), alpha=1e-2, max_iter=60, early_stopping=True, random_state=11)),
    "knn": lambda: make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), KNeighborsRegressor(n_neighbors=400, n_jobs=2)),
}
WANT = WANT or list(MODELS)


def main():
    df = P.df
    L, cols = T.long_table(df)
    dates = P.dates
    q = P.q
    X = L[cols].values.astype(np.float32)
    y = L["y"].values.astype(np.float32)
    rows = L["row"].values
    combo = ((L["side"].values < 0).astype(int) * 3 + np.searchsorted(T.STOPS, L["stop_k"].values))
    res = {}
    if os.path.exists("ai_models.json"):
        res = json.load(open("ai_models.json"))
    for name in WANT:
        t0 = time.time()
        er = np.full((len(df), 6), np.nan)
        for Q in P.quarters:
            yq, qq = divmod(int(Q), 10)
            q_start = date(yq, 3 * (qq - 1) + 1, 1)
            tr_rows = np.nonzero(dates < q_start)[0]
            te_rows = np.nonzero(q == Q)[0]
            trm = np.isin(rows, tr_rows)
            tem = np.isin(rows, te_rows)
            if name == "knn":                             # kNN: subsample the training rows, it is slow
                idx = np.nonzero(trm)[0]
                rng = np.random.default_rng(int(Q))
                trm = np.zeros(len(L), bool); trm[rng.choice(idx, min(len(idx), 150_000), replace=False)] = True
            m = MODELS[name]()
            if name == "logistic":
                m.fit(X[trm], (y[trm] > 0).astype(int))
                pred = m.predict_proba(X[tem])[:, 1]
                pred = pred * 2.0 - (1 - pred) * 1.0          # P(win) x 2R target - P(loss) x 1R = expected R if exits are clean
            else:
                m.fit(X[trm], y[trm])
                pred = m.predict(X[tem])
            er[rows[tem], combo[tem]] = pred
            print(f"{name} Q{Q} done ({round(time.time()-t0)} s)", flush=True)
        np.save(f"ai_er_oos_{name}.npy", er)
        out = {}
        for pol, thr_fn in (("fixed 0.2", P.fixed(0.2)), ("top 5% of past scores", P.quantile(5)), ("top 2% of past scores", P.quantile(2))):
            s = P.run_policy(f"{name} / {pol}", er, thr_fn)
            if s:
                out[pol] = s
        res[name] = {"policies": out, "seconds": round(time.time() - t0)}
        json.dump(res, open("ai_models.json", "w"), indent=1, default=str)
    # the LightGBM reference on the same rows
    for pol, thr_fn in (("fixed 0.2", P.fixed(0.2)), ("top 5% of past scores", P.quantile(5)), ("top 2% of past scores", P.quantile(2))):
        s = P.run_policy(f"lightgbm / {pol}", P.er, thr_fn)
        res.setdefault("lightgbm", {"policies": {}})["policies"][pol] = s
    json.dump(res, open("ai_models.json", "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
