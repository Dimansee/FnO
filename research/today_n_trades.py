"""How much could N trades have made TODAY? (a) the rules allowed up to N trades and re-entries on every
5-minute close back above the band/VWAP; (b) perfect hindsight: the best N non-overlapping option trades on
today's 5-minute path (1 ITM, real-corrected premiums, slippage and full charges) - an upper bound no rule reaches."""
import io, sys, contextlib, json
from datetime import date
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    sys.argv = ["x", "2026-10-13", "NIFTY"]
    import day_review as DR
import engine as E, numpy as np
from fno import indicators as I
d, meta, SYM = DR.dobj, E.META["NIFTY"], "NIFTY"
lot = meta["lot"]
print(f"charges model per 1-lot round trip at ~₹220 premium: ₹{I.charges(220, 240, lot):.0f} (brokerage ₹40 + STT + exchange + GST)")

# (a) rules with more trades + 5-minute re-entry checks instead of half-hour
def rules(max_trades, every5=False):
    W = dict(mult=1.75, stop_atr=2.0, rr=4.0, vwap=1, max_trades=max_trades, max_losses=99, vix_min=11, last_entry=870, otm=-1, be=0, ctx=-9, rmin=1.0, rmax=2.5)
    CA = {'rr': None, 'be': 1.0, 'tstop': 45, 'ctx': -9, 'otm': -1, 'last_entry': 780, 'max_trades': max_trades, 'max_losses': 99, 'rmin': 1.0, 'rmax': 3.0, 'vix_min': 11, 'vix_max': 22, 'mode': 'break', 'trail': ''}
    if every5:
        orig = E.s_noise
        def s5(dd, j, p, st):            # same rule, checked at every 5-min close
            up, lo = st.setdefault(("nb", dd.d, p["mult"]), E.noise_bands(dd, p["mult"]))
            for i in range(max(j, 5), len(dd.t)):
                if dd.t[i] + 5 > p.get("last_entry", 870): return None
                c = dd.c[i]
                for side, band in ((1, up[i]), (-1, lo[i])):
                    if np.isnan(band) or side * (c - band) <= 0 or side * (c - dd.vwap[i]) <= 0: continue
                    stop = E._risk_clip(dd, i, c, c - side * 2.0 * dd.atr[i], side, {"rmin": 0.5, "rmax": 3})
                    return E.Sig(i, side, stop, c + side * 4 * abs(c - stop), "noise", 0, 0, "noise")
            return None
        E.STRATS["noise"] = s5
    tr = [dict(t, strat="noise") for t in E.run([d], SYM, "noise", W, capital=400_000)] + [dict(t, strat="cam") for t in E.run([d], SYM, "camarilla", CA, capital=400_000)]
    if every5: E.STRATS["noise"] = orig
    tr.sort(key=lambda t: t["in"]); out, busy = [], -1
    for t in tr:
        if t["in"] < busy: continue
        out.append(t); busy = t["out"]
    return out
print("\n(a) the rules with a higher trade cap (capital ₹4L so the Camarilla trade sizes to 1 lot at 1% risk)")
for cap, e5 in ((1, False), (3, False), (10, False), (10, True)):
    tr = rules(cap, e5)
    net = sum(t["pnl"] for t in tr); ch = sum(I.charges(t["prem_in"], t["prem_out"], t["lots"] * lot) for t in tr)
    print(f"  cap {cap:2d}{' + 5-min re-entry' if e5 else '                 '}: {len(tr)} trade(s)  net ₹{net:+,.0f}  charges ₹{ch:,.0f}  " +
          "; ".join(f"{t['strat']} {t['in']//60:02d}:{t['in']%60:02d}->{t['out']//60:02d}:{t['out']%60:02d} {t['why']} ₹{t['pnl']:+,.0f}" for t in tr))

# (b) perfect hindsight: best N non-overlapping trades (enter/exit at 5-min closes, long CE or long PE, 1 lot, 1 ITM)
n = len(d.t)
def leg(i, j, side):
    s = E.Sig(i, side, d.c[i] - side * 2 * d.atr[i], None, "", 0, 0, "x")
    p = E.trade_pnl(d, s, j, d.c[j], meta, {"otm": -1, "one_lot": True})
    return p
best = {}
first = next(k for k, m in enumerate(d.t) if m + 5 >= 575)         # entries from 09:35
last = next(k for k, m in enumerate(d.t) if m + 5 >= 915)          # exits by 15:15
P = {}
for i in range(first, last):
    for j in range(i + 1, last + 1):
        for side in (1, -1):
            p = leg(i, j, side)
            if p: P[(i, j, side)] = (p["pnl"], I.charges(p["prem_in"], p["prem_out"], lot))
# DP over non-overlapping intervals, exactly k trades
import functools
@functools.lru_cache(None)
def dp(start, k):
    if k == 0: return (0.0, ())
    bestv, bestp = -1e18, ()
    for (i, j, side), (pnl, ch) in P.items():
        if i < start: continue
        v, path = dp(j, k - 1)
        if v > -1e17 and pnl + v > bestv:
            bestv, bestp = pnl + v, ((i, j, side, pnl, ch),) + path
    return (bestv, bestp)
print("\n(b) perfect hindsight, best N trades on the 5-minute closes (1 lot, 1 ITM, slippage + charges included in P&L):")
for k in (1, 2, 3, 5, 10):
    v, path = dp(first, k)
    if v < -1e17: print(f"  {k} trades: not possible"); continue
    ch = sum(x[4] for x in path)
    print(f"  {k:2d} trades: net ₹{v:+,.0f}  (charges paid ₹{ch:,.0f})  " + "; ".join(f"{'CE' if s>0 else 'PE'} {d.t[i]+5:04d}->{d.t[j]+5:04d} ₹{p:+,.0f}".replace("->", "→") for i, j, s, p, c in path))
json.dump({"rules": {}, "note": "see printout"}, open("today_n_trades.json", "w"))
