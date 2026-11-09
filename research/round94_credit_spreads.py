"""Round 94 (9 Nov 2026): credit strategies (selling hedged option spreads) on real NSE prices.

User: "what is credit strategy - back test it on the basis of research". A credit strategy sells an option and buys a further-out
one as a wing, so the premium comes in up front and the most it can lose is (width - credit). Research (see
fno/index_options_strategy_research_2026-10.md): the volatility premium is real but thin (Bank Nifty IV ~2 vol pts above realised,
2018-22); Bhat, Pandey & Rao (Nifty 2017-20) find short options earn their premium OVERNIGHT, not open-to-close; always own the
wing; trust nothing that dies without its best 10% of days. Round 31's first try was discarded (missing crash days); the
bhavcopy now holds strikes within 15% (4 Jun 2024 included).

Data: NSE F&O bhavcopy, Nifty and Bank Nifty, 9 Oct 2023 - 9 Oct 2026 (741 days). Index close/open from the research candles.
Structures: BPS (bull put spread: sell put, buy lower put), BCS (bear call spread), IC (iron condor = both).
  Positional: enter at the close N trading days before the nearest expiry (N = 1..4), hold to expiry (cash settled at the index
  close), short strike k x expected move away (EM = spot x VIX x sqrt(N / 252); k = 0.5 / 1.0 / 1.5), wing width w = 0.5 / 1 / 2%
  of spot; stop: none, or close the side at the day's close once its loss reaches 1x or 2x the credit (daily closes only).
  Overnight: every day, enter at the close, buy back at the next day's open (k x one-day EM = 0.25 / 0.5 / 1.0, w = 0.5 / 1%).
Fills: price = close if traded, else settle; sells 2% (min 0.25 pt) below, buys 2% (min 0.25 pt) above; charges from the app
(Rs 20 per order, STT 0.15% on sells, exchange, GST, stamp) and STT 0.125% on an exercised long wing. Lots 65 / 30.
Bar (research rulebook): positive in Oct 2023 - Dec 2024 AND 2025 - Oct 2026, still positive without its best 10% of trades,
profit factor >= 1.2, and the stop choice must not flip the sign.
Usage: python research/round94_credit_spreads.py
"""
import itertools, json, os, sys, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.dirname(HERE))
import numpy as np, pandas as pd
from fno import indicators as I
import round26_harness as H

LOT = {"NIFTY": 65, "BANKNIFTY": 30}; STEP = {"NIFTY": 50, "BANKNIFTY": 100}
HAIR, MINP = 0.02, 0.25
BH = pd.read_csv(os.path.join(HERE, "data", "OPT_bhavcopy.csv.gz"))
BH["px"] = np.where(BH.volume > 0, BH.close, BH.settle)
VIX = H.load("INDIAVIX").close
VIXD = VIX.groupby(VIX.index.date).last()


def sell_fill(p):
    return max(p - max(HAIR * p, MINP), 0.05)


def buy_fill(p):
    return p + max(HAIR * p, MINP)


def load(sym):
    b = BH[BH.sym == sym].copy()
    b["date"] = pd.to_datetime(b.date).dt.date; b["expiry"] = pd.to_datetime(b.expiry).dt.date
    px = {(r.date, r.expiry, r.strike, r.opt): r.px for r in b.itertuples(index=False)}
    op = {(r.date, r.expiry, r.strike, r.opt): r.open for r in b.itertuples(index=False) if r.open > 0 and r.volume > 0}
    days = sorted(b.date.unique())
    near = b.groupby("date").expiry.apply(lambda s: sorted(e for e in s.unique() if e >= s.name)).to_dict()
    c = H.load(sym); g = c.groupby(c.index.date)
    return px, op, days, near, g.open.first().to_dict(), g.close.last().to_dict()


def side_trade(sym, D, T, E, side, spot, em, k, w, stop, dates_between):
    """One credit spread (side 'P' or 'C') from close T to expiry E. Returns rupee P&L per lot, max loss per lot, info."""
    px, op, days, near, dopen, dclose = D
    st, lot = STEP[sym], LOT[sym]
    opt = "PE" if side == "P" else "CE"
    if side == "P":
        ks = np.floor((spot - k * em) / st) * st; kw = ks - max(st, round(w * spot / st) * st)
    else:
        ks = np.ceil((spot + k * em) / st) * st; kw = ks + max(st, round(w * spot / st) * st)
    ps, pw = px.get((T, E, ks, opt)), px.get((T, E, kw, opt))
    if ps is None or pw is None:
        return None
    credit = sell_fill(ps) - buy_fill(pw)
    if credit <= 0.5:
        return None
    width = abs(ks - kw)
    pnl = credit * lot - I.charges(buy_fill(pw), sell_fill(ps), lot, orders=2)
    # daily stop check on closes before expiry
    if stop:
        for d in dates_between:
            vs, vw = px.get((d, E, ks, opt)), px.get((d, E, kw, opt))
            if vs is None or vw is None:
                continue
            if (vs - vw) - credit >= stop * credit:
                cost = buy_fill(vs) - sell_fill(vw)
                pnl -= cost * lot + I.charges(buy_fill(vs), sell_fill(vw), lot, orders=2)
                return {"pnl": pnl, "maxloss": (width - credit) * lot, "exit": "stop", "credit": credit}
    se = dclose.get(E)
    if se is None:
        return None
    iv_s = max(ks - se, 0) if side == "P" else max(se - ks, 0)
    iv_w = max(kw - se, 0) if side == "P" else max(se - kw, 0)
    pnl -= (iv_s - iv_w) * lot + 0.00125 * iv_w * lot
    return {"pnl": pnl, "maxloss": (width - credit) * lot, "exit": "expiry", "credit": credit}


def positional(sym, D, struct, N, k, w, stop):
    px, op, days, near, dopen, dclose = D
    idx = {d: i for i, d in enumerate(days)}
    expiries = sorted({e[0] for e in near.values() if e})
    out = []
    for E in expiries:
        if E not in idx:
            continue
        i = idx[E] - N
        if i < 0:
            continue
        T = days[i]
        if not near.get(T) or near[T][0] != E or T not in VIXD.index or T not in dclose:
            continue
        spot = dclose[T]; em = spot * VIXD[T] / 100 * np.sqrt(N / 252)
        between = days[i + 1: idx[E]]
        sides = {"BPS": "P", "BCS": "C", "IC": "PC"}[struct]
        legs = [side_trade(sym, D, T, E, s, spot, em, k, w, stop, between) for s in sides]
        if any(l is None for l in legs):
            continue
        out.append({"date": str(T), "expiry": str(E), "pnl": sum(l["pnl"] for l in legs),
                    "maxloss": max(l["maxloss"] for l in legs) if struct == "IC" else legs[0]["maxloss"],
                    "stopped": any(l["exit"] == "stop" for l in legs)})
    return pd.DataFrame(out)


def overnight(sym, D, struct, k, w):
    px, op, days, near, dopen, dclose = D
    st, lot = STEP[sym], LOT[sym]
    out = []
    for i in range(len(days) - 1):
        T, T1 = days[i], days[i + 1]
        if not near.get(T) or T not in VIXD.index or T not in dclose:
            continue
        E = next((e for e in near[T] if e > T), None)       # never carry a contract that expires today
        if E is None or E < T1:
            continue
        spot = dclose[T]; em = spot * VIXD[T] / 100 * np.sqrt(1 / 252)
        pnl, ml, ok = 0.0, 0.0, True
        for s in {"BPS": "P", "BCS": "C", "IC": "PC"}[struct]:
            opt = "PE" if s == "P" else "CE"
            if s == "P":
                ks = np.floor((spot - k * em) / st) * st; kw = ks - max(st, round(w * spot / st) * st)
            else:
                ks = np.ceil((spot + k * em) / st) * st; kw = ks + max(st, round(w * spot / st) * st)
            a_s, a_w = px.get((T, E, ks, opt)), px.get((T, E, kw, opt))
            b_s, b_w = op.get((T1, E, ks, opt)), op.get((T1, E, kw, opt))
            if None in (a_s, a_w, b_s, b_w):
                ok = False; break
            credit = sell_fill(a_s) - buy_fill(a_w)
            if credit <= 0.25:
                ok = False; break
            cost = buy_fill(b_s) - sell_fill(b_w)
            pnl += (credit - cost) * lot - I.charges(buy_fill(a_w), sell_fill(a_s), lot) - I.charges(buy_fill(b_s), sell_fill(b_w), lot)
            ml = max(ml, (abs(ks - kw) - credit) * lot)
        if ok:
            out.append({"date": str(T), "expiry": str(E), "pnl": pnl, "maxloss": ml, "stopped": False})
    return pd.DataFrame(out)


def stats(T):
    if T is None or not len(T):
        return {"n": 0}
    p = T.pnl.values; srt = np.sort(p)[::-1]
    a, b = T[T.date < "2025"], T[T.date >= "2025"]
    eq = np.cumsum(p); dd = float((eq - np.maximum.accumulate(np.r_[0, eq])[1:]).min())
    gw, gl = p[p > 0].sum(), -p[p < 0].sum()
    k10 = int(np.ceil(0.1 * len(p)))
    return {"n": int(len(p)), "win": round(float((p > 0).mean()), 3), "total": round(float(p.sum())), "a": round(float(a.pnl.sum())),
            "b": round(float(b.pnl.sum())), "pf": round(float(gw / gl), 2) if gl > 0 else None, "ex_top10pct": round(float(srt[k10:].sum())),
            "worst": round(float(p.min())), "maxdd": round(dd), "avg_maxloss": round(float(T.maxloss.mean())),
            "stopped": round(float(T.stopped.mean()), 3), "years": T.groupby(T.date.str[:4]).pnl.sum().round(0).to_dict()}


def ok(s):
    return bool(s.get("n", 0) >= 30 and s["a"] > 0 and s["b"] > 0 and s["ex_top10pct"] > 0 and (s["pf"] or 0) >= 1.2)


def main():
    res = {"positional": {}, "overnight": {}}
    for sym in ("NIFTY", "BANKNIFTY"):
        D = load(sym)
        for struct, N, k, w in itertools.product(("BPS", "BCS", "IC"), (1, 2, 3, 4), (0.5, 1.0, 1.5), (0.005, 0.01, 0.02)):
            row = {}
            for stop in (0, 1, 2):
                row[str(stop)] = stats(positional(sym, D, struct, N, k, w, stop))
            row["pass"] = {s: ok(row[s]) for s in ("0", "1", "2")}
            row["sign_flip"] = len({np.sign(row[s].get("total", 0)) for s in ("0", "1", "2") if row[s].get("n")}) > 1
            key = f"{sym}|{struct}|N{N}|k{k}|w{w}"
            res["positional"][key] = row
            best = max(("0", "1", "2"), key=lambda s: row[s].get("total", -1e18))
            r = row[best]
            if r.get("n"):
                print(f"POS {key:<30} stop{best} n={r['n']:>3} win {r['win']:.0%} total {r['total']:+9,} (a {r['a']:+8,} b {r['b']:+8,}) "
                      f"ex-top10% {r['ex_top10pct']:+9,} PF {r['pf']} worst {r['worst']:+8,} maxDD {r['maxdd']:+9,} maxloss/lot {r['avg_maxloss']:,} "
                      f"PASS {[s for s, v in row['pass'].items() if v]} flip {row['sign_flip']}", flush=True)
        for struct, k, w in itertools.product(("BPS", "BCS", "IC"), (0.25, 0.5, 1.0), (0.005, 0.01)):
            s = stats(overnight(sym, D, struct, k, w))
            key = f"{sym}|{struct}|k{k}|w{w}"
            res["overnight"][key] = {"stats": s, "pass": ok(s)}
            if s.get("n"):
                print(f"ON  {key:<30} n={s['n']:>3} win {s['win']:.0%} total {s['total']:+9,} (a {s['a']:+8,} b {s['b']:+8,}) "
                      f"ex-top10% {s['ex_top10pct']:+9,} PF {s['pf']} worst {s['worst']:+8,} PASS {ok(s)}", flush=True)
    P = [k for k, v in res["positional"].items() if any(v["pass"].values()) and not v["sign_flip"]]
    O = [k for k, v in res["overnight"].items() if v["pass"]]
    print("POSITIONAL PASS (no sign flip):", len(P), "of", len(res["positional"]), P[:40])
    print("OVERNIGHT PASS:", len(O), "of", len(res["overnight"]), O)
    json.dump(res, open(os.path.join(HERE, "round94_results.json"), "w"), indent=1, default=str)


if __name__ == "__main__":
    main()
