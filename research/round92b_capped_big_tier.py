"""Round 92b: the Rs 1,500-per-lot loss cap ONLY on the 2-OTM (big-account) tier, from gate G (Rs 5L today, also 2L / 1L).
Round 92: '2 OTM always + Rs 1,500 cap' beat today at Rs 2L in every year and half (37.8L / 31.1L / 190.0L vs 18.3L / 22.9L / 171.7L)
but lost at Rs 20k in both halves - the same split as round 88. So here small accounts keep today's rules exactly and only the
tier that is already 2 OTM gets the cap. Chosen after seeing round 92 results: a generous test.
Usage: python research/round92b_capped_big_tier.py
"""
import json, os, sys, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import outcomes as O
from fno import portfolio_bt as PB
from fno import config as C
import round92_outcome_combos as R92

_orig_hi = PB._hi_key


def capped_hi(q):
    k = _orig_hi(q)
    return (k[0], k[1], k[2], 1500) if k else None


def main():
    res = {}
    base_gate = C.BIG_OTM_FROM
    for name, gate, cap_on in (("today", base_gate, False), ("cap on 2-OTM tier, gate Rs 5L", 500_000, True),
                               ("cap on 2-OTM tier, gate Rs 2L", 200_000, True), ("cap on 2-OTM tier, gate Rs 1L", 100_000, True),
                               ("2 OTM from Rs 2L, no cap", 200_000, False)):
        C.BIG_OTM_FROM = gate
        PB._hi_key = capped_hi if cap_on else _orig_hi
        res[name] = {}
        for cap in (200_000, 20_000):
            res[name][str(cap)] = {p: R92.run({}, cap, a, b) for p, (a, b) in R92.PER.items()}
        r2, r20 = res[name]["200000"], res[name]["20000"]
        print(f"{name:<32} Rs2L " + " ".join(f"{p}:{r2[p]['final'] / 1e5:7.2f}L" for p in R92.PER) + f" fall {r2['full']['dd']:+.1f}% | "
              "Rs20k " + " ".join(f"{p}:{r20[p]['final'] / 1e5:6.2f}L" for p in ("2023-24", "2025-26", "full")) + f" fall {r20['full']['dd']:+.1f}%",
              flush=True)
    C.BIG_OTM_FROM = base_gate; PB._hi_key = _orig_hi
    base = res["today"]
    res["_pass"] = {n: R92.passes(r, base) for n, r in res.items() if n != "today" and not n.startswith("_")}
    print("PASS:", [n for n, v in res["_pass"].items() if v], flush=True)
    json.dump(res, open(os.path.join(HERE, "round92b_results.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
