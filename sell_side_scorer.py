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
TRIM_T = 52; REDUCE_T = 65; EXIT_T = 78
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
    # Thai mutual funds
    "SCB_SP500":["us_equity","broad"],"SCB_NDQ":["us_equity","tech","growth"],
    "SCB_SEMI":["semiconductor","ai","tech"],"SCB_WORLD":["global","broad"],
    "SCB_GOLD":["gold","commodity","macro"],"SCB_NK225":["japan","international"],
    "SCB_SET50":["us_equity","broad"],"SCB_DJ":["us_equity","broad"],
    "SCB_AIEM":["international","asia"],"SCB_FINTECH":["fintech","tech"],
    "SCB_AUTO":["ai","tech","ev"],"SCB_INNOV":["tech","growth"],
    "SCB_GENO":["healthcare"],"SCB_CHINA":["international","asia"],
    "SCB_EV":["ev","tech"],"SCB_BUSAA":["us_equity","tech","growth"],
    "KT_INDIA":["india","international"],"KT_WORLD":["global","broad"],
    "KT_WTAI":["ai","tech","global"],"KT_BLOCK":["crypto","fintech"],
    "KT_TECH":["us_equity","tech","growth"],"KT_ESG":["global","broad"],
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
    """
    Sector sentiment model with conditional hierarchy.
    All signals computed vectorised with shift(1) to eliminate look-ahead bias —
    every condition uses only data available at prior day's close.

    Conditional hierarchy (ALL four must be true for entry signal):
      1. Macro Filter:     SPY > 200d SMA (regime gate — if false, tighten)
      2. Trend Check:      Sector_ETF / SPY RS line > its own 200d SMA
      3. Dip Detection:    Sector price Z-score < -2.0 (20d rolling)
      4. Confidence Check: Sector breadth EMA is stable or rising
                           (proxied by ETF 10d EMA slope > 0)

    Returns dict: sector_name -> {
        'entry_signal': bool,   # all four conditions met
        'macro_ok':     bool,
        'trend_ok':     bool,
        'dip_ok':       bool,
        'breadth_ok':   bool,
        'rs_vs_spy':    float,  # relative strength ratio
        'z_score':      float,  # 20d price z-score
    }
    """
    end   = datetime.today()
    start = end - timedelta(days=3 * 365 + 90)

    # ── Fetch SPY first (needed for macro filter and RS calc) ─────────
    spy_cl = None
    try:
        spy_raw = yf.download('SPY', start=start, end=end,
                              interval='1d', auto_adjust=True, progress=False)
        spy_cl  = (spy_raw['Close']['SPY'] if isinstance(spy_raw.columns, pd.MultiIndex)
                   else spy_raw['Close']).dropna()
    except Exception:
        pass

    # ── Macro filter: SPY > 200d SMA ──────────────────────────────────
    # shift(1) ensures we use yesterday's SMA — no look-ahead
    macro_ok = False
    spy_sma200 = None
    if spy_cl is not None and len(spy_cl) >= 200:
        spy_sma200 = spy_cl.rolling(200).mean().shift(1)
        macro_ok   = bool(spy_cl.iloc[-1] > spy_sma200.iloc[-1])

    results = {}
    unique_etfs = list(set(SECTOR_ETFS.values()))

    for etf in unique_etfs:
        try:
            raw = yf.download(etf, start=start, end=end,
                              interval='1d', auto_adjust=True, progress=False)
            if raw is None or len(raw) < 220:
                results[etf] = _empty_sector_result(macro_ok)
                continue

            cl = (raw['Close'][etf] if isinstance(raw.columns, pd.MultiIndex)
                  else raw['Close']).dropna()

            # ── Condition 2: Trend — RS line > 200d SMA ──────────────
            # RS = Sector_ETF / SPY. Use shift(1) on SMA to avoid look-ahead.
            trend_ok  = False
            rs_value  = float('nan')
            if spy_cl is not None:
                # Align on common index
                cl_aligned  = cl.reindex(spy_cl.index).dropna()
                spy_aligned = spy_cl.reindex(cl_aligned.index).dropna()
                cl_aligned  = cl_aligned.reindex(spy_aligned.index).dropna()
                if len(cl_aligned) >= 200:
                    rs_line    = cl_aligned / spy_aligned.replace(0, float('nan'))
                    rs_sma200  = rs_line.rolling(200).mean().shift(1)  # shift(1) = no look-ahead
                    rs_value   = round(float(rs_line.iloc[-1]), 4)
                    trend_ok   = bool(rs_line.iloc[-1] > rs_sma200.iloc[-1])

            # ── Condition 3: Dip — 20d Z-score < -2.0 ────────────────
            # Z = (price - 20d_mean) / 20d_std. shift(1) on mean and std.
            dip_ok  = False
            z_score = float('nan')
            if len(cl) >= 21:
                roll_mean = cl.rolling(20).mean().shift(1)
                roll_std  = cl.rolling(20).std().shift(1)
                z_series  = (cl - roll_mean) / roll_std.replace(0, float('nan'))
                z_score   = round(float(z_series.iloc[-1]), 3)
                dip_ok    = z_score < -2.0

            # ── Condition 4: Breadth — 10d EMA slope > 0 ─────────────
            # Proxy for sector breadth: ETF's own 10d EMA trending upward.
            # slope = EMA_today - EMA_yesterday (both available before today's open).
            breadth_ok = False
            if len(cl) >= 12:
                ema10      = cl.ewm(span=10, adjust=False).mean()
                ema_slope  = ema10.diff(1).shift(1)  # yesterday's slope — no look-ahead
                breadth_ok = bool(ema_slope.iloc[-1] > 0)

            entry_signal = macro_ok and trend_ok and dip_ok and breadth_ok

            results[etf] = {
                'entry_signal': entry_signal,
                'macro_ok':     macro_ok,
                'trend_ok':     trend_ok,
                'dip_ok':       dip_ok,
                'breadth_ok':   breadth_ok,
                'rs_vs_spy':    rs_value,
                'z_score':      z_score,
            }

        except Exception:
            results[etf] = _empty_sector_result(macro_ok)

    # Map ETF results back to sector names
    sector_results = {}
    for sector, etf in SECTOR_ETFS.items():
        sector_results[sector] = results.get(etf, _empty_sector_result(macro_ok))

    # Summary print
    entry_sectors = [s for s, v in sector_results.items() if v.get('entry_signal')]
    print(f'Sector signals: macro_ok={macro_ok} | '
          f'{len(entry_sectors)} sectors with entry signal: {entry_sectors[:5]}')

    return sector_results


def _empty_sector_result(macro_ok):
    return {
        'entry_signal': False,
        'macro_ok':     macro_ok,
        'trend_ok':     False,
        'dip_ok':       False,
        'breadth_ok':   False,
        'rs_vs_spy':    float('nan'),
        'z_score':      float('nan'),
    }

def fetch_market_signals():
    result = {
        "cnn_score": 50, "cnn_label": "Neutral",
        "sp500_extended": False, "buffett_extended": False,
        "vix_current": 20.0, "vix_zscore": 0.0, "vix_label": "Neutral",
    }
    # ── CNN Fear & Greed (kept for cap logic / display only) ──────────
    try:
        r = requests.get("https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
                        timeout=8, headers={"User-Agent":"Mozilla/5.0"})
        d = r.json()
        result["cnn_score"] = float(d["fear_and_greed"]["score"])
        result["cnn_label"] = d["fear_and_greed"]["rating"]
    except: pass

    # ── VIX z-score (used for sell score multiplier) ──────────────────
    # z-score vs 252d mean: >+2=extreme fear, <-2=extreme greed (complacency)
    # Sell multiplier is INVERTED vs CNN: fear → dampen sells, greed → amplify sells
    try:
        vix = yf.download("^VIX", period="2y", interval="1d", auto_adjust=True, progress=False)
        vix_cl = (vix["Close"]["^VIX"] if isinstance(vix.columns, pd.MultiIndex) else vix["Close"]).dropna()
        vix_last  = float(vix_cl.iloc[-1])
        vix_mean  = float(vix_cl.rolling(252).mean().iloc[-1])
        vix_std   = float(vix_cl.rolling(252).std().iloc[-1])
        vix_z     = round((vix_last - vix_mean) / vix_std, 2) if vix_std > 0 else 0.0
        # Label
        if vix_z > 2:    vix_label = "Extreme Fear"
        elif vix_z > 1:  vix_label = "Fear"
        elif vix_z > -1: vix_label = "Neutral"
        elif vix_z > -2: vix_label = "Greed"
        else:            vix_label = "Extreme Greed"
        result["vix_current"] = round(vix_last, 1)
        result["vix_zscore"]  = vix_z
        result["vix_label"]   = vix_label
    except: pass

    # ── S&P500 and Buffett extension (kept as sell boosters) ──────────
    try:
        spy = yf.download("SPY", period="2y", interval="1d", auto_adjust=True, progress=False)
        cl  = (spy["Close"]["SPY"] if isinstance(spy.columns,pd.MultiIndex) else spy["Close"]).dropna()
        sma200 = cl.rolling(200).mean()
        result["sp500_extended"] = bool((cl.iloc[-1]/sma200.iloc[-1]-1)>0.15)
        m5 = cl.rolling(1260).mean(); s5 = cl.rolling(1260).std()
        result["buffett_extended"] = bool(((cl.iloc[-1]-m5.iloc[-1])/s5.iloc[-1])>2.0)
    except: pass
    return result

def score_ticker(ticker, sell_sigs, market, sector_states, framework_score=None):
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
    dist = sell_sigs.get("dist")  # float DECIMAL from compute_sell_signals e.g. -0.168 = 16.8% below high
    near_high = sell_sigs.get("near_high", False)
    if dist is not None:
        # Convert decimal to percentage points for scoring
        dist_pct = dist * 100  # e.g. -0.168 → -16.8
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

    # ── Market context multiplier — VIX z-score (replaces CNN F&G) ────
    # VIX z-score vs 252d mean:
    #   > +2 (extreme fear/panic): ×0.75 — dampen sells, don't force exits at lows
    #   +1 to +2 (fear):           ×0.90
    #   -1 to +1 (neutral):        ×1.00
    #   -1 to -2 (greed/complacency): ×1.15
    #   < -2 (extreme greed):      ×1.30 — amplify sells, market is euphoric
    #
    # Inverted from CNN: HIGH VIX = fear = reduce sell urgency (buy opportunity)
    #                    LOW VIX  = complacency = increase sell urgency (trim winners)
    # Literature: VIX as sentiment proxy — Whaley (2009), Baker-Wurgler (2006) spirit
    vix_z = market.get("vix_zscore", 0.0) or 0.0
    if   vix_z > 2:    vix_mult = 0.75; vix_flag = f"VIX_panic(z={vix_z:.1f})×0.75"
    elif vix_z > 1:    vix_mult = 0.90; vix_flag = f"VIX_fear(z={vix_z:.1f})×0.90"
    elif vix_z > -1:   vix_mult = 1.00; vix_flag = None
    elif vix_z > -2:   vix_mult = 1.15; vix_flag = f"VIX_greed(z={vix_z:.1f})×1.15"
    else:              vix_mult = 1.30; vix_flag = f"VIX_euphoria(z={vix_z:.1f})×1.30"
    if vix_flag: flags.append(vix_flag)

    # CNN kept in flags for display transparency but not used in scoring
    cnn = market.get("cnn_score", 50)
    if cnn > 75 or cnn < 25:
        flags.append(f"CNN{cnn:.0f}({market.get('cnn_label','?')})")

    sector_boost = 0
    for sector in TICKER_SECTORS.get(ticker, []):
        sector_data = sector_states.get(sector, {})
        # Handle both old bool format and new dict format
        if isinstance(sector_data, dict):
            if sector_data.get('entry_signal'):
                sector_boost = 5; flags.append(f"{sector}_entry+5"); break
        elif sector_data:
            sector_boost = 5; flags.append(f"{sector}_ext+5"); break

    if market.get("sp500_extended"):
        sector_boost += 5; flags.append("SP500_ext+5")
    if market.get("buffett_extended"):
        sector_boost += 5; flags.append("Buffett_ext+5")

    score = min(100, round(score * vix_mult + sector_boost, 1))

    # ── Framework score damping — continuous curve, no threshold cliffs ─
    # fw_damp ranges from 1.0 (FW=0, no protection) to 0.50 (FW=100, max protection)
    # Formula: damp = 1.0 - 0.5 * (fw/100)^1.5
    # Shape: convex — damping accelerates at high FW values
    # FW=0:  ×1.00 (no damping)
    # FW=50: ×0.82
    # FW=68: ×0.72
    # FW=70: ×0.71  ← barely different from 68, as it should be
    # FW=80: ×0.64
    # FW=90: ×0.57
    # FW=100:×0.50
    fw = framework_score
    if fw is not None and fw > 0:
        fw_damp = round(1.0 - 0.50 * ((fw / 100) ** 1.5), 3)
        if fw >= 70:
            caution.append(f"FW{fw}(×{fw_damp:.2f})")
    else:
        fw_damp = 1.0

    score = min(100, round(score * fw_damp, 1))

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

def apply_portfolio_cap(rows, cnn_score=50, held_count=None):
    """
    Cap sell signals based on held positions only, CNN-aware.
    Cap is a % of held positions, not universe size:
      Extreme Fear  (<20):  5% of held
      Fear          (20-40): 8% of held
      Neutral       (40-60): 10% of held
      Greed         (60-80): 15% of held
      Extreme Greed (>80):  20% of held

    Only held positions can generate actionable signals.
    Candidates are scored but never capped into actionable signals.
    """
    held_rows = [r for r in rows if r.get("is_holding")]
    n_held = held_count or len(held_rows)

    # CNN-aware cap percentage
    if cnn_score > 80:   cap_pct = 0.20
    elif cnn_score > 60: cap_pct = 0.15
    elif cnn_score > 40: cap_pct = 0.10
    elif cnn_score > 20: cap_pct = 0.08
    else:                cap_pct = 0.05

    max_signals = max(1, round(n_held * cap_pct))

    # Only held positions can be flagged — candidates keep score but get HOLD action
    for r in rows:
        if not r.get("is_holding") and r.get("sell_action") != "HOLD":
            r["sell_action"] = "HOLD"
            r["sell_capped"] = True

    # Among held, keep only top max_signals by sell_score
    held_flagged = [r for r in held_rows if r.get("sell_action") != "HOLD"]
    if len(held_flagged) > max_signals:
        keep = {r["ticker"] for r in sorted(held_flagged, key=lambda x: -x["sell_score"])[:max_signals]}
        for r in held_flagged:
            if r["ticker"] not in keep:
                r["sell_action"] = "HOLD"
                r["sell_capped"] = True
        print(f"Sell cap: {len(held_flagged)} held signals → {max_signals} (CNN={cnn_score:.0f}, {cap_pct*100:.0f}% of {n_held} held)")
    else:
        print(f"Sell cap: {len(held_flagged)}/{max_signals} held signals (CNN={cnn_score:.0f}, {cap_pct*100:.0f}% of {n_held} held)")

    return rows
