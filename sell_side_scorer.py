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
    if cl is None or len(cl) < 20: return {}
    # Ensure hi/lo/vol are valid series — fall back to close if needed
    if hi is None or len(hi) < len(cl): hi = cl
    if lo is None or len(lo) < len(cl): lo = cl
    if vol is None or len(vol) < len(cl): vol = pd.Series(1e6, index=cl.index)
    df = pd.DataFrame({"close":cl,"high":hi,"low":lo,"volume":vol})
    # Use available history for high — 252d if possible, else max available
    lookback = min(252, len(df)-1)
    df["high252"]   = df["close"].rolling(lookback).max().shift(1)
    df["dist"]      = (df["close"] - df["high252"]) / df["high252"].replace(0, float('nan'))
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
    """
    Continuous sell scoring — no flat clusters.
    Each signal contributes a smooth 0-to-max score based on magnitude,
    not a binary on/off weight. Total is then adjusted by market context.

    Validated signal weights (unchanged from backtest):
      near_high:          35pts max  (-15.8% at 252d)
      cmf_neg_near_high:  30pts max  (-9.1% at 252d)
      sma_atr_gt35:       20pts max  (-13.4% at 252d)
      rv_z2:              10pts max  (-1.4% at 30d)
    """
    score = 0.0; flags = []; caution = []

    # ── Signal 1: Distance from 252d high — continuous 0→35 ──────────
    # dist is negative (e.g. -0.02 = 2% below high, -0.25 = 25% below)
    # Score peaks at 0% (at high) and fades linearly to 0 at -10% and beyond
    dist = sell_sigs.get("dist")  # float, e.g. -2.5 means 2.5% below high
    near_high = sell_sigs.get("near_high", False)
    if dist is not None:
        # dist is already *100 (percentage points) from compute_sell_signals
        dist_pct = dist  # e.g. -2.5
        if dist_pct > -10:  # within 10% of high
            # Linear: 0% below = 35pts, 10% below = 0pts
            near_score = max(0, 35 * (1 + dist_pct / 10))
            score += near_score
            if near_score >= 20:
                flags.append(f"near_high({dist_pct:+.1f}%)+{near_score:.0f}")
            elif near_score >= 5:
                caution.append(f"approaching_high({dist_pct:+.1f}%)")

    # ── Signal 2: CMF — continuous 0→30 ──────────────────────────────
    # cmf_20 ranges roughly -1 to +1; negative = distribution; only counts near high
    cmf_val = sell_sigs.get("cmf_20")
    if cmf_val is not None and near_high:
        if cmf_val < 0:
            # Linear: cmf=-1.0 = 30pts, cmf=0 = 0pts
            cmf_score = min(30, abs(cmf_val) * 30)
            score += cmf_score
            if cmf_score >= 5:
                flags.append(f"CMF_dist({cmf_val:+.3f})+{cmf_score:.0f}")

    # ── Signal 3: ATR extension — continuous 0→20 ────────────────────
    # sma_atr_dist: how many ATRs above 200d MA; 3.5+ = overextended
    # Only meaningful when also near high
    atr_dist = sell_sigs.get("sma_atr_dist")
    if atr_dist is not None and near_high:
        if atr_dist > 2.0:
            # Linear: 2.0 ATRs = 0pts, 5.0+ ATRs = 20pts
            atr_score = min(20, max(0, (atr_dist - 2.0) / 3.0 * 20))
            score += atr_score
            if atr_score >= 3:
                flags.append(f"ATR_ext({atr_dist:.1f})+{atr_score:.0f}")
    elif sell_sigs.get("sma_atr_gt35") and not near_high:
        caution.append("ATR_ext(not near high)")

    # ── Signal 4: RV Z-score — continuous 0→10 ───────────────────────
    # rv_z: z-score of 20d realised vol vs 3yr mean; >2 = elevated
    rv_z = sell_sigs.get("rv_z")
    if rv_z is not None and rv_z > 1.0:
        rv_score = min(10, max(0, (rv_z - 1.0) / 2.0 * 10))
        score += rv_score
        if rv_score >= 2:
            flags.append(f"RVz({rv_z:.1f})+{rv_score:.0f}")

    # ── Caution flags (buy continuation signals — reduce urgency) ─────
    if sell_sigs.get("wrsi_80"):   caution.append("wRSI>80(momentum)")
    elif sell_sigs.get("wrsi_75"): caution.append("wRSI>75(momentum)")
    if sell_sigs.get("rv_z3"):     caution.append("RVz>3(buy_cont)")

    # ── Market context multiplier (not additive — avoids flat clusters) ─
    # CNN greed boosts score proportionally; sector extension adds small boost
    cnn = market.get("cnn_score", 50)
    cnn_mult = 1.0 + max(0, (cnn - 50) / 50) * 0.4  # 1.0x at CNN=50, 1.4x at CNN=100
    if cnn > 75:
        flags.append(f"CNN{cnn:.0f}×{cnn_mult:.2f}")

    sector_boost = 0
    for sector in TICKER_SECTORS.get(ticker, []):
        if sector_states.get(sector):
            sector_boost = 5; flags.append(f"{sector}_ext+5"); break

    if market.get("sp500_extended"):
        sector_boost += 5; flags.append("SP500_ext+5")
    if market.get("buffett_extended"):
        sector_boost += 5; flags.append("Buffett_ext+5")

    score = min(100, round(score * cnn_mult + sector_boost, 1))

    if   score >= EXIT_T:   action = "EXIT"
    elif score >= REDUCE_T: action = "REDUCE"
    elif score >= TRIM_T:   action = "TRIM"
    else:                   action = "HOLD"

    return score, action, " | ".join(flags) or "—", " | ".join(caution) or "—"

def score_all(universe_rows, market, sector_states):
    """
    Score every ticker in the universe for sell signals.
    universe_rows: list of dicts, each must have keys:
        ticker (str), cl (pd.Series), hi (pd.Series|None), lo (pd.Series|None), vol (pd.Series|None)
    Returns same list with sell_score, sell_action, sell_flags, sell_caution added to each row.

    Usage in signal_scanner.py (replace per-ticker sell scoring loop with):
        from sell_side_scorer import score_all, fetch_market_signals, fetch_sector_signals
        market = fetch_market_signals()
        sectors = fetch_sector_signals()
        rows = score_all(universe_rows, market, sectors)
        rows = apply_portfolio_cap(rows)
    """
    for row in universe_rows:
        try:
            sigs = compute_sell_signals(
                row.get("cl"), row.get("hi"), row.get("lo"), row.get("vol")
            )
        except Exception as e:
            print(f"  sell signals failed for {row.get('ticker','?')}: {e}")
            sigs = {}
        sc, action, flags, caution = score_ticker(row.get("ticker",""), sigs, market, sector_states)
        row["sell_score"]   = sc
        row["sell_action"]  = action
        row["sell_flags"]   = flags
        row["sell_caution"] = caution
        # Pass through raw signal fields for dashboard display
        row["sell_dist"]       = sigs.get("dist")
        row["sell_weekly_rsi"] = sigs.get("weekly_rsi")
        row["sell_cmf"]        = sigs.get("cmf_20")
        row["sell_rv_z"]       = sigs.get("rv_z")
        row["near_high"]       = sigs.get("near_high", False)
    return universe_rows

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
