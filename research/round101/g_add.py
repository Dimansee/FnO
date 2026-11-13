"""Part G: the new families that looked best in the DESIGN period (2023-24), added to today's Autopilot as extra slots.
Same protocol as round 90: pieces = family x index; pre-filter and greedy forward selection on 2023-24 only (Rs 2L; add the
piece that raises the 2023-24 final most without deepening the worst fall by more than 2 points; full or half size; max 4);
then judged on 2025-26 (never seen), the full period and Rs 20k."""
import sys, json, importlib, time; sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
from lib import *
sys.argv = [sys.argv[0], "15m"]
import b_families as bf
SPECS = [("ichimoku cross+cloud", "15m", "trail 1.5R, stop 1 ATR", 3), ("keltner 2.0", "15m", "trail 1.5R, stop 1 ATR", 3), ("keltner 2.0", "15m", "3R target, stop 2 ATR", 3),
         ("linreg trend r2>0.7", "15m", "trail 1.5R, stop 1 ATR", 1), ("linreg trend r2>0.7", "5m", "3R target, stop 2 ATR", 1), ("avg-price band 2.0 trend", "15m", "trail 1.5R, stop 1 ATR", 1)]
pieces, own = {}, {}
t0 = time.time()
for tf in ("15m", "5m"):
    bf.TF, bf.STEP = tf, (5 if tf == "5m" else 15)
    for sym in SYMS:
        df, o, h, l, c, hm, day, a14, F = bf.families(sym)
        for fam, tf_, en, pdy in SPECS:
            if tf_ != tf:
                continue
            L, S, xL, xS, ex = F[fam]; e = bf.EXITS[en]
            L = np.nan_to_num(np.asarray(L, float)) > 0; S = np.nan_to_num(np.asarray(S, float)) > 0
            rows = simulate(o, h, l, c, hm, day, L, S, e["k"] * a14, tp_R=e["tp_R"], xL=xL if e["sig"] else None, xS=xS if e["sig"] else None, per_day=pdy, step=bf.STEP, trail_R=e.get("trail_R", 0))
            T = price(sym, df, rows, bf.STEP)
            key = f"{fam} {tf} [{en.split(',')[0]}, {pdy}/day] {sym}"
            pieces[key] = to_piece(sym, T, key); own[key] = (float(T[T.date < "2025"].rs.sum()), float(T[T.date >= "2025"].rs.sum()), len(T))
print("pieces", len(pieces), round(time.time() - t0), "s", flush=True)
for k, v in own.items():
    print(f"   {k:<78} design {v[0]:>+10,.0f}  unseen {v[1]:>+10,.0f}  n={v[2]}")
def build(chosen):
    return [pieces[k].assign(risk_lot=pieces[k].risk_lot * (2 if half else 1), risk_lot_capped=pieces[k].risk_lot_capped * (2 if half else 1)) for k, half in chosen]
ref = {(cap, p): replay(cap, a, b) for cap in (200_000, 20_000) for p, (a, b) in PER.items()}
res = {"own": own, "today": {f"{c}|{p}": v for (c, p), v in ref.items()}}
# every piece alone
print("\neach piece alone added to Autopilot (full size):")
for k in pieces:
    row = {(cap, p): replay(cap, a, b, extra=build([(k, False)])) for cap in (200_000, 20_000) for p, (a, b) in PER.items()}
    ok = all(row[x]["final"] > ref[x]["final"] and row[x]["dd"] >= ref[x]["dd"] - 2 for x in ref)
    res["alone|" + k] = {f"{c}|{p}": v for (c, p), v in row.items()}; res["alone|" + k]["pass"] = ok
    print(f"   {k[:70]:<71}" + "".join(f"{row[(c, p)]['final'] / 1e5:>9.2f}{'*' if row[(c, p)]['final'] > ref[(c, p)]['final'] else ' '}" for c in (200_000, 20_000) for p in PER) + ("  PASS" if ok else ""), flush=True)
print(f"   {'(today)':<71}" + "".join(f"{ref[(c, p)]['final'] / 1e5:>9.2f} " for c in (200_000, 20_000) for p in PER))
# greedy search on the design period
keys = [k for k, v in own.items() if v[0] > 0]
chosen, best = [], ref[(200_000, "2023-24")]; floor = best["dd"] - 2.0
print(f"\nsearch on 2023-24 (Rs 2L): today {best['final']:,.0f} fall {best['dd']:+.1f}%; {len(keys)} of {len(own)} pieces make money alone in 2023-24")
for step in range(4):
    top = None
    for k in keys:
        if any(k == c_[0] for c_ in chosen):
            continue
        for half in (False, True):
            r = replay(200_000, *PER["2023-24"], extra=build(chosen + [(k, half)]))
            if r["dd"] >= floor and r["final"] > best["final"] and (top is None or r["final"] > top[1]["final"]):
                top = ((k, half), r)
    if top is None:
        print(f"  step {step + 1}: nothing improves 2023-24 without a deeper fall - stop"); break
    chosen.append(top[0]); best = top[1]
    print(f"  step {step + 1}: + {top[0][0]}{' (half size)' if top[0][1] else ''} -> {best['final']:,.0f} fall {best['dd']:+.1f}%", flush=True)
res["chosen"] = chosen
if chosen:
    row = {(cap, p): replay(cap, a, b, extra=build(chosen)) for cap in (200_000, 20_000) for p, (a, b) in PER.items()}
    ok = all(row[x]["final"] > ref[x]["final"] and row[x]["dd"] >= ref[x]["dd"] - 2 for x in ref)
    res["combo"] = {f"{c}|{p}": v for (c, p), v in row.items()}; res["combo_pass"] = ok
    for cap in (200_000, 20_000):
        for p in PER:
            print(f"    Rs {cap:>7,} {p:<8} today {ref[(cap, p)]['final']:>12,.0f} ({ref[(cap, p)]['dd']:+.1f}%)  combo {row[(cap, p)]['final']:>12,.0f} ({row[(cap, p)]['dd']:+.1f}%)  {'SEARCHED' if p == '2023-24' and cap == 200_000 else 'UNSEEN' if p == '2025-26' else ''}")
    print("   combination passes the bar:", ok)
json.dump(res, open(OUT + "/g_add.json", "w"), indent=1, default=str)
