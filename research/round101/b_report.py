import json, sys, os
OUT = os.environ.get("R101_OUT") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
import pandas as pd
R = pd.DataFrame(json.load(open(OUT + "/b_5m.json")) + json.load(open(OUT + "/b_15m.json")))
R = R[R.n > 0]
print("cells", len(R), "| positive in design:", int((R.a > 0).sum()), "| positive in unseen:", int((R.b > 0).sum()), "| positive in both:", int(((R.a > 0) & (R.b > 0)).sum()))
print("of the design-positive cells, positive unseen:", int(((R.a > 0) & (R.b > 0)).sum()), "/", int((R.a > 0).sum()))
# family level: a variant = family x tf x exit x per_day, summed over the three indices
V = R.groupby(["family", "tf", "exit", "per_day"]).agg(a=("a", "sum"), b=("b", "sum"), n=("n", "sum"), a_n=("a_n", "sum"), b_n=("b_n", "sum"), pos_a=("a", lambda x: (x > 0).sum()), pos_b=("b", lambda x: (x > 0).sum())).reset_index()
V["a_avg"] = V.a / V.a_n.clip(lower=1); V["b_avg"] = V.b / V.b_n.clip(lower=1)
print("variants (3 indices together):", len(V), "| design +:", int((V.a > 0).sum()), "| unseen +:", int((V.b > 0).sum()), "| both +:", int(((V.a > 0) & (V.b > 0)).sum()))
pd.set_option("display.width", 250); pd.set_option("display.max_rows", 200)
F = V.groupby("family").agg(variants=("a", "size"), design_pos=("a", lambda x: (x > 0).sum()), unseen_pos=("b", lambda x: (x > 0).sum()), best_design=("a_avg", "max"), med_design=("a_avg", "median"), med_unseen=("b_avg", "median")).round(0)
print(F.sort_values("med_unseen", ascending=False).to_string())
print("\nTop 15 variants by DESIGN-period Rs per trade (min 150 design trades) and what they did unseen:")
top = V[V.a_n >= 150].sort_values("a_avg", ascending=False).head(15)
print(top[["family", "tf", "exit", "per_day", "a_n", "a_avg", "pos_a", "b_n", "b_avg", "pos_b", "a", "b"]].round(0).to_string(index=False))
V.to_json(OUT + "/b_variants.json", orient="records")
