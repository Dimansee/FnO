"""Builds the data tables of the round-101 write-up from the saved result files -> tables.md"""
import json, os
import numpy as np, pandas as pd
H = os.environ.get("R101_OUT") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
L = lambda f: json.load(open(os.path.join(H, f)))
C = ["200000|2023-24", "200000|2025-26", "200000|full", "20000|2023-24", "20000|2025-26", "20000|full"]
HEAD = "| Rs 2L 2023-24 | Rs 2L 2025-26 | Rs 2L full | Rs 20k 2023-24 | Rs 20k 2025-26 | Rs 20k full |"
out = []
P = out.append
lk = lambda v: f"{v / 1e5:.2f}L"


def cells(v, ref):
    return " | ".join(f"{'**' if v[c]['final'] > ref[c]['final'] else ''}{lk(v[c]['final'])}{'**' if v[c]['final'] > ref[c]['final'] else ''} ({v[c]['dd']:.0f}%)" for c in C)


def verdict(v, ref):
    b = sum(v[c]["final"] > ref[c]["final"] for c in C); f = all(v[c]["dd"] >= ref[c]["dd"] - 2 for c in C)
    return b, f, ("PASS" if b == 6 and f else "no")


a = L("a_review.json"); ref = a["today"]
P("### Today (reference)\n"); P("| | " + HEAD[2:]); P("|---|" + "---|" * 6)
P("| Today's rules | " + " | ".join(f"{lk(ref[c]['final'])} ({ref[c]['dd']:.0f}%)" for c in C) + " |\n")
# A
P("### A. The 11 slots traded today (1 lot, Rs per lot by year) and what happens if one is removed\n")
P("| Slot | Trades | Wins | Avg R | 2023 | 2024 | 2025 | 2026 | Avg R 2023-24 | Avg R 2025-26 | Removed: cells better (of 6) | Fall rule | Verdict |"); P("|---|" + "---|" * 12)
for k, s in sorted(a["slots"].items(), key=lambda x: -x[1]["avgR"]):
    b, f, vd = verdict(a["loo"][k], ref); y = s["years"]
    P(f"| {k} | {s['n']} | {s['win'] * 100:.0f}% | {s['avgR']:.2f} | {y.get('2023', 0):+,.0f} | {y.get('2024', 0):+,.0f} | {y.get('2025', 0):+,.0f} | {y.get('2026', 0):+,.0f} | {s['avgR_a']:.2f} | {s['avgR_b']:.2f} | {b} | {'ok' if f else 'deeper'} | {vd} |")
P("")
# B
R = pd.DataFrame(L("b_5m.json") + L("b_15m.json")); R = R[R.n > 0]
V = R.groupby(["family", "tf", "exit", "per_day"]).agg(a=("a", "sum"), b=("b", "sum"), a_n=("a_n", "sum"), b_n=("b_n", "sum")).reset_index()
V["a_avg"] = V.a / V.a_n.clip(lower=1); V["b_avg"] = V.b / V.b_n.clip(lower=1)
P(f"### B. Indicator families not in today's rules: {len(V)} versions ({len(R)} index x version cells)\n")
P(f"Versions making money (three indices together, 1 lot, after costs): design 2023-24 **{int((V.a > 0).sum())}**, unseen 2025-26 **{int((V.b > 0).sum())}**, both **{int(((V.a > 0) & (V.b > 0)).sum())}** of {len(V)}.\n")
F = V.groupby("family").agg(n=("a", "size"), dp=("a", lambda x: (x > 0).sum()), up=("b", lambda x: (x > 0).sum()), both=("a", lambda x: 0), md=("a_avg", "median"), mu=("b_avg", "median"), bu=("b_avg", "max")).reset_index()
F["both"] = [int(((V[V.family == f].a > 0) & (V[V.family == f].b > 0)).sum()) for f in F.family]
P("| Family | Versions | + in 2023-24 | + in 2025-26 | + in both | Median Rs/trade 2023-24 | Median Rs/trade 2025-26 | Best Rs/trade 2025-26 |"); P("|---|" + "---|" * 7)
for _, r in F.sort_values("mu", ascending=False).iterrows():
    P(f"| {r.family} | {r.n} | {r.dp} | {r.up} | {r.both} | {r.md:+,.0f} | {r.mu:+,.0f} | {r.bu:+,.0f} |")
P("")
P("Top 10 versions by 2023-24 result (what one would have picked), and what they did afterwards:\n")
P("| Version | Trades 2023-24 | Rs/trade 2023-24 | Trades 2025-26 | Rs/trade 2025-26 |"); P("|---|---|---|---|---|")
for _, r in V[V.a_n >= 150].sort_values("a_avg", ascending=False).head(10).iterrows():
    P(f"| {r.family} {r.tf}, {r.exit}, {r.per_day}/day | {r.a_n} | {r.a_avg:+,.0f} | {r.b_n} | {r.b_avg:+,.0f} |")
P("")
# C
c = L("c_itsm.json")
P("### C. Intraday momentum (first half-hour predicts last half-hour)\n")
P("| Index | Period | Days | Correlation with last 30 min | Avg move 15:00-close when following the sign (bps) | Hit rate |"); P("|---|---|---|---|---|---|")
for s, d in c["raw"].items():
    for p in ("2023-24", "2025-26"):
        x = d.get(f"r1|{p}")
        if x:
            P(f"| {s} | {p} | {x['days']} | {x['corr_last30']:+.3f} | {x['bps_15:00-close']:+.2f} | {x['hit_15:00-close'] * 100:.0f}% |")
o = c["opt"]; o = o if isinstance(o, list) else list(o.values())
if o and isinstance(o[0], dict) and "a" in o[0]:
    P(f"\nAs bought options (1 lot, after costs): {len(o)} index x version cells; positive in 2023-24: {sum(x['a'] > 0 for x in o)}; in 2025-26: {sum(x['b'] > 0 for x in o)}; in both: {sum(x['a'] > 0 and x['b'] > 0 for x in o)}. Median Rs per trade: 2023-24 {np.median([x['a_avg'] for x in o]):+,.0f}, 2025-26 {np.median([x['b_avg'] for x in o]):+,.0f}.\n")
# D
d = pd.DataFrame(L("d_straddle.json")); d = d[(d.a_n > 0) | (d.b_n > 0)]
P("### D. Buying both sides (long straddle), model prices\n")
if "a" in d and "b" in d:
    g = d.groupby("filter").agg(n=("a", "size"), ap=("a", lambda x: (x > 0).sum()), bp=("b", lambda x: (x > 0).sum())).reset_index()
    g["both"] = [int(((d[d["filter"] == f].a > 0) & (d[d["filter"] == f].b > 0)).sum()) for f in g["filter"]]
    P("| Day filter | Versions | + in 2023-24 | + in 2025-26 | + in both |"); P("|---|---|---|---|---|")
    for _, r in g.iterrows():
        P(f"| {r['filter']} | {r.n} | {r.ap} | {r.bp} | {r.both} |")
P("")
# E
e = L("e_blocks.json"); e2 = L("e2.json")
P("### E. Today's rules on indices where they are not traded (added to today)\n"); P("| Added | " + HEAD[2:] + " Verdict |"); P("|---|" + "---|" * 7)
for k, v in e.items():
    if k == "today" or not isinstance(v, dict) or C[0] not in v:
        continue
    b, f, vd = verdict(v, ref); P(f"| {k} | {cells(v, ref)} | {vd} ({b}/6{'' if f else ', fall deeper'}) |")
v = e2["cam_sensex"]; b, f, vd = verdict(v, ref); P(f"| Camarilla SENSEX (exact rebuild of the Sensex set) | {cells(v, ref)} | {vd} ({b}/6{'' if f else ', fall deeper'}) |")
P("")
# F
fo = L("f_overlays.json")
P("### F. Portfolio overlays on today's trades\n"); P("| Overlay | " + HEAD[2:] + " Verdict |"); P("|---|" + "---|" * 7)
for k, v in fo.items():
    if k == "today" or not isinstance(v, dict) or C[0] not in v:
        continue
    b, f, vd = verdict(v, ref); P(f"| {k} | {cells(v, ref)} | {vd} ({b}/6{'' if f else ', fall deeper'}) |")
f3 = L("f3.json")
for k, v in f3.items():
    if k == "today" or not isinstance(v, dict) or C[0] not in v:
        continue
    b, f, vd = verdict(v, ref); P(f"| {k} | {cells(v, ref)} | {vd} ({b}/6{'' if f else ', fall deeper'}) |")
P("")
# H
h = L("h_perm.json")
P("### H. Size tilts against random tilts of the same strength\n")
P("Share of random tilts (same multipliers, same number of trades tilted, random trades) that the real tilt beats, per cell.\n")
P("| Tilt | " + HEAD[2:] + " Better in all six | Fall rule |"); P("|---|" + "---|" * 8)
for k, v in h.items():
    if k == "today":
        continue
    r_, sh = v["real"], v["beats_random_share"]
    row = " | ".join(f"{'**' if r_[c_]['final'] > ref[c_]['final'] else ''}{lk(r_[c_]['final'])}{'**' if r_[c_]['final'] > ref[c_]['final'] else ''} (beats {sh[c_] * 100:.0f}%)" for c_ in C)
    P(f"| {k} | {row} | {'yes' if v['better_everywhere'] else 'no'} | {'kept' if v['fall_ok'] else 'deeper'} |")
P("")
# G
g = L("g_add.json")
P("### G. Best new families of 2023-24 added to today's Autopilot as extra slots\n")
P("| Added piece (full size) | Alone 2023-24 Rs/lot | Alone 2025-26 Rs/lot | " + HEAD[2:] + " Verdict |"); P("|---|" + "---|" * 9)
for k, o_ in g["own"].items():
    v = g["alone|" + k]; b, f, vd = verdict(v, ref)
    P(f"| {k} | {o_[0]:+,.0f} | {o_[1]:+,.0f} | {cells(v, ref)} | {vd} ({b}/6{'' if f else ', fall deeper'}) |")
if g.get("chosen"):
    v = g["combo"]; b, f, vd = verdict(v, ref)
    P(f"| **Search result (chosen on 2023-24):** " + " + ".join(f"{k}{' (half size)' if hf else ''}" for k, hf in g["chosen"]) + f" | | | {cells(v, ref)} | {vd} ({b}/6{'' if f else ', fall deeper'}) |")
P("")
# I, J
i = L("i_keltner.json"); j = L("j_matched.json")
P("### I. Keltner breakout: sizes, neighbours, random control\n")
P("| Keltner 2.0, 3R target, stop 2 ATR, 3/day, Sensex | " + HEAD[2:] + " Verdict |"); P("|---|" + "---|" * 7)
for nm, v in i["sizes"].items():
    b, f, vd = verdict(v["cells"], ref); P(f"| {nm} size | {cells(v['cells'], ref)} | {vd} ({b}/6{'' if f else ', fall deeper'}) |")
P("")
P("| Group | Versions | Better in all six | Better in 5+ | Fall rule kept | Pass | Avg cells better (of 6) |"); P("|---|---|---|---|---|---|---|")
for sym in ("NIFTY", "SENSEX", "BANKNIFTY"):
    for size in ("full", "half"):
        X = [x for x in i["neigh"] if x["sym"] == sym and x["size"] == size]
        P(f"| Keltner on {sym}, {size} size | {len(X)} | {sum(x['better'] == 6 for x in X)} | {sum(x['better'] >= 5 for x in X)} | {sum(x['fall_ok'] for x in X)} | {sum(x['better'] == 6 and x['fall_ok'] for x in X)} | {np.mean([x['better'] for x in X]):.2f} |")
for size in ("full", "half"):
    X = [x for x in i["null"] if x["size"] == size]
    P(f"| Random entries on SENSEX (control), {size} size | {len(X)} | {sum(x['better'] == 6 for x in X)} | {sum(x['better'] >= 5 for x in X)} | {sum(x['fall_ok'] for x in X)} | {sum(x['better'] == 6 and x['fall_ok'] for x in X)} | {np.mean([x['better'] for x in X]):.2f} |")
P("")
own = j["own"]
P("Keltner alone (1 lot, Rs per lot), 30 versions per index:\n"); P("| Index | + in 2023-24 | + in 2025-26 | + in both | Median 2023 | Median 2024 | Median 2025 | Median 2026 |"); P("|---|" + "---|" * 7)
for sym in ("NIFTY", "SENSEX"):
    K = [k for k in own if k.startswith(sym)]
    P(f"| {sym} | {sum(own[k]['design'] > 0 for k in K)} | {sum(own[k]['unseen'] > 0 for k in K)} | {sum(own[k]['design'] > 0 and own[k]['unseen'] > 0 for k in K)} | "
      + " | ".join(f"{np.median([own[k]['years'].get(y, 0) for k in K]):+,.0f}" for y in ("2023", "2024", "2025", "2026")) + " |")
P("")
P("### J. Keltner with the whole account's risk lowered to keep the worst fall (chosen on 2023-24 only)\n")
P("Today at other risk settings (shows how much the final money depends on the path):\n"); P("| Risk per trade | " + HEAD[2:]); P("|---|" + "---|" * 6)
for r, v in j["today_frontier"].items():
    P(f"| {r}% | " + " | ".join(f"{lk(v[c_]['final'])} ({v[c_]['dd']:.0f}%)" for c_ in C) + " |")
P("")
P(f"Search on 2023-24 (Rs 2L): {j['design_n']} versions (60 Keltner versions x full/half size x risk 0.6-1.0%); better than today with the fall inside 2 points: **{j['design_ok']}**. Those, judged on everything else:\n")
P("| Chosen on 2023-24 | Size | Risk | " + HEAD[2:] + " Verdict |"); P("|---|" + "---|" * 9)
for x in j["top"]:
    b, f, vd = verdict(x["cells"], ref); P(f"| Keltner {x['key']} | {x['size']} | {x['risk']}% | {cells(x['cells'], ref)} | {vd} ({b}/6{'' if f else ', fall deeper'}) |")
S = j.get("scan", [])
P(f"\nWith hindsight (every version x size x risk 0.7-0.9%, all six cells at once): pass **{sum(x['better'] == 6 and x['fall_ok'] for x in S)} of {len(S)}**; better in all six with a deeper fall {sum(x['better'] == 6 for x in S)}; fall rule kept {sum(x['fall_ok'] for x in S)}.\n")
open(os.path.join(H, "tables.md"), "w").write("\n".join(out))
print("\n".join(out))
