"""B3: does booking a trade's result at its entry (plain replay) flatter today's weekly limit and drawdown brake?
Strict = only closed trades count for sizing, brake, limits and cash (what the live app sees)."""
import json
import r106lib as X
T, A, H = X.load_set("base"); out = {}
CAPS = (20_000, 40_000, 100_000, 200_000, 500_000); PERS = ("2023-24", "2025-26", "full", "fwd")
g = lambda **kw: X.grid(T, A, H, pers=PERS, caps=CAPS, **kw)
plain = g(); strict = g(strict_eq=True); out["plain"] = plain; out["strict"] = strict
print("parity plain vs house:", all(abs(plain[f'{c}|{p}']['final'] - X.base_grid()[f'{c}|{p}']['final']) < 1 for c, p in X.SIX))
for k in plain: print(f"  {k:16s} plain {X.fmt(plain[k]):16s} strict {X.fmt(strict[k])}")
def cmp(nm, **kw):
    r = g(strict_eq=True, **kw); out[nm] = r
    main = [k for k in strict if not k.endswith("fwd")]
    b = sum(1 for k in main if r[k]["final"] > strict[k]["final"]); d = max(strict[k]["dd"] - r[k]["dd"] for k in main)
    print(f"{nm:40s} vs strict baseline: better {b}/{len(main)} fall {d:+.1f} | " + " ".join(f"{k.split('|')[0][:-3]}k/{k.split('|')[1]} {X.fmt(v)}" for k, v in r.items()), flush=True)
print("\nToday's brakes judged in strict mode (one off at a time):")
cmp("strict: brake OFF", dd_levels=())
cmp("strict: weekly limits OFF", weekly=1.0, weekly_small=1.0)
cmp("strict: brake 0.5x below 20%", dd_levels=((0.20, 0.5),))
cmp("strict: brake 0.5x below 10%", dd_levels=((0.10, 0.5),))
cmp("strict: brake 0.33x below 15%", dd_levels=((0.15, 0.33),))
cmp("strict: weekly 3%", weekly=0.03)
cmp("strict: weekly 6%", weekly=0.06)
cmp("strict: monthly 8%", monthly=0.08)
cmp("strict: streak 3 -> 0.5x", streak=(3, 0.5), strict=True)
json.dump(out, open("r106_b3.json", "w"), indent=1, default=str)
