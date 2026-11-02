"""Daily post-market review of the Indian Autopilot ("where did we lag, what can improve") - added 2 Nov 2026.

Reads what the nightly recorder saved for one session (research/data/live/<date>/: 1-minute index + option candles and
the app's journal) and prints a markdown review:
  - per index: gap, open->close move, range, trend / range day, CPR width class (round 71 sizing), opening-candle levels
  - what Autopilot did: every signal (executed / skipped + reason), trades with P&L and exit reason, scalp book
  - lag: index points the book caught vs the day's move, exits that left points before the close, breakeven-stop
    exits that lost money on the option, trend days with no trade in the move's direction
  - the user's level setups replayed on the day with the recorded option prices: opening-candle retest -> top (round 72)
  - each lag linked to the research that already tested its fix, so a repeated lag is only re-tested when it is new
It also appends one line per day to research/data/live/review_log.json (rolling tally used by the next reviews).

Nothing here changes a rule. A rule changes only after a backtest on the full history passes the user's bar:
better over Jan 2023 - today and in both halves (2023-24, 2025-26) in the Outcomes-tab replay, without a deeper worst fall.

Usage: python research/daily_review.py YYYY-MM-DD [data_dir]
"""
import gzip, json, os, sys
from datetime import date, timedelta

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DAY = sys.argv[1] if len(sys.argv) > 1 else date.today().isoformat()
DATA = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "data")
LIVE = os.path.join(DATA, "live")
SYMS = ["NIFTY", "BANKNIFTY", "SENSEX"]
STEP = {"NIFTY": 50, "BANKNIFTY": 100, "SENSEX": 100}
TREND_PCT = 0.8

# lag -> research that tested the fix (kept short; the Research tab has the detail)
VERDICTS = {
    "breakeven_loss": "r40: breakeven at +0.25R / later / locks all tested - current exits best over 2023-25; a breakeven exit losing on the option is decay + spread, expected.",
    "trail_exit": "r23 / r40: holding to 15:15 or wider trails win on such days but lose overall.",
    "wrong_way": "r21d / r14a: re-entry after a loss was worse; watch-list p-reentry.",
    "no_trade_trend": "r23 / r24: trend-day catcher exists; earlier or wider checks earned less.",
    "range_day": "r32 / r67 / r69 / r70 / r72: level bounces and range rotations have no edge after option costs; Autopilot skipping range days is by design.",
    "range_day_loss": "sideways-flag tests (16 Oct, two rounds) / r33: no pre-open or first-30-min flag separates losing range-day breakouts from winners; every filter cut profit.",
    "wide_cpr": "r71: wide-CPR days are skipped on purpose (they lost in every year).",
    "skip": "see the skip reason; each limit was tested (weekly r18b/r20c, daily r19c, cash/risk r25, big-run r42, CPR r71).",
}


def idx_candles(day):
    f = os.path.join(LIVE, day, "candles_1m.csv.gz")
    if not os.path.exists(f):
        return None, None
    c = pd.read_csv(f, parse_dates=["ts"])
    return c[c.kind == "IDX"], c[c.kind.isin(["CE", "PE"])]


def prev_hlc(sym, day):
    """Previous session high / low / close: previous live day if recorded, else the 1-minute history file."""
    d = date.fromisoformat(day)
    for k in range(1, 8):
        p = (d - timedelta(days=k)).isoformat()
        ix, _ = idx_candles(p)
        if ix is not None and len(ix[ix.sym == sym]):
            x = ix[ix.sym == sym]
            return float(x.high.max()), float(x.low.min()), float(x.close.iloc[-1])
    f = os.path.join(DATA, f"{sym}_1m_upstox.csv.gz")
    if os.path.exists(f):
        h = pd.read_csv(f, parse_dates=["ts"]).set_index("ts")
        h = h[h.index < pd.Timestamp(day)]
        last = h[h.index.normalize() == h.index[-1].normalize()]
        return float(last.high.max()), float(last.low.min()), float(last.close.iloc[-1])
    return None


def five(x):
    x = x.set_index("ts")[["open", "high", "low", "close"]].sort_index()
    return x.resample("5min", origin=x.index[0], label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()


def r72(b):
    """Round-72 setup on today's 5-min bars (5-min opening candle, run 0.15%, tol 0.05%, room 1R, exit at the top)."""
    o, h, l, c = (b[k].values for k in ("open", "high", "low", "close"))
    hm = b.index.hour * 60 + b.index.minute
    out = []
    for s in (1, -1):
        L = h[0] if s == 1 else l[0]
        broken, top = False, None
        for i in range(1, len(c)):
            if not broken:
                if (s == 1 and c[i] > L) or (s == -1 and c[i] < L):
                    broken, top = True, (h[i] if s == 1 else l[i])
                continue
            touch = (l[i] <= L * 1.0005) if s == 1 else (h[i] >= L * 0.9995)
            if touch:
                if (s == 1 and c[i] <= L) or (s == -1 and c[i] >= L):
                    break
                if s * (top - L) / L >= 0.0015 and i + 1 < len(c) and 570 <= hm[i + 1] <= 870:
                    j, e = i + 1, o[i + 1]
                    stop = l[i] * 0.9997 if s == 1 else h[i] * 1.0003
                    if s * (top - e) < abs(e - stop) or s * (e - stop) <= 0:
                        break
                    tgt = top * (1 - s * 0.0002)
                    k, xp = len(c) - 1, c[-1]
                    for k in range(j, len(c)):
                        if (s == 1 and l[k] <= stop) or (s == -1 and h[k] >= stop):
                            xp = stop; break
                        if (s == 1 and h[k] >= tgt) or (s == -1 and l[k] <= tgt):
                            xp = tgt; break
                    out.append({"side": "CALL" if s == 1 else "PUT", "level": round(L, 1), "top": round(top, 1), "entry_t": b.index[j].strftime("%H:%M"),
                                "entry": round(e, 1), "stop": round(stop, 1), "exit_t": (b.index[k] + pd.Timedelta(minutes=5)).strftime("%H:%M"),
                                "exit": round(xp, 1), "R": round(s * (xp - e) / abs(e - stop), 2), "pts": round(s * (xp - e), 1)})
                break
            top = max(top, h[i]) if s == 1 else min(top, l[i])
    return out


def option_px(opt, sym, kind, strike, t):
    x = opt[(opt.sym == sym) & (opt.kind == kind) & (opt.strike == strike)]
    if x.empty:
        return None
    x = x[x.ts <= t]
    return float(x.close.iloc[-1]) if len(x) else None


def main():
    ix, opt = idx_candles(DAY)
    if ix is None:
        print(f"# Daily review {DAY}\n\nNo recording for {DAY} yet (the recorder runs 19:10 IST and 08:50 IST next day).")
        return
    jf = os.path.join(LIVE, DAY, "journal.json")
    J = json.load(open(jf)) if os.path.exists(jf) else {}
    dec = (J.get("decisions") or {}).get("rows", []) if isinstance(J.get("decisions"), dict) else []
    blocks = (J.get("decisions") or {}).get("blocks", []) if isinstance(J.get("decisions"), dict) else []
    trades = J.get("paper_trades") or []
    scalps = J.get("scalp_trades") or []
    L = [f"# Daily review {DAY}", ""]
    log = {"day": DAY, "idx": {}, "lags": []}
    tot = 0.0
    for sym in SYMS:
        x = ix[ix.sym == sym]
        if x.empty:
            continue
        b = five(x)
        o, hi, lo, cl = float(b.open.iloc[0]), float(b.high.max()), float(b.low.min()), float(b.close.iloc[-1])
        p = prev_hlc(sym, DAY)
        gap = (o / p[2] - 1) * 100 if p else None
        mv = (cl / o - 1) * 100
        cpr = None
        if p:
            P = (p[0] + p[1] + p[2]) / 3; bc = (p[0] + p[1]) / 2; tc = 2 * P - bc
            cpr = abs(tc - bc) / P * 100
        cls = None if cpr is None else ("narrow" if cpr < 0.25 else "wide" if cpr > 0.5 else "middle")
        trend = abs(mv) >= TREND_PCT
        L.append(f"## {sym}")
        L.append(f"- Open {o:,.1f} · high {hi:,.1f} · low {lo:,.1f} · close {cl:,.1f}"
                 + (f" · gap {gap:+.2f}% vs prev close {p[2]:,.1f}" if p else ""))
        L.append(f"- Open→close {mv:+.2f}% ({cl - o:+.0f} pts), range {(hi - lo) / o * 100:.2f}% → "
                 + ("**trend day**" if trend else "range / two-way day")
                 + (f"; CPR width {cpr:.2f}% ({cls})" if cpr is not None else ""))
        L.append(f"- Opening 5-min candle: high {b.high.iloc[0]:,.1f}, low {b.low.iloc[0]:,.1f}; morning (to 11:30) high {b[b.index.hour * 60 + b.index.minute < 690].high.max():,.1f}, low {b[b.index.hour * 60 + b.index.minute < 690].low.min():,.1f}")
        mine = [t for t in trades if t.get("symbol") == sym]
        rows = [r for r in dec if r.get("symbol") == sym]
        caught = 0.0
        if rows:
            L.append("- Autopilot signals:")
            for r in rows:
                st = r.get("status") or {}
                tr = st.get("trade") or {}
                L.append(f"  - {r.get('time')} {r.get('strategy')} {r.get('opt')} → **{st.get('label')}**"
                         + (f" · {tr.get('lots')} lot · exit {tr.get('closed')} {tr.get('reason')} · P&L Rs {tr.get('pnl', 0):+,.0f}" if tr else "")
                         + (f" · {st.get('reason') or st.get('detail') or ''}" if st.get('label') != 'Executed' else ""))
        else:
            L.append("- Autopilot: no signal in this index.")
        for t in mine:
            pl = t.get("plan") or {}
            s = 1 if (pl.get("opt") or (t.get("legs") or [{}])[0].get("opt", "CE")) == "CE" else -1
            e = float(pl.get("entry") or t.get("spot_at_entry") or o)
            cl_t = (t.get("closed") or "")[11:16]
            xs = b[b.index.strftime("%H:%M") <= cl_t].close.iloc[-1] if cl_t else cl
            got = s * (xs - e); caught += got
            pnl = float(t.get("pnl") or 0); tot += pnl
            left = s * (cl - xs)
            reason = t.get("reason") or ""
            if "Breakeven" in reason and pnl < 0:
                log["lags"].append({"sym": sym, "lag": "breakeven_loss", "rs": round(-pnl)})
            if left > 0.5 * abs(e - float(pl.get("sl") or e - 1)) and ("trail" in reason.lower() or "time stop" in reason.lower()):
                log["lags"].append({"sym": sym, "lag": "trail_exit", "pts": round(left, 1)})
            if trend and (s > 0) != (mv > 0):
                log["lags"].append({"sym": sym, "lag": "wrong_way", "rs": round(-pnl)})
        if trend and not any(((1 if (t.get("plan") or {}).get("opt", "CE") == "CE" else -1) > 0) == (mv > 0) for t in mine):
            log["lags"].append({"sym": sym, "lag": "no_trade_trend", "pts": round(abs(cl - o), 1)})
        if not trend and not mine:
            log["lags"].append({"sym": sym, "lag": "range_day"})
        if not trend and mine and sum(float(t.get("pnl") or 0) for t in mine) < 0:
            log["lags"].append({"sym": sym, "lag": "range_day_loss", "rs": round(-sum(float(t.get("pnl") or 0) for t in mine))})
        if cls == "wide":
            log["lags"].append({"sym": sym, "lag": "wide_cpr"})
        if mine:
            L.append(f"- Book caught {caught:+.0f} index pts of a {cl - o:+.0f}-pt day.")
        st = r72(b)
        for z in st:
            k = "CE" if z["side"] == "CALL" else "PE"
            strike = round(z["entry"] / STEP[sym]) * STEP[sym]
            pi = option_px(opt, sym, k, strike, pd.Timestamp(f"{DAY} {z['entry_t']}"))
            po = option_px(opt, sym, k, strike, pd.Timestamp(f"{DAY} {z['exit_t']}") - pd.Timedelta(minutes=1))
            prem = f" · {strike:.0f} {k} {pi:.1f} → {po:.1f} ({(po / pi - 1) * 100:+.0f}%)" if pi and po else ""
            L.append(f"- Your opening-candle retest (r72) fired: {z['side']} at {z['entry_t']} {z['entry']:,.1f} (level {z['level']:,.1f}, top {z['top']:,.1f}, stop {z['stop']:,.1f}) → out {z['exit_t']} at {z['exit']:,.1f} = {z['pts']:+.0f} pts ({z['R']:+.2f}R){prem}")
        log["idx"][sym] = {"move_pct": round(mv, 2), "trend": trend, "cpr": cls, "trades": len(mine), "caught_pts": round(caught, 1),
                           "r72": [{"side": z["side"], "R": z["R"]} for z in st]}
        L.append("")
    if blocks:
        L.append("## Blocks without a signal")
        for bl in blocks:
            L.append(f"- {json.dumps(bl)[:200]}")
        L.append("")
    sp = sum(float(s.get("pnl") or 0) for s in scalps)
    L.append(f"## Book\n- Autopilot trades: {len(trades)}, P&L Rs {tot:+,.0f}; scalp book: {len(scalps)} trades, Rs {sp:+,.0f}\n")
    # rolling tally
    lf = os.path.join(LIVE, "review_log.json")
    hist = json.load(open(lf)) if os.path.exists(lf) else []
    hist = [h for h in hist if h.get("day") != DAY] + [log]
    hist = sorted(hist, key=lambda h: h["day"])[-60:]
    json.dump(hist, open(lf, "w"), indent=0)
    last20 = hist[-20:]
    tally = {}
    for h in last20:
        for g in h["lags"]:
            t = tally.setdefault(g["lag"], {"n": 0, "rs": 0.0, "pts": 0.0})
            t["n"] += 1; t["rs"] += g.get("rs") or 0; t["pts"] += g.get("pts") or 0
    L.append("## Where we lagged today")
    if log["lags"]:
        for g in log["lags"]:
            L.append(f"- {g['sym']}: **{g['lag']}**" + (f" (Rs {g['rs']:,})" if g.get("rs") else "") + (f" ({g['pts']} pts)" if g.get("pts") else "")
                     + f" — {VERDICTS.get(g['lag'], 'not researched yet: candidate for a backtest')}")
    else:
        L.append("- Nothing missed worth noting.")
    L.append(f"\n## Last {len(last20)} reviewed sessions")
    for k, t in sorted(tally.items(), key=lambda kv: -kv[1]["n"]):
        L.append(f"- {k}: {t['n']}×" + (f", Rs {t['rs']:,.0f}" if t["rs"] else "") + (f", {t['pts']:,.0f} pts" if t["pts"] else "")
                 + (" ← repeated: check whether its last test is older than 60 sessions" if t["n"] >= 3 and k not in ("range_day", "wide_cpr") else ""))
    print("\n".join(L))


if __name__ == "__main__":
    main()
