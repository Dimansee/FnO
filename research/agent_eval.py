"""Round 15 - score the LLM trading desk's decisions (agent_out_*.json) on the actual sessions with the
same option pricing, slippage, charges and sizing as the rules, and compare with the two rules on the
same days.  usage: FNO_CALIB=1 python agent_eval.py <dir with agent_out_*.json>  -> round15.json
"""
import glob, json, os, sys
from datetime import date
import numpy as np
import pandas as pd

os.environ.setdefault("FNO_CALIB", "1")
import engine as E
import round6 as R6   # noqa: loads days, W, CA, both()

SRC = sys.argv[1] if len(sys.argv) > 1 else "."
dec = []
for f in sorted(glob.glob(os.path.join(SRC, "agent_out_*.json"))):
    dec += json.load(open(f))
dates = sorted({d["date"] for d in dec})
print(f"{len(dec)} decisions on {len(dates)} sessions {dates[0]}..{dates[-1]}")
DAYS = {(s, d.d): d for s in R6.SYMS for d in R6.D[s]}


def run_decision(x):
    d = DAYS.get((x["symbol"], date.fromisoformat(x["date"])))
    if d is None or x["action"] == "NO TRADE":
        return None
    side = 1 if x["action"] == "BUY CALL" else -1
    ent = x.get("entry")
    i0 = next(k for k in range(len(d.t)) if d.t[k] + 5 == 570)          # 09:30 close bar
    if ent == "0930" or ent is None:
        i = i0
    else:
        lvl = ent.get("trigger_above") if isinstance(ent, dict) else None
        lvl_b = ent.get("trigger_below") if isinstance(ent, dict) else None
        i = None
        for k in range(i0 + 1, len(d.t)):
            if d.t[k] + 5 > 870:
                break
            if lvl is not None and d.c[k] > lvl:
                i = k; break
            if lvl_b is not None and d.c[k] < lvl_b:
                i = k; break
        if i is None:
            return {"skipped": "trigger never reached"}
    entry = d.c[i]
    stop = float(x["stop"])
    if side * (entry - stop) <= 0:                      # stop on the wrong side after a trigger fill: use 1 ATR
        stop = entry - side * d.atr[i]
    if abs(entry - stop) < 0.5 * d.atr[i]:
        stop = entry - side * 0.5 * d.atr[i]
    tgt = float(x["target"]) if x.get("target") is not None else None
    if tgt is not None and side * (tgt - entry) <= 0:
        tgt = None
    s = E.Sig(i, side, stop, tgt, "", 0, 0, "agent")
    k, px, why = E.simulate(d, s, E.META[x["symbol"]], {})
    tr = E.trade_pnl(d, s, k, px, E.META[x["symbol"]], {"otm": -1})
    if tr is None:
        return {"skipped": "could not size"}
    tr.update({"sym": x["symbol"], "why": why, "conf": x.get("confidence"), "entry_kind": "0930" if i == i0 else "trigger", "action": x["action"]})
    return tr


rows, skipped = [], []
for x in dec:
    r = run_decision(x)
    if r is None:
        continue
    if "skipped" in r:
        skipped.append((x["symbol"], x["date"], r["skipped"]))
    else:
        rows.append(r)
df = pd.DataFrame(rows)


def summ(p):
    p = np.asarray(p, float)
    pos, neg = p[p > 0].sum(), -p[p <= 0].sum()
    return {"n": int(len(p)), "net": round(float(p.sum())), "win": round(float((p > 0).mean() * 100), 1) if len(p) else None,
            "pf": round(float(pos / neg), 2) if neg else None, "avg": round(float(p.mean())) if len(p) else None}


print("\nLLM desk:", summ(df.pnl), "| no-trade", sum(1 for x in dec if x["action"] == "NO TRADE"), "| triggers never hit", sum(1 for s in skipped if "trigger" in s[2]))
print(" by index:", {s: summ(df[df.sym == s].pnl) for s in R6.SYMS})
print(" by action:", {a: summ(df[df.action == a].pnl) for a in df.action.unique()})
print(" by entry:", {a: summ(df[df.entry_kind == a].pnl) for a in df.entry_kind.unique()})
print(" by exit:", df.groupby("why").pnl.agg(["count", "sum"]).round().to_dict("index"))
df["cbin"] = pd.cut(df.conf.astype(float), [0, 0.45, 0.5, 0.55, 1.0])
print(" by confidence:", df.groupby("cbin", observed=True).pnl.agg(["count", "sum", "mean"]).round().to_dict("index"))
daily = df.groupby("date").pnl.sum().reindex([date.fromisoformat(x) for x in dates], fill_value=0)
eq = daily.cumsum(); dd = float((eq - eq.cummax()).min())
print(" max drawdown ₹", round(dd))

# the rules on the same sessions
sel = {date.fromisoformat(x) for x in dates}
rules = [t for t in R6.book() if t["date"] in sel]
rp = np.array([t["pnl"] for t in rules])
rdaily = pd.Series(rp, index=[t["date"] for t in rules]).groupby(level=0).sum().reindex(sorted(sel), fill_value=0)
rdd = float((rdaily.cumsum() - rdaily.cumsum().cummax()).min())
print("\nRules on the same sessions:", summ(rp), " max drawdown ₹", round(rdd))
# agreement: days where both traded the same index - same direction?
rk = {(t["sym"], t["date"]): t for t in rules}
agree = [(r, rk[(r["sym"], r["date"])]) for r in rows if (r["sym"], r["date"]) in rk]
same = [1 for a, b in agree if a["side"] == b["side"]]
print(f"both traded the same index on {len(agree)} occasions; same direction {len(same)}; desk P&L on those ₹{sum(a['pnl'] for a, b in agree):+,.0f} vs rules ₹{sum(b['pnl'] for a, b in agree):+,.0f}")
# combined: desk as a filter on the rules (only take rule trades whose direction the desk agrees with / did not oppose)
dk = {(r["sym"], r["date"]): r for r in rows}
filt = [t for t in rules if (t["sym"], t["date"]) in dk and dk[(t["sym"], t["date"])]["side"] == t["side"]]
nopp = [t for t in rules if not ((t["sym"], t["date"]) in dk and dk[(t["sym"], t["date"])]["side"] != t["side"])]
print("rules only when the desk agrees:", summ([t["pnl"] for t in filt]), "| rules unless the desk opposes:", summ([t["pnl"] for t in nopp]))
out = {"sessions": len(dates), "from": dates[0], "to": dates[-1], "decisions": len(dec), "desk": summ(df.pnl), "desk_dd": round(dd),
       "desk_by_index": {s: summ(df[df.sym == s].pnl) for s in R6.SYMS}, "desk_by_action": {a: summ(df[df.action == a].pnl) for a in df.action.unique()},
       "desk_by_entry": {a: summ(df[df.entry_kind == a].pnl) for a in df.entry_kind.unique()},
       "desk_by_exit": df.groupby("why").pnl.agg(["count", "sum"]).round().to_dict("index"),
       "no_trade": sum(1 for x in dec if x["action"] == "NO TRADE"), "triggers_unfilled": sum(1 for s in skipped if "trigger" in s[2]),
       "rules_same_sessions": summ(rp), "rules_dd": round(rdd), "overlap": {"n": len(agree), "same_direction": len(same)},
       "rules_when_desk_agrees": summ([t["pnl"] for t in filt]), "rules_unless_desk_opposes": summ([t["pnl"] for t in nopp]),
       "charges_desk": round(float(sum(__import__("fno.indicators", fromlist=["x"]).charges(t["prem_in"], t["prem_out"], t["lots"] * E.META[t["sym"]]["lot"]) for t in rows)))}
json.dump(out, open("round15.json", "w"), indent=1, default=str)
df.to_csv("round15_trades.csv", index=False)
