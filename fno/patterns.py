"""Classic chart patterns: one detector shared by the chart overlay (Crypto / US dashboards) and the round-60 backtest,
so what is drawn on the chart is exactly what was tested.

How a pattern is found (no hindsight):
  1. Swings come from an ATR zigzag: a high / low counts only once price has reversed `k` x ATR(14) from it. The bar where
     that happens is when the swing becomes known ("confirmed"); nothing before it can use it.
  2. Each pattern is a rule on the last 4-6 confirmed swings (equal tops, converging lines, Fibonacci ratios ...).
  3. A trade needs a CLOSE through the pattern's breakout line within the pattern's width after the last swing is confirmed,
     before price closes beyond the stop side (invalidation). Harmonic patterns (Gartley, Cypher) enter when point D is
     confirmed instead - they are reversal-at-a-level setups, not breakouts.
  4. Stop = the other side of the pattern; target = measured move (pattern height from the break) unless stated.
"""
from __future__ import annotations

import math

import numpy as np

# ------------------------------------------------------------------ guide (shown in the app; stats filled by round 60)
GROUPS = {"continuation": "Continuation", "neutral": "Neutral (breaks either way)", "reversal": "Reversal", "special": "Special (wedges & harmonics)"}

GUIDE = {
    "bull_flag": dict(label="Bullish flag", group="continuation", bias="bull",
        logic="A strong up-move (the pole, at least 4 ATR) followed by a short, shallow pull-back (at most half the pole) - buyers resting, not leaving.",
        entry="Close above the top of the pole.", stop="Below the flag's low.", target="Pole height added to the break."),
    "bear_flag": dict(label="Bearish flag", group="continuation", bias="bear",
        logic="A sharp drop (pole) followed by a weak bounce of at most half the pole - sellers pausing.",
        entry="Close below the bottom of the pole.", stop="Above the flag's high.", target="Pole height below the break."),
    "bull_pennant": dict(label="Pennant (bullish)", group="continuation", bias="bull",
        logic="Pole up, then a small converging triangle (lower highs, higher lows) no taller than half the pole.",
        entry="Close above the pennant's upper line.", stop="Below the pennant's last low.", target="Pole height from the break."),
    "bear_pennant": dict(label="Pennant (bearish)", group="continuation", bias="bear",
        logic="Pole down, then a small converging triangle no taller than half the pole.",
        entry="Close below the pennant's lower line.", stop="Above the pennant's last high.", target="Pole height from the break."),
    "channel_up": dict(label="Rising channel", group="continuation", bias="both",
        logic="Two parallel rising lines: higher highs and higher lows at the same pace.",
        entry="Close outside the channel (either side).", stop="Last swing on the other side.", target="Channel height from the break."),
    "channel_down": dict(label="Falling channel", group="continuation", bias="both",
        logic="Two parallel falling lines: lower highs and lower lows at the same pace.",
        entry="Close outside the channel (either side).", stop="Last swing on the other side.", target="Channel height from the break."),
    "rect": dict(label="Range / flat channel", group="continuation", bias="both",
        logic="Flat highs and flat lows: price boxed between support and resistance.",
        entry="Close outside the box.", stop="The other side of the box.", target="Box height from the break."),
    "megaphone": dict(label="Megaphone (broadening)", group="continuation", bias="both",
        logic="Higher highs AND lower lows: swings get wider - an unstable, emotional market.",
        entry="Close outside either line.", stop="Last swing on the other side.", target="Last swing range from the break."),
    "sym_tri": dict(label="Symmetrical triangle", group="neutral", bias="both",
        logic="Lower highs and higher lows squeeze into a point; whoever wins the break usually runs.",
        entry="Close outside the triangle.", stop="Last swing on the other side.", target="Widest part of the triangle from the break."),
    "asc_tri": dict(label="Ascending triangle", group="neutral", bias="bull",
        logic="Flat resistance with rising lows: buyers keep stepping in higher against a fixed seller.",
        entry="Close above the flat top.", stop="Below the last higher low.", target="Triangle height above the break."),
    "desc_tri": dict(label="Descending triangle", group="neutral", bias="bear",
        logic="Flat support with falling highs: sellers keep pressing lower against a fixed buyer.",
        entry="Close below the flat bottom.", stop="Above the last lower high.", target="Triangle height below the break."),
    "double_top": dict(label="Double top", group="reversal", bias="bear",
        logic="Two highs at about the same price after a rise, with a dip between (the neckline).",
        entry="Close below the neckline (the dip between the tops).", stop="Above the higher top.", target="Top-to-neckline height below the break."),
    "double_bottom": dict(label="Double bottom", group="reversal", bias="bull",
        logic="Two lows at about the same price after a fall, with a bounce between (the neckline).",
        entry="Close above the neckline.", stop="Below the lower bottom.", target="Neckline-to-bottom height above the break."),
    "triple_top": dict(label="Triple top", group="reversal", bias="bear",
        logic="Three highs at about the same level: resistance held three times.",
        entry="Close below the lowest dip between them.", stop="Above the highest top.", target="Pattern height below the break."),
    "triple_bottom": dict(label="Triple bottom", group="reversal", bias="bull",
        logic="Three lows at about the same level: support held three times.",
        entry="Close above the highest bounce between them.", stop="Below the lowest bottom.", target="Pattern height above the break."),
    "hs": dict(label="Head and shoulders", group="reversal", bias="bear",
        logic="Left shoulder, a higher head, a right shoulder near the left one's height; the two dips form the neckline.",
        entry="Close below the neckline.", stop="Above the right shoulder.", target="Head-to-neckline height below the break."),
    "ihs": dict(label="Inverse head and shoulders", group="reversal", bias="bull",
        logic="Mirror of head and shoulders at a bottom.",
        entry="Close above the neckline.", stop="Below the right shoulder.", target="Neckline-to-head height above the break."),
    "diamond": dict(label="Diamond", group="reversal", bias="both",
        logic="Swings first widen (megaphone) then narrow (triangle) - a diamond outline, usually at the end of a trend.",
        entry="Close outside the right-hand lines.", stop="Last swing on the other side.", target="Widest part of the diamond from the break."),
    "cup_handle": dict(label="Cup and handle", group="reversal", bias="bull",
        logic="A rounded bottom between two similar highs (the cup), then a small shallow dip (the handle) in the cup's top half.",
        entry="Close above the cup's rim.", stop="Below the handle low.", target="Cup depth above the rim."),
    "inv_cup_handle": dict(label="Inverted cup and handle", group="reversal", bias="bear",
        logic="A rounded top between two similar lows, then a small bounce (handle) in its lower half.",
        entry="Close below the rim.", stop="Above the handle high.", target="Cup depth below the rim."),
    "livermore": dict(label="Livermore cylinder", group="reversal", bias="bull",
        logic="A rising, widening pattern (highs climb faster than lows). Jesse Livermore bought the break of the latest high.",
        entry="Close above the upper line.", stop="Below the last swing low.", target="Last swing range above the break."),
    "rising_wedge": dict(label="Rising wedge", group="special", bias="bear",
        logic="Both lines rise but converge: each push higher gains less - the rise is tiring.",
        entry="Close below the lower line.", stop="Above the last high.", target="Wedge height below the break."),
    "falling_wedge": dict(label="Falling wedge", group="special", bias="bull",
        logic="Both lines fall but converge: each new low is shallower - selling is drying up.",
        entry="Close above the upper line.", stop="Below the last low.", target="Wedge height above the break."),
    "gartley_bull": dict(label="Gartley (bullish)", group="special", bias="bull",
        logic="Harmonic XABCD: AB retraces about 61.8% of XA, BC 38-89% of AB, D lands near the 78.6% retracement of XA.",
        entry="When the low at D is confirmed.", stop="Below X.", target="61.8% of the A-D move back up."),
    "gartley_bear": dict(label="Gartley (bearish)", group="special", bias="bear",
        logic="Mirror of the bullish Gartley at a top.",
        entry="When the high at D is confirmed.", stop="Above X.", target="61.8% of the A-D move back down."),
    "cypher_bull": dict(label="Cypher (bullish)", group="special", bias="bull",
        logic="Harmonic XABCD: B retraces 38-62% of XA, C overshoots A (127-141% of AB), D near the 78.6% retracement of XC.",
        entry="When the low at D is confirmed.", stop="Below X.", target="61.8% of the C-D move back up."),
    "cypher_bear": dict(label="Cypher (bearish)", group="special", bias="bear",
        logic="Mirror of the bullish Cypher at a top.",
        entry="When the high at D is confirmed.", stop="Above X.", target="61.8% of the C-D move back down."),
}
NAMES = list(GUIDE)
HARMONIC = {"gartley_bull", "gartley_bear", "cypher_bull", "cypher_bear"}


# ------------------------------------------------------------------ helpers
def atr(h, l, c, n=14):
    pc = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    out = np.empty(len(tr))
    acc = tr[0]
    for i, x in enumerate(tr):
        acc = x if i == 0 else acc + (x - acc) / n
        out[i] = acc
    return out


def zigzag(h, l, a, k):
    """ATR zigzag -> list of (bar of the extreme, price, +1 high / -1 low, bar where it was confirmed)."""
    piv = []
    n = len(h)
    d = 0
    hi_i, hi_p, lo_i, lo_p = 0, h[0], 0, l[0]
    ei, ep = 0, h[0]
    for i in range(1, n):
        th = k * a[i]
        if not (th > 0):
            continue
        if d == 0:
            if h[i] > hi_p:
                hi_i, hi_p = i, h[i]
            if l[i] < lo_p:
                lo_i, lo_p = i, l[i]
            if hi_p - l[i] >= th and hi_i < i:
                piv.append((hi_i, float(hi_p), 1, i)); d, ei, ep = -1, i, l[i]
            elif h[i] - lo_p >= th and lo_i < i:
                piv.append((lo_i, float(lo_p), -1, i)); d, ei, ep = 1, i, h[i]
            continue
        if d == 1:
            if h[i] >= ep:
                ei, ep = i, h[i]
            elif ep - l[i] >= th:
                piv.append((ei, float(ep), 1, i)); d, ei, ep = -1, i, l[i]
        else:
            if l[i] <= ep:
                ei, ep = i, l[i]
            elif h[i] - ep >= th:
                piv.append((ei, float(ep), -1, i)); d, ei, ep = 1, i, h[i]
    return piv


def line_at(ln, i):
    """ln = (i1, p1, i2, p2) -> price on that line at bar i (flat if i1 == i2)."""
    i1, p1, i2, p2 = ln
    return p1 + (p2 - p1) * (i - i1) / (i2 - i1) if i2 != i1 else p1


def _flat(i1, i2, p):
    return (i1, p, i2, p)


def _near(x, lo, hi, tol=0.05):
    return lo - tol <= x <= hi + tol


# ------------------------------------------------------------------ detection
def find(h, l, c, a, k=2.0, names=None):
    """All pattern instances, each a dict:
      name, side (+1 long only / -1 short only / 0 either), ready (bar it becomes known), end (last bar a breakout counts),
      pivots [(bar, price)], up / dn (breakout lines as (i1,p1,i2,p2) or None), draw [lines to draw],
      stop_l / stop_s, height (measured move), entry_now (harmonics: trade at `ready`), target (absolute, harmonics)."""
    want = set(names or NAMES)
    piv = zigzag(h, l, a, k)
    out = []
    for j in range(3, len(piv)):
        ready = piv[j][3]
        A = a[ready]
        if not (A > 0):
            continue

        def add(name, side, up, dn, stop_l, stop_s, height, pv, w, draw=None, **kw):
            if name not in want or height < 2 * A:
                return
            out.append(dict(name=name, side=side, ready=int(ready), end=int(ready + max(w, 3)), up=up, dn=dn, stop_l=stop_l,
                            stop_s=stop_s, height=float(height), pivots=[(int(x), float(p)) for x, p in pv],
                            draw=draw if draw is not None else [ln for ln in (up, dn) if ln], **kw))

        P4 = piv[j - 3:j + 1]
        (i1, p1, t1, _), (i2, p2, t2, _), (i3, p3, t3, _), (i4, p4, t4, _) = P4
        # ---- double top / bottom (last 3 swings) + flags (pole i2->i3, pull-back i4)
        if t4 == 1:
            hh = (p2 + p4) / 2 - p3
            if hh > 0 and abs(p2 - p4) <= 0.15 * hh and p1 < p3:
                neck = _flat(i3, i4, p3)
                add("double_top", -1, None, neck, None, max(p2, p4) + 0.1 * A, hh, [(i2, p2), (i3, p3), (i4, p4)], max(i4 - i2, 3),
                    draw=[_flat(i2, i4, max(p2, p4)), neck])
            pole = p2 - p3
            if pole >= 4 * A and (p4 - p3) <= 0.5 * pole and (i4 - i3) <= 1.5 * max(i3 - i2, 1):
                lvl = _flat(i3, i4, p3)
                add("bear_flag", -1, None, lvl, None, p4 + 0.1 * A, pole, [(i2, p2), (i3, p3), (i4, p4)], max(i4 - i3, 3) * 2,
                    draw=[(i2, p2, i3, p3), (i3, p3, i4, p4), lvl])
        else:
            hh = p3 - (p2 + p4) / 2
            if hh > 0 and abs(p2 - p4) <= 0.15 * hh and p1 > p3:
                neck = _flat(i3, i4, p3)
                add("double_bottom", 1, neck, None, min(p2, p4) - 0.1 * A, None, hh, [(i2, p2), (i3, p3), (i4, p4)], max(i4 - i2, 3),
                    draw=[_flat(i2, i4, min(p2, p4)), neck])
            pole = p3 - p2
            if pole >= 4 * A and (p3 - p4) <= 0.5 * pole and (i4 - i3) <= 1.5 * max(i3 - i2, 1):
                lvl = _flat(i3, i4, p3)
                add("bull_flag", 1, lvl, None, p4 - 0.1 * A, None, pole, [(i2, p2), (i3, p3), (i4, p4)], max(i4 - i3, 3) * 2,
                    draw=[(i2, p2, i3, p3), (i3, p3, i4, p4), lvl])

        # ---- cup and handle: rim, bottom, rim, handle (4 swings)
        if t4 == -1 and t1 == 1:          # H(rim) L(bottom) H(rim) L(handle)
            depth = min(p1, p3) - p2
            if depth > 0 and abs(p1 - p3) <= 0.15 * depth and p4 > p2 + 0.5 * depth and (i4 - i3) <= 0.5 * (i3 - i1) and i3 - i1 >= 10:
                seg = l[i1:i3 + 1]
                if (seg <= p2 + depth / 3).mean() >= 0.25:       # rounded: price spends time near the bottom (not a V)
                    rim = _flat(i3, i4, max(p1, p3))
                    add("cup_handle", 1, rim, None, p4 - 0.1 * A, None, depth, [(i1, p1), (i2, p2), (i3, p3), (i4, p4)], max(i4 - i3, 3) * 2,
                        draw=[(i1, p1, i2, p2), (i2, p2, i3, p3), (i3, p3, i4, p4), _flat(i1, i4, max(p1, p3))])
        if t4 == 1 and t1 == -1:
            depth = p2 - max(p1, p3)
            if depth > 0 and abs(p1 - p3) <= 0.15 * depth and p4 < p2 - 0.5 * depth and (i4 - i3) <= 0.5 * (i3 - i1) and i3 - i1 >= 10:
                seg = h[i1:i3 + 1]
                if (seg >= p2 - depth / 3).mean() >= 0.25:
                    rim = _flat(i3, i4, min(p1, p3))
                    add("inv_cup_handle", -1, None, rim, None, p4 + 0.1 * A, depth, [(i1, p1), (i2, p2), (i3, p3), (i4, p4)], max(i4 - i3, 3) * 2,
                        draw=[(i1, p1, i2, p2), (i2, p2, i3, p3), (i3, p3, i4, p4), _flat(i1, i4, min(p1, p3))])

        # ---- line patterns from the last 4 swings (2 highs + 2 lows)
        hi = [(x, p) for x, p, tt, _ in P4 if tt == 1]
        lo = [(x, p) for x, p, tt, _ in P4 if tt == -1]
        if len(hi) == 2 and len(lo) == 2:
            (ha, hpa), (hb, hpb) = hi
            (la, lpa), (lb, lpb) = lo
            h0 = max(hpa, hpb) - min(lpa, lpb)
            if h0 > 0:
                up = (ha, hpa, hb, hpb)
                dn = (la, lpa, lb, lpb)
                flat_h, flat_l = abs(hpa - hpb) <= 0.1 * h0, abs(lpa - lpb) <= 0.1 * h0
                rise_h, fall_h = hpb > hpa + 0.1 * h0, hpb < hpa - 0.1 * h0
                rise_l, fall_l = lpb > lpa + 0.1 * h0, lpb < lpa - 0.1 * h0
                sh, sl_ = (hpb - hpa) / max(hb - ha, 1), (lpb - lpa) / max(lb - la, 1)
                w4 = max(i4 - i1, 4)
                pv = [(x, p) for x, p, _, _ in P4]
                sl_long, sl_short = lpb - 0.1 * A, hpb + 0.1 * A
                if flat_h and rise_l:
                    add("asc_tri", 1, up, None, sl_long, None, h0, pv, w4, draw=[up, dn])
                if flat_l and fall_h:
                    add("desc_tri", -1, None, dn, None, sl_short, h0, pv, w4, draw=[up, dn])
                if fall_h and rise_l:
                    add("sym_tri", 0, up, dn, sl_long, sl_short, h0, pv, w4)
                if flat_h and flat_l:
                    add("rect", 0, up, dn, min(lpa, lpb) - 0.1 * A, max(hpa, hpb) + 0.1 * A, h0, pv, w4)
                if rise_h and rise_l and sl_ > sh:
                    add("rising_wedge", -1, None, dn, None, sl_short, h0, pv, w4, draw=[up, dn])
                if fall_h and fall_l and sh < sl_:
                    add("falling_wedge", 1, up, None, sl_long, None, h0, pv, w4, draw=[up, dn])
                par = abs(sh - sl_) <= 0.25 * max(abs(sh), abs(sl_))
                if rise_h and rise_l and par:
                    add("channel_up", 0, up, dn, sl_long, sl_short, abs(line_at(up, i4) - line_at(dn, i4)), pv, w4)
                if fall_h and fall_l and par:
                    add("channel_down", 0, up, dn, sl_long, sl_short, abs(line_at(up, i4) - line_at(dn, i4)), pv, w4)
                if rise_h and fall_l:
                    add("megaphone", 0, up, dn, sl_long, sl_short, abs(p4 - p3), pv, w4)
                if rise_h and rise_l and sh > sl_ and not par:
                    add("livermore", 1, up, None, sl_long, None, abs(p4 - p3), pv, w4, draw=[up, dn])

        if j < 4:
            continue
        P5 = piv[j - 4:j + 1]
        (i0, p0, t0, _), (i1, p1, t1, _), (i2, p2, t2, _), (i3, p3, t3, _), (i4, p4, t4, _) = P5
        pv5 = [(x, p) for x, p, _, _ in P5]
        w5 = max((i4 - i0) // 2, 3)
        # ---- head & shoulders, triple top / bottom
        if t4 == 1:
            Hs, Ls = (p0, p2, p4), (p1, p3)
            hh = max(Hs) - min(Ls)
            if p2 > max(p0, p4) + 0.15 * (p2 - min(Ls)) and abs(p0 - p4) <= 0.2 * (p2 - min(Ls)):
                neck = (i1, p1, i3, p3)
                add("hs", -1, None, neck, None, p4 + 0.1 * A, p2 - line_at(neck, i2), pv5, w5,
                    draw=[(i0, p0, i1, p1), (i1, p1, i2, p2), (i2, p2, i3, p3), (i3, p3, i4, p4), neck])
            if max(Hs) - min(Hs) <= 0.15 * hh:
                lvl = _flat(i1, i4, min(Ls))
                add("triple_top", -1, None, lvl, None, max(Hs) + 0.1 * A, hh, pv5, w5, draw=[_flat(i0, i4, max(Hs)), lvl])
        else:
            Hs, Ls = (p1, p3), (p0, p2, p4)
            hh = max(Hs) - min(Ls)
            if p2 < min(p0, p4) - 0.15 * (max(Hs) - p2) and abs(p0 - p4) <= 0.2 * (max(Hs) - p2):
                neck = (i1, p1, i3, p3)
                add("ihs", 1, neck, None, p4 - 0.1 * A, None, line_at(neck, i2) - p2, pv5, w5,
                    draw=[(i0, p0, i1, p1), (i1, p1, i2, p2), (i2, p2, i3, p3), (i3, p3, i4, p4), neck])
            if max(Ls) - min(Ls) <= 0.15 * hh:
                lvl = _flat(i1, i4, max(Hs))
                add("triple_bottom", 1, lvl, None, min(Ls) - 0.1 * A, None, hh, pv5, w5, draw=[_flat(i0, i4, min(Ls)), lvl])
        # ---- pennants: pole p0->p1, then a small converging triangle p1..p4
        if t4 == -1 and t0 == -1:          # L H L H L: pole up
            pole, tri = p1 - p0, p1 - p2
            if pole >= 4 * A and tri <= 0.5 * pole and p3 < p1 and p4 > p2:
                up_, dn_ = (i1, p1, i3, p3), (i2, p2, i4, p4)
                add("bull_pennant", 1, up_, None, p4 - 0.1 * A, None, pole, pv5, max(i4 - i1, 4), draw=[(i0, p0, i1, p1), up_, dn_])
        if t4 == 1 and t0 == 1:            # H L H L H: pole down
            pole, tri = p0 - p1, p2 - p1
            if pole >= 4 * A and tri <= 0.5 * pole and p3 > p1 and p4 < p2:
                up_, dn_ = (i2, p2, i4, p4), (i1, p1, i3, p3)
                add("bear_pennant", -1, None, dn_, None, p4 + 0.1 * A, pole, pv5, max(i4 - i1, 4), draw=[(i0, p0, i1, p1), up_, dn_])
        # ---- harmonics: X=p0 A=p1 B=p2 C=p3 D=p4, trade when D is confirmed
        XA = abs(p1 - p0)
        if XA >= 3 * A:
            bull = t4 == -1                 # X low, A high, B low, C high, D low
            s = 1 if bull else -1
            AB, BC, CD = abs(p1 - p2), abs(p3 - p2), abs(p3 - p4)
            ab, bc = AB / XA, BC / max(AB, 1e-12)
            xd = abs(p1 - p4) / XA          # D's retracement of XA
            ok_side = (p1 - p0) * s > 0
            if ok_side and _near(ab, 0.618, 0.618) and _near(bc, 0.382, 0.886, 0.0) and _near(xd, 0.786, 0.786):
                tgt = p4 + s * 0.618 * abs(p1 - p4)
                add("gartley_bull" if bull else "gartley_bear", s, None, None, (p0 - 0.1 * A) if bull else None, None if bull else (p0 + 0.1 * A),
                    abs(tgt - p4), pv5, 1, draw=[(i0, p0, i1, p1), (i1, p1, i2, p2), (i2, p2, i3, p3), (i3, p3, i4, p4), (i0, p0, i2, p2), (i2, p2, i4, p4)],
                    entry_now=True, target=float(tgt))
            # cypher: C beyond A, D = 0.786 of XC
            XC = abs(p3 - p0)
            beyond = (p3 - p1) * s > 0
            if ok_side and beyond and XC > 0 and _near(ab, 0.382, 0.618, 0.0) and _near(BC / max(AB, 1e-12), 1.272, 1.414, 0.05) \
                    and _near(abs(p3 - p4) / XC, 0.786, 0.786):
                tgt = p4 + s * 0.618 * CD
                add("cypher_bull" if bull else "cypher_bear", s, None, None, (p0 - 0.1 * A) if bull else None, None if bull else (p0 + 0.1 * A),
                    abs(tgt - p4), pv5, 1, draw=[(i0, p0, i1, p1), (i1, p1, i2, p2), (i2, p2, i3, p3), (i3, p3, i4, p4), (i0, p0, i3, p3)],
                    entry_now=True, target=float(tgt))

        # ---- diamond: 6 swings, widening then narrowing; trade the break of the right-hand lines
        if j >= 5:
            P6 = piv[j - 5:j + 1]
            hs6 = [(x, p) for x, p, tt, _ in P6 if tt == 1]
            ls6 = [(x, p) for x, p, tt, _ in P6 if tt == -1]
            if len(hs6) == 3 and len(ls6) == 3:
                (xa, ha_), (xb, hb_), (xc, hc_) = hs6
                (ya, la_), (yb, lb_), (yc, lc_) = ls6
                if hb_ > ha_ and hb_ > hc_ and lb_ < la_ and lb_ < lc_:
                    width = hb_ - lb_
                    if width >= 3 * A and (hc_ - lc_) < 0.7 * width:
                        up_, dn_ = (xb, hb_, xc, hc_), (yb, lb_, yc, lc_)
                        add("diamond", 0, up_, dn_, lc_ - 0.1 * A, hc_ + 0.1 * A, width, [(x, p) for x, p, _, _ in P6], max(P6[-1][0] - P6[0][0], 6) // 2,
                            draw=[(xa, ha_, xb, hb_), (ya, la_, yb, lb_), up_, dn_])
    return out


def walk(P, h, l, c, n):
    """Follow one pattern forward from the bar it became known: breakout (bar, side), failure, target / stop hits."""
    res = {"status": "forming", "break_i": None, "side": None, "entry": None, "stop": None, "target": None, "exit_i": None}
    if P.get("entry_now"):
        s = P["side"]
        res.update(status="signal", break_i=P["ready"], side=s, entry=float(c[P["ready"]]), stop=P["stop_l"] if s == 1 else P["stop_s"], target=P["target"])
    else:
        last = min(P["end"], n - 1)
        for i in range(P["ready"], last + 1):
            if i == 0:
                continue
            if P["side"] == -1 and P["stop_s"] is not None and c[i] > P["stop_s"]:
                res["status"] = "failed"; res["exit_i"] = i; return res
            if P["side"] == 1 and P["stop_l"] is not None and c[i] < P["stop_l"]:
                res["status"] = "failed"; res["exit_i"] = i; return res
            up, dn = P["up"], P["dn"]
            if up and P["side"] in (1, 0) and c[i] > line_at(up, i) and c[i - 1] <= line_at(up, i - 1):
                res.update(status="signal", break_i=i, side=1, entry=float(c[i]), stop=P["stop_l"], target=float(c[i] + P["height"])); break
            if dn and P["side"] in (-1, 0) and c[i] < line_at(dn, i) and c[i - 1] >= line_at(dn, i - 1):
                res.update(status="signal", break_i=i, side=-1, entry=float(c[i]), stop=P["stop_s"], target=float(c[i] - P["height"])); break
        else:
            if last < P["end"]:
                return res                  # still inside its window: forming
            res["status"] = "expired"; return res
    s, i0 = res["side"], res["break_i"]
    for i in range(i0 + 1, n):
        if (s == 1 and l[i] <= res["stop"]) or (s == -1 and h[i] >= res["stop"]):
            res.update(status="stopped", exit_i=i); return res
        if (s == 1 and h[i] >= res["target"]) or (s == -1 and l[i] <= res["target"]):
            res.update(status="target hit", exit_i=i); return res
    res["status"] = "in play"
    return res


def signals(h, l, c, a, k=2.0, names=None):
    """Backtest signals per pattern name: (side[i], stop[i], target[i]) decided on bar i's close (entry next open)."""
    n = len(c)
    out = {nm: (np.zeros(n, np.int64), np.full(n, np.nan), np.full(n, np.nan)) for nm in (names or NAMES)}
    for P in find(h, l, c, a, k, names):
        side_a, stop_a, tgt_a = out[P["name"]]
        if P["ready"] >= n - 1:
            continue
        w = walk(P, h, l, c, min(n, P["end"] + 1) if not P.get("entry_now") else P["ready"] + 1)
        if w["break_i"] is None or w["break_i"] >= n - 1:
            continue
        i = w["break_i"]
        if side_a[i] == 0:
            side_a[i], stop_a[i], tgt_a[i] = w["side"], w["stop"], w["target"]
    return out


# ------------------------------------------------------------------ for the chart
def for_chart(times, o, h, l, c, k=2.0, keep=10, since=None):
    """Recent patterns on these candles, newest first, in chart coordinates (unix seconds)."""
    h, l, c = (np.asarray(x, float) for x in (h, l, c))
    n = len(c)
    if n < 60:
        return []
    a = atr(h, l, c)
    pats = find(h, l, c, a, k)
    since = since if since is not None else max(0, n - 300)
    rows = []
    seen = set()
    for P in sorted(pats, key=lambda p: -p["ready"]):
        first = P["pivots"][0][0]
        if P["ready"] < since:
            continue
        key = (P["name"], P["pivots"][-1][0])
        if key in seen:
            continue
        seen.add(key)
        w = walk(P, h, l, c, n)
        stop_i = w["exit_i"] if w["exit_i"] is not None else (w["break_i"] if w["break_i"] is not None else min(P["end"], n - 1))
        stop_i = max(stop_i, P["pivots"][-1][0] + 1)
        stop_i = min(stop_i, n - 1)
        T = lambda i: int(times[int(max(0, min(n - 1, i)))])          # noqa: E731
        lines = []
        for ln in P["draw"]:
            i1, p1, i2, p2 = ln
            end_i = stop_i if (ln is P["up"] or ln is P["dn"]) else i2
            if end_i <= i1:
                end_i = i2
            lines.append([{"time": T(i1), "value": float(p1)}, {"time": T(end_i), "value": float(line_at(ln, end_i))}])
        g = GUIDE[P["name"]]
        rows.append({"name": P["name"], "label": g["label"], "group": g["group"], "bias": g["bias"], "status": w["status"],
                     "known_at": T(P["ready"]), "start": T(first), "pivots": [{"time": T(x), "value": p} for x, p in P["pivots"]],
                     "lines": lines, "side": {1: "long", -1: "short"}.get(w["side"]), "break_time": T(w["break_i"]) if w["break_i"] is not None else None,
                     "entry": w["entry"], "stop": w["stop"], "target": w["target"], "exit_time": T(w["exit_i"]) if w["exit_i"] is not None else None,
                     "watch_up": float(line_at(P["up"], n - 1)) if P["up"] and w["status"] == "forming" else None,
                     "watch_dn": float(line_at(P["dn"], n - 1)) if P["dn"] and w["status"] == "forming" else None,
                     "stop_l": P["stop_l"], "stop_s": P["stop_s"], "height": P["height"]})
        if len(rows) >= keep:
            break
    return rows
