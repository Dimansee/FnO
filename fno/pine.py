"""Pine Script (TradingView, v5) for a portfolio-backtest setup: the Autopilot rules of one index with the chosen sizing,
confidence, big-run skip and exit / target style, so the same strategy can be watched on a live TradingView chart.

What carries over exactly: Noise band (1.75x the 14-session time-of-day move, half-hour checks 09:45-14:30, close beyond
band and VWAP, stop 2 ATR, 4R target, band/VWAP trail), Camarilla (H4/L4 cross 09:20-13:00, stop H3/L3 kept 1-3 ATR, stop to
entry at +1R, 45-min time stop), Fibonacci 0.618 (same with pivot +/- 0.618 / 0.382 x range), trend-day catcher (10:45-14:15,
band + VWAP + 80% of bars on that side, stop VWAP + 0.5 ATR), VIX filters (11 / 18 / 20 at the open), Budget days, 15:15
square-off, the big-run skip, weekly loss limit and Autopilot equity gates, and every exit variant of the Backtest tab.
Indices have no volume, so VWAP is the session TWAP of (H+L+C)/3, exactly as in the app.

What is approximated (said in the script header too): TradingView trades the INDEX, not the option. Position size =
lots x lot size x an option delta (default 0.55), so the P&L tracks the option's move but not its time decay or IV;
option costs other than brokerage are not in. One netted position per chart: a signal opposite to an open trade on the same
index is skipped (rare). The 15 / 60-minute confidence score uses TradingView's own higher-timeframe bars.
"""
from __future__ import annotations

from datetime import date

from . import config as C

TV = {"NIFTY": ("NSE:NIFTY", 65), "BANKNIFTY": ("NSE:BANKNIFTY", 30), "SENSEX": ("BSE:SENSEX", 20)}


def _b(v) -> str:
    return "true" if v else "false"


def _f(v, d=0.0) -> str:
    return repr(float(d if v is None else v))


def generate(p: dict, index: str, exit_ov: dict, exit_label: str, lot: int | None = None) -> dict:
    if index not in TV:
        raise ValueError("Pine Script is available for Nifty, Bank Nifty and Sensex.")
    tv_sym, lot0 = TV[index]
    lot = int(lot or lot0)
    strats = C.AUTOPILOT_SYM_STRATEGIES.get(index, C.AUTOPILOT_STRATEGIES)
    parts = list(exit_ov.get("partials") or [])
    parts = (parts + [(0, 0)] * 3)[:3]
    locks = list(exit_ov.get("lock_steps") or [])
    locks = (locks + [(0, 0)] * 2)[:2]
    gb = exit_ov.get("giveback") or (0, 0)
    conf = p.get("confidence", "standard")
    budget = ", ".join(f"timestamp(\"Asia/Kolkata\", {d.year}, {d.month}, {d.day}, 0, 0)" for d in sorted(C.BUDGET_DAYS))
    sub = {
        "INDEX": index, "TV_SYM": tv_sym, "LOT": str(lot), "CAPITAL": str(int(p.get("capital", 200000))),
        "SIZING": p.get("sizing", "compound"), "RISK": _f(p.get("risk_pct", 1.0)), "FIXED_LOTS": str(int(p.get("lots", 1))),
        "MAX_LOTS": str(int(p.get("max_lots", 0) or 0)), "CONF": conf, "CONF_IDX": _b(index in C.CONVICTION_SYMS),
        "BIGRUN": _b(p.get("big_run_skip", True)), "DD_HALF": _b(p.get("dd_half", True)), "BIGRUN_ATR": _f(C.BIG_RUN_ATR), "GATES": _b(p.get("gates", True)),
        "USE_NOISE": _b("noise" in strats), "USE_CAM": _b("camarilla" in strats), "USE_TREND": _b("trend" in strats), "USE_FIB": _b("fib" in strats),
        "NOISE_MULT": _f(C.NOISE_MULT), "NOISE_RR": _f(exit_ov["noise_rr"] if "noise_rr" in exit_ov else C.RR_NOISE),
        "NOISE_BE": _f(exit_ov.get("noise_be_r"), 0), "BE_R": _f(exit_ov["be_r"] if exit_ov.get("be_r") is not None else 1.0),
        "BE_LOCK": _f(exit_ov.get("be_lock", 0.0)),
        "L1R": _f(locks[0][0]), "L1L": _f(locks[0][1]), "L2R": _f(locks[1][0]), "L2L": _f(locks[1][1]),
        "GBS": _f(gb[0]), "GBR": _f(gb[1]),
        "T1R": _f(parts[0][0]), "T1P": _f(parts[0][1] * 100), "T2R": _f(parts[1][0]), "T2P": _f(parts[1][1] * 100),
        "T3R": _f(parts[2][0]), "T3P": _f(parts[2][1] * 100), "BE_T1": _b(exit_ov.get("be_after_t1", False)),
        "REV": exit_ov.get("rev_exit") or "none", "REV_R": _f(exit_ov.get("rev_after_R", 1.0)),
        "VIX_MIN": _f(C.VIX_MIN), "NOISE_VIX": _f(C.NOISE_VIX_MAX), "CAM_VIX": _f(C.CAM_VIX_MAX),
        "TREND_EQ": str(int(C.TREND_MIN_EQUITY)), "FIB_EQ": str(int(C.FIB_MIN_EQUITY)),
        "BUDGET": budget, "EXIT_LABEL": exit_label.replace('"', "'"), "DATE": date.today().isoformat(),
        "OTM": {-1: "1 strike ITM", 0: "ATM", 1: "1 strike OTM", 2: "2 strikes OTM"}.get(int(p.get("otm", -1)), "1 strike ITM"),
        "STEP": str({"NIFTY": 50, "BANKNIFTY": 100, "SENSEX": 100}[index]), "OTMN": str(int(p.get("otm", -1))),
        "EXPIRY": "monthly expiry" if index == "BANKNIFTY" else "nearest weekly expiry",
        "DELTA": _f({-1: 0.6, 0: 0.5, 1: 0.4, 2: 0.3}.get(int(p.get("otm", -1)), 0.55)),
    }
    code = TEMPLATE
    for k, v in sub.items():
        code = code.replace("{{" + k + "}}", v)
    assert "{{" not in code, "unfilled placeholder"
    return {"index": index, "tv_symbol": tv_sym, "filename": f"fno_{index.lower()}_{p.get('exit', 'current')}.pine", "code": code,
            "how_to": [f"Open TradingView, chart {tv_sym} on the 5-minute timeframe.",
                       "Pine Editor (bottom panel) -> delete what is there -> paste this script -> Save -> Add to chart.",
                       "Strategy Tester (bottom panel) shows the trades and results; Settings (gear icon) lets you change every input.",
                       "For live signals: Alerts -> Create alert -> Condition: this strategy -> 'alert() function calls only'."]}


TEMPLATE = r'''// FnO Trainer - {{INDEX}} Autopilot rules as a TradingView strategy (generated {{DATE}})
// Exit style: {{EXIT_LABEL}}
// Use on {{TV_SYM}}, 5-minute chart. Same rules as the app's Backtest tab: Noise band, Camarilla, trend-day catcher and
// Fibonacci 0.618 (as Autopilot trades this index), VIX filters, Budget days, 15:15 square-off, big-run skip, weekly loss
// limit and the chosen exits.
// APPROXIMATIONS: TradingView trades the index, not the option. Size = lots x lot size x option delta ({{OTM}}), so P&L
// follows the option's move but not time decay / IV / option taxes - expect the app's option backtest to be lower.
// One netted position per chart: a signal opposite to an open trade is skipped. Paper/learning use only - not advice.
//@version=5
strategy("FnO Trainer · {{INDEX}} · Autopilot", overlay=true, initial_capital={{CAPITAL}}, currency=currency.INR,
     process_orders_on_close=true, pyramiding=4, commission_type=strategy.commission.cash_per_order, commission_value=23.6,
     max_labels_count=500, max_lines_count=500, calc_on_every_tick=false)

// ------------------------------------------------------------------ inputs (defaults = your Backtest-tab setup)
grpS = "Size"
lotSize   = input.int({{LOT}}, "Lot size", group=grpS)
optDelta  = input.float({{DELTA}}, "Option delta (index -> option move)", minval=0.1, maxval=1.0, step=0.05, group=grpS)
sizing    = input.string("{{SIZING}}", "Lots", options=["compound", "fixed_risk", "fixed"], group=grpS,
     tooltip="compound = risk % of the current account; fixed_risk = risk % of the starting capital; fixed = a fixed number of lots")
riskPct   = input.float({{RISK}}, "Risk per trade %", minval=0.1, maxval=5, step=0.25, group=grpS)
fixedLots = input.int({{FIXED_LOTS}}, "Fixed lots", minval=1, group=grpS)
maxLots   = input.int({{MAX_LOTS}}, "Max lots per trade (0 = no cap)", minval=0, group=grpS)
confMode  = input.string("{{CONF}}", "Confidence sizing", options=["none", "standard", "strong"], group=grpS)
confIdx   = input.bool({{CONF_IDX}}, "Standard confidence applies to this index", group=grpS)
grpR = "Rules"
useNoise  = input.bool({{USE_NOISE}}, "Noise band", group=grpR)
useCam    = input.bool({{USE_CAM}}, "Camarilla breakout", group=grpR)
useTrend  = input.bool({{USE_TREND}}, "Trend-day catcher", group=grpR)
useFib    = input.bool({{USE_FIB}}, "Fibonacci 0.618 breakout", group=grpR)
gates     = input.bool({{GATES}}, "Autopilot gates (trend from Rs {{TREND_EQ}}, Fibonacci from Rs {{FIB_EQ}})", group=grpR)
bigRunOn  = input.bool({{BIGRUN}}, "Skip trades against a big 3-day run", group=grpR)
bigRunK   = input.float({{BIGRUN_ATR}}, "Big run = 3-day move of at least (daily ATRs)", group=grpR)
weeklyOn  = input.bool(true, "Weekly loss limit (4%, 15% below Rs 1 lakh)", group=grpR)
ddHalf    = input.bool({{DD_HALF}}, "Half risk while 15%+ below the account's peak", group=grpR)
vixSym    = input.symbol("NSE:INDIAVIX", "India VIX symbol", group=grpR)
noiseMult = input.float({{NOISE_MULT}}, "Noise band width (x usual move)", group=grpR)
grpX = "Exits"
noiseRR   = input.float({{NOISE_RR}}, "Noise target (R, 0 = none)", group=grpX)
noiseBeR  = input.float({{NOISE_BE}}, "Noise: stop to entry at (R, 0 = off)", group=grpX)
beR       = input.float({{BE_R}}, "Camarilla / Fib / trend: move stop at (R, 0 = off)", group=grpX)
beLock    = input.float({{BE_LOCK}}, "...to entry + (R)", group=grpX)
l1R       = input.float({{L1R}}, "Lock 1: once (R)", group=grpX, inline="l1")
l1L       = input.float({{L1L}}, "lock (R)", group=grpX, inline="l1")
l2R       = input.float({{L2R}}, "Lock 2: once (R)", group=grpX, inline="l2")
l2L       = input.float({{L2L}}, "lock (R)", group=grpX, inline="l2")
gbStart   = input.float({{GBS}}, "Trail: once (R)", group=grpX, inline="gb")
gbGive    = input.float({{GBR}}, "behind best by (R)", group=grpX, inline="gb")
t1R       = input.float({{T1R}}, "T1 at (R, 0 = off)", group=grpX, inline="t1")
t1P       = input.float({{T1P}}, "book %", group=grpX, inline="t1")
t2R       = input.float({{T2R}}, "T2 at (R)", group=grpX, inline="t2")
t2P       = input.float({{T2P}}, "book %", group=grpX, inline="t2")
t3R       = input.float({{T3R}}, "T3 at (R)", group=grpX, inline="t3")
t3P       = input.float({{T3P}}, "book %", group=grpX, inline="t3")
beAfterT1 = input.bool({{BE_T1}}, "Stop to entry after T1", group=grpX)
revExit   = input.string("{{REV}}", "Reversal exit", options=["none", "ema9", "vwap", "give50", "candle"], group=grpX)
revAfterR = input.float({{REV_R}}, "...only once the trade has been in profit by (R)", group=grpX)
showLv    = input.bool(true, "Show bands / levels", group="Display")
showTrade = input.bool(true, "Show trade boxes (option, entry, SL, targets)", group="Display")
strikeStep = input.int({{STEP}}, "Strike step", group="Option")
otmSteps  = input.int({{OTMN}}, "Strike: -1 = 1 ITM, 0 = ATM, 1 = 1 OTM", minval=-3, maxval=3, group="Option")

if timeframe.period != "5"
    runtime.error("Switch the chart to 5 minutes (timeframe button at the top: choose 5m). The rules are built on 5-minute candles.")

// ------------------------------------------------------------------ clock and session state
tz = "Asia/Kolkata"
barMod = hour(time, tz) * 60 + minute(time, tz)          // bar start, minutes after midnight IST
endMod = barMod + 5                                       // bar end
int k = math.floor((barMod - 555) / 5)                    // 0 = the 09:15 bar
isCheck = endMod % 30 == 15                               // closes on :15 / :45
newDay = ta.change(time("D")) != 0 or barstate.isfirst
newWeek = ta.change(weekofyear(time, tz)) != 0

budgetDays = array.from({{BUDGET}})
isBudget(t) =>
    bool hit = false
    for d in budgetDays
        if t >= d and t < d + 86400000
            hit := true
    hit

atr5 = ta.atr(14)
vixClose = request.security(vixSym, "5", close)

var float dOpen = na
var float dHigh = na
var float dLow = na
var float dClose = na
var float pH = na
var float pL = na
var float pC = na
var float vixOpen = na
var float twSum = 0.0
var int twN = 0
var int aboveN = 0
var int dayBars = 0
var array<float> dCloses = array.new_float()
var array<float> dTR = array.new_float()
var float runMove = na
var float runATR = na
var array<float> curMv = array.new_float(75, na)
var matrix<float> mvHist = matrix.new<float>()

if newDay
    if not na(dOpen)
        dtr = na(pC) ? dHigh - dLow : math.max(dHigh - dLow, math.max(math.abs(dHigh - pC), math.abs(dLow - pC)))
        array.push(dTR, dtr)
        array.push(dCloses, dClose)
        pH := dHigh
        pL := dLow
        pC := dClose
        matrix.add_row(mvHist, matrix.rows(mvHist), array.copy(curMv))
        if matrix.rows(mvHist) > 14
            matrix.remove_row(mvHist, 0)
    n = array.size(dCloses)
    runMove := na
    if n >= 4 and array.size(dTR) >= 15
        runMove := array.get(dCloses, n - 1) - array.get(dCloses, n - 4)
    if array.size(dTR) >= 14
        float s = 0.0
        for i = array.size(dTR) - 14 to array.size(dTR) - 1
            s += array.get(dTR, i)
        runATR := s / 14
    dOpen := open
    dHigh := high
    dLow := low
    twSum := 0.0
    twN := 0
    aboveN := 0
    dayBars := 0
    vixOpen := vixClose
    array.fill(curMv, na)
dHigh := math.max(dHigh, high)
dLow := math.min(dLow, low)
dClose := close
twSum += hlc3
twN += 1
vw = twSum / twN                                          // session TWAP (indices have no volume)
aboveN += close > vw ? 1 : 0
dayBars += 1
if k >= 0 and k < 75
    array.set(curMv, k, math.abs(close / dOpen - 1))

// usual move at this time of day over the last 14 sessions (needs 10)
sigmaAt(int kk) =>
    float s = 0.0
    int c = 0
    if kk >= 0 and kk < 75 and matrix.rows(mvHist) >= 10
        for r = 0 to matrix.rows(mvHist) - 1
            v = matrix.get(mvHist, r, kk)
            if not na(v)
                s += v
                c += 1
    c > 0 ? s / c : na
sig = sigmaAt(k)
bandUp = na(sig) or na(pC) ? na : math.max(dOpen, pC) * (1 + noiseMult * sig)
bandLo = na(sig) or na(pC) ? na : math.min(dOpen, pC) * (1 - noiseMult * sig)
camR = na(pC) ? na : (pH - pL) * 1.1
camH4 = pC + camR / 2
camH3 = pC + camR / 4
camL3 = pC - camR / 4
camL4 = pC - camR / 2
piv = (pH + pL + pC) / 3
fibH4 = piv + 0.618 * (pH - pL)
fibH3 = piv + 0.382 * (pH - pL)
fibL3 = piv - 0.382 * (pH - pL)
fibL4 = piv - 0.618 * (pH - pL)

// ------------------------------------------------------------------ confidence score (0-6): 20-EMA + trend line on 5 / 15 / 60 min
tl() =>
    e = ta.ema(close, 20)
    var float lo1 = na
    var float lo2 = na
    var int li1 = na
    var int li2 = na
    var float hi1 = na
    var float hi2 = na
    var int hi1i = na
    var int hi2i = na
    lo5 = ta.lowest(low, 5)
    hi5 = ta.highest(high, 5)
    if bar_index >= 4
        if low[2] == lo5
            lo1 := lo2
            li1 := li2
            lo2 := low[2]
            li2 := bar_index - 2
        if high[2] == hi5
            hi1 := hi2
            hi1i := hi2i
            hi2 := high[2]
            hi2i := bar_index - 2
    upT = not na(lo1) and lo2 > lo1 ? close > lo2 + (lo2 - lo1) / (li2 - li1) * (bar_index - li2) : false
    dnT = not na(hi1) and hi2 < hi1 ? close < hi2 + (hi2 - hi1) / (hi2i - hi1i) * (bar_index - hi2i) : false
    [(close > e ? 1 : 0) + (upT ? 1 : 0), (close < e ? 1 : 0) + (dnT ? 1 : 0)]
[u5, d5] = tl()
[u15, d15] = request.security(syminfo.tickerid, "15", tl())
[u60, d60] = request.security(syminfo.tickerid, "60", tl())
scoreUp = u5 + u15 + u60
scoreDn = d5 + d15 + d60
confMult(int sc) =>
    confMode == "strong" ? (sc >= 5 ? 2.0 : sc >= 3 ? 1.0 : 0.5) : confMode == "standard" and confIdx ? (sc >= 5 ? 1.33 : 0.67) : 1.0

// ------------------------------------------------------------------ account guards
realEq = strategy.initial_capital + strategy.netprofit
var float peakEq = strategy.initial_capital
peakEq := math.max(peakEq, realEq)
var float weekStart = strategy.initial_capital
var bool weekBlocked = false
if newWeek
    weekStart := realEq
    weekBlocked := false
if weeklyOn and realEq - weekStart <= -(weekStart >= 100000 ? 0.04 : 0.15) * weekStart
    weekBlocked := true

// ------------------------------------------------------------------ slots: 0 Noise, 1 Camarilla, 2 Trend, 3 Fibonacci
var string[] ids = array.from("NOISE", "CAM", "TREND", "FIB")
var bool[] isOpen = array.from(false, false, false, false)
var bool[] doneToday = array.from(false, false, false, false)
var int[] sideA = array.from(0, 0, 0, 0)
var float[] entA = array.from(0.0, 0.0, 0.0, 0.0)
var float[] riskA = array.from(0.0, 0.0, 0.0, 0.0)
var float[] slA = array.from(0.0, 0.0, 0.0, 0.0)
var float[] bestA = array.from(0.0, 0.0, 0.0, 0.0)
var float[] qtyA = array.from(0.0, 0.0, 0.0, 0.0)
var int[] inEnd = array.from(0, 0, 0, 0)
var bool[] halfR = array.from(false, false, false, false)
var line[] lnE = array.from(line(na), line(na), line(na), line(na))
var line[] lnS = array.from(line(na), line(na), line(na), line(na))
var line[] lnT = array.new_line(12)
var bool[] marks = array.from(false, false)        // [entry on this bar, exit on this bar]
array.set(marks, 0, false)
array.set(marks, 1, false)

// draw / extend / finish the lines of a slot
drawTrade(int s, int sd, float riskPts, int lots, string nm) =>
    e = close
    stp = e - sd * riskPts
    k0 = math.round(e / strikeStep) * strikeStep + sd * otmSteps * strikeStep
    opt = sd > 0 ? "CE" : "PE"
    pSL = optDelta * riskPts
    float tgt = s == 0 and noiseRR > 0 ? e + sd * noiseRR * riskPts : na
    txt = nm + ": BUY {{INDEX}} " + str.tostring(k0, "#") + " " + opt + " ({{EXPIRY}})\n" + str.tostring(lots) + " lot" + (lots > 1 ? "s" : "") + " · index entry " + str.tostring(e, "#.#") + "\nSL: index " + str.tostring(stp, "#.#") + " (option ≈ premium − ₹" + str.tostring(pSL, "#") + ")"
    if s == 0
        txt += "\nTarget: index " + (na(tgt) ? "none" : str.tostring(tgt, "#.#")) + "\nTrail: exit when a :15/:45 candle closes back inside the band / across VWAP"
    else
        txt += "\nStop to entry at " + str.tostring(e + sd * beR * riskPts, "#.#") + " (+" + str.tostring(beR) + "R)" + (s == 1 or s == 3 ? "\nTime stop: out after 45 min if not +0.5R" : "") + "\nNo fixed target - holds to 15:15"
    for j = 0 to 2
        tr0 = j == 0 ? t1R : j == 1 ? t2R : t3R
        if tr0 > 0
            txt += "\nT" + str.tostring(j + 1) + ": index " + str.tostring(e + sd * tr0 * riskPts, "#.#")
            array.set(lnT, s * 3 + j, line.new(bar_index, e + sd * tr0 * riskPts, bar_index + 1, e + sd * tr0 * riskPts, color=color.new(color.green, 20), style=line.style_dashed))
    if not na(tgt) and t1R <= 0
        array.set(lnT, s * 3, line.new(bar_index, tgt, bar_index + 1, tgt, color=color.new(color.green, 0), width=2))
    array.set(lnE, s, line.new(bar_index, e, bar_index + 1, e, color=color.new(color.gray, 0), style=line.style_dotted))
    array.set(lnS, s, line.new(bar_index, stp, bar_index + 1, stp, color=color.new(color.red, 0), width=2))
    label.new(bar_index, sd > 0 ? low : high, txt, style=sd > 0 ? label.style_label_up : label.style_label_down, color=sd > 0 ? color.new(color.green, 10) : color.new(color.red, 10), textcolor=color.white, size=size.small)
    true

extendTrade(int s, float sl) =>
    le = array.get(lnE, s)
    if not na(le)
        line.set_x2(le, bar_index)
        ls = array.get(lnS, s)
        line.set_x2(ls, bar_index)
        line.set_y1(ls, sl)
        line.set_y2(ls, sl)
        for j = 0 to 2
            lt = array.get(lnT, s * 3 + j)
            if not na(lt)
                line.set_x2(lt, bar_index)
    true

finishTrade(int s, string why) =>
    if not na(array.get(lnE, s))
        sd = array.get(sideA, s)
        label.new(bar_index, sd > 0 ? high : low, "EXIT " + array.get(ids, s) + ": " + why + "\nindex " + str.tostring(close, "#.#"), style=sd > 0 ? label.style_label_down : label.style_label_up, color=color.new(color.gray, 10), textcolor=color.white, size=size.tiny)
    array.set(lnE, s, line(na))
    array.set(lnS, s, line(na))
    for j = 0 to 2
        array.set(lnT, s * 3 + j, line(na))
    true
if newDay
    for s = 0 to 3
        array.set(doneToday, s, false)

openQty(string id) =>
    float q = 0.0
    if strategy.opentrades > 0
        for i = 0 to strategy.opentrades - 1
            if strategy.opentrades.entry_id(i) == id
                q += math.abs(strategy.opentrades.size(i))
    q

// place / refresh the exit orders of a slot: up to 3 target pieces + the rest, all sharing the stop.
// Pieces already filled (targets fill in order) are not placed again.
placeExits(int s, float qOpen) =>
    id = array.get(ids, s)
    sd = array.get(sideA, s)
    e = array.get(entA, s)
    r = array.get(riskA, s)
    q = array.get(qtyA, s)
    sl = array.get(slA, s)
    tgt = s == 0 and noiseRR > 0 ? e + sd * noiseRR * r : na
    float used = 0.0
    filled = q - qOpen
    tR = array.from(t1R, t2R, t3R)
    tP = array.from(t1P, t2P, t3P)
    for j = 0 to 2
        if array.get(tR, j) > 0 and array.get(tP, j) > 0
            pq = q * array.get(tP, j) / 100
            used += pq
            if used > filled + q * 0.001
                lim = e + sd * array.get(tR, j) * r
                strategy.exit(id + "_T" + str.tostring(j + 1), id, qty=pq, limit=(na(tgt) ? lim : (sd > 0 ? math.min(lim, tgt) : math.max(lim, tgt))), stop=sl, comment=id + " T" + str.tostring(j + 1) + "/SL")
    if q - used > q * 0.001
        strategy.exit(id + "_R", id, qty=q - used, limit=tgt, stop=sl, comment=id + " exit")
    true

closeSlot(int s, string why) =>
    strategy.close(array.get(ids, s), comment=why)
    if showTrade
        finishTrade(s, why)
    array.set(marks, 1, true)
    array.set(isOpen, s, false)
    alert("{{INDEX}} " + array.get(ids, s) + " exit: " + why + " at " + str.tostring(close), alert.freq_once_per_bar_close)
    true

lotsFor(int sd, float riskPts) =>
    rl = optDelta * riskPts * lotSize
    base = sizing == "compound" ? realEq : strategy.initial_capital
    m = confMult(sd > 0 ? scoreUp : scoreDn) * (ddHalf and realEq < 0.85 * peakEq ? 0.5 : 1.0)
    int lots = sizing == "fixed" ? fixedLots : int(math.floor(base * riskPct / 100 * m / rl))
    if lots == 0 and sizing != "fixed"
        lots := rl <= 0.15 * realEq ? 1 : 0
    if maxLots > 0
        lots := math.min(lots, maxLots)
    lots

// 1 = entered, 0 = skipped (signal used up), -1 = size rounds to zero (keep looking, as the app does)
tryEnter(int s, int sd, float riskPts, string nm) =>
    bool ok = true
    netSide = strategy.position_size > 0 ? 1 : strategy.position_size < 0 ? -1 : 0
    if netSide != 0 and netSide != sd
        ok := false
    if ok and bigRunOn and not na(runMove) and not na(runATR) and math.abs(runMove) >= bigRunK * runATR and math.sign(runMove) != sd
        ok := false
    lots = ok ? lotsFor(sd, riskPts) : 0
    if ok and lots > 0
        q = lots * lotSize * optDelta
        id = array.get(ids, s)
        array.set(isOpen, s, true)
        array.set(sideA, s, sd)
        array.set(entA, s, close)
        array.set(riskA, s, riskPts)
        array.set(slA, s, close - sd * riskPts)
        array.set(bestA, s, 0.0)
        array.set(qtyA, s, q)
        array.set(inEnd, s, endMod)
        array.set(halfR, s, false)
        strategy.entry(id, sd > 0 ? strategy.long : strategy.short, qty=q, comment=nm + " " + str.tostring(lots) + " lot" + (lots > 1 ? "s" : ""))
        placeExits(s, q)
        if showTrade
            drawTrade(s, sd, riskPts, lots, nm)
        array.set(marks, 0, true)
        alert("{{INDEX}} " + nm + (sd > 0 ? " BUY CALL" : " BUY PUT") + " at " + str.tostring(close) + ", stop " + str.tostring(close - sd * riskPts) + ", " + str.tostring(lots) + " lots", alert.freq_once_per_bar_close)
    ok and lots > 0 ? 1 : ok ? -1 : 0

ema9 = ta.ema(close, 9)

// ------------------------------------------------------------------ manage open slots (stops / targets filled by the broker emulator)
for s = 0 to 3
    if array.get(isOpen, s)
        id = array.get(ids, s)
        q = openQty(id)
        if q <= 0
            array.set(isOpen, s, false)
            if showTrade
                finishTrade(s, "stop / target hit")
            array.set(marks, 1, true)
        else if endMod > array.get(inEnd, s)
            sd = array.get(sideA, s)
            e = array.get(entA, s)
            r = array.get(riskA, s)
            fav = sd > 0 ? high : low
            best = math.max(array.get(bestA, s), sd * (fav - e) / r)
            array.set(bestA, s, best)
            sl = array.get(slA, s)
            bR = s == 0 ? noiseBeR : beR
            bL = s == 0 ? 0.0 : beLock
            if bR > 0 and best >= bR
                sl := sd > 0 ? math.max(sl, e + bL * r) : math.min(sl, e - bL * r)
            if l1R > 0 and best >= l1R
                sl := sd > 0 ? math.max(sl, e + l1L * r) : math.min(sl, e - l1L * r)
            if l2R > 0 and best >= l2R
                sl := sd > 0 ? math.max(sl, e + l2L * r) : math.min(sl, e - l2L * r)
            if gbStart > 0 and best >= gbStart
                sl := sd > 0 ? math.max(sl, e + (best - gbGive) * r) : math.min(sl, e - (best - gbGive) * r)
            if beAfterT1 and t1R > 0 and q < array.get(qtyA, s) * 0.999
                sl := sd > 0 ? math.max(sl, e) : math.min(sl, e)
            if sl != array.get(slA, s)
                array.set(slA, s, sl)
                placeExits(s, q)
            if showTrade
                extendTrade(s, sl)
            if best >= 0.5
                array.set(halfR, s, true)
            string why = na
            if (s == 1 or s == 3) and not array.get(halfR, s) and endMod - array.get(inEnd, s) >= 45
                why := "Time stop (45 min)"
            if s == 0 and isCheck and not na(bandUp)
                lvl = sd > 0 ? math.max(bandUp, vw) : math.min(bandLo, vw)
                if sd * (close - lvl) < 0
                    why := "Trailing exit (band/VWAP)"
            if na(why) and revExit != "none" and best >= revAfterR
                rng = high - low
                if revExit == "ema9" and sd * (close - ema9) < 0
                    why := "Reversal exit (9-EMA)"
                else if revExit == "vwap" and sd * (close - vw) < 0
                    why := "Reversal exit (VWAP)"
                else if revExit == "give50" and sd * (close - e) / r <= best * 0.5
                    why := "Reversal exit (half given back)"
                else if revExit == "candle" and rng > 0 and sd * (close - open) <= -0.6 * rng and rng >= 0.5 * atr5
                    why := "Reversal exit (opposite candle)"
            if endMod >= 915
                why := "Square-off 15:15"
            if not na(why)
                closeSlot(s, why)

// ------------------------------------------------------------------ entries (signals on the close of the 5-minute bar)
canTrade = not isBudget(time) and not weekBlocked and not na(vixOpen) and vixOpen >= {{VIX_MIN}} and not na(pC) and k >= 0 and k < 75
mainBusy = array.get(isOpen, 0) or array.get(isOpen, 1)

// Noise band: half-hour checks 09:45-14:30
if canTrade and useNoise and not array.get(doneToday, 0) and vixOpen < {{NOISE_VIX}} and isCheck and endMod >= 585 and endMod <= 870 and not na(bandUp)
    sd = close > bandUp and close > vw ? 1 : close < bandLo and close < vw ? -1 : 0
    if sd != 0
        res = mainBusy ? 0 : tryEnter(0, sd, 2.0 * atr5, "Noise")
        if res != -1
            array.set(doneToday, 0, true)

// Camarilla: 5-min close crossing yesterday's H4 / L4, 09:20-13:00
if canTrade and useCam and not array.get(doneToday, 1) and vixOpen <= {{CAM_VIX}} and dayBars >= 2 and endMod <= 780
    sd = close[1] <= camH4 and close > camH4 ? 1 : close[1] >= camL4 and close < camL4 ? -1 : 0
    if sd != 0
        mainBusy2 = array.get(isOpen, 0) or array.get(isOpen, 1)
        ref = sd > 0 ? camH3 : camL3
        res = mainBusy2 ? 0 : tryEnter(1, sd, math.min(math.max(math.abs(close - ref), atr5), 3 * atr5), "Camarilla")
        if res != -1
            array.set(doneToday, 1, true)

// Trend-day catcher (own slot): half-hour checks 10:45-14:15
if canTrade and useTrend and not array.get(doneToday, 2) and (not gates or realEq >= {{TREND_EQ}}) and isCheck and endMod >= 645 and endMod <= 855 and not na(bandUp)
    share = aboveN * 1.0 / dayBars
    sd = close > bandUp and close > vw and share >= 0.8 ? 1 : close < bandLo and close < vw and 1 - share >= 0.8 ? -1 : 0
    if sd != 0
        res = tryEnter(2, sd, math.min(math.max(math.abs(close - vw) + 0.5 * atr5, atr5), 3 * atr5), "Trend")
        if res != -1
            array.set(doneToday, 2, true)

// Fibonacci 0.618 (own slot): close crossing pivot +/- 0.618 x range, stop at 0.382, 09:20-13:00
if canTrade and useFib and not array.get(doneToday, 3) and (not gates or realEq >= {{FIB_EQ}}) and vixOpen <= {{CAM_VIX}} and dayBars >= 2 and endMod <= 780
    sd = close[1] <= fibH4 and close > fibH4 ? 1 : close[1] >= fibL4 and close < fibL4 ? -1 : 0
    if sd != 0
        ref = sd > 0 ? fibH3 : fibL3
        res = tryEnter(3, sd, math.min(math.max(math.abs(close - ref), atr5), 3 * atr5), "Fib")
        if res != -1
            array.set(doneToday, 3, true)

// ------------------------------------------------------------------ display
plot(showLv ? bandUp : na, "Noise band up", color.new(color.orange, 30), style=plot.style_linebr)
plot(showLv ? bandLo : na, "Noise band low", color.new(color.orange, 30), style=plot.style_linebr)
plot(showLv ? vw : na, "VWAP (TWAP)", color.new(color.aqua, 40), style=plot.style_linebr)
plot(showLv and useCam ? camH4 : na, "Camarilla H4", color.new(color.green, 50), style=plot.style_stepline)
plot(showLv and useCam ? camL4 : na, "Camarilla L4", color.new(color.red, 50), style=plot.style_stepline)
plot(showLv and useFib ? fibH4 : na, "Fib +0.618", color.new(color.lime, 60), style=plot.style_stepline)
plot(showLv and useFib ? fibL4 : na, "Fib -0.618", color.new(color.maroon, 60), style=plot.style_stepline)
bgcolor(showTrade and array.get(marks, 0) ? color.new(color.blue, 80) : showTrade and array.get(marks, 1) ? color.new(color.gray, 85) : na, title="Entry / exit bars")
var table info = table.new(position.top_right, 2, 6, bgcolor=color.new(color.black, 20), border_width=1)
if barstate.islast
    runTxt = na(runMove) or na(runATR) ? "-" : str.tostring(runMove, "#") + " pts (" + str.tostring(math.abs(runMove) / runATR, "#.0") + "x ATR)"
    table.cell(info, 0, 0, "FnO Trainer · {{INDEX}}", text_color=color.white)
    table.cell(info, 1, 0, "{{EXIT_LABEL}}", text_color=color.silver, text_size=size.small)
    table.cell(info, 0, 1, "India VIX at open", text_color=color.silver)
    table.cell(info, 1, 1, str.tostring(vixOpen, "#.##"), text_color=color.white)
    table.cell(info, 0, 2, "Confidence CALL / PUT", text_color=color.silver)
    table.cell(info, 1, 2, str.tostring(scoreUp) + " / " + str.tostring(scoreDn) + " of 6", text_color=color.white)
    table.cell(info, 0, 3, "3-day move", text_color=color.silver)
    table.cell(info, 1, 3, runTxt, text_color=color.white)
    table.cell(info, 0, 4, "Weekly limit", text_color=color.silver)
    table.cell(info, 1, 4, weekBlocked ? "hit - no new trades" : "ok", text_color=weekBlocked ? color.red : color.lime)
    table.cell(info, 0, 5, "Account (realised)", text_color=color.silver)
    table.cell(info, 1, 5, str.tostring(realEq, "#,###"), text_color=color.white)
'''
