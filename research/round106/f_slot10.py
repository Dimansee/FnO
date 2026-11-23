"""F: the one survivor - no entries 10:00-10:29. What is in the slot, five starting sizes, forward window, neighbours, and a
chance check (how often does SOME half-hour slot lose in all four years when trade results are shuffled across slots)."""
import json
import numpy as np, pandas as pd
import r106lib as X
T, A, H = X.load_set("base"); out = {}
slot = lambda t: t[:2] + ":" + ("00" if int(t[3:5]) < 30 else "30")
T = T.assign(slot=T.entry_time.map(slot), y=T.date.str[:4])
z = T[T.slot == "10:00"]
print("10:00 slot per-lot by strategy and year:")
print(z.pivot_table(index="strategy", columns="y", values="pnl", aggfunc="sum").round(0).to_string())
print(z.pivot_table(index="strategy", columns="y", values="pnl", aggfunc="count").to_string())
print("by index:"); print(z.pivot_table(index="sym", columns="y", values="pnl", aggfunc="sum").round(0).to_string())
print("by 5-min entry time:"); print(z.groupby("entry_time").pnl.agg(["count", "sum", "mean"]).round(0).to_string())
print("R by year (avg):", z.groupby("y").R.mean().round(3).to_dict(), " all other slots:", T[T.slot != "10:00"].groupby("y").R.mean().round(3).to_dict())
# chance check
rng = np.random.default_rng(7); hits = 0; N = 2000
pn, sl, yy = T.pnl.values, T.slot.values, T.y.values
for _ in range(N):
    p = rng.permutation(pn)
    df = pd.DataFrame({"p": p, "s": sl, "y": yy}).groupby(["s", "y"]).p.sum().unstack()
    hits += int(((df < 0).sum(axis=1) == 4).any())
print(f"chance: some slot loses in all 4 years in {hits / N * 100:.1f}% of shuffles")
CAPS = (20_000, 40_000, 100_000, 200_000, 500_000); PERS = ("2023-24", "2025-26", "full", "fwd")
base = X.grid(T, A, H, pers=PERS, caps=CAPS)
def test(nm, fn):
    r = X.grid(T, A, H, pers=PERS, caps=CAPS, skip=fn); out[nm] = r
    main = [k for k in base if not k.endswith("fwd")]
    b = sum(1 for k in main if r[k]["final"] > base[k]["final"]); d = max(base[k]["dd"] - r[k]["dd"] for k in main)
    fw = sum(1 for k in base if k.endswith("fwd") and r[k]["final"] >= base[k]["final"])
    print(f"\n{nm}: better {b}/{len(main)} fall {d:+.1f} fwd not worse {fw}/5", flush=True)
    for k in r: print(f"   {k:16s} {X.fmt(r[k]):16s} base {X.fmt(base[k])}")
    ys = X.grid(T, A, H, pers=tuple(X.YEARS), caps=(200_000, 20_000), skip=fn); yb = X.grid(T, A, H, pers=tuple(X.YEARS), caps=(200_000, 20_000))
    print("   years: " + " | ".join(f"{k} {X.fmt(ys[k])} vs {X.fmt(yb[k])}" for k in ys))
test("no entries 10:00-10:29", lambda r: "10:00" <= r.entry_time <= "10:29")
test("no entries 10:05-10:25 (neighbour)", lambda r: "10:05" <= r.entry_time <= "10:25")
test("no entries 10:00-10:59 (neighbour)", lambda r: "10:00" <= r.entry_time <= "10:59")
test("no Channel entries 10:00-10:29 only", lambda r: "10:00" <= r.entry_time <= "10:29" and r.strategy.startswith("Channel"))
json.dump(out, open("r106_f.json", "w"), indent=1, default=str)
