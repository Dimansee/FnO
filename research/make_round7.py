"""Adds research rounds 6-7 (stress tests, rallies/scalping, stop-loss study) to fno/research_summary.json."""
import json
import pandas as pd

S = json.load(open("../fno/research_summary.json"))
c6 = json.load(open("round6_checks.json"))
rules = json.load(open("round6_rules.json"))
delay = json.load(open("round6_delay.json"))
limit = json.load(open("round6_limit.json"))
rally = json.load(open("r7_rally.json"))
stops = json.load(open("r7_stops.json"))
prem = json.load(open("r7_stops_premium.json"))
sc = pd.read_pickle("r7_scalp.pkl")
sc_low = pd.read_pickle("r7_scalp_lowslip.pkl")

k = lambda v: ("−" if v < 0 else "") + f"₹{abs(v) / 1000:.1f}"  # noqa: E731
VD = {"matched": 840, "change": 4386}          # verify_delay.py: same trades, entry 1 minute later
NZ = [v["net_per_2L"] for v in json.load(open("round6_noise.json")).values()]
base, new = c6["base"], rules["rerun"]
r7 = {
    "rules": {
        "before": {x: base[x] for x in ("trades", "net", "win", "pf", "max_dd", "unseen120", "years")},
        "after": {x: new[x] for x in ("trades", "net", "win", "pf", "max_dd", "unseen120", "years")},
        "windows": rules["windows"],
        "noise_range": [min(NZ), max(NZ)],
        "changes": ["Nifty: when the nearest weekly expiry has 1 day or less left, buy the next week's",
                    "No new signals on Union Budget day"],
    },
    "checks": [
        {"check": "Expiry days and days to expiry",
         "found": f"Bank Nifty on its expiry day: {c6['by_expiry_day']['BANKNIFTY expiry day']['trades']} trades, profit factor {c6['by_expiry_day']['BANKNIFTY expiry day']['pf']} "
                  f"(lost in 3 of 4 years). Nifty on its expiry day: PF {c6['by_expiry_day']['NIFTY expiry day']['pf']}. Options with 1 day left: PF {c6['by_dte']['1']['pf']}; 3-7 days left: PF {c6['by_dte']['5-7']['pf']}.",
         "action": "Rule added: roll Nifty to next week with 1 day left. Skipping Bank Nifty's expiry day was tested and not adopted: the losses came from its weekly-expiry era (to Nov 2024) and an independent check found expiry days only slightly more range-bound (not significant)."},
        {"check": "Entering late",
         "found": f"On the same {VD['matched']} trades, buying 1 minute after the signal (real 1-minute prices) changed profit by {k(VD['change'])}k and the premium paid by about 0.1%. "
                  f"Small changes reshuffle which marginal trades get sized in or out: starting capital ±3% moved the 3¾-year total between {k(min(NZ))}k and {k(max(NZ))}k.",
         "action": "Entering within 1-2 minutes is fine. Treat differences smaller than about ±₹20k between rule variants as noise."},
        {"check": "Luck test (5,000 reshuffled years)",
         "found": f"At 1% risk: a typical year made {k(c6['risk']['1%']['median_year'])}k on ₹2 lakh; {c6['risk']['1%']['p_losing_year']}% of years lost money; "
                  f"the usual deepest fall was {c6['risk']['1%']['dd_median_pct']}% of capital, 1 year in 20 saw {c6['risk']['1%']['dd_95_pct']}%. "
                  f"At 2% risk the 1-in-20 fall was {c6['risk']['2%']['dd_95_pct']}% and profit was lower.",
         "action": "Keep 1% risk per trade. Expect a 15-35% fall from a peak at some point in a year."},
        {"check": "Event days",
         "found": f"Budget days: {c6['events']['Budget']['trades']} trades, all lost ({k(c6['events']['Budget']['net'])}k). Day after a US Fed decision: PF {c6['events']['Day after US Fed']['pf']} "
                  f"({c6['events']['Day after US Fed']['trades']} trades). RBI policy days: PF {c6['events']['RBI policy']['pf']} ({c6['events']['RBI policy']['trades']} trades).",
         "action": "Skip Budget day. Fed and RBI samples are too small to act on."},
        {"check": "Where the profit comes from",
         "found": f"Trend days (close far from the open, {c6['daytype_share'].get('trend', 0)}% of days): {k(c6['by_daytype']['trend day (close far from open)']['net'])}k, PF {c6['by_daytype']['trend day (close far from open)']['pf']}. "
                  f"Range days: {k(c6['by_daytype']['range day']['net'])}k. Mixed days: {k(c6['by_daytype']['mixed day']['net'])}k. VIX 20+ at the open: PF {c6['by_vix']['20+']['pf']} ({c6['by_vix']['20+']['trades']} trades).",
         "action": "These are trend-catching rules: most days lose a little, trend days pay for them."},
        {"check": "Real contract history",
         "found": f"With the expiries actually listed (Bank Nifty weeklies until Nov 2024), the lot sizes and the STT of each period: {k(c6['specs_real']['net'])}k vs {k(c6['specs_today']['net'])}k with today's; "
                  f"unseen 120 days {k(c6['specs_real']['unseen120'])}k vs {k(c6['specs_today']['unseen120'])}k.",
         "action": "Results hold."},
    ],
}
fam_n = sc.groupby("family").size().to_dict()
r7["rally"] = {
    sym: {**{x: rally[sym]["describe"][x] for x in ("legs_per_day_80plus", "pct_days_with_80plus", "duration_min_median", "duration_min_p25_p75", "start_time_share")},
          "size_per_100_days": rally[sym]["describe"]["size_buckets_per_100_days"], "after": rally[sym]["after"],
          "realtime_goal": rally[sym]["realtime_goal"],
          "lift": [x for x in rally[sym]["lift"] if x["condition"] in ("after_opposite_40", "at_day_extreme", "vix=16+", "gap=gap down", "quiet_30m", "vix=<13", "tod=11:00-13:00", "near_camarilla", "near_prev_hl")],
          "realtime": [x for x in rally[sym]["realtime"] if x["condition"] in ("all", "tod=09:15-09:45", "tod=14:30-15:30", "vix=16+", "vix=<13", "with_trend_vs_open=True", "vs_twap=against", "quiet_30m=True")]}
    for sym in ("NIFTY", "BANKNIFTY")}
r7["scalp"] = {"tested": int(len(sc)), "by_family": fam_n,
               "profitable_after_costs": int((sc.train > 0).sum()), "profitable_low_slippage": int((sc_low.train > 0).sum()),
               "positive_in_points_pct": round(float((sc.train_pts > 0).mean() * 100)),
               "best_points_per_trade": round(float((sc.train_pts / sc.n).max()), 2),
               "best": sc.sort_values("train", ascending=False).head(1)[["family", "X", "T", "S", "H", "train", "unseen", "n"]].to_dict("records")[0]}
r7["stops"] = {
    "A": stops["A"], "B": [{"name": n, **v} for n, v in stops["B"].items()],
    "C": {kk: stops["C"][kk] for kk in ("S10_R1", "S10_R2", "S20_R2", "S30_R2", "S40_R2", "S60_R2")},
    "D": {"days_5min": prem["real_5min"]["days"], "days_1min": prem["real_1min"]["days"],
          "rows": {kk: prem["real_1min"]["table"][kk] for kk in ("SL-20% TP+20%", "SL-20% TP+40%", "SL-30% TP+40%", "SL-40% TP+40%")},
          "rows_5min": {kk: prem["real_5min"]["table"][kk] for kk in ("SL-20% TP+20%", "SL-20% TP+40%", "SL-30% TP+40%", "SL-40% TP+40%")}},
}
S["round7"] = r7
json.dump(S, open("../fno/research_summary.json", "w"), default=str, indent=1)
print(json.dumps(r7, default=str)[:3000])
