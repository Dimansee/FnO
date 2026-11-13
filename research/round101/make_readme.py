"""Assembles the round-101 write-up (README.md next to this file) from tables.md (make_doc.py) and the squeeze result files."""
import json, os, sys
import numpy as np
H = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get("R101_OUT") or os.path.join(H, "out")
L = lambda f: json.load(open(os.path.join(OUT, f)))
C = ["200000|2023-24", "200000|2025-26", "200000|full", "20000|2023-24", "20000|2025-26", "20000|full"]
HEAD = "| ₹2L 2023-24 | ₹2L 2025-26 | ₹2L full | ₹20k 2023-24 | ₹20k 2025-26 | ₹20k full |"
lk = lambda v: f"{v / 1e5:.2f}L"
ref = L("l_squeeze.json")["today"]


def cells(v):
    return " | ".join(f"{'**' if v[c]['final'] > ref[c]['final'] else ''}{lk(v[c]['final'])}{'**' if v[c]['final'] > ref[c]['final'] else ''} ({v[c]['dd']:.0f}%)" for c in C)


def verdict(v):
    b = sum(v[c]["final"] > ref[c]["final"] for c in C); f = all(v[c]["dd"] >= ref[c]["dd"] - 2 for c in C)
    return f"{'PASS' if b == 6 and f else 'no'} ({b}/6{'' if f else ', fall deeper'})"


T = open(os.path.join(OUT, "tables.md")).read().replace("Rs 2L", "₹2L").replace("Rs 20k", "₹20k").replace("Rs/", "₹/").replace("Rs ", "₹").replace("₹per", "₹ per")
keep = []
for ln in T.splitlines():
    if ln.startswith("| ") and " + " in ln.split("|")[1] and ("BANKNIFTY" in ln.split("|")[1] or "SENSEX" in ln.split("|")[1]) and "Search result" not in ln:
        continue                                       # the 11 combinations of missing blocks: summarised in one line instead
    keep.append(ln)
T = "\n".join(keep).replace("### E. Today's rules on indices where they are not traded (added to today)\n",
                            "### E. Today's rules on indices where they are not traded (added to today)\n\nAll 11 combinations of these four were also run: none passes (best 2 of 6 cells).\n")
k = L("k_others.json"); c20 = L("l_squeeze.json"); c14 = L("l_squeeze_n14_13.json"); ng = L("l_squeeze_2.json")["neigh"]; S = L("l_summary.json"); m = L("m_more.json"); xd = L("m_exitdelay.json")
P = []
A = P.append
A("### K. The three families with several versions that made money alone in both periods, added as a slot (chosen on 2023-24 only)\n")
A(f"Average-price band 2.0 trend, Ichimoku cross + cloud, TTM squeeze release (15-min; 4 exits x 1 or 3 a day x 3 indices = {len(k['own'])} pieces) x full / half size x risk 0.6-1.0% = {k['design_n']} versions. "
  f"Better than today in 2023-24 at ₹2L with the fall inside 2 points: **{k['design_ok']}**. The 12 best of those, judged on the other five cells:\n")
A("| Chosen on 2023-24 | Size | " + HEAD[2:] + " Verdict |"); A("|---|" + "---|" * 8)
for x in k["top"]:
    A(f"| {x['key']} | {x['size']} | {cells(x['cells'])} | {verdict(x['cells'])} |")
A("")
A("### L. Squeeze release - deep check\n")
A("Rule: on 15-min bars the Bollinger band (n bars, 2 sd) sits inside the Keltner channel (EMA n +/- 1.5 x ATR 10) = squeeze. When the band comes back out, buy a call if momentum (close minus the average of the n-bar mid-range and the n-bar mean) is up, a put if it is down, "
  "at the next bar's open. Stop 1.5 x ATR 14; exit when momentum changes sign; square-off 15:15. Entries 09:30-14:45.\n")
A("Two versions: **n = 20** (the one the design-only search of part K found) and **n = 14** (the best of the neighbour grid on 2023-24).\n")
A("| | n = 20, 3 a day | n = 14, 1 a day |"); A("|---|---|---|")
o20, o14 = c20["cand"]["own"], c14["cand"]["own"]
A(f"| Trades (Sensex, 2023 - Oct 2026) | {o20['n']} | {o14['n']} |")
A(f"| Alone, 1 lot: 2023-24 / 2025-26 | {o20['design']:+,.0f} / {o20['unseen']:+,.0f} | {o14['design']:+,.0f} / {o14['unseen']:+,.0f} |")
A(f"| Alone by year 2023 / 24 / 25 / 26 | " + " / ".join(f"{o20['years'][y]:+,.0f}" for y in ('2023', '2024', '2025', '2026')) + " | " + " / ".join(f"{o14['years'][y]:+,.0f}" for y in ('2023', '2024', '2025', '2026')) + " |")
A(f"| Wins, average R | {o20['win'] * 100:.0f}%, {o20['avgR']:.2f} | {o14['win'] * 100:.0f}%, {o14['avgR']:.2f} |")
A("")
A("Added to today's Autopilot at half size:\n")
A("| Version | " + HEAD[2:] + " Verdict |"); A("|---|" + "---|" * 7)
A("| Today | " + " | ".join(f"{lk(ref[c]['final'])} ({ref[c]['dd']:.0f}%)" for c in C) + " | |")
A(f"| Squeeze n = 20, 3 a day | {cells(c20['cand']['cells'])} | {verdict(c20['cand']['cells'])} |")
A(f"| Squeeze n = 14, 1 a day | {cells(c14['cand']['cells'])} | {verdict(c14['cand']['cells'])} |")
A("")
A("Execution stress (half size):\n")
A("| Stress | Version | " + HEAD[2:] + " Verdict |"); A("|---|" + "---|" * 8)
for tag, v in c20["stress"].items():
    if tag.endswith("|3") and not tag.startswith("as tested"):
        A(f"| {tag[:-2]} | n = 20 | {cells(v['cells'])} | {verdict(v['cells'])} |")
for tag, v in list(c14["stress"].items()) + list(xd.items()):
    if not tag.startswith("as tested"):
        A(f"| {tag} | n = 14 | {cells(v['cells'])} | {verdict(v['cells'])} |")
A("")
A("Each year alone (restart at the capital) and other starting capitals, n = 14:\n")
A("| | Today | With squeeze | |"); A("|---|---|---|---|")
for key, (t, c_) in c14["cand"]["years"].items():
    cap, y = key.split("|"); A(f"| {y}, ₹{int(cap):,} | {lk(t['final'])} ({t['dd']:.0f}%) | {lk(c_['final'])} ({c_['dd']:.0f}%) | {'better' if c_['final'] > t['final'] else '**worse**'} |")
for key, (t, c_) in c14["cand"]["caps"].items():
    cap, p = key.split("|"); A(f"| ₹{int(cap):,}, {p} | {lk(t['final'])} ({t['dd']:.0f}%) | {lk(c_['final'])} ({c_['dd']:.0f}%) | {'better' if c_['final'] > t['final'] else '**worse**'}{'' if c_['dd'] >= t['dd'] - 2 else ', fall deeper'} |")
A("")
A("One continuous run, money at each year end (n = 14):\n"); A("| Start | | 2023 | 2024 | 2025 | Oct 2026 |"); A("|---|---|---|---|---|---|")
for key, v in m["year_end"].items():
    cap, tag = key.split("|"); A(f"| ₹{int(cap):,} | {tag} | " + " | ".join(lk(v[y]) for y in ("2023", "2024", "2025", "2026")) + " |")
A("")
A("Neighbours (8 band settings x 4 exits x 1 or 3 a day, each at half and full size, added to today):\n")
A("| Group | Versions | Pass | Better in all six | Better in 5+ | Fall rule kept | Alone + in both periods |"); A("|---|---|---|---|---|---|---|")
for sym in ("SENSEX", "NIFTY", "BANKNIFTY"):
    for sz in ("half", "full"):
        X = [x for x in ng if x["sym"] == sym and x["size"] == sz]
        A(f"| Squeeze on {sym}, {sz} size | {len(X)} | {sum(x['better'] == 6 and x['fall_ok'] for x in X)} | {sum(x['better'] == 6 for x in X)} | {sum(x['better'] >= 5 for x in X)} | {sum(x['fall_ok'] for x in X)} | {sum(x['own']['design'] > 0 and x['own']['unseen'] > 0 for x in X)} |")
for tag, lab in (("n20_null", "Random entries, same exit and count as n = 20 (control)"), ("n14_null", "Random entries, same exit and count as n = 14 (control)")):
    v = S[tag]; A(f"| {lab}, half size | {v['n']} | {v['pass']} | 0 | {v['five_plus']} | | |")
A("")
ok = [x for x in ng if x["cells"][C[0]]["dd"] >= ref[C[0]]["dd"] - 2 and x["cells"][C[0]]["final"] > ref[C[0]]["final"]]
A(f"Forward selection on the grid: {len(ok)} of {len(ng)} versions beat today in 2023-24 at ₹2L inside the fall rule; of those **{sum(x['better'] == 6 and x['fall_ok'] for x in ok)} pass all six cells** and {sum(x['better'] >= 5 for x in ok)} are better in five or more. "
  f"The n = 14 candidate beats {min(S['n14_null']['cand_beats_share'].values()) * 100:.0f}% of its random controls in every cell; n = 20 beats {min(S['n20_null']['cand_beats_share'].values()) * 100:.0f}-{max(S['n20_null']['cand_beats_share'].values()) * 100:.0f}%.\n")
A("Block bootstrap (200 resampled histories, 20-day blocks, same resample for both):\n")
A("| Version | Capital | Squeeze ahead | Final money ratio 10th / median / 90th pct | Fall deeper by > 2 pts | Fall shallower by > 2 pts |"); A("|---|---|---|---|---|---|")
for tag, lab in (("n20", "n = 20"), ("n14", "n = 14")):
    for cap in ("200000", "20000"):
        v = S[tag][cap]; A(f"| {lab} | ₹{int(cap):,} | {v['ahead'] * 100:.0f}% | {v['ratio_p10_p50_p90'][0]:.2f} / {v['ratio_p10_p50_p90'][1]:.2f} / {v['ratio_p10_p50_p90'][2]:.2f} | {v['fall_deeper_2pts'] * 100:.0f}% | {v['fall_shallower_2pts'] * 100:.0f}% |")
A("")
ov = m["overlap"]
A(f"Overlap with today's trades (n = 14, {ov['n']} trades): an existing trade is open on the same side during {ov['same_side'] / ov['n'] * 100:.0f}% of them ({ov['same_side_sensex'] / ov['n'] * 100:.0f}% on Sensex itself), on the opposite side {ov['opposite'] / ov['n'] * 100:.0f}%, "
  f"nothing else open {ov['alone'] / ov['n'] * 100:.0f}%. Daily P&L correlation with today's rules {ov['corr']:.2f}. On today's worst 10% of days the squeeze slot lost another ₹{-ov['worst10_sum']:,.0f} per lot in total. "
  f"Quarters with a profit (alone): {sum(v > 0 for v in m['quarters'].values())} of {len(m['quarters'])}.\n")
A("Sizes and a Nifty twin (n = 14, exit on momentum turn, 1 a day):\n"); A("| Version | " + HEAD[2:] + " Verdict |"); A("|---|" + "---|" * 7)
for tag, v in m["variants"].items():
    A(f"| {tag} | {cells(v['cells'])} | {verdict(v['cells'])} |")
A("")
intro = open(os.path.join(H, "intro.md")).read(); outro = open(os.path.join(H, "outro.md")).read()
open(os.path.join(H, "README.md"), "w").write(intro + "\n" + T + "\n\n" + "\n".join(P) + "\n" + outro)
print(len(intro + T + "\n".join(P) + outro), "chars")
