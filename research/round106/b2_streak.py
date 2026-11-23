"""B2: loss-streak sizing, strict (only closed trades count) vs the plain replay, at five starting sizes, by year and forward."""
import json
import r106lib as X
T, A, H = X.load_set("base"); out = {}
CAPS = (20_000, 40_000, 100_000, 200_000, 500_000)
PERS = ("2023-24", "2025-26", "full", "fwd")
def g(**kw): return X.grid(T, A, H, pers=PERS, caps=CAPS, **kw)
base = g(); out["base"] = base
yb = X.grid(T, A, H, pers=tuple(X.YEARS), caps=(200_000, 20_000)); out["base_years"] = yb
def show(nm, r, ys=None):
    main = [k for k in base if not k.endswith("fwd")]
    b = sum(1 for k in main if r[k]["final"] > base[k]["final"]); d = max(base[k]["dd"] - r[k]["dd"] for k in main)
    fw = [k for k in base if k.endswith("fwd")]; bf = sum(1 for k in fw if r[k]["final"] >= base[k]["final"])
    print(f"{nm:40s} better {b}/{len(main)} fall {d:+.1f} fwd not worse {bf}/{len(fw)}", flush=True)
    for k, v in r.items(): print(f"    {k:16s} {X.fmt(v):16s} base {X.fmt(base[k])}")
    if ys:
        for k, v in ys.items(): print(f"    {k:16s} {X.fmt(v):16s} base {X.fmt(yb[k])}")
for n, m in ((3, 0.5), (3, 0.67), (4, 0.5), (2, 0.5)):
    for strict in (True, False):
        nm = f"streak {n} -> {m}x {'strict' if strict else 'plain'}"
        r = g(streak=(n, m), strict=strict); out[nm] = r
        ys = X.grid(T, A, H, pers=tuple(X.YEARS), caps=(200_000, 20_000), streak=(n, m), strict=strict) if strict else None
        if ys: out[nm + " years"] = ys
        show(nm, r, ys)
json.dump(out, open("r106_b2.json", "w"), indent=1, default=str)
