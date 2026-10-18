"""Scalping spec (Oct 2026 research doc) tested exactly as written, on 1-minute Nifty / Bank Nifty 2023-01 -> 2026-10.

Spec under test:
  Day-type classifier (6 points, re-scored every 15 min from 09:30): narrow CPR (bottom 30% of last 20 days), price beyond
  PDH/PDL, price outside the 09:15-09:30 opening range, 80%+ of 5-min closes on one side of VWAP, ADX(14, 5-min) > 25 and
  rising, EMA 9 > 21 > 50 (5-min). 4+ = Trend mode, 3 = in-between (setup A only), 0-2 = no trades.
  Setups on 1-minute closes:  A opening-range breakout 09:30-11:00 with ADX rising;  B pullback to the 1-min EMA 9 in Trend
  mode (EMA 9 > 21, close back above EMA 9 and VWAP);  C break-and-retest of a level (PDH/PDL/PDC, CPR, R1/S1, R2/S2,
  round 100s, OR high/low).
  Exits: stop below trigger/pullback low capped at 1 x ATR(14, 1-min); at +1R stop to entry + costs, then exit on a 1-min
  close through EMA 9; time stop 5 bars without +0.5R; exit on a close back through VWAP; outside Trend mode, target = next
  level; 15:15 square-off; no entries after 14:45 or before 09:30.
  Confidence 0-100 (day type 25, setup 20, room to next level 20, volume 15 -> no index volume history, scored 7.5 flat,
  OI 10 -> no intraday OI history, scored 5 flat, time of day 10), trade at 65+.
  Governor (capital Rs 20,000): -2% day stop, give-back lock (peak >= 1.5% then 40% given back), charges > 30% of gross
  profit after 5 trades, 3 losses in a row -> 30-min pause, 5 losses -> stop, a setup that loses twice is off for the day,
  skip a trade whose room to the next level is under 3 x the round-trip cost.
Each trade: 1 lot of the 1-strike-ITM option of the app's expiry, Black-Scholes with the real-price correction (round 4),
0.5% slippage per fill (and a 0.1% variant), full charges at the current 0.15% STT. Training = before the last 120 trading
days; unseen = the last 120.
Output: scalp_spec.json
"""
import json, math, os, sys
from collections import defaultdict

import numpy as np

os.environ.setdefault("FNO_CALIB", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import engine as E
import m1
from fno import indicators as I

E.use_calibration(True)
CAPITAL = 20_000
SYMS = ("NIFTY", "BANKNIFTY")


def ema(a, n):
    out = np.empty_like(a)
    k = 2 / (n + 1)
    out[0] = a[0]
    for i in range(1, len(a)):
        out[i] = out[i - 1] + k * (a[i] - out[i - 1])
    return out


def atr1(h, l, c, n=14):
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.r_[c[0], c[:-1]]), np.abs(l - np.r_[c[0], c[:-1]])))
    out = np.empty_like(tr)
    out[0] = tr[0]
    for i in range(1, len(tr)):
        out[i] = out[i - 1] + (tr[i] - out[i - 1]) / n
    return out


def prep(sym):
    days5 = E.load_days(sym)
    by5 = {d.d: d for d in days5}
    cprs = {d.d: d.cpr_w for d in days5}
    order = [d.d for d in days5]
    days = m1.load(sym, by5)
    rnd = 100 if sym == "NIFTY" else 500
    out = []
    for dd in days:
        d5 = by5[dd.d]
        k = order.index(dd.d)
        past = [cprs[x] for x in order[max(0, k - 20):k]]
        narrow = len(past) >= 10 and d5.cpr_w <= np.percentile(past, 30)
        e9, e21 = ema(dd.c, 9), ema(dd.c, 21)
        a1 = atr1(dd.h, dd.l, dd.c)
        t5 = np.array(d5.t)
        # last completed 5-min bar for each 1-min bar (1-min bar at m closes at m+1; 5-min bar at t closes at t+5)
        j5 = np.searchsorted(t5 + 5, dd.m + 1, side="right") - 1
        orm = dd.m < 570
        orh, orl = dd.h[orm].max(), dd.l[orm].min()
        ph, pl, pc = dd.ph, dd.pl, dd.pc
        pv = (ph + pl + pc) / 3
        bc = (ph + pl) / 2
        tc = 2 * pv - bc
        lv = [ph, pl, pc, pv, tc, bc, 2 * pv - pl, 2 * pv - ph, pv + (ph - pl), pv - (ph - pl), orh, orl]
        base = math.floor(dd.c.min() / rnd) * rnd
        lv += list(np.arange(base - rnd, dd.c.max() + 2 * rnd, rnd))
        out.append(dict(dd=dd, d5=d5, narrow=narrow, e9=e9, e21=e21, a1=a1, j5=j5, orh=orh, orl=orl,
                        levels=np.array(sorted(set(round(x, 2) for x in lv)))))
    return out


def classify(x, i):
    """Score (0-6) and direction at 1-min bar i."""
    dd, d5, j = x["dd"], x["d5"], x["j5"][i]
    c = dd.c[i]
    side = 1 if c >= dd.twap[i] else -1
    s = 1 if x["narrow"] else 0
    s += 1 if (side > 0 and c > dd.ph) or (side < 0 and c < dd.pl) else 0
    s += 1 if (side > 0 and c > x["orh"]) or (side < 0 and c < x["orl"]) else 0
    if j >= 1:
        cl = np.array(d5.c[:j + 1])
        vw = np.array(d5.vwap[:j + 1])
        share = np.mean(np.sign(cl - vw) == side)
        s += 1 if share >= 0.8 else 0
        adx = d5.adx
        s += 1 if adx[j] > 25 and j >= 3 and adx[j] > adx[j - 3] else 0
        e50 = d5.x["e50"][j]
        s += 1 if (side > 0 and d5.e9[j] > d5.e21[j] > e50) or (side < 0 and d5.e9[j] < d5.e21[j] < e50) else 0
    return s, side


def next_level(x, px, side):
    lv = x["levels"]
    if side > 0:
        a = lv[lv > px + 1e-6]
        return a[0] if len(a) else None
    a = lv[lv < px - 1e-6]
    return a[-1] if len(a) else None


def cost_points(dd, sym):
    """Round-trip fees of 1 lot in index points (premium ~ delta 0.6)."""
    lot = E.META[sym]["lot"]
    prem = 0.012 * dd.c[0] if sym == "NIFTY" else 0.012 * dd.c[0]
    return I.charges(prem, prem, lot) / lot / 0.6


def run(data, sym, cfg):
    trades = []
    for x in data:
        dd = x["dd"]
        n = len(dd.c)
        cp = cost_points(dd, sym)
        g = dict(net=0.0, peak=0.0, gross_pos=0.0, chg=0.0, n=0, losses=0, streak=0, pause_until=-1, stopped=False,
                 setup_losses=defaultdict(int), orb_done={1: False, -1: False}, last_exit=-10, last_side=0)
        score, sdir, next_cls = 0, 0, 570
        i = 1
        while i < n - 1:
            m = dd.m[i]
            if m + 1 >= next_cls:
                score, sdir = classify(x, i)
                next_cls += 15
            if cfg.get("no_classifier"):
                score, sdir = 6, (1 if dd.c[i] >= dd.twap[i] else -1)
            mode = "trend" if score >= 4 else "mid" if score == 3 else "none"
            if m + 1 < 570 or m + 1 > 885 or mode == "none" or (cfg["governor"] and g["stopped"]) or i <= g["pause_until"]:
                i += 1
                continue
            c, e9, e21 = dd.c[i], x["e9"], x["e21"]
            a1 = x["a1"][i] if not cfg.get("atr5") else x["d5"].atr[x["j5"][i]]
            sig = None
            # A - opening-range breakout
            if "A" in cfg["setups"] and m + 1 <= 660:
                j = x["j5"][i]
                adx_up = j >= 1 and x["d5"].adx[j] > x["d5"].adx[j - 1]
                for sd in (1, -1):
                    lvl = x["orh"] if sd > 0 else x["orl"]
                    if not g["orb_done"][sd] and sd == sdir and adx_up and sd * (c - lvl) > 0 and sd * (dd.c[i - 1] - lvl) <= 0:
                        g["orb_done"][sd] = True
                        stopx = dd.l[i] if sd > 0 else dd.h[i]
                        sig = ("A", sd, stopx)
            # B - pullback to EMA 9 (Trend mode)
            if sig is None and "B" in cfg["setups"] and mode == "trend":
                sd = sdir
                touched = any(sd * (e9[k] - (dd.l[k] if sd > 0 else dd.h[k])) >= 0 for k in range(max(0, i - 2), i + 1))
                if sd * (e9[i] - e21[i]) > 0 and touched and sd * (c - e9[i]) > 0 and sd * (c - dd.twap[i]) > 0 \
                        and sd * (c - dd.o[i]) > 0:
                    lo = min(dd.l[max(0, i - 4):i + 1]) if sd > 0 else max(dd.h[max(0, i - 4):i + 1])
                    sig = ("B", sd, lo)
            # C - level break and retest (Trend mode)
            if sig is None and "C" in cfg["setups"] and mode == "trend" and i >= 11:
                sd = sdir
                for L in x["levels"]:
                    if abs(c - L) > 3 * a1:
                        continue
                    broke = any(sd * (dd.c[k] - L) > 0 and sd * (dd.c[k - 1] - L) <= 0 for k in range(i - 10, i))
                    probe = (dd.l[i] if sd > 0 else dd.h[i])
                    if broke and sd * (probe - L) <= 0.1 * a1 and sd * (c - L) > 0 and sd * (c - dd.o[i]) > 0:
                        sig = ("C", sd, probe)
                        break
            if sig is None:
                i += 1
                continue
            tag, sd, sx = sig
            if sd == g["last_side"] and i - g["last_exit"] < 3:
                i += 1
                continue
            if cfg.get("ride") and g["n"] >= cfg.get("max_trades", 2):
                i += 1
                continue
            risk = min(abs(c - sx), a1)
            risk = max(risk, 0.3 * a1, 2.0 * dd.scale)
            if cfg.get("ride"):
                risk = cfg.get("ride_atr", 2.0) * x["d5"].atr[x["j5"][i]]
            stop = c - sd * risk
            nl = next_level(x, c, sd)
            room = abs(nl - c) if nl is not None else 10 * risk
            conf = (25 if mode == "trend" else 12) + 20 + min(20, 20 * room / (2 * risk)) + 7.5 + 5 \
                + (10 if (570 <= m + 1 <= 690 or 810 <= m + 1 <= 885) else 0)
            if conf < cfg["conf"]:
                i += 1
                continue
            if cfg["governor"]:
                if g["setup_losses"][tag] >= 2 or room < 3 * cp:
                    i += 1
                    continue
            # ---- manage the trade on the following 1-min bars
            hi_fav, trail_on, k = c, False, i + 1
            exit_px, why = None, ""
            while k < n:
                hk, lk, ck = dd.h[k], dd.l[k], dd.c[k]
                adverse = lk if sd > 0 else hk
                if sd * (adverse - stop) <= 0:
                    exit_px, why = (stop if sd * (dd.o[k] - stop) > 0 else dd.o[k]), "stop"
                    break
                fav = hk if sd > 0 else lk
                if sd * (fav - hi_fav) > 0:
                    hi_fav = fav
                if mode != "trend" and not cfg.get("ride") and nl is not None and sd * (fav - nl) >= 0:
                    exit_px, why = nl, "level"
                    break
                if not trail_on and sd * (hi_fav - c) >= risk:
                    trail_on = True
                    stop = c + sd * cp if sd * (c + sd * cp - stop) > 0 else stop
                if cfg.get("ride"):
                    jk = x["j5"][k]
                    if jk != x["j5"][k - 1] and jk >= 0 and sd * (x["d5"].c[jk] - x["d5"].e21[jk]) < 0:
                        exit_px, why = ck, "trail"
                        break
                    if dd.m[k] + 1 >= 915:
                        exit_px, why = ck, "eod"
                        break
                    k += 1
                    continue
                if trail_on and sd * (ck - e9[k]) < 0:
                    exit_px, why = ck, "trail"
                    break
                if cfg["time_stop"] and k - i >= 5 and sd * (hi_fav - c) < 0.5 * risk:
                    exit_px, why = ck, "time"
                    break
                if sd * (ck - dd.twap[k]) < 0:
                    exit_px, why = ck, "vwap"
                    break
                if dd.m[k] + 1 >= 915:
                    exit_px, why = ck, "eod"
                    break
                k += 1
            if exit_px is None:
                k, exit_px, why = n - 1, dd.c[-1], "eod"
            p_in, p_out = m1.option_trade(dd, sd, i, k, c, exit_px)
            slip = cfg["slip"]
            buy, sell = p_in * (1 + slip), p_out * (1 - slip)
            lot = E.META[sym]["lot"]
            chg = I.charges(buy, sell, lot)
            gross = (sell - buy) * lot
            net = gross - chg
            trades.append(dict(d=str(dd.d), tag=tag, side=sd, mode=mode, i=int(i), k=int(k), pts=float(sd * (exit_px - c)),
                               risk=float(risk), why=why, gross=float(gross), chg=float(chg), net=float(net), conf=float(conf),
                               prem=float(buy)))
            g["net"] += net
            g["chg"] += chg
            g["gross_pos"] += gross
            g["n"] += 1
            g["peak"] = max(g["peak"], g["net"])
            g["last_exit"], g["last_side"] = k, sd
            if net < 0:
                g["losses"] += 1
                g["streak"] += 1
                g["setup_losses"][tag] += 1
            else:
                g["streak"] = 0
            if cfg["governor"]:
                if g["net"] <= -0.02 * CAPITAL or g["losses"] >= 5:
                    g["stopped"] = True
                if g["peak"] >= 0.015 * CAPITAL and g["net"] <= 0.6 * g["peak"]:
                    g["stopped"] = True
                if g["n"] >= 5 and (g["gross_pos"] <= 0 or g["chg"] > 0.3 * g["gross_pos"]):
                    g["stopped"] = True
                if g["streak"] >= 3:
                    g["pause_until"] = k + 30
                    g["streak"] = 0
            i = k + 1
    return trades


def summary(tr, days):
    ds = set(days)
    t = [x for x in tr if x["d"] in ds]
    if not t:
        return dict(trades=0)
    net = np.array([x["net"] for x in t])
    gross = np.array([x["gross"] for x in t])
    chg = np.array([x["chg"] for x in t])
    byday = defaultdict(float)
    for x in t:
        byday[x["d"]] += x["net"]
    eq = np.cumsum([byday.get(d, 0.0) for d in days])
    dd = float(np.max(np.maximum.accumulate(np.r_[0, eq]) - np.r_[0, eq]))
    pos, neg = net[net > 0].sum(), -net[net < 0].sum()
    return dict(trades=len(t), per_day=round(len(t) / len(days), 2), win_pct=round(100 * np.mean(net > 0), 1),
                net=round(float(net.sum())), gross=round(float(gross.sum())), charges=round(float(chg.sum())),
                pf=round(float(pos / neg), 2) if neg > 0 else None, avg_net=round(float(net.mean()), 1),
                avg_pts=round(float(np.mean([x["pts"] for x in t])), 2), max_dd=round(dd),
                green_days=round(100 * np.mean([v > 0 for v in byday.values()]), 1),
                by_setup={s: round(float(sum(x["net"] for x in t if x["tag"] == s))) for s in "ABC"},
                exits={w: sum(1 for x in t if x["why"] == w) for w in ("stop", "trail", "time", "vwap", "level", "eod")})


def day_types(data):
    """Trend days (|close/open - 1| >= 0.8%) and quiet days (range < 0.6% of open)."""
    td, qd = {}, set()
    for x in data:
        dd = x["dd"]
        mv = dd.c[-1] / dd.o[0] - 1
        if abs(mv) >= 0.008:
            td[str(dd.d)] = 1 if mv > 0 else -1
        if (dd.h.max() - dd.l.min()) / dd.o[0] < 0.006:
            qd.add(str(dd.d))
    return td, qd


CFGS = {
    "spec_full": dict(setups="ABC", governor=True, time_stop=True, conf=65, slip=0.005),
    "spec_full_slip0.1": dict(setups="ABC", governor=True, time_stop=True, conf=65, slip=0.001),
    "A_only": dict(setups="A", governor=True, time_stop=True, conf=65, slip=0.005),
    "B_only": dict(setups="B", governor=True, time_stop=True, conf=65, slip=0.005),
    "C_only": dict(setups="C", governor=True, time_stop=True, conf=65, slip=0.005),
    "no_governor": dict(setups="ABC", governor=False, time_stop=True, conf=65, slip=0.005),
    "no_classifier": dict(setups="ABC", governor=True, time_stop=True, conf=65, slip=0.005, no_classifier=True),
    "no_time_stop": dict(setups="ABC", governor=True, time_stop=False, conf=65, slip=0.005),
    "wide_stop_atr5": dict(setups="ABC", governor=True, time_stop=True, conf=65, slip=0.005, atr5=True),
    "conf80": dict(setups="ABC", governor=True, time_stop=True, conf=80, slip=0.005),
    # not scalping: same classifier + entries, but ride the trend (2 x ATR 5-min stop, trail on the 5-min EMA 21)
    "ride_AB": dict(setups="AB", governor=True, time_stop=False, conf=65, slip=0.005, ride=True, max_trades=2),
    "ride_A": dict(setups="A", governor=True, time_stop=False, conf=65, slip=0.005, ride=True, max_trades=1),
    "ride_B_1": dict(setups="B", governor=True, time_stop=False, conf=65, slip=0.005, ride=True, max_trades=1),
}

if __name__ == "__main__":
    only = os.environ.get("ONLY")
    res = {}
    for sym in SYMS:
        data = prep(sym)
        days = [str(x["dd"].d) for x in data]
        split = days[-120]
        tr_days, un_days = [d for d in days if d < split], [d for d in days if d >= split]
        td, qd = day_types(data)
        res[sym] = {"days": len(days), "train": [tr_days[0], tr_days[-1]], "unseen": [un_days[0], un_days[-1]],
                    "trend_days": len(td), "quiet_days": len(qd), "runs": {}}
        for name, cfg in CFGS.items():
            if only and name not in only.split(","):
                continue
            t = run(data, sym, cfg)
            tdays = defaultdict(list)
            for x in t:
                tdays[x["d"]].append(x)
            caught = [d for d, s in td.items() if any(x["side"] == s for x in tdays.get(d, []))]
            res[sym]["runs"][name] = {
                "train": summary(t, tr_days), "unseen": summary(t, un_days),
                "trend_days_traded_right_way_pct": round(100 * len(caught) / max(1, len(td)), 1),
                "trend_days_net": round(sum(x["net"] for d in td for x in tdays.get(d, []))),
                "quiet_days_with_trades_pct": round(100 * sum(1 for d in qd if tdays.get(d)) / max(1, len(qd)), 1),
                "by_year": {y: round(sum(x["net"] for x in t if x["d"].startswith(y))) for y in ("2023", "2024", "2025", "2026")},
            }
            r = res[sym]["runs"][name]
            print(sym, name, "train", r["train"].get("trades"), r["train"].get("net"), r["train"].get("pf"),
                  "| unseen", r["unseen"].get("trades"), r["unseen"].get("net"), r["unseen"].get("pf"),
                  "| years", r["by_year"], flush=True)
        # the app's current rules (Noise + Camarilla, same parameters as combo_check.py) on the same trend days
        if not only or "rules" in only:
            from datetime import timedelta
            W = dict(mult=1.75, stop_atr=2.0, rr=4.0, vwap=1, max_trades=1, vix_min=11, vix_max=18, last_entry=870, otm=-1, be=0,
                     ctx=-9, rmin=1.0, rmax=2.5)
            CA = {'rr': None, 'be': 1.0, 'tstop': 45, 'ctx': -9, 'otm': -1, 'last_entry': 780, 'max_trades': 1, 'rmin': 1.0,
                  'rmax': 3.0, 'vix_min': 11, 'vix_max': 20, 'mode': 'break', 'trail': ''}
            d5s = [x["d5"] for x in data]
            for d in d5s:
                if sym == "NIFTY" and (d.expiry - d.d).days <= 1:
                    d.expiry = E.expiry_for(d.d + timedelta(days=1), "weekly")
            rt = [dict(t, strat="noise") for t in E.run(d5s, sym, "noise", W)] + \
                 [dict(t, strat="camarilla") for t in E.run(d5s, sym, "camarilla", CA)]
            rdays = defaultdict(list)
            for t in rt:
                rdays[str(t["date"])].append(t)
            caught = [d for d, s in td.items() if any(t.get("side", 0) == s for t in rdays.get(d, []))]
            traded = [d for d in td if rdays.get(d)]
            res[sym]["app_rules_on_trend_days"] = {
                "trend_days": len(td), "traded_pct": round(100 * len(traded) / max(1, len(td)), 1),
                "traded_right_way_pct": round(100 * len(caught) / max(1, len(td)), 1),
                "trend_day_pnl_200k_1pct": round(sum(t["pnl"] for d in td for t in rdays.get(d, []))),
                "all_days_pnl_200k_1pct": round(sum(t["pnl"] for t in rt)),
                "trades": len(rt), "sample_keys": sorted(rt[0].keys()) if rt else []}
            print(sym, "app rules on trend days", res[sym]["app_rules_on_trend_days"], flush=True)
    json.dump(res, open("scalp_spec.json", "w"), indent=1, default=str)
