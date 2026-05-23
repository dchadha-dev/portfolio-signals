"""
sell_side_scorer.py
Standalone sell-side scoring module.
Called by signal_scanner.py on every daily run.

Validated signals (24 walk-forward windows, dual-seed, 2026-05-19):
  near_high:         -15.8% at 252d, p=0.000  weight=35
  cmf_neg_near_high: -9.1%  at 252d, p=0.000  weight=30
  sma_atr_gt35:      -13.4% at 252d, p=0.001  weight=20
  rv_z2:             -1.4%  at 30d,  p=0.000  weight=10 (short-term only)
"""

import pandas as pd
import numpy as np
import requests
import yfinance as yf
from datetime import datetime, timedelta

SELL_WEIGHTS = {
    "near_high":          35,
    "cmf_neg_near_high":  30,
    "sma_atr_gt35":       20,
    "rv_z2":              10,
    "cnn_greed_75":       15,
    "cnn_greed_90":       25,
    "sp500_extended":     10,
    "buffett_extended":   10,
    "sector_ext":         10,
}
TRIM_T = 35; REDUCE_T = 55; EXIT_T = 70
PORTFOLIO_CAP_PCT = 0.10

SECTOR_ETFS = {
    "ai":"SMH","semiconductor":"SMH","tech":"QQQ","cloud":"QQQ","software":"QQQ",
    "crypto":"BLOK","ev":"QQQ","luxury":"IEV","healthcare":"XLV","fintech":"QQQ",
    "energy":"XLE","reit":"VNQ","europe":"IEV","japan":"EWJ","india":"INDA",
    "gold":"GLD","us_equity":"SPY","global":"URTH","international":"VXUS",
}

TICKER_SECTORS = {
    "NVDA":["ai","semiconductor","tech"],"AVGO":["ai","semiconductor","tech"],
    "AMAT":["semiconductor","industrial"],"ASML":["semiconductor","tech"],
    "AMD":["ai","semiconductor","tech"],"CRDO":["ai","semiconductor","tech"],
    "MU":["semiconductor","tech"],"TSM":["semiconductor","tech"],
    "NBIS":["ai","cloud","tech"],"CRWV":["ai","cloud","tech"],
    "MSFT":["ai","cloud","software","tech"],"GOOG":["ai","tech","cloud"],
    "META":["ai","tech","social"],"AMZN":["ai","cloud","ecommerce","tech"],
    "AAPL":["tech","consumer","hardware"],"ANET":["ai","networking","tech"],
    "DDOG":["ai","cloud","software","tech"],"INTU":["software","fintech"],
    "ADBE":["software","tech"],"TEAM":["software","tech"],
    "NFLX":["streaming","consumer","tech"],"SHOP":["ecommerce","fintech","tech"],
    "MELI":["ecommerce","fintech","latam"],"BKNG":["travel","consumer"],
    "TSLA":["ev","auto","tech"],"RACE":["luxury","auto","consumer"],
    "TM":["auto","japan"],"CPRT":["industrial","auto"],"PGR":["insurance","financial"],
    "UNH":["healthcare","insurance"],"FISV":["fintech","financial"],
    "COIN":["crypto","fintech"],"TTD":["ad_tech","tech"],"O":["reit","income"],
    "BRKB":["diversified","financial"],"NVO":["healthcare","pharma"],
    "RELX":["data","media"],"NEXT":["energy","lng"],
    "VOO":["us_equity","broad"],"VTI":["us_equity","broad"],
    "QQQ":["us_equity","tech","growth"],"TQQQ":["us_equity","tech","leveraged"],
    "SCHD":["dividend","income","us_equity"],"JEPI":["income","covered_call"],
    "GLD":["gold","commodity","macro"],"IEV":["europe","international"],
    "VXUS":["international","broad"],"VSS":["international","small_cap"],
    "URTH":["global","broad"],"AIQG":["ai","tech"],"QNTM.L":["quantum","tech"],
    "QTUM":["quantum","tech"],
}

def _calc_rsi(series, period=14):
    delta    = series.diff()
    avg_gain = delta.clip(lower=0).ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = (-delta).clip(lower=0).ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))

def compute_sell_signals(cl, hi, lo, vol):
    if cl is None or len(cl) < 100: return {}
    df = pd.DataFrame({"close":cl,"high":hi,"low":lo,"volume":vol})
    df["high252"]   = df["close"].rolling(252).max().shift(1)
    df["dist"]      = (df["close"] - df["high252"]) / df["high252"]
    df["near_high"] = df["dist"] > -0.05
    wkly = df["close"].resample("W").last().dropna()
    wrsi = _calc_rsi(wkly, 14)
    df["weekly_rsi"] = wrsi.reindex(df.index, method="ffill")
    df["wrsi_75"]    = df["weekly_rsi"] > 75
    df["wrsi_80"]    = df["weekly_rsi"] > 80
    df["sma200"]     = df["close"].rolling(200).mean()
    tr = pd.concat([
        df["high"]-df["low"],
        (df["high"]-df["close"].shift(1)).abs(),
        (df["low"]-df["close"].shift(1)).abs()
    ], axis=1).max(axis=1)
    df["atr14"]        = tr.rolling(14).mean()
    df["sma_atr_dist"] = (df["close"]-df["sma200"]) / df["atr14"].replace(0,float("nan"))
    df["sma_atr_gt35"] = df["sma_atr_dist"] > 3.5
    hl  = (df["high"]-df["low"]).replace(0,float("nan"))
    mfm = ((df["close"]-df["low"])-(df["high"]-df["close"])) / hl
    df["cmf_20"]            = (mfm*df["volume"]).rolling(20).sum() / df["volume"].rolling(20).sum()
    df["cmf_neg_near_high"] = (df["cmf_20"]<0) & df["near_high"]
    logr = np.log(df["close"]/df["close"].shift(1))
    df["rv20"]      = logr.rolling(20).std()*np.sqrt(252)
    df["rv_mean3y"] = df["rv20"].rolling(756).mean()
    df["rv_std3y"]  = df["rv20"].rolling(756).std()
    df["rv_z"]      = (df["rv20"]-df["rv_mean3y"]) / df["rv_std3y"].replace(0,float("nan"))
    df["rv_z2"]     = df["rv_z"] > 2.0
    df["rv_z3"]     = df["rv_z"] > 3.0
    df = df.dropna(subset=["dist"])
    if len(df)==0: return {}
    last = df.iloc[-1]
    def safe(v): return None if (isinstance(v,float) and v!=v) else v
    return {
        "dist":              round(float(last["dist"])*100,1),
        "near_high":         bool(last["near_high"]),
        "weekly_rsi":        safe(round(float(last["weekly_rsi"]),1)) if "weekly_rsi" in last.index else None,
        "wrsi_75":           bool(last.get("wrsi_75",False)),
        "wrsi_80":           bool(last.get("wrsi_80",False)),
        "sma_atr_dist":      safe(round(float(last["sma_atr_dist"]),2)) if "sma_atr_dist" in last.index else None,
        "sma_atr_gt35":      bool(last.get("sma_atr_gt35",False)),
        "cmf_20":            safe(round(float(last["cmf_20"]),3)) if "cmf_20" in last.index else None,
        "cmf_neg_near_high": bool(last.get("cmf_neg_near_high",False)),
        "rv_z":              safe(round(float(last["rv_z"]),2)) if "rv_z" in last.index else None,
        "rv_z2":             bool(last.get("rv_z2",False)),
        "rv_z3":             bool(last.get("rv_z3",False)),
    }

def fetch_sector_signals():
    end = datetime.today(); start = end - timedelta(days=3*365+60)
    unique_etfs = list(set(SECTOR_ETFS.values()))
    etf_ext = {}
    for etf in unique_etfs:
        try:
            df = yf.download(etf, start=start, end=end, interval="1d", auto_adjust=True, progress=False)
            if df is None or len(df)<100: continue
            cl   = (df["Close"][etf] if isinstance(df.columns,pd.MultiIndex) else df["Close"]).dropna()
            h252 = cl.rolling(252).max().shift(1)
            dist = (cl-h252)/h252
            logr = np.log(cl/cl.shift(1))
            rv20 = logr.rolling(20).std()*np.sqrt(252)
            rvm  = rv20.rolling(756).mean(); rvs = rv20.rolling(756).std()
            rvz  = (rv20-rvm)/rvs.replace(0,float("nan"))
            etf_ext[etf] = bool((dist>-0.10).iloc[-1] and (rvz>1.5).iloc[-1])
        except: etf_ext[etf] = False
    return {s: etf_ext.get(e,False) for s,e in SECTOR_ETFS.items()}

def fetch_market_signals():
    result = {"cnn_score":50,"cnn_label":"Neutral","sp500_extended":False,"buffett_extended":False}
    try:
        r = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
                        timeout=8, headers={"User-Agent":"Mozilla/5.0"})
        d = r.json()
        result["cnn_score"] = float(d["fear_and_greed"]["score"])
        result["cnn_label"] = d["fear_and_greed"]["rating"]
    except: pass
    try:
        spy = yf.download("SPY", period="2y", interval="1d", auto_adjust=True, progress=False)
        cl  = (spy["Close"]["SPY"] if isinstance(spy.columns,pd.MultiIndex) else spy["Close"]).dropna()
        sma200 = cl.rolling(200).mean()
        result["sp500_extended"] = bool((cl.iloc[-1]/sma200.iloc[-1]-1)>0.15)
        m5 = cl.rolling(1260).mean(); s5 = cl.rolling(1260).std()
        result["buffett_extended"] = bool(((cl.iloc[-1]-m5.iloc[-1])/s5.iloc[-1])>2.0)
    except: pass
    return result

def score_ticker(ticker, sell_sigs, market, sector_states):
    score=0; flags=[]; caution=[]
    near = sell_sigs.get("near_high", False)
    cmf  = sell_sigs.get("cmf_neg_near_high", False)
    atr  = sell_sigs.get("sma_atr_gt35", False)
    rv2  = sell_sigs.get("rv_z2", False)

    # near_high alone = caution only, no score — must have confluence
    # Mirrors buy side: Factor alone = watch, Factor+DFV = act
    if near and (cmf or atr):
        score+=SELL_WEIGHTS["near_high"]; flags.append(f"near_high+{SELL_WEIGHTS['near_high']}")
    elif near:
        caution.append("near_high(no confluence — watch only)")

    if cmf:
        score+=SELL_WEIGHTS["cmf_neg_near_high"]; flags.append(f"CMF_dist+{SELL_WEIGHTS['cmf_neg_near_high']}")
    if atr and near:   # ATR extension only meaningful when also near high
        score+=SELL_WEIGHTS["sma_atr_gt35"]; flags.append(f"ATR_ext+{SELL_WEIGHTS['sma_atr_gt35']}")
    elif atr:
        caution.append("ATR_ext(not near high)")
    if rv2:
        score+=SELL_WEIGHTS["rv_z2"]; flags.append(f"RVz>2_short+{SELL_WEIGHTS['rv_z2']}")
    if sell_sigs.get("wrsi_80"):   caution.append("wRSI>80(buy_cont)")
    elif sell_sigs.get("wrsi_75"): caution.append("wRSI>75(buy_cont)")
    if sell_sigs.get("rv_z3"):     caution.append("RVz>3(buy_cont)")
    for sector in TICKER_SECTORS.get(ticker,[]):
        if sector_states.get(sector):
            score+=SELL_WEIGHTS["sector_ext"]; flags.append(f"{sector}_ext+{SELL_WEIGHTS['sector_ext']}"); break
    cnn = market.get("cnn_score",50)
    if cnn>90:       score+=SELL_WEIGHTS["cnn_greed_90"]; flags.append(f"CNN>90+{SELL_WEIGHTS['cnn_greed_90']}")
    elif cnn>75:     score+=SELL_WEIGHTS["cnn_greed_75"]; flags.append(f"CNN>75+{SELL_WEIGHTS['cnn_greed_75']}")
    if market.get("sp500_extended"):  score+=SELL_WEIGHTS["sp500_extended"];  flags.append(f"SP500+{SELL_WEIGHTS['sp500_extended']}")
    if market.get("buffett_extended"): score+=SELL_WEIGHTS["buffett_extended"]; flags.append(f"Buffett+{SELL_WEIGHTS['buffett_extended']}")
    score = min(100, score)
    if   score>=EXIT_T:   action="EXIT"
    elif score>=REDUCE_T: action="REDUCE"
    elif score>=TRIM_T:   action="TRIM"
    else:                 action="HOLD"
    return score, action, " | ".join(flags) or "—", " | ".join(caution) or "—"

def apply_portfolio_cap(rows):
    total=len(rows); max_flag=max(1,int(total*PORTFOLIO_CAP_PCT))
    flagged=[r for r in rows if r.get("sell_action")!="HOLD"]
    if len(flagged)>max_flag:
        keep={r["ticker"] for r in sorted(flagged,key=lambda x:-x["sell_score"])[:max_flag]}
        for r in rows:
            if r.get("sell_action")!="HOLD" and r["ticker"] not in keep:
                r["sell_action"]="HOLD"; r["sell_capped"]=True
        print(f"Portfolio cap: {len(flagged)} → {max_flag} ({PORTFOLIO_CAP_PCT*100:.0f}% of {total})")
    else:
        print(f"Portfolio cap: {len(flagged)}/{total} flagged — within limit")
    return rows
