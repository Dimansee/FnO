"""C: trade filters - gap filter, expiry-day rules, day-of-week / time-of-day breakdown (and skipping any slot that loses in
every year). Entry cutoffs and the 15:00 square-off need new trades: see d_exits.py."""
import json
import numpy as np, pandas as pd
import r106lib as X
T0, A, H = X.load_set("base")
T = X.features(T0); out = {}
print("BASE  " + " | ".join(f"{k} {X.fmt(v)}" for k, v in X.base_grid().items()), flush=True)
R = lambda nm, **kw: X.report(nm, T, A, H, out, **kw)
sg = lambda r: 1 if r.side == "CALL" else -1
print("\nGap filter (open vs previous close, in daily ATR)")
for g in (0.75, 1.0, 1.5):
    n = int((T.gap_atr.abs() > g).sum()); d = T[T.gap_atr.abs() > g].date.nunique()
    print(f"  gap > {g} ATR: {n} trades on {d} days, per-lot {T[T.gap_atr.abs() > g].pnl.sum():+.0f}; with the gap {T[(T.gap_atr.abs() > g) & (np.sign(T.gap_atr) == np.where(T.side=='CALL',1,-1))].pnl.sum():+.0f}, against {T[(T.gap_atr.abs() > g) & (np.sign(T.gap_atr) != np.where(T.side=='CALL',1,-1))].pnl.sum():+.0f}")
    R(f"skip the day when gap > {g} ATR", skip=lambda r, g=g: abs(r.gap_atr) > g)
    R(f"gap > {g} ATR: only trades in the gap's direction", skip=lambda r, g=g: abs(r.gap_atr) > g and np.sign(r.gap_atr) != sg(r))
print("\nExpiry day of the traded index")
e = T[T.expiry_day == True]
print(f"  expiry-day trades {len(e)}, per-lot {e.pnl.sum():+.0f} (avg {e.pnl.mean():+.0f}) vs other days avg {T[T.expiry_day != True].pnl.mean():+.0f}")
print(e.assign(y=e.date.str[:4]).pivot_table(index="sym", columns="y", values="pnl", aggfunc="sum").round(0).to_string())
R("expiry day: half size", mult=lambda r: 0.5 if r.expiry_day else 1.0)
for cut in ("12:00", "13:00", "14:00"):
    R(f"expiry day: no entries after {cut}", skip=lambda r, c=cut: bool(r.expiry_day) and r.entry_time > c)
R("expiry day: skip", skip=lambda r: bool(r.expiry_day))
print("\nDay of week (per-lot rupees, 1 ITM, by year)")
dn = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri"}
pv = T[T.dow < 5].assign(y=T.date.str[:4], d=T.dow.map(dn)).pivot_table(index="d", columns="y", values="pnl", aggfunc="sum").round(0)
print(pv.to_string())
print("\nTime of entry (per-lot rupees by half hour, by year)")
T["slot"] = T.entry_time.str[:2] + ":" + np.where(T.entry_time.str[3:5].astype(int) < 30, "00", "30")
pv2 = T.assign(y=T.date.str[:4]).pivot_table(index="slot", columns="y", values="pnl", aggfunc="sum").round(0)
pv2["n"] = T.groupby("slot").size(); print(pv2.to_string())
print("\nStrategy x day (per-lot, all years) and years a cell lost:")
sd = T[T.dow < 5].assign(y=T.date.str[:4], d=T.dow.map(dn)).pivot_table(index=["strategy", "d"], columns="y", values="pnl", aggfunc="sum")
lose_all = sd[(sd.fillna(0) < 0).sum(axis=1) == 4]
print(sd.round(0).to_string()); print("cells losing in all 4 years:", list(lose_all.index))
for (st, d) in lose_all.index:
    R(f"skip {st} on {d}", skip=lambda r, st=st, d=d: r.strategy == st and dn.get(r.dow) == d)
ls = pv2.drop(columns="n"); bad = [s for s in ls.index if (ls.loc[s].fillna(0) < 0).sum() == 4]
print("half-hour slots losing in all 4 years:", bad)
for s in bad:
    R(f"skip entries in the {s} slot", skip=lambda r, s=s: (r.entry_time[:2] + ":" + ("00" if int(r.entry_time[3:5]) < 30 else "30")) == s)
json.dump(out, open("r106_c.json", "w"), indent=1, default=str)
