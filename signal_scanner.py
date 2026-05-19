"""
Portfolio Signal Scanner
Runs daily via GitHub Actions → writes signals_payload.json
Consumed by index.html (portfolio dashboard) on Netlify
"""
import warnings; warnings.filterwarnings('ignore')
import yfinance as yf
import pandas as pd
import numpy as np
import requests, json, os, time
from datetime import datetime, timedelta
try:
    from sell_side_scorer import (
        compute_sell_signals, fetch_sector_signals,
        fetch_market_signals, score_ticker, apply_portfolio_cap,
        EXIT_T, REDUCE_T, TRIM_T
    )
    SELL_SCORER_AVAILABLE = True
except ImportError:
    SELL_SCORER_AVAILABLE = False
    EXIT_T = 70; REDUCE_T = 55; TRIM_T = 35
    print("sell_side_scorer.py not found -- sell signals disabled")



# ── CONFIGURATION ─────────────────────────────────────────────────────
# Set FINNHUB_TOKEN as a GitHub Actions secret (never hardcode here)
FINNHUB_TOKEN = os.environ.get('FINNHUB_TOKEN', '')

MY_HOLDINGS = [
    'NVDA','AVGO','TSLA','MELI','AMAT','MSFT','AAPL','AMZN','META','GOOG',
    'NFLX','BKNG','SHOP','RACE','AMD','CRWV',
    'ASML','ANET','DDOG','CRDO','NBIS','TSM','TM','MU','INTU','CPRT',
    'PGR','TTD','UNH','FISV','O','TEAM',
    'VOO','VTI','QQQ','TQQQ','SCHD','JEPI','VXUS','VSS','IEV','URTH','GLD',
]

CANDIDATES = [
    'NOW','PANW','ORCL','COIN','AXON','CEG','CELH','DECK','ENPH','HIMS',
    'IDXX','KNSL','LULU','MPWR','NET','PLNT','RCL','SPOT','SQ','UBER',
    'ULTA','VEEV','SMCI','CAVA','SNOW','MEDP','PODD','HEI','ACLS',
    'FICO','APP','HOOD','RKLB','ARM',
]

UNIVERSE  = list(dict.fromkeys(MY_HOLDINGS + CANDIDATES))
BENCHMARK = 'VOO'
DIST_T    = -0.15   # validated 2026-05-17
QUALITY_T =  0.20   # validated 2026-05-17
TREND_T   =  0.00
DFV_LIFT  =  2.5   # validated 2026-05-17
YEARS     =  5
FWD_DAYS  =  252
TEST_YRS  =  2

FRAMEWORK_SCORES = {
    'NVDA':93,'CRDO':93,'ASML':93,'AVGO':92,'ANET':91,'DDOG':90,
    'MELI':90,'NBIS':87,'AMD':86,'MSFT':82,'META':82,'TSM':89,
    'BKNG':85,'AAPL':80,'AMZN':84,'GOOG':83,'SHOP':78,'NFLX':76,
    'RACE':79,'TM':65,'ADBE':61,'TSLA':56,'TQQQ':48,'CPRT':80,
    'PGR':78,'UNH':75,'TTD':77,'INTU':79,'FISV':74,'O':65,
    'AMAT':82,'MU':76,'TEAM':75,'CRWV':72,
}

# ── SIGNAL ENGINE ─────────────────────────────────────────────────────
def calc_rsi(series, period):
    delta    = series.diff()
    avg_gain = delta.clip(lower=0).ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = (-delta).clip(lower=0).ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float('nan'))
    return 100 - (100 / (1 + rs))

def calc_quality(series, window=252):
    ret = series.pct_change(window)
    vol = series.pct_change().rolling(window).std() * np.sqrt(252)
    return (ret / vol.replace(0, float('nan')) / 3).clip(0, 1)

def compute_signals(cl):
    cl = cl.dropna()
    if len(cl) < 252:
        return pd.DataFrame()
    df = pd.DataFrame({'close': cl})
    df['rsi14']       = calc_rsi(df['close'], 14)
    df['rsi40']       = calc_rsi(df['close'], 40)
    df['rsi47']       = calc_rsi(df['close'], 47)
    df['hm_rsi']      = ((df['rsi40'] - 30) * 0.7).clip(0, 20)
    df['hm_prev']     = df['hm_rsi'].shift(1)
    df['hm_floor10']  = df['hm_rsi'].rolling(10).min().shift(1)
    df['hm_lift']     = df['hm_rsi'] - df['hm_floor10']
    df['banker_rsi']  = ((df['rsi47'] - 51) * 1.5).clip(0, 20)
    df['banker_prev'] = df['banker_rsi'].shift(1)
    df['high252']     = df['close'].rolling(252).max().shift(1)
    df['dist']        = (df['close'] - df['high252']) / df['high252']
    df['ma200']       = df['close'].rolling(200).mean()
    df['trend']       = (df['close'] - df['ma200']) / df['ma200']
    df['quality']     = calc_quality(df['close'])
    df['f']           = (df['dist'] < DIST_T) & (df['trend'] > TREND_T) & (df['quality'] > 0.05)
    df['dfv1']        = (df['hm_rsi'] > df['hm_prev']) & (df['hm_prev'] >= 0) & (df['hm_prev'] <= 5)
    df['dfv3']        = df['hm_lift'] > DFV_LIFT
    df['fdfv3']       = df['f'] & df['dfv3']
    df['fdfv1']       = df['f'] & df['dfv1']
    ret252            = df['close'].pct_change(252)
    ret126            = df['close'].pct_change(126)
    df['pfd']         = ((ret252 - 2*ret126) > 0.05) & (df['quality'] > 0.10)
    df['triple']      = df['close'].pct_change(63) > 0.20
    df['banker_weak'] = (df['banker_prev'] >= 20) & (df['banker_rsi'] < 20)
    rsi_above         = (df['rsi14'] - 60).clip(lower=0).ewm(span=3, adjust=False).mean()
    rsi_pk            = rsi_above.rolling(20).max().shift(1)
    price_pk          = df['close'].rolling(20).max().shift(1)
    df['rbear']       = (
        (df['close'] > price_pk * 1.01) &
        (rsi_above < rsi_pk * 0.85) &
        (rsi_above > 0)
    )
    return df.dropna(subset=['dist', 'hm_lift'])

# ── FETCH DATA ────────────────────────────────────────────────────────
def fetch_history():
    end   = datetime.today()
    start = end - timedelta(days=YEARS*365 + 60)
    all_t = list(dict.fromkeys([BENCHMARK] + UNIVERSE))
    print(f'Fetching {len(all_t)} tickers ({YEARS}yr daily)...')
    raw = yf.download(all_t, start=start, end=end, interval='1d',
                      auto_adjust=True, progress=False, threads=True)
    if isinstance(raw.columns, pd.MultiIndex):
        closes = raw['Close'].dropna(how='all')
    else:
        closes = raw[['Close']]; closes.columns = [all_t[0]]
    ok   = [t for t in all_t if t in closes.columns and closes[t].notna().sum() > 100]
    fail = [t for t in all_t if t not in ok]
    print(f'✓ {len(ok)} ok | ✗ {fail}')
    return closes, ok

# ── HISTORICAL ALPHA ──────────────────────────────────────────────────
def compute_ticker_alpha(all_signals, closes, voo):
    cutoff      = closes.index[-1] - pd.Timedelta(days=TEST_YRS*365)
    ticker_alpha = {}
    for t, sig in all_signals.items():
        cl = sig['close']; dates = sig.index; obs = []
        for i in range(len(dates) - FWD_DAYS):
            if dates[i] < cutoff: continue
            t0, t1 = dates[i], dates[i+FWD_DAYS]
            try:
                v0, v1 = voo.asof(t0), voo.asof(t1)
                if pd.isna(v0) or pd.isna(v1) or v0 == 0: continue
                exc = cl.iloc[i+FWD_DAYS]/cl.iloc[i] - 1 - (v1/v0-1)
                row = sig.iloc[i].to_dict(); row['excess'] = exc
                obs.append(row)
            except: pass
        if len(obs) < 5: continue
        obs_df = pd.DataFrame(obs)
        def sep(col):
            if col not in obs_df.columns: return float('nan')
            s = obs_df[obs_df[col]==True]; ns = obs_df[obs_df[col]==False]
            return s['excess'].mean() - ns['excess'].mean() if len(s) >= 3 else float('nan')
        ticker_alpha[t] = {'factor_sep': sep('f'), 'fdfv3_sep': sep('fdfv3')}
    return ticker_alpha

# ── FINNHUB LIVE PRICES ───────────────────────────────────────────────
def fetch_live_prices(tickers):
    if not FINNHUB_TOKEN:
        print('No FINNHUB_TOKEN — skipping live prices, using yfinance last close')
        return {}
    live = {}
    print(f'Fetching live prices from Finnhub for {len(tickers)} tickers...')
    for t in tickers:
        sym = t.replace('.L','').replace('.PA','').replace('.MC','')
        try:
            r = requests.get(
                f'https://finnhub.io/api/v1/quote?symbol={sym}&token={FINNHUB_TOKEN}',
                timeout=5)
            d = r.json()
            if d.get('c') and d['c'] > 0:
                live[t] = {'price': round(d['c'], 2), 'change': round(d.get('dp', 0), 2)}
        except: pass
        time.sleep(0.05)
    print(f'Live prices: {len(live)}/{len(tickers)}')
    return live

# ── GUIDANCE TEXT ─────────────────────────────────────────────────────
def build_guidance(t, last, ta, fs):
    parts = []
    dist = last['dist']; trend = last['trend']; lift = last['hm_lift']
    fa   = ta.get('factor_sep', float('nan'))
    fa_valid = not (isinstance(fa, float) and fa != fa)

    if last['fdfv3']:
        parts.append(
            f"Factor+DFV V3 active: {dist*100:.1f}% below 252d high, above 200d MA, "
            f"DFV floor lift {lift:.1f}pts. Strongest entry signal (4/4 horizons, +62% at 504d).")
    elif last['f'] and last['dfv1']:
        parts.append(
            f"Factor gate + DFV V1: value zone ({dist*100:.1f}% below 252d high) "
            f"with hot money RSI turning up from floor.")
    elif last['f']:
        parts.append(
            f"In factor value zone: {dist*100:.1f}% below 252d high, "
            f"{trend*100:.1f}% above 200d MA. "
            f"Waiting for DFV trigger (lift {lift:.1f}, need >4.0).")
    elif last['pfd']:
        parts.append(
            f"PFD signal: price compressed vs own trend. "
            f"Supporting buy (4/4 horizons, +17.4% at 504d).")

    if last['triple'] and not last['f']:
        parts.append(f"Caution: up >20% in 63 days — mean reversion risk short-term.")
    if last['banker_weak']:
        parts.append(f"Banker Weak: RSI47 institutional signal dropping from max.")
    if last['rbear']:
        parts.append(f"RSI14 bearish divergence vs price.")
    if fa_valid and abs(fa) > 0.03:
        direction = "outperformed" if fa > 0 else "underperformed"
        parts.append(f"Own-ticker historical alpha: {direction} VOO by {abs(fa*100):.1f}% at 252d.")
    if fs:
        verdict = "Strong keeper." if fs >= 85 else "Monitor." if fs >= 70 else "Sell candidate on framework."
        parts.append(f"Framework: {fs}/100. {verdict}")

    return " ".join(parts) if parts else "No active signal. Monitoring."

# ── SCORE + BUILD PAYLOAD ─────────────────────────────────────────────
def build_payload(all_signals, ticker_alpha, live_prices):
    signals_list = []; buy_ideas = []; sell_guidance = []

    for t, sig in all_signals.items():
        if len(sig) == 0: continue
        last = sig.iloc[-1]
        ta   = ticker_alpha.get(t, {})
        lp   = live_prices.get(t, {})
        fa   = ta.get('factor_sep', float('nan'))
        fa_valid = not (isinstance(fa, float) and fa != fa)
        fs   = FRAMEWORK_SCORES.get(t)
        is_holding = t in MY_HOLDINGS
        price  = lp.get('price') or round(float(last['close']), 2)
        change = lp.get('change', 0.0)

        buy = 0
        if last['f']:      buy += 40
        if last['fdfv3']:  buy += 25
        if last['pfd']:    buy += 20
        if last['triple']: buy += 10
        if last['dfv3'] and not last['f']: buy += 5
        if last['banker_weak']: buy -= 20
        if last['rbear']:       buy -= 10
        buy = max(0, min(100, buy))

    # ── SELL SCORE ────────────────────────────────────────────────────
    sell_score = 0; sell_action = 'HOLD'; sell_flags = '—'; sell_caution = '—'
    sell_sigs_data = {}
    if SELL_SCORER_AVAILABLE:
        try:
            cl_s  = closes[t].dropna() if t in closes.columns else None
            hi_s  = highs[t].dropna()   if t in highs.columns   else cl_s
            lo_s  = lows[t].dropna()    if t in lows.columns    else cl_s
            vol_s = volumes[t].dropna() if t in volumes.columns else pd.Series(dtype=float)
            sell_sigs_data = compute_sell_signals(cl_s, hi_s, lo_s, vol_s)
            sell_score, sell_action, sell_flags, sell_caution = score_ticker(
                t, sell_sigs_data, sell_market, sell_sectors)
        except Exception as e:
            pass

        # Signal classification uses sell_score from sell_side_scorer
        if sell_score >= EXIT_T and is_holding:  signal = 'SELL'
        elif buy >= 60:                          signal = 'BUY'
        else:                                    signal = 'HOLD'

    guidance = build_guidance(t, last, ta, fs)

        row = {
            'ticker':          t,
            'price':           price,
            'change_pct':      change,
            'signal':          signal,
            'guidance':        guidance,
            'buy_score':       buy,
            'sell_score':      sell,
            'is_holding':      is_holding,
            'dist_252h':       round(float(last['dist'])*100, 1),
            'vs_200ma':        round(float(last['trend'])*100, 1),
            'dfv_lift':        round(float(last['hm_lift']), 1),
            'factor':          bool(last['f']),
            'dfv3':            bool(last['dfv3']),
            'fdfv3':           bool(last['fdfv3']),
            'pfd':             bool(last['pfd']),
            'triple':          bool(last['triple']),
            'banker_weak':     bool(last['banker_weak']),
            'factor_sep':      None if not fa_valid else round(fa*100, 1),
            'framework_score': fs,
        }
        signals_list.append(row)
        if signal == 'BUY' or buy >= 30:             buy_ideas.append(row)
        if signal == 'SELL' or (sell >= 40 and is_holding): sell_guidance.append(row)

    signals_list.sort(key=lambda x: -x['buy_score'])
    if SELL_SCORER_AVAILABLE:
        signals_list = apply_portfolio_cap(signals_list)
    buy_ideas.sort(key=lambda x: -x['buy_score'])
    sell_guidance.sort(key=lambda x: -x['sell_score'])

    return {
        'generated_at':  datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'run_date':      datetime.today().strftime('%A %d %b %Y'),
        'universe_size': len(signals_list),
        'summary': {
            'strong_buy':  sum(1 for s in signals_list if s['fdfv3']),
            'buy':         sum(1 for s in signals_list if s['signal']=='BUY'),
            'sell':        sum(1 for s in signals_list if s['signal']=='SELL'),
            'factor_zone': sum(1 for s in signals_list if s['factor']),
            'banker_weak': sum(1 for s in signals_list if s['banker_weak']),
        },
        'analytics': {
            'signals':       signals_list,
            'buy_ideas':     buy_ideas[:20],
            'sell_guidance': sell_guidance[:15],
        }
    }

# ── MAIN ──────────────────────────────────────────────────────────────
def main():
    print(f'Signal scanner starting — {datetime.today().strftime("%Y-%m-%d %H:%M UTC")}')

    closes, ok = fetch_history()
    voo = closes[BENCHMARK].dropna()

    print('Computing signals...')
    all_signals = {}
    for t in ok:
        if t == BENCHMARK: continue
        try:
            sig = compute_signals(closes[t])
            if len(sig) >= 50:
                all_signals[t] = sig
        except: pass
    print(f'Signals ready: {len(all_signals)} tickers')

    print('Computing historical alpha...')
    ticker_alpha = compute_ticker_alpha(all_signals, closes, voo)

    live_prices = fetch_live_prices(list(all_signals.keys()))

    payload = build_payload(all_signals, ticker_alpha, live_prices)

    out = 'signals_payload.json'
    with open(out, 'w') as f:
        json.dump(payload, f, indent=2, default=str)

    print(f'Written: {out} ({os.path.getsize(out):,} bytes)')
    print(f"Summary: {payload['summary']}")

    # Print top signals
    for b in payload['analytics']['buy_ideas'][:5]:
        star = '★' if b['fdfv3'] else '↑' if b['factor'] else '·'
        print(f"  {star} {b['ticker']:<8} buy={b['buy_score']} {'(held)' if b['is_holding'] else ''}")

if __name__ == '__main__':
    main()
