"""A: what does the next day do after 2 / 3 / 4+ closes one way? Long daily history + the 2023-26 research period.
All numbers are signed IN THE DIRECTION OF THE RUN (positive = the run continued, negative = it reversed)."""
import json, numpy as np, pandas as pd, r105lib as X
out = {}
PERS = {"to 2022": ("2007-01-01", "2022-12-31"), "2023-24": ("2023-01-01", "2024-12-31"), "2025-26": ("2025-01-01", "2026-11-20")}
def row(d):
    s = np.sign(d.p_run)
    gap = s * (d.open / d.pc - 1) * 100; oc = s * (d.close / d.open - 1) * 100; cc = s * (d.close / d.pc - 1) * 100
    adverse = np.where(s > 0, (d.low / d.open - 1), (1 - d.high / d.open)) * 100          # worst move against the run after the open
    return {"n": int(len(d)), "continue_%": round(float((cc > 0).mean() * 100), 1), "cc": round(float(cc.mean()), 3), "gap": round(float(gap.mean()), 3),
            "open_to_close": round(float(oc.mean()), 3), "oc_down_%": round(float((oc < 0).mean() * 100), 1),
            "gap_against_%": round(float((gap < 0).mean() * 100), 1),
            "gap_against_then_close_with_%": round(float(((gap < 0) & (oc > 0)).sum() / max(1, (gap < 0).sum()) * 100), 1),
            "range_%": round(float(((d.high - d.low) / d.pc * 100).mean()), 2), "t_oc": round(float(oc.mean() / (oc.std() / np.sqrt(max(2, len(d))))), 2)}
for sym in X.SYMS:
    d = X.streaks(X.daily_long(sym)).dropna(subset=["pc"])
    for pn, (a, b) in PERS.items():
        q = d[(d.index >= a) & (d.index <= b)]
        allr = {"n": len(q), "up_%": round(float((q.close > q.pc).mean() * 100), 1), "oc": round(float((q.close / q.open - 1).mean() * 100), 3),
                "range_%": round(float(((q.high - q.low) / q.pc * 100).mean()), 2)}
        out[f"{sym}|{pn}|all"] = allr; print(f"\n{sym} {pn}: all days {allr}")
        for name, m in (("up2", q.p_run == 2), ("up3", q.p_run == 3), ("up4+", q.p_run >= 4), ("up2+", q.p_run >= 2), ("up3+", q.p_run >= 3),
                        ("dn2", q.p_run == -2), ("dn3", q.p_run == -3), ("dn4+", q.p_run <= -4), ("dn2+", q.p_run <= -2), ("dn3+", q.p_run <= -3),
                        ("up2-3 & >=1.5%", q.p_run.between(2, 3) & (q.p_cum >= 1.5)), ("up2-3 & >=2.5%", q.p_run.between(2, 3) & (q.p_cum >= 2.5)),
                        ("dn2-3 & <=-1.5%", q.p_run.between(-3, -2) & (q.p_cum <= -1.5)), ("dn2-3 & <=-2.5%", q.p_run.between(-3, -2) & (q.p_cum <= -2.5))):
            if m.sum() < 8: continue
            r = row(q[m]); out[f"{sym}|{pn}|{name}"] = r
            print(f"  {name:16s} n {r['n']:4d} cont {r['continue_%']:5.1f}% c2c {r['cc']:+.3f} gap {r['gap']:+.3f} o2c {r['open_to_close']:+.3f} (t {r['t_oc']:+.2f}) o2c<0 {r['oc_down_%']:.0f}% gapAgainst {r['gap_against_%']:.0f}% ->closeWith {r['gap_against_then_close_with_%']:.0f}% range {r['range_%']}")
json.dump(out, open("r105_a.json", "w"), indent=1)
