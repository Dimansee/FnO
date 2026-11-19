"""Round 102a: round-number reversal zones (Devansh, 19 Nov 2026).

Idea: near round numbers (Nifty 25200, 25300 ...) count how many times price was rejected from the level today and this week;
treat level +/- 50 points as the zone where a reversal can come; enter only on candle open / close logic.

Levels: NIFTY every 100, BANKNIFTY every 200, SENSEX every 250 (each about 0.3-0.4% apart).
Zone: level +/- z, z = zf x half the step (zf 1.0 = the asked +/-50 on Nifty; 0.5 = +/-25; 0.3 = +/-15).
Test of a level from below (resistance): previous close below the zone, bar high reaches the zone. It ends as
  rejection  - a close back below the zone (can be the same bar: a wick into the zone)
  break      - a close above the zone.
Mirror for support. Tests reset at the start of each session. Rejections are counted per level and side: today (earlier bars) and
week (the 4 previous sessions + today).
Entry (candle open / close logic): on the rejection bar, the candle must close in the direction of the reversal (close < open for a
resistance rejection) -> buy the put (call for support) at the next bar's open. Break mode: the close beyond the zone, candle in the
break direction, level already rejected >= k times -> trade the break.
Stops: 'candle' = the signal bar's extreme (+0.02% buffer); 'zone' = far edge of the zone (reversal) / the level (break).
Exits: 'next' = next round level, '2R', 'trail' (stop follows the best close by 1R once ahead), 'eod' = 15:15.
Options: house model (round 34), 1 lot ATM, 0.5% slippage a fill, charges. Random-direction twin = same bars, coin-flip side.
"""
import sys, os, json, itertools, time
sys.path.insert(0, "/home/claude/dimansee/fno/research/round101")
import lib as L
import numpy as np, pandas as pd
M = L.M

STEP = {"NIFTY": 100, "BANKNIFTY": 200, "SENSEX": 250}


def events(df, o, h, l, c, day, sym, zf):
    """Per bar: rej_r / rej_s / brk_u / brk_d = level (nan if none); counts of earlier same-side rejections today / week."""
    step = STEP[sym]; z = zf * step / 2
    n = len(c)
    out = {k: np.full(n, np.nan) for k in ("rej_r", "rej_s", "brk_u", "brk_d", "rd", "rw", "sd", "sw", "bud", "buw", "bdd", "bdw")}
    days = pd.unique(day); dpos = {d: i for i, d in enumerate(days)}
    rej_hist = {}   # (side, L) -> list of session index of each rejection
    act_r, act_s = set(), set()
    cur = None
    for t in range(n):
        if day[t] != cur:
            cur = day[t]; act_r.clear(); act_s.clear(); di = dpos[cur]
            pc = None
        else:
            pc = c[t - 1]
        lo_L = int(np.floor((l[t] - 2 * z) / step)); hi_L = int(np.ceil((h[t] + 2 * z) / step))
        for k in range(lo_L, hi_L + 1):
            Lv = k * step
            if pc is not None and pc < Lv - z and h[t] >= Lv - z:
                act_r.add(Lv)
            if pc is not None and pc > Lv + z and l[t] <= Lv + z:
                act_s.add(Lv)
        for Lv in list(act_r):
            if c[t] > Lv + z or c[t] < Lv - z:
                hist = rej_hist.get(("r", Lv), [])
                cd = sum(1 for x in hist if x == di); cw = sum(1 for x in hist if di - 4 <= x <= di)
                if c[t] < Lv - z:
                    if np.isnan(out["rej_r"][t]) or Lv < out["rej_r"][t]:
                        out["rej_r"][t] = Lv; out["rd"][t] = cd; out["rw"][t] = cw
                    rej_hist.setdefault(("r", Lv), []).append(di)
                else:
                    if np.isnan(out["brk_u"][t]) or Lv > out["brk_u"][t]:
                        out["brk_u"][t] = Lv; out["bud"][t] = cd; out["buw"][t] = cw
                act_r.discard(Lv)
        for Lv in list(act_s):
            if c[t] > Lv + z or c[t] < Lv - z:
                hist = rej_hist.get(("s", Lv), [])
                cd = sum(1 for x in hist if x == di); cw = sum(1 for x in hist if di - 4 <= x <= di)
                if c[t] > Lv + z:
                    if np.isnan(out["rej_s"][t]) or Lv > out["rej_s"][t]:
                        out["rej_s"][t] = Lv; out["sd"][t] = cd; out["sw"][t] = cw
                    rej_hist.setdefault(("s", Lv), []).append(di)
                else:
                    if np.isnan(out["brk_d"][t]) or Lv < out["brk_d"][t]:
                        out["brk_d"][t] = Lv; out["bdd"][t] = cd; out["bdw"][t] = cw
                act_s.discard(Lv)
    out["z"] = z; out["step"] = step
    return out


def sim(o, h, l, c, hm, day, sig, stop, tgt, trail_R=0.0, first=9 * 60 + 20, last=14 * 60 + 45, sq=15 * 60 + 15, step=5, per_day=1):
    """sig[t] = +1/-1 decided at bar t's close; entry at t+1 open. stop / tgt absolute index prices (tgt nan = none).
    tgt < 0 encodes an R multiple (-2 -> 2R)."""
    n = len(c); rows = []; i = 1; taken = 0; cur = -1; last_start = sq - step
    while i < n:
        if day[i] != cur:
            cur = day[i]; taken = 0
        j0 = i - 1
        if day[j0] != day[i] or taken >= per_day or not (first <= hm[i] <= last) or sig[j0] == 0:
            i += 1; continue
        s = int(sig[j0]); ep = o[i]; sl = stop[j0]
        if not np.isfinite(sl) or s * (ep - sl) <= 0:
            i += 1; continue
        r = abs(ep - sl); sl0 = sl
        tp = tgt[j0]
        if np.isfinite(tp) and tp < 0:
            tp = ep + s * (-tp) * r
        elif np.isfinite(tp) and s * (tp - ep) <= 0:
            i += 1; continue
        best = ep; j = i
        while True:
            adv, fav = (l[j], h[j]) if s > 0 else (h[j], l[j])
            if s * (adv - sl) <= 0:
                xs = o[j] if (j > i and s * (o[j] - sl) < 0) else sl; break
            if np.isfinite(tp) and s * (fav - tp) >= 0:
                xs = o[j] if (j > i and s * (o[j] - tp) > 0) else tp; break
            if hm[j] >= last_start or j + 1 >= n or day[j + 1] != day[i]:
                xs = c[j]; break
            if s * (c[j] - best) > 0:
                best = c[j]
            if trail_R and s * (best - ep) > 0:
                t = best - s * trail_R * r
                if s * (t - sl) > 0:
                    sl = t
            j += 1
        rows.append((i, j, ep, xs, s, sl0)); taken += 1
        i = j + 1
    return rows


def priced(sym, df, rows, stepmin, twin=None):
    if not rows:
        return pd.DataFrame()
    T = pd.DataFrame(rows, columns=["i", "j", "ep", "xs", "side", "sl"]).astype({"i": int, "j": int})
    T["date"] = df.index[T.i.values].strftime("%Y-%m-%d"); T["t_in"] = df.index[T.i.values].strftime("%H:%M")
    T["t_out"] = (df.index[T.j.values] + pd.Timedelta(minutes=stepmin)).strftime("%H:%M")
    T["pts"] = T.side * (T["xs"] - T.ep)
    return M.price(sym, df, T, twin=twin)


def signals(E, o, h, l, c, mode, cnt, k, color, stop_kind, exit_kind, buf):
    n = len(c); sig = np.zeros(n); stop = np.full(n, np.nan); tgt = np.full(n, np.nan)
    z, step = E["z"], E["step"]
    if mode == "rev":
        pairs = [(-1, "rej_r", "rd", "rw"), (1, "rej_s", "sd", "sw")]
    else:
        pairs = [(1, "brk_u", "bud", "buw"), (-1, "brk_d", "bdd", "bdw")]
    for s, key, kd, kw in pairs:
        Lv = E[key]; m = np.isfinite(Lv)
        cntv = E[kd] if cnt == "day" else E[kw]
        m &= (cntv >= k) if k > 0 else True
        if color:
            m &= (c > o) if s > 0 else (c < o)
        m &= sig == 0
        idx = np.where(m)[0]
        sig[idx] = s
        if stop_kind == "candle":
            stop[idx] = (l[idx] - buf[idx]) if s > 0 else (h[idx] + buf[idx])
        else:
            if mode == "rev":
                stop[idx] = Lv[idx] - z if s > 0 else Lv[idx] + z
            else:
                stop[idx] = Lv[idx]
        if exit_kind == "next":
            tgt[idx] = Lv[idx] + s * step if mode == "rev" else Lv[idx] + s * step * 1.0 + s * z
        elif exit_kind == "2R":
            tgt[idx] = -2.0
    return sig, stop, tgt


def run_all(sym, out_path):
    res = {}; trades = {}
    for tf, stepmin in (("5m", 5), ("15m", 15)):
        df, o, h, l, c, hm, day = L.bar_of(sym, tf)
        buf = c * 0.0002
        for zf in (1.0, 0.5, 0.3):
            E = events(df, o, h, l, c, day, sym, zf)
            for mode, (cnt, k), color, stop_kind, exit_kind, per_day in itertools.product(
                    ("rev", "brk"), (("day", 0), ("day", 1), ("day", 2), ("day", 3), ("week", 1), ("week", 2), ("week", 3), ("week", 5)),
                    (True, False), ("candle", "zone"), ("next", "2R", "trail", "eod"), (1, 3)):
                if mode == "brk" and k == 0:
                    continue
                sig, stop, tgt = signals(E, o, h, l, c, mode, cnt, k, color, stop_kind, exit_kind, buf)
                rows = sim(o, h, l, c, hm, day, sig, stop, tgt, trail_R=1.0 if exit_kind == "trail" else 0.0, step=stepmin, per_day=per_day)
                if len(rows) < 20:
                    continue
                key = f"{sym}|{tf}|z{zf}|{mode}|{cnt}{k}|{'col' if color else 'any'}|{stop_kind}|{exit_kind}|{per_day}d"
                T = priced(sym, df, rows, stepmin)
                W = priced(sym, df, rows, stepmin, twin=7)
                res[key] = {"v": L.summ(T), "twin": L.summ(W)}
                trades[key] = T[["i", "j", "ep", "xs", "side", "sl", "date", "t_in", "t_out", "pts", "rs"]]
    json.dump(res, open(out_path, "w"))
    pd.to_pickle(trades, out_path.replace(".json", "_trades.pkl"))


if __name__ == "__main__":
    sym = sys.argv[1]
    t = time.time()
    run_all(sym, f"/home/claude/r102/out/zones_{sym}.json")
    print(sym, "done", round(time.time() - t))
