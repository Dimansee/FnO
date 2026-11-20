"""Dhan adapter against mocked responses in Dhan's documented shapes (not wired into Market)."""
import os, sys
from datetime import date
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
from fno import dhan as D, brokers as B

class R:
    def __init__(s, j, code=200): s.j, s.status_code, s.text = j, code, str(j)
    def json(s): return s.j

def mk(responses):
    d = D.Dhan("1000", "tok")
    calls = []
    def post(url, json=None, timeout=None):
        calls.append((url, json)); return responses[url.replace(D.BASE, "")]
    d.s.post = post
    return d, calls

def test_keys():
    d = D.Dhan("1", "t")
    assert d.underlying_key({"symbol": "NIFTY"}) == "IDX_I|13"
    assert d.vix_key() == "IDX_I|21"

def test_candles1_ist_first_bar():
    ts = [1700000100 + 60 * i for i in range(3)]      # epoch seconds
    ts[0] = int(pd.Timestamp("2026-11-20 09:15", tz="Asia/Kolkata").timestamp()); ts = [ts[0] + 60 * i for i in range(3)]
    d, calls = mk({"/charts/intraday": R({"open": [1, 2, 3], "high": [2, 3, 4], "low": [0, 1, 2], "close": [1.5, 2.5, 3.5], "volume": [10, 0, 5], "timestamp": ts})})
    B._today1.clear()
    df = d.candles1("IDX_I|13")
    assert f"{df.index[0]:%H:%M}" == "09:15" and len(df) == 3 and df.close.iloc[-1] == 3.5
    assert calls[0][1]["securityId"] == "13" and calls[0][1]["exchangeSegment"] == "IDX_I" and calls[0][1]["interval"] == "1"

def test_expiries_and_chain():
    oc = {"last_price": 24500.5, "oc": {"24500.000000": {
        "ce": {"security_id": 111, "last_price": 100, "oi": 500, "previous_oi": 400, "volume": 9, "implied_volatility": 12.5,
               "top_bid_price": 99.5, "top_ask_price": 100.5, "top_bid_quantity": 75, "top_ask_quantity": 150, "greeks": {"delta": 0.5}},
        "pe": {"security_id": 222, "last_price": 90, "oi": 300, "previous_oi": 300, "volume": 4, "implied_volatility": 13,
               "top_bid_price": 0, "top_ask_price": 0, "top_bid_quantity": 0, "top_ask_quantity": 0, "greeks": {"delta": -0.5}}}}}
    d, calls = mk({"/optionchain/expirylist": R({"data": ["2026-11-27", "2026-11-24"]}), "/optionchain": R({"data": oc})})
    exps, lot = d.expiries("IDX_I|13")
    assert exps == [date(2026, 11, 24), date(2026, 11, 27)] and lot is None
    D._chain_cache.clear(); D._chain_last.clear()
    df = d.chain("IDX_I|13", exps[0])
    r = df.iloc[0]
    assert r.strike == 24500 and r.spot == 24500.5 and r.ce_key == "NSE_FNO|111" and r.ce_chg_oi == 100 and r.ce_delta == 0.5
    assert pd.isna(r.pe_bid) and r.ce_bid == 99.5 and r.ce_aq == 150
    d.chain("IDX_I|13", exps[0])
    assert sum(1 for c in calls if c[0].endswith("/optionchain")) == 1      # 3-second rule: second ask reuses the cache

def test_quotes():
    d, _ = mk({"/marketfeed/quote": R({"data": {"IDX_I": {"13": {"last_price": 24500, "ohlc": {"close": 24400}}},
                                               "NSE_FNO": {"111": {"last_price": 100, "ohlc": {"close": 90},
                                                                   "depth": {"buy": [{"price": 99.5}], "sell": [{"price": 100.5}]}}}}})})
    q = d.quotes(["IDX_I|13", "NSE_FNO|111"])
    assert q["IDX_I|13"]["prev_close"] == 24400 and q["NSE_FNO|111"]["bid"] == 99.5 and q["NSE_FNO|111"]["ask"] == 100.5

def test_errors():
    B._today1.clear()
    d, _ = mk({"/charts/intraday": R({}, 401)})
    try: d.candles1("IDX_I|13"); assert False
    except B.AuthExpired: pass
