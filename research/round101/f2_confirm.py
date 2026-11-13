"""Part F2: cross-index confirmation, causal. At a Nifty / Sensex entry, has the SAME rule already fired the SAME side on the
other index today (at this time or earlier)? 'confirmed' is known at entry; a trade whose partner comes later is not."""
import sys, json; sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from lib import *
T0, A0, H0 = base_sets(); T0 = T0.sort_values(["date", "entry_time"]).reset_index(drop=True)
mins = lambda s: int(s[:2]) * 60 + int(s[3:])
T0["em"] = T0.entry_time.map(mins)
other = {"NIFTY": "SENSEX", "SENSEX": "NIFTY"}
first = T0.groupby(["date", "strategy", "side", "sym"]).em.min().to_dict()
def status(r):
    if r.sym not in other: return "bank nifty"
    t = first.get((r.date, r.strategy, r.side, other[r.sym]))
    if t is None: return "alone (no partner all day)"
    if t == r.em: return "same time"
    if t < r.em: return "partner fired earlier"
    return "partner fires later (unknown at entry)"
T0["st"] = [status(r) for r in T0.itertuples()]
T0["rs"] = (T0.prem_out - T0.prem_in) * T0.lot
pd.set_option("display.width", 250)
G = T0.groupby("st").agg(n=("R", "size"), avgR=("R", "mean"), win=("R", lambda x: (x > 0).mean()), rs=("rs", "mean")).round(3)
G["avgR_2023_24"] = T0[T0.date < "2025"].groupby("st").R.mean().round(3); G["avgR_2025_26"] = T0[T0.date >= "2025"].groupby("st").R.mean().round(3)
print(G.to_string())
print("\nby rule (Nifty + Sensex only): avg R  confirmed-at-entry (same time / partner earlier) vs not")
T0["conf"] = T0.st.isin(["same time", "partner fired earlier"])
X = T0[T0.sym.isin(other)]
print(X.groupby(["strategy", "conf"]).agg(n=("R", "size"), avgR=("R", "mean"), a=("R", lambda x: x[X.loc[x.index, "date"] < "2025"].mean()), b=("R", lambda x: x[X.loc[x.index, "date"] >= "2025"].mean())).round(3).to_string())
T0[["sym", "date", "entry_time", "strategy", "st", "conf"]].to_pickle(OUT + "/confirm.pkl")
