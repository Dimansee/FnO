"""Post-mortem of one recorded session: what every indicator said, what the rules did, what the
alternatives would have done, and how similar days behaved historically.
usage: FNO_CALIB=1 python day_review.py 2026-10-13 [NIFTY]     -> prints the review, writes day_review_<date>.json
"""
import gzip, json, math, os, sys
from datetime import date, datetime, timedelta
import numpy as np
import pandas as pd

os.environ.setdefault("FNO_CALIB", "1")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
HERE = os.path.dirname(os.path.abspath(__file__))
import engine as E
from fno import ai as AI, config as C, indicators as I

D = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()
SYM = sys.argv[2] if len(sys.argv) > 2 else "NIFTY"
LIVE = os.path.join(HERE, "data", "live")
STEP = {"NIFTY": 50, "BANKNIFTY": 100}[SYM]
OUT = {"date": D.isoformat(), "symbol": SYM}


def live_day(d):
    p = os.path.join(LIVE, d.isoformat(), "candles_1m.csv.gz")
    if not os.path.exists(p):
        return None
    c = pd.read_csv(p)
    return c


def bars5(d, kind="IDX", sym=None):
    c = live_day(d)
    if c is None:
        return None
    x = c[(c["sym"] == (sym or SYM)) & (c["kind"] == kind)].copy()
    x["ts"] = pd.to_datetime(x["ts"]).dt.tz_localize(C.IST)
    x = x.set_index("ts").sort_index()
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    if "oi" in x:
        agg["oi"] = "last"
    return x.resample("5min", label="left", closed="left").agg(agg).dropna(subset=["close"])


# ---- continuous 5-min series: history to the last fetched day + recorded live days after it
hist = pd.read_csv(os.path.join(HERE, "data", f"{SYM}_5m_upstox.csv.gz"))
hist["ts"] = pd.to_datetime(hist["ts"]).dt.tz_localize(C.IST)
hist = hist.set_index("ts").sort_index()
last_hist = hist.index[-1].date()
extra = []
dd = last_hist + timedelta(days=1)
while dd <= D:
    b = bars5(dd)
    if b is not None and len(b):
        extra.append(b[["open", "high", "low", "close", "volume"]])
    dd += timedelta(days=1)
full = pd.concat([hist] + extra).sort_index()
full = full[~full.index.duplicated()]
vixd = bars5(D, sym="INDIAVIX")
vix_level = float(vixd["close"].iloc[0]) if vixd is not None and len(vixd) else 14.0
df, d15 = AI.enrich(full, vix_level)
day = df[df.index.date == D].copy()
prev = df[df.index.date < D]
prev_day = prev[prev.index.date == prev.index.date[-1]]
pdh, pdl, pc = float(prev_day["high"].max()), float(prev_day["low"].min()), float(prev_day["close"].iloc[-1])
o = float(day["open"].iloc[0])
sess = AI.session(df, d15, D, SYM, date.fromisoformat("2026-10-19") if SYM == "NIFTY" else date.fromisoformat("2026-10-27"), None, 0.0)
bars, scal, b15 = sess
sig = bars["sig"]
up = [max(o, pc) * (1 + 1.75 * s) if not np.isnan(s) else np.nan for s in sig]
lo = [min(o, pc) * (1 - 1.75 * s) if not np.isnan(s) else np.nan for s in sig]
pv = prev_day
rng = float(pv["high"].max() - pv["low"].min())
cam = {"h4": pc + rng * 1.1 / 2, "h3": pc + rng * 1.1 / 4, "l3": pc - rng * 1.1 / 4, "l4": pc - rng * 1.1 / 2}

print(f"=== {SYM} {D}: open {o:.1f} (gap {100*(o/pc-1):+.2f}%), high {day['high'].max():.1f}, low {day['low'].min():.1f}, close {day['close'].iloc[-1]:.1f} "
      f"({day['close'].iloc[-1]-pc:+.1f} pts, {100*(day['close'].iloc[-1]/pc-1):+.2f}%) | PDH {pdh:.1f} PDL {pdl:.1f} | VIX {vix_level:.1f}")
print(f"Camarilla H4 {cam['h4']:.1f} H3 {cam['h3']:.1f} L3 {cam['l3']:.1f} L4 {cam['l4']:.1f}")
OUT["day"] = {"open": o, "prev_close": pc, "high": float(day["high"].max()), "low": float(day["low"].min()), "close": float(day["close"].iloc[-1]),
              "pdh": pdh, "pdl": pdl, "vix": vix_level, "camarilla": cam}

# ---- the indicator table at every half hour + the key minutes
day["band_up"], day["band_lo"] = up[:len(day)], lo[:len(day)]
cols = ["close", "vwap", "e9", "e21", "e50", "rsi", "adx", "st", "band_up", "macdh", "bbu", "atr"]
rows = []
print("\nbar_end  close    vwap     e9     e21    e50   rsi  adx st  band_up  macdh   atr")
for ts, r in day.iterrows():
    end = (ts + timedelta(minutes=5)).strftime("%H:%M")
    if end[-2:] in ("15", "45") or end in ("09:20", "09:25", "09:30", "09:50", "10:40", "10:50", "15:15"):
        print(f"{end}  {r.close:8.1f} {r.vwap:8.1f} {r.e9:7.1f} {r.e21:7.1f} {r.e50:7.1f} {r.rsi:5.0f} {r.adx:4.0f} {int(r.st):+d} {r.band_up:8.1f} {r.macdh:6.1f} {r.atr:5.1f}")
    rows.append({"end": end, **{c: (None if pd.isna(r[c]) else float(r[c])) for c in cols}})
OUT["bars"] = rows

# ---- what the rules did (from the journal + the paper trades) and counterfactual exits
jr = json.load(open(os.path.join(LIVE, D.isoformat(), "journal.json")))
trades = [t for t in jr.get("paper_trades", []) if t["symbol"] == SYM]
print("\n--- paper trades")
for t in trades:
    print(f"{t['plan']['strategy']:9s} {t['opened'][11:19]} -> {t['closed'][11:19]}  {t['legs'][0]['opt']} {t['legs'][0]['strike']:g} @ {t['entry_prem']} -> {t['exit_prem']}  "
          f"P&L ₹{t['pnl']:+,.0f}  ({t['reason']})")
OUT["paper_trades"] = [{k: t[k] for k in ("opened", "closed", "entry_prem", "exit_prem", "pnl", "reason")} | {"strategy": t["plan"]["strategy"], "entry": t["plan"]["entry"], "sl": t["plan"]["sl"], "target": t["plan"]["target"]} for t in trades]

meta = E.META[SYM]
days = {d.d: d for d in E.load_days(SYM)}
# build an engine Day for today from the enriched frame (the engine's loader stops at the fetched history)
def day_obj():
    d = days.get(D)
    if d is not None:
        return d
    x = day
    t = [int(ts.hour * 60 + ts.minute) for ts in x.index]
    from engine import Day
    return Day(d=D, t=t, o=list(x.open), h=list(x.high), l=list(x.low), c=list(x.close), vwap=list(x.vwap), e5=list(x.e5), s44=list(x.s44),
               e20=list(x.e20), e9=list(x.e9), e21=list(x.e21), st=list(x.st.astype(int)), st15=list(x.st15.astype(int)), atr=list(x.atr), adx=list(x.adx),
               rsi=list(x.rsi), vix=[vix_level] * len(x), sig=list(sig[:len(x)]), prev_close=pc, prev_high=pdh, prev_low=pdl, cpr_w=0.3, pivot=(pdh + pdl + pc) / 3,
               ctx=0, gap=100 * (o / pc - 1), vix_chg=0.0, expiry=date.fromisoformat("2026-10-19") if SYM == "NIFTY" else date.fromisoformat("2026-10-27"),
               cam=cam)
dobj = day_obj()
print("\n--- counterfactual exits for the noise trade (engine, same entry bar, corrected premiums, 1 ITM)")
W = dict(mult=1.75, stop_atr=2.0, rr=4.0, vwap=1, max_trades=1, vix_min=11, last_entry=870, otm=-1, be=0, ctx=-9, rmin=1.0, rmax=2.5)
st = {}
s = E.s_noise(dobj, 0, W, st)
cf = {}
if s:
    upb, lob = st[("nb", D, 1.75)]
    variants = {"as traded (half-hour band/VWAP trail, 4R target)": dict(trail="noise"),
                "no trail: stop 2xATR / 4R target / 15:15": dict(trail=""),
                "no trail, no target: stop only, hold to 15:15": dict(trail="", target=None),
                "ATR x3 chandelier trail": dict(trail="atr", target=None),
                "breakeven at 1R + band trail": dict(trail="noise", be_r=1.0),
                "supertrend(10,3) exit": dict(trail="st", target=None),
                "VWAP close-below exit": dict(trail="vwap", target=None)}
    for name, kw in variants.items():
        s2 = E.Sig(s.i, s.side, s.stop, kw.get("target", s.target), kw.get("trail", ""), kw.get("be_r", 0), 0, "noise")
        k, px, why = E.simulate(dobj, s2, meta, {"trail_atr": 3.0}, upb, lob)
        tr = E.trade_pnl(dobj, s2, k, px, meta, {"otm": -1})
        end = dobj.t[k] + 5
        print(f"{name:52s} exit {end//60:02d}:{end%60:02d} @ {px:8.1f} ({why:11s}) 1 lot ₹{(tr['pnl']/tr['lots'] if tr else 0):+8,.0f}")
        cf[name] = {"exit": f"{end//60:02d}:{end%60:02d}", "spot": px, "why": why, "pnl_1lot": round(tr["pnl"] / tr["lots"]) if tr else None}
    OUT["noise_entry"] = {"bar_end": f"{(dobj.t[s.i]+5)//60:02d}:{(dobj.t[s.i]+5)%60:02d}", "entry": dobj.c[s.i], "stop": s.stop, "target": s.target}
OUT["counterfactual_exits"] = cf

# re-entry: after the trailing exit, next half-hour close above band & VWAP
print("\n--- re-entry after the trailing exit (max_trades=2)")
W2 = dict(W, max_trades=2)
for tr in E.run([dobj], SYM, "noise", W2):
    print(f"  in {tr['in']//60:02d}:{tr['in']%60:02d} out {tr['out']//60:02d}:{tr['out']%60:02d} spot {tr['spot_in']:.1f}->{tr['spot_out']:.1f} {tr['why']:11s} 1 lot ₹{tr['pnl']/tr['lots']:+,.0f}")
OUT["reentry"] = [{k: tr[k] for k in ("in", "out", "spot_in", "spot_out", "why")} | {"pnl_1lot": round(tr["pnl"] / tr["lots"])} for tr in E.run([dobj], SYM, "noise", W2)]
CA = {'rr': None, 'be': 1.0, 'tstop': 45, 'ctx': -9, 'otm': -1, 'last_entry': 780, 'max_trades': 1, 'rmin': 1.0, 'rmax': 3.0, 'vix_min': 11, 'vix_max': 22, 'mode': 'break', 'trail': ''}
print("--- camarilla on its own today")
cam_tr = E.run([dobj], SYM, "camarilla", CA)
if not cam_tr:
    s_c = E.s_camarilla(dobj, 0, CA, {})
    print("  (no camarilla trade from the engine; raw signal:", s_c, ")")
for tr in cam_tr:
    print(f"  in {tr['in']//60:02d}:{tr['in']%60:02d} out {tr['out']//60:02d}:{tr['out']%60:02d} spot {tr['spot_in']:.1f}->{tr['spot_out']:.1f} {tr['why']:11s} 1 lot ₹{tr['pnl']/tr['lots']:+,.0f}")

# ---- option flow through the day (snapshots)
snap = json.load(gzip.open(os.path.join(LIVE, D.isoformat(), "snapshots.json.gz")))
F = snap["fields"]
ix = {f: i for i, f in enumerate(F)}
flow = []
first = {}
exps = sorted({x["exp"] for x in snap["snapshots"] if x["sym"] == SYM})
print(f"\n--- option chain through the day (expiries {exps}; the app trades the one with 2+ days left)")
print("exp         time   spot     PCR   dCE_OI(k)  dPE_OI(k)  CE vol%  ATM straddle")
for sn in sorted([x for x in snap["snapshots"] if x["sym"] == SYM], key=lambda x: (x["exp"], x["t"])):
    rows_ = sn["rows"]
    ce_oi = sum((r[ix["ce_oi"]] or 0) for r in rows_); pe_oi = sum((r[ix["pe_oi"]] or 0) for r in rows_)
    ce_v = sum((r[ix["ce_vol"]] or 0) for r in rows_); pe_v = sum((r[ix["pe_vol"]] or 0) for r in rows_)
    if sn["exp"] not in first:
        first[sn["exp"]] = (ce_oi, pe_oi)
    f0 = first[sn["exp"]]
    spot = sn["spot"] or 0
    atm = min(rows_, key=lambda r: abs(r[0] - spot))
    strad = (atm[ix["ce_ltp"]] or 0) + (atm[ix["pe_ltp"]] or 0)
    rec = {"t": sn["t"], "spot": spot, "pcr": round(pe_oi / ce_oi, 2) if ce_oi else None, "exp": sn["exp"], "dce_oi": (ce_oi - f0[0]) / 1000, "dpe_oi": (pe_oi - f0[1]) / 1000,
           "ce_vol_share": round(100 * ce_v / (ce_v + pe_v), 0) if ce_v + pe_v else None, "straddle": strad}
    flow.append(rec)
    if sn["t"][-2:] in ("15", "45"):
        print(f"{sn['exp']}  {sn['t']} {spot:8.1f}  {rec['pcr']:5}  {rec['dce_oi']:+9.0f}  {rec['dpe_oi']:+9.0f}  {rec['ce_vol_share']:5}%   {strad:7.1f}")
OUT["flow"] = flow

# ---- futures volume / OI for the day
fut = bars5(D, "FUT")
if fut is not None and len(fut):
    fv = fut["volume"]; med = fv.median()
    bursts = fut[fv >= 2 * med]
    print(f"\n--- futures: volume median/5min {med:,.0f}; bursts >=2x at: {', '.join((t + timedelta(minutes=5)).strftime('%H:%M') for t in bursts.index)}; OI change {fut['oi'].iloc[-1]-fut['oi'].iloc[0]:+,.0f}")
    OUT["futures"] = {"vol_median": float(med), "bursts": [(t + timedelta(minutes=5)).strftime("%H:%M") for t in bursts.index], "oi_change": float(fut["oi"].iloc[-1] - fut["oi"].iloc[0])}

# ---- how similar days behaved in history: gap up 0.2-0.8% and close-open >= +1.0%
gap_today = 100 * (o / pc - 1)
print(f"\n--- history: trend days like today (gap within 0.3% of {gap_today:+.2f}%, close >= open + 1.0%) and what the noise rule did on them")
alld = E.load_days(SYM)
sim = [d for d in alld if abs(d.gap - gap_today) <= 0.3 and (d.c[-1] / d.o[0] - 1) * 100 >= 1.0]
print(f"{len(sim)} such days in {len(alld)} ({100*len(sim)/len(alld):.1f}%)")
trs = E.run(sim, SYM, "noise", W)
whys = pd.Series([t["why"] for t in trs]).value_counts().to_dict()
pn = pd.Series([t["pnl"] for t in trs])
print(f"noise trades on them: {len(trs)}, net ₹{pn.sum():+,.0f}, avg ₹{pn.mean():+,.0f}, exits {whys}")
# of the noise-exit trades on those days, how often did the index go on to make a new high by 15:15 in the trade direction?
resumed, cnt = 0, 0
for t in trs:
    if t["why"] != "noise exit":
        continue
    d = days[t["date"]]
    k = next(i for i, m in enumerate(d.t) if m + 5 == t["out"])
    later = max(d.h[k:]) if t["side"] > 0 else min(d.l[k:])
    if t["side"] * (later - t["spot_out"]) >= 0.5 * abs(t["spot_in"] - t["spot_out"] if t["spot_in"] != t["spot_out"] else 1) + 50:
        resumed += 1
    cnt += 1
print(f"after a band/VWAP trailing exit on such days, the move resumed by 50+ pts {resumed}/{cnt} times")
# all days: noise-exit trades - what came after, and the P&L of 'hold instead of trail' on those same trades
trs_all = E.run(alld, SYM, "noise", W)
ne = [t for t in trs_all if t["why"] == "noise exit"]
pn_ne = sum(t["pnl"] for t in ne)
hold = []
for t in ne:
    d = days[t["date"]]
    i = next(i for i, m in enumerate(d.t) if m + 5 == t["in"])
    s2 = E.Sig(i, t["side"], t["spot_in"] - t["side"] * abs(t["spot_in"] - t["spot_in"]) if False else (t["spot_in"] - t["side"] * 2 * d.atr[i]), t["spot_in"] + t["side"] * 8 * d.atr[i], "", 0, 0, "noise")
    k, px, why = E.simulate(d, s2, meta, {})
    h = E.trade_pnl(d, s2, k, px, meta, {"otm": -1})
    hold.append(h["pnl"] if h else 0)
print(f"all {len(alld)} days: {len(ne)} trailing exits worth ₹{pn_ne:+,.0f}; holding those same trades to stop/4R/15:15 instead: ₹{sum(hold):+,.0f}")
OUT["history"] = {"similar_days": len(sim), "noise_trades": len(trs), "net": float(pn.sum()), "exits": whys, "resumed_after_trail_exit": f"{resumed}/{cnt}",
                  "trail_exits_all_days": len(ne), "trail_exit_pnl": float(pn_ne), "hold_instead_pnl": float(sum(hold))}
json.dump(OUT, open(os.path.join(HERE, f"day_review_{D.isoformat()}.json"), "w"), indent=1, default=str)
