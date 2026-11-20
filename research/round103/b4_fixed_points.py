"""Round 103 B4: 'exit after getting 10-25 points' applied to the WHOLE position (index points from entry), stop unchanged."""
import sys, json
sys.path.insert(0, __file__.rsplit("/", 1)[0])
import b2_pyramid as B
import r103lib as R3
import numpy as np, pandas as pd, math
from fno import portfolio_bt as PB, indicators as I
L = R3.L
out = {}
for cap in (200_000, 1_000_000, 5_000_000):
    for per in ("2023-24", "2025-26", "full", "fwd"):
        a, b = R3.PER[per]
        R0 = L.replay(cap, a, b, T=R3.T0, A=R3.A0, H=R3.H0, raw=True, max_lots=500)
        s0 = PB._summary(R0, cap)
        Rb = R0[R0["skipped"].isna()].reset_index(drop=True)
        Rb = B.attach(Rb, (R3.T0, R3.A0, R3.H0))
        row = {"today": (s0["final"], s0["max_dd_pct"])}
        for X in (10, 15, 20, 25, 40):
            dpnl = []
            hits = 0
            for r in Rb.itertuples(index=False):
                P = B.path(r)
                if P is None or len(P["mark"]) < 1:
                    dpnl.append(0.0); continue
                sign = P["sign"]; tgt = r.spot_in + sign * X
                hit = np.where(P["high"] >= tgt)[0] if sign > 0 else np.where(P["low"] <= tgt)[0]
                if len(hit):
                    j = hit[0]; hits += 1
                    lot, lots = int(r.src_lot), int(r.lots); q = lot * lots
                    px = P["price"](tgt, P["whens"][j]) * (1 - B.SLIP) * (1 - B.impact(r.sym, q))
                    alt = (px - r.prem_in) * q - I.charges(r.prem_in, px, q, orders=2 * math.ceil(q / PB.FREEZE[r.sym]), exch_pct=I.exch_rate(r.sym))
                    # keep the original result if the trade had already stopped out before reaching the target (original exit earlier)
                    t_hit = P["whens"][j]
                    dpnl.append(alt - float(r.pnl) if t_hit <= P["t_out"] else 0.0)
                else:
                    dpnl.append(0.0)
            Rb2 = Rb.copy(); Rb2["pnl"] = Rb2["pnl"].astype(float) + np.array(dpnl)
            Rb2["equity"] = float(cap) + Rb2["pnl"].cumsum()
            eq = pd.concat([pd.Series([float(cap)]), Rb2.equity], ignore_index=True)
            row[f"X{X}"] = (float(eq.iloc[-1]), float((eq / eq.cummax() - 1).min() * 100), round(hits / max(len(Rb), 1), 3), round(float((Rb2.pnl > 0).mean()), 3))
        out[f"{cap}|{per}"] = row
        print(f"cap {cap/1e5:.0f}L {per}: today {row['today'][0]/1e5:.1f}L ({row['today'][1]}%) | " + " | ".join(f"+{X}pt {row[f'X{X}'][0]/1e5:.1f}L ({row[f'X{X}'][1]:.0f}%, hit {row[f'X{X}'][2]:.0%}, win {row[f'X{X}'][3]:.0%})" for X in (10, 15, 20, 25, 40)), flush=True)
json.dump(out, open("/tmp/r103_b4.json", "w"), indent=1)
