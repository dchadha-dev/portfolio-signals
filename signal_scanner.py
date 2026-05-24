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
from zoneinfo import ZoneInfo

BANGKOK = ZoneInfo("Asia/Bangkok")

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
FINNHUB_TOKEN = os.environ.get('FINNHUB_TOKEN', '')

MY_HOLDINGS = [
    # Direct US stocks
    'NVDA','AVGO','TSLA','MELI','AMAT','MSFT','AAPL','AMZN','META','GOOG',
    'NFLX','BKNG','SHOP','RACE','AMD','CRWV','ASML','ANET','DDOG','CRDO',
    'NBIS','TSM','TM','MU','INTU','CPRT','PGR','TTD','UNH','FISV','O','TEAM',
    'BRKB','NVO','RELX','DELL',
    # European stocks
    'RMS.PA','MC.PA','ITX.MC',
    # US ETFs
    'VOO','VTI','QQQ','TQQQ','SCHD','JEPI','VXUS','VSS','IEV','URTH','GLD','XLG',
    # Thematic ETFs
    'AIQG','QNTM.L','QTUM','FLAX',
    # Crypto
    'COIN',
    # Thai mutual funds (signal via proxy ETF)
    'SCB_SP500','SCB_NDQ','SCB_SEMI','SCB_WORLD','SCB_GOLD','SCB_NK225',
    'SCB_SET50','SCB_DJ','SCB_AIEM','SCB_FINTECH','SCB_AUTO','SCB_INNOV',
    'SCB_GENO','SCB_CHINA','SCB_EV','SCB_BUSAA',
    'KT_INDIA','KT_WORLD','KT_WTAI','KT_BLOCK','KT_TECH','KT_ESG',
]

CANDIDATES = [
    # Original candidates
    'NOW','PANW','ORCL','COIN','AXON','CEG','CELH','DECK','ENPH','HIMS',
    'IDXX','KNSL','LULU','MPWR','NET','PLNT','RCL','SPOT','UBER',
    'ULTA','VEEV','SMCI','CAVA','SNOW','MEDP','PODD','HEI','ACLS',
    'FICO','APP','HOOD','RKLB','ARM',
    # Full screener universe (all 179 new tickers)
    'QBTS','RGTI','APLD','QUBT','IONQ','ASTS',
    'QUCY','SERV','VRT','ALAB','LRCX',
    'CDNS','SNPS','KLAC','QCOM','MRVL','ADI',
    'TXN','TSEM','COHR','GLW','RMBS','NVTS',
    'WDC','STX','HIMX','LSCC','CRM','CRWD',
    'ISRG','ADBE','MA','V','SPGI','MSCI',
    'IBKR','ICE','CME','NDAQ','JPM','GS',
    'MS','BAC','C','SCHW','AXP','KKR',
    'BX','LLY','ZTS','TMO','JNJ','ABBV',
    'PFE','GILD','BIIB','MRNA','MDT','BSX',
    'ABT','UTHR','UHS','ELV','NVS','ABNB',
    'DASH','SE','TOST','GRAB','DKNG','SOFI',
    'CVNA','FUTU','TCOM','EXPE','MAR','ONON',
    'COST','CMG','MCD','HD','LOW','SBUX',
    'NKE','PG','KO','PEP','MNST','CL',
    'UL','DIS','NEE','FSLR','CCJ','NXE',
    'XOM','CVX','BP','SHEL','EQNR','EQT',
    'VLO','HAL','BKR','OXY','EOSE','BE',
    'CAT','DE','GE','WM','UPS','FDX',
    'RTX','LMT','BA','MMM','GEV','MSI',
    'HON','BABA','BIDU','PDD','JD','T',
    'VZ','WMT','IBM','CSCO','INTC','ON',
    'INFY','SONY','HDB','HSBC','AER','CB',
    'HPE','SOUN','FIVN','ONDS','CBRS','NXT',
    'BULL','SOLS','RDDT','IREN','KEEL','SEDG',
    'SNDK','SYM','FORM','VICR','RIO','FCX',
    'GOLD','BEP','DNN','MSTR','AAL','OWL',
    'PI','ALGN',
]


# ── THAI FUND PROXY MAP ───────────────────────────────────────────────
# Thai mutual funds are analysed via their underlying ETF proxy
TICKER_PROXY_MAP = {
    # SCB funds — corrected to exact underlying proxies per fund mapping
    'SCB_SP500':  'IVV',   # iShares S&P 500 (not VOO — IVV is the direct underlying)
    'SCB_NDQ':    'QQQ',   # Invesco QQQM → QQQ proxy
    'SCB_SEMI':   'SMH',   # VanEck Semiconductor
    'SCB_WORLD':  'URTH',  # iShares MSCI World
    'SCB_GOLD':   'GLD',   # SPDR Gold
    'SCB_NK225':  'EWJ',   # iShares Nikkei (1329) → EWJ proxy
    'SCB_SET50':  'SPY',  # Thai SET50 — no liquid ETF proxy; SPY used as directional fallback
    'SCB_DJ':     'DIA',   # SPDR Dow Jones
    'SCB_AIEM':   'AAXJ',  # Asian EM blend
    'SCB_FINTECH':'FINX',  # Global X Fintech — was wrongly QQQ
    'SCB_AUTO':   'BOTZ',  # Global X Robotics/Autonomous — was wrongly QQQ
    'SCB_INNOV':  'ARKK',  # ARK Innovation style — was wrongly QQQ
    'SCB_GENO':   'ARKG',  # ARK Genomic style — was wrongly XLV
    'SCB_CHINA':  'KWEB',  # KraneShares China Internet — was wrongly QQQ
    'SCB_EV':     'DRIV',  # Global X EV & Mobility — was wrongly QQQ
    'SCB_BUSAA':  'IWF',   # SCB US Business (MS US Growth) — was missing
    # KT funds — corrected proxies
    'KT_INDIA':   'INDA',  # iShares India
    'KT_WORLD':   'ACWV',  # AB Low Vol Global → ACWV (not URTH)
    'KT_WTAI':    'AIQ',   # KTAM World Tech AI → AIQ (not QQQ)
    'KT_BLOCK':   'BLOK',  # KTAM Blockchain → BLOK (not QQQ)
    'KT_TECH':    'IWF',   # KTAM Technology (AB American Growth) → IWF (not QQQ)
    'KT_ESG':     'ACWV',  # KTAM Global ESG → ACWV (not URTH)
}

# Proxy tickers to fetch for Thai funds
PROXY_TICKERS = list(set(TICKER_PROXY_MAP.values()))
# Tickers kept in holdings but excluded from signal computation
# (LSE-listed or insufficient yfinance history — shown as NO_DATA with reason)
SKIP_SIGNAL = {'AIQG', 'QNTM.L'}
# Full universe for signal computation — direct tickers only (Thai funds use proxy)
DIRECT_HOLDINGS = [t for t in MY_HOLDINGS if t not in TICKER_PROXY_MAP]
UNIVERSE  = list(dict.fromkeys(DIRECT_HOLDINGS + CANDIDATES + PROXY_TICKERS))
BENCHMARK = 'VOO'
DIST_T    = -0.20   # raised from -0.15 — requires 20%+ below 252d high
QUALITY_T =  0.20
TREND_T   =  0.00
DFV_LIFT  =  2.5
YEARS     =  5
FWD_DAYS  =  252
TEST_YRS  =  2

FRAMEWORK_SCORES = {
    # ≥85 Core keepers
    'NVDA':93,'CRDO':93,'ASML':93,'AVGO':92,'ANET':91,'DDOG':90,
    'MELI':90,'LLY':90,'TSM':89,'MA':88,'V':88,'NBIS':87,
    'AMD':86,'NOW':86,'MSCI':86,'BKNG':85,'ISRG':85,'SPGI':85,
    # 70–84 Good
    'AMZN':84,'CRWD':84,'KLAC':84,'TMO':84,'GOOG':83,'PANW':83,
    'LRCX':83,'ZTS':83,'MSFT':82,'META':82,'AMAT':82,'RMS.PA':82,
    'CDNS':82,'SNPS':82,'AXON':82,'ICE':82,'IDXX':82,'COST':82,
    'CMG':82,'FICO':81,'CME':81,'AAPL':80,'CPRT':80,'NVO':80,
    'MC.PA':80,'CRM':80,'NDAQ':80,'ADI':80,'TXN':80,'BX':80,
    'MCD':80,'RACE':79,'INTU':79,'MPWR':79,'MRVL':79,'SPOT':79,
    'HD':79,'SHOP':78,'PGR':78,'BRKB':78,'ORCL':78,'AXP':78,
    'KKR':78,'MSI':78,'TTD':77,'NET':77,'VRT':77,'NFLX':76,
    'MU':76,'ITX.MC':76,'VEEV':76,'HEI':76,'QCOM':76,'JPM':76,
    'JNJ':76,'ABT':76,'ABNB':76,'MAR':76,'CEG':76,'DE':76,
    'WMT':76,'ARM':76,'UNH':75,'TEAM':75,'KNSL':75,'BSX':75,
    'FISV':74,'APP':74,'IBKR':74,'GS':74,'ABBV':74,'ELV':74,
    'NVS':74,'PODD':74,'UBER':74,'LOW':74,'PG':74,'CAT':74,
    'WM':74,'MS':73,'CRWV':72,'RELX':72,'ALAB':72,'SMH':72,
    'ON':72,'SCHW':72,'UTHR':72,'MEDP':72,'ONON':72,'NKE':72,
    'LULU':72,'KO':72,'PEP':72,'MNST':72,'DECK':72,'NEE':72,
    'GEV':72,'CB':72,'LSCC':71,'CSCO':70,'DASH':70,'GE':70,
    'LMT':70,
    # 55–69 Marginal
    'ACLS':68,'COHR':68,'GLW':68,'TSEM':68,'DELL':68,'BAC':68,
    'OWL':68,'MDT':68,'ALGN':68,'RCL':68,'SBUX':68,'CL':68,
    'ULTA':68,'CCJ':68,'XOM':68,'RTX':68,'AER':68,'PDD':68,
    'HDB':68,'CVX':67,'GILD':66,'UL':66,'CAVA':66,'SONY':66,
    'TM':65,'O':65,'SNOW':65,'IBM':65,'TOST':65,'DIS':65,
    'RMBS':64,'FSLR':64,'UPS':64,'PI':64,'C':62,'UHS':62,
    'CELH':62,'SE':62,'TCOM':62,'EXPE':62,'SHEL':62,'EQNR':62,
    'VLO':62,'OXY':62,'FDX':62,'BABA':62,'INFY':62,'FIVN':62,
    'ADBE':61,'STX':60,'BIIB':60,'DKNG':60,'HIMS':60,'EQT':60,
    'BKR':60,'BEP':60,'RIO':60,'FCX':60,'COIN':58,'WDC':58,
    'PFE':58,'CVNA':58,'PLNT':58,'HAL':58,'FORM':58,'HSBC':58,
    'SNDK':58,'VICR':58,'GOLD':58,'TSLA':56,'RDDT':56,'BP':56,
    'MMM':56,'JD':56,'FUTU':55,
    # <55 Weak / sell candidates
    'SOFI':54,'ENPH':54,'BIDU':54,'NVTS':52,'HPE':52,'HOOD':52,
    'MRNA':52,'SMCI':52,'NXE':52,'T':52,'VZ':52,'RKLB':52,
    'GRAB':50,'SYM':50,'TQQQ':48,'HIMX':48,'BE':48,'BA':48,
    'IREN':48,'ASTS':46,'SOUN':44,'SEDG':44,'SERV':42,'INTC':42,
    'DNN':42,'APLD':42,'AAL':40,'EOSE':38,'MSTR':38,'IONQ':36,
    'NEXT':35,'KEEL':32,'QBTS':28,'RGTI':28,'QUBT':24,'QUCY':20,
    # Thai mutual funds — same 5-axis framework [Moat/30, Earnings/25, Runway/20, Val/15, Redundancy/10]
    'SCB_SP500':63, 'SCB_NDQ':61,  'SCB_SEMI':72,  'SCB_WORLD':68, 'SCB_GOLD':65,
    'SCB_NK225':62, 'SCB_SET50':55, 'SCB_DJ':56,   'SCB_AIEM':64,  'SCB_FINTECH':60,
    'SCB_AUTO':52,  'SCB_INNOV':47, 'SCB_GENO':48,  'SCB_CHINA':54, 'SCB_EV':58,
    'SCB_BUSAA':62,
    'KT_INDIA':70,  'KT_WORLD':65,  'KT_WTAI':68,   'KT_BLOCK':55,
    'KT_TECH':64,   'KT_ESG':58,
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
    df['f']           = (df['dist'] < DIST_T) & (df['trend'] > TREND_T) & (df['quality'] > QUALITY_T)
    df['dfv1']        = (df['hm_rsi'] > df['hm_prev']) & (df['hm_prev'] >= 0) & (df['hm_prev'] <= 5)
    df['dfv3']        = df['hm_lift'] > DFV_LIFT
    df['fdfv3']       = df['f'] & df['dfv3']
    df['fdfv1']       = df['f'] & df['dfv1']
    ret252            = df['close'].pct_change(252)
    ret126            = df['close'].pct_change(126)
    df['pfd']         = ((ret252 - 2*ret126) > 0.05) & (df['quality'] > QUALITY_T * 2)
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

# ── TICKER ALIASES — yfinance uses different symbols for some tickers ──
TICKER_ALIASES = {
    'BRKB': 'BRK-B',   # Berkshire B — yfinance requires hyphen
}
ALIAS_REVERSE = {v: k for k, v in TICKER_ALIASES.items()}

# ── FETCH DATA ────────────────────────────────────────────────────────
def fetch_history():
    end   = datetime.today()
    start = end - timedelta(days=YEARS*365 + 60)

    # Apply aliases for batch fetch
    all_t = list(dict.fromkeys([BENCHMARK] + UNIVERSE))
    fetch_t = [TICKER_ALIASES.get(t, t) for t in all_t]
    fetch_t = list(dict.fromkeys(fetch_t))  # dedupe after alias

    print(f'Fetching {len(fetch_t)} tickers ({YEARS}yr daily)...')
    raw = yf.download(fetch_t, start=start, end=end, interval='1d',
                      auto_adjust=True, progress=False, threads=True)

    if isinstance(raw.columns, pd.MultiIndex):
        closes = raw['Close'].dropna(how='all')
        try:
            highs   = raw['High'].dropna(how='all')
            lows    = raw['Low'].dropna(how='all')
            volumes = raw['Volume'].dropna(how='all')
        except:
            highs = lows = volumes = closes
    else:
        closes = raw[['Close']]; closes.columns = [fetch_t[0]]
        highs = lows = volumes = closes

    # Rename aliased columns back to canonical ticker names
    closes  = closes.rename(columns=ALIAS_REVERSE)
    highs   = highs.rename(columns=ALIAS_REVERSE)
    lows    = lows.rename(columns=ALIAS_REVERSE)
    volumes = volumes.rename(columns=ALIAS_REVERSE)

    ok_batch = [t for t in all_t if t in closes.columns and closes[t].notna().sum() > 100]
    fail_batch = [t for t in all_t if t not in ok_batch]

    # ── Retry failed tickers individually (handles 403 batch blocks) ──
    if fail_batch:
        print(f'Retrying {len(fail_batch)} failed tickers individually...')
        for t in fail_batch:
            fetch_sym = TICKER_ALIASES.get(t, t)
            try:
                time.sleep(0.5)  # avoid rate limiting
                single = yf.download(fetch_sym, start=start, end=end, interval='1d',
                                     auto_adjust=True, progress=False)
                if len(single) > 100:
                    cl = single['Close'] if 'Close' in single.columns else single.iloc[:, 0]
                    closes[t]  = cl
                    highs[t]   = single['High']  if 'High'   in single.columns else cl
                    lows[t]    = single['Low']   if 'Low'    in single.columns else cl
                    volumes[t] = single['Volume'] if 'Volume' in single.columns else pd.Series(1e6, index=cl.index)
                    print(f'  ✓ {t} recovered ({len(single)} rows)')
                else:
                    print(f'  ✗ {t} still failed ({len(single)} rows)')
            except Exception as e:
                print(f'  ✗ {t} retry error: {str(e)[:60]}')

    ok   = [t for t in all_t if t in closes.columns and closes[t].notna().sum() > 100]
    fail = [t for t in all_t if t not in ok]
    print(f'✓ {len(ok)} ok | ✗ {len(fail)} failed: {fail[:10]}')
    return closes, highs, lows, volumes, ok

# ── HISTORICAL ALPHA ──────────────────────────────────────────────────
def compute_ticker_alpha(all_signals, closes, voo):
    cutoff       = closes.index[-1] - pd.Timedelta(days=TEST_YRS*365)
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

# ── LIVE PRICES ───────────────────────────────────────────────────────
def fetch_live_prices(tickers):
    if not FINNHUB_TOKEN:
        print('No FINNHUB_TOKEN — using yfinance last close')
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
            f"DFV floor lift {lift:.1f}pts. Strongest entry signal (4/4 horizons).")
    elif last['f'] and last['dfv1']:
        parts.append(
            f"Factor gate + DFV V1: value zone ({dist*100:.1f}% below 252d high) "
            f"with hot money RSI turning up from floor.")
    elif last['f']:
        parts.append(
            f"In factor value zone: {dist*100:.1f}% below 252d high, "
            f"{trend*100:.1f}% above 200d MA. "
            f"Waiting for DFV trigger (lift {lift:.1f}, need >{DFV_LIFT}).")
    elif last['pfd']:
        parts.append("PFD signal: price compressed vs own trend.")

    if last['triple'] and not last['f']:
        parts.append("Caution: up >20% in 63 days — mean reversion risk short-term.")
    if last['banker_weak']:
        parts.append("Banker Weak: RSI47 institutional signal dropping from max.")
    if last['rbear']:
        parts.append("RSI14 bearish divergence vs price.")
    if fa_valid and abs(fa) > 0.03:
        direction = "outperformed" if fa > 0 else "underperformed"
        parts.append(f"Own-ticker historical alpha: {direction} VOO by {abs(fa*100):.1f}% at 252d.")
    if fs:
        verdict = "Strong keeper." if fs >= 85 else "Monitor." if fs >= 70 else "Sell candidate on framework."
        parts.append(f"Framework: {fs}/100. {verdict}")

    return " ".join(parts) if parts else "No active signal. Monitoring."

# ── SCORE + BUILD PAYLOAD ─────────────────────────────────────────────
def build_payload(all_signals, ticker_alpha, live_prices, closes, highs, lows, volumes,
                  sell_market, sell_sectors, failed_tickers=None):
    signals_list = []; buy_ideas = []; sell_guidance = []
    if failed_tickers is None: failed_tickers = {}

    for t, sig in all_signals.items():
        if len(sig) == 0: continue
        last     = sig.iloc[-1]
        ta       = ticker_alpha.get(t, {})
        lp       = live_prices.get(t, {})
        fa       = ta.get('factor_sep', float('nan'))
        fa_valid = not (isinstance(fa, float) and fa != fa)
        fs       = FRAMEWORK_SCORES.get(t)
        is_holding = t in MY_HOLDINGS
        price    = lp.get('price') or round(float(last['close']), 2)
        change   = lp.get('change', 0.0)

        # ── BUY SCORE ─────────────────────────────────────────────────
        buy = 0
        if last['f']:      buy += 40
        if last['fdfv3']:  buy += 25
        if last['pfd']:    buy += 20
        if last['triple']: buy += 10
        if last['dfv3'] and not last['f']: buy += 5
        if last['banker_weak']: buy -= 20
        if last['rbear']:       buy -= 10
        buy = max(0, min(100, buy))

        # ── SELL SCORE ────────────────────────────────────────────────
        sell_score = 0; sell_action = 'HOLD'
        sell_flags = '—'; sell_caution = '—'; sell_sigs_data = {}

        if SELL_SCORER_AVAILABLE:
            try:
                cl_s  = closes[t].dropna() if t in closes.columns else None
                if cl_s is None or len(cl_s) < 20:
                    raise ValueError(f"No close data for {t}")
                # Use actual hi/lo/vol if available, else fall back gracefully
                hi_s  = highs[t].dropna()   if (t in highs.columns   and len(highs[t].dropna())  > 20) else cl_s
                lo_s  = lows[t].dropna()    if (t in lows.columns    and len(lows[t].dropna())   > 20) else cl_s
                vol_s = volumes[t].dropna() if (t in volumes.columns and len(volumes[t].dropna())> 20) else pd.Series(1e6, index=cl_s.index)
                sell_sigs_data = compute_sell_signals(cl_s, hi_s, lo_s, vol_s)
                sell_score, sell_action, sell_flags, sell_caution = score_ticker(
                    t, sell_sigs_data, sell_market, sell_sectors, fs)
            except Exception as sell_err:
                sell_caution = f'err:{str(sell_err)[:40]}'

        # ── SIGNAL CLASSIFICATION ─────────────────────────────────────
        fw_blocks_buy = fs is not None and fs < 70  # raised FW minimum from 55 to 70
        if sell_score >= EXIT_T and is_holding: signal = 'SELL'
        elif buy >= 80 and fw_blocks_buy:    signal = 'WEAK_BUY'
        elif buy >= 80:                      signal = 'BUY'
        elif buy >= 60:                      signal = 'WATCH'   # factor zone but not full conviction
        else:                                signal = 'HOLD'

        guidance = build_guidance(t, last, ta, fs)

        row = {
            'ticker':          t,
            'price':           price,
            'change_pct':      change,
            'signal':          signal,
            'guidance':        guidance,
            'buy_score':       buy,
            'sell_score':      sell_score,
            'sell_action':     sell_action,
            'sell_flags':      sell_flags,
            'sell_caution':    sell_caution,
            'sell_dist':       sell_sigs_data.get('dist'),
            'sell_cmf':        sell_sigs_data.get('cmf_20'),
            'sell_rv_z':       sell_sigs_data.get('rv_z'),
            'sell_weekly_rsi': sell_sigs_data.get('weekly_rsi'),
            'near_high':       sell_sigs_data.get('near_high', False),
            'sell_atr_dist':   sell_sigs_data.get('sma_atr_dist'),
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
            'factor_sep':      None if not fa_valid else round(float(fa)*100, 1),
            'framework_score': fs,
        }

        signals_list.append(row)
        if signal == 'BUY' or buy >= 30:                         buy_ideas.append(row)
        if signal == 'SELL' or (sell_score >= 40 and is_holding): sell_guidance.append(row)

    # ── Add Thai fund rows using proxy signal data ───────────────────
    for fund, proxy in TICKER_PROXY_MAP.items():
        if proxy not in all_signals: continue
        sig = all_signals[proxy]
        if len(sig) == 0: continue
        last = sig.iloc[-1]
        ta   = ticker_alpha.get(proxy, {})
        lp   = {}  # no live price for Thai funds
        fa   = ta.get('factor_sep', float('nan'))
        fa_valid = not (isinstance(fa, float) and fa != fa)
        fs   = FRAMEWORK_SCORES.get(fund)
        price  = round(float(last['close']), 2)

        buy = 0
        if last['f']:      buy += 40
        if last['fdfv3']:  buy += 25
        if last['pfd']:    buy += 20
        if last['triple']: buy += 10
        if last['dfv3'] and not last['f']: buy += 5
        if last['banker_weak']: buy -= 20
        buy = max(0, min(100, buy))

        sell_score = 0; sell_action = 'HOLD'; sell_flags = '—'; sell_caution = '—'
        sell_sigs_data = {}
        if SELL_SCORER_AVAILABLE:
            try:
                cl_s  = closes[proxy].dropna() if proxy in closes.columns else None
                hi_s  = highs[proxy].dropna()  if proxy in highs.columns  else cl_s
                lo_s  = lows[proxy].dropna()   if proxy in lows.columns   else cl_s
                vol_s = volumes[proxy].dropna() if proxy in volumes.columns else pd.Series(dtype=float)
                sell_sigs_data = compute_sell_signals(cl_s, hi_s, lo_s, vol_s)
                sell_score, sell_action, sell_flags, sell_caution = score_ticker(
                    fund, sell_sigs_data, sell_market, sell_sectors, fs)
            except Exception as sell_err:
                sell_caution = f'err:{str(sell_err)[:40]}'

        fw_blocks_buy2 = fs is not None and fs < 55
        if sell_score >= EXIT_T: signal = 'SELL'
        elif buy >= 80 and fw_blocks_buy2: signal = 'WEAK_BUY'
        elif buy >= 80:                    signal = 'BUY'
        elif buy >= 60:                    signal = 'WATCH'
        else:                              signal = 'HOLD'

        guidance = build_guidance(proxy, last, ta, fs)

        row = {
            'ticker': fund, 'price': price, 'change_pct': 0.0,
            'signal': signal, 'guidance': f"[via {proxy}] {guidance}",
            'buy_score': buy, 'sell_score': sell_score,
            'sell_action': sell_action, 'sell_flags': sell_flags,
            'sell_caution': sell_caution,
            'sell_dist': sell_sigs_data.get('dist'),
            'sell_cmf': sell_sigs_data.get('cmf_20'),
            'sell_rv_z': sell_sigs_data.get('rv_z'),
            'sell_weekly_rsi': sell_sigs_data.get('weekly_rsi'),
            'near_high': sell_sigs_data.get('near_high', False),
            'sell_atr_dist': sell_sigs_data.get('sma_atr_dist'),
            'is_holding': True, 'proxy': proxy,
            'dist_252h': round(float(last['dist'])*100, 1),
            'vs_200ma':  round(float(last['trend'])*100, 1),
            'dfv_lift':  round(float(last['hm_lift']), 1),
            'factor': bool(last['f']), 'dfv3': bool(last['dfv3']),
            'fdfv3': bool(last['fdfv3']), 'pfd': bool(last['pfd']),
            'triple': bool(last['triple']), 'banker_weak': bool(last['banker_weak']),
            'factor_sep': None if not fa_valid else round(float(fa)*100, 1),
            'framework_score': fs,
        }
        signals_list.append(row)
        if signal == 'BUY' or buy >= 30:                          buy_ideas.append(row)
        if signal == 'SELL' or (sell_score >= 40):                sell_guidance.append(row)

    # ── Add stub rows for failed tickers so they appear in Rankings ──
    for t, reason in (failed_tickers or {}).items():
        is_holding = t in MY_HOLDINGS
        signals_list.append({
            'ticker':          t,
            'price':           None,
            'change_pct':      0.0,
            'signal':          'NO_DATA',
            'guidance':        f'Data unavailable: {reason}',
            'buy_score':       0,
            'sell_score':      0,
            'sell_action':     'HOLD',
            'sell_flags':      '—',
            'sell_caution':    reason,
            'is_holding':      is_holding,
            'data_error':      reason,
            'dist_252h':       None,
            'vs_200ma':        None,
            'dfv_lift':        None,
            'factor':          False,
            'dfv3':            False,
            'fdfv3':           False,
            'pfd':             False,
            'triple':          False,
            'banker_weak':     False,
            'factor_sep':      None,
            'framework_score': FRAMEWORK_SCORES.get(t),
        })

    signals_list.sort(key=lambda x: -x['buy_score'])
    if SELL_SCORER_AVAILABLE:
        signals_list = apply_portfolio_cap(signals_list)

    # ── BUY CAP: CNN-aware (mirrors dashboard renderBuysFromPayload logic) ──
    # Extreme Fear (<20): 6+6 | Fear (20-40): 4+5 | Neutral (40-60): 3+3
    # Greed (60-80): 2+2  | Extreme Greed (>80): 1+1
    cnn = sell_market.get('cnn_score', 50)
    strong_cap  = 6 if cnn < 20 else 4 if cnn < 40 else 3 if cnn < 60 else 2 if cnn < 80 else 1
    regular_cap = 6 if cnn < 20 else 5 if cnn < 40 else 3 if cnn < 60 else 2 if cnn < 80 else 1
    sell_cap    = 6 if cnn > 80 else 4 if cnn > 60 else 3 if cnn > 40 else 2 if cnn > 20 else 1

    strong_buy_count = 0; regular_buy_count = 0
    for row in signals_list:
        if row['signal'] == 'BUY':
            if row.get('fdfv3') and row['buy_score'] >= 95:
                if strong_buy_count >= strong_cap:
                    row['signal'] = 'WATCH'; row['buy_capped'] = True
                else:
                    row['signal_tier'] = 'STRONG'; strong_buy_count += 1
            else:
                if regular_buy_count >= regular_cap:
                    row['signal'] = 'WATCH'; row['buy_capped'] = True
                else:
                    row['signal_tier'] = 'BUY'; regular_buy_count += 1
    print(f"CNN {cnn:.0f} → buy cap: {strong_buy_count}/{strong_cap} strong + {regular_buy_count}/{regular_cap} regular | sell cap: {sell_cap}")
    buy_ideas.sort(key=lambda x: -x['buy_score'])
    sell_guidance.sort(key=lambda x: -x['sell_score'])

    return {
        'generated_at':  datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'run_date':      datetime.now(BANGKOK).strftime('%A %d %b %Y'),
        'universe_size': len(signals_list),
        'market': {
            'cnn_score':       sell_market.get('cnn_score', 50),
            'cnn_label':       sell_market.get('cnn_label', 'Neutral'),
            'sp500_extended':  sell_market.get('sp500_extended', False),
            'buffett_extended':sell_market.get('buffett_extended', False),
            'strong_cap':      strong_cap,
            'regular_cap':     regular_cap,
            'sell_cap':        sell_cap,
        },
        'summary': {
            'strong_buy':  sum(1 for s in signals_list if s['fdfv3']),
            'buy':         sum(1 for s in signals_list if s['signal']=='BUY'),  # score>=80, FW>=70
            'strong_buy':  sum(1 for s in signals_list if s['signal']=='BUY' and s.get('signal_tier')=='STRONG'),
            'watch':       sum(1 for s in signals_list if s['signal']=='WATCH' and s['buy_score']>=60),
            'weak_buy':    sum(1 for s in signals_list if s['signal']=='WEAK_BUY'),
            'sell':        sum(1 for s in signals_list if s['signal']=='SELL'),
            'factor_zone': sum(1 for s in signals_list if s['factor']),
            'banker_weak': sum(1 for s in signals_list if s['banker_weak']),
        },
        'analytics': {
            'signals':       signals_list,
            'buy_ideas':     buy_ideas[:20],
            'sell_guidance': sell_guidance[:15],
        },
        'failed_tickers': failed_tickers,
        'failed_count':   len(failed_tickers),
        'held_failed':    [t for t in failed_tickers if t in MY_HOLDINGS and failed_tickers[t] != 'lse_no_signal'],
    }

# ── MAIN ──────────────────────────────────────────────────────────────
def main():
    print(f'Signal scanner starting — {datetime.now(BANGKOK).strftime("%Y-%m-%d %H:%M")} Bangkok')

    closes, highs, lows, volumes, ok = fetch_history()
    voo = closes[BENCHMARK].dropna()

    # Sell-side market + sector signals (fetched once)
    if SELL_SCORER_AVAILABLE:
        print("Fetching sell-side market + sector signals...")
        sell_market  = fetch_market_signals()
        sell_sectors = fetch_sector_signals()
        print(f"CNN: {sell_market['cnn_score']:.0f} ({sell_market['cnn_label']}) | "
              f"SP500_ext: {sell_market['sp500_extended']}")
    else:
        sell_market  = {'cnn_score':50,'cnn_label':'Neutral','sp500_extended':False,'buffett_extended':False}
        sell_sectors = {}

    print('Computing signals...')
    all_signals = {}
    failed_tickers = {}  # ticker -> reason string

    # Failure mode 1: yfinance never returned data
    all_universe = list(dict.fromkeys([BENCHMARK] + UNIVERSE))
    for t in all_universe:
        if t == BENCHMARK: continue
        if t in SKIP_SIGNAL:
            failed_tickers[t] = 'lse_no_signal'  # expected — LSE/non-yfinance tickers
        elif t not in ok:
            failed_tickers[t] = 'fetch_failed'

    # Failure mode 2: fetched but stale (last bar >5 days before latest date in dataset)
    latest_date = closes.index[-1]
    stale_threshold = latest_date - pd.Timedelta(days=5)
    for t in ok:
        if t == BENCHMARK: continue
        series = closes[t].dropna()
        if len(series) > 0 and series.index[-1] < stale_threshold:
            failed_tickers[t] = f"stale:{series.index[-1].strftime('%Y-%m-%d')}"

    # Failure mode 3: fetched but compute_signals failed or insufficient history
    for t in ok:
        if t == BENCHMARK: continue
        if t in failed_tickers: continue
        try:
            sig = compute_signals(closes[t])
            if len(sig) >= 50:
                all_signals[t] = sig
            else:
                failed_tickers[t] = f"thin_history:{len(sig)}rows"
        except Exception as e:
            failed_tickers[t] = f"signal_error:{str(e)[:60]}"

    held_fails = [t for t in failed_tickers if t in MY_HOLDINGS]
    if failed_tickers:
        print(f"Warning: {len(failed_tickers)} tickers failed ({len(held_fails)} held): {list(failed_tickers.keys())[:20]}")
    print(f"Signals ready: {len(all_signals)} tickers")

    print('Computing historical alpha...')
    ticker_alpha = compute_ticker_alpha(all_signals, closes, voo)

    live_prices = fetch_live_prices(list(all_signals.keys()))

    payload = build_payload(all_signals, ticker_alpha, live_prices,
                            closes, highs, lows, volumes,
                            sell_market, sell_sectors,
                            failed_tickers)

    out = 'signals_payload.json'
    with open(out, 'w') as f:
        json.dump(payload, f, indent=2, default=str)

    print(f'Written: {out} ({os.path.getsize(out):,} bytes)')
    print(f"Summary: {payload['summary']}")
    print(f"Sell signals: {sum(1 for s in payload['analytics']['signals'] if s.get('sell_action') not in ['HOLD','—'])}")

    # Force Netlify redeploy — updates timestamp so Netlify sees changed content
    with open('_netlify_trigger.txt', 'w') as f:
        f.write(datetime.now(BANGKOK).strftime('%Y-%m-%d %H:%M:%S Bangkok'))
    print("Netlify trigger updated.")

    for b in payload['analytics']['buy_ideas'][:5]:
        star = '★' if b['fdfv3'] else '↑' if b['factor'] else '·'
        print(f"  {star} {b['ticker']:<8} buy={b['buy_score']} {'(held)' if b['is_holding'] else ''}")

if __name__ == '__main__':
    main()
