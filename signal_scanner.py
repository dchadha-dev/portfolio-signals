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
        sell_ceiling, EXIT_T, REDUCE_T, TRIM_T, TICKER_SECTORS
    )
    SELL_SCORER_AVAILABLE = True
except ImportError:
    SELL_SCORER_AVAILABLE = False
    EXIT_T = 78; REDUCE_T = 65; TRIM_T = 52   # keep identical to sell_side_scorer.py
    TICKER_SECTORS = {}
    def sell_ceiling(_fs): return 'HOLD'
    print("sell_side_scorer.py not found -- sell signals disabled")

# ── SIGNAL THRESHOLDS — single source of truth ────────────────────────
# Every surface (dashboard tabs, daily/weekly email, weekly log) reads these
# from the payload's `thresholds` block instead of hard-coding its own copy.
BUY_T           = 80   # buy score needed for BUY
WATCH_T         = 60   # buy score needed for WATCH (factor gate must also be open)
STRONG_T        = 95   # BUY at/above this score = STRONG tier (deep dip + overlay, or +15 of overlays)
import framework_scorer
FW_MIN_BUY      = framework_scorer.BUY_MIN        # band 'Good' or better; 70 on the pre-rebuild scale
FUND_FW_MIN_BUY = framework_scorer.FUND_BUY_MIN   # 48 for funds/ETFs (was 55)

# Display label for each final state. `label` is the ONLY thing a page or
# email should print for a row — never re-derive it from buy_score/flags.
LABEL_NO_DATA    = 'NO DATA'
LABEL_STRONG_BUY = 'STRONG BUY'
LABEL_BUY        = 'BUY'
LABEL_FW_BLOCKED = 'FW BLOCKED'
LABEL_WATCH      = 'WATCH'
LABEL_HOLD       = 'HOLD'
SELL_LABELS      = ('EXIT', 'REDUCE', 'TRIM')


# ── BUY SCORE v3 ──────────────────────────────────────────────────────
# Same purpose as before: flag a good moment to add to a name you already rate.
# The setup is unchanged in spirit — "a strong stock on sale" — but the score is
# now built only from the parts that held up when re-tested (MODEL_AUDIT.md):
#
#   Strong run   12-month return / volatility > 0.6 (quality > 0.2)   +25  (0.3-0.6: +15)
#   Uptrend      close 5%+ above the 200d MA                          +25  (within 5% either side: +15)
#   Dip          20%+ below the 252d closing high                     +30  (15-20% below: +15)
#                                                          all three  = 80 = BUY
#   Deep dip     25%+ below, with the two conditions above            +10
#   Overlays     insider / PEAD / sector, combined cap                +25
#
# All three of strong run + uptrend + dip (the "gate") are needed for a BUY.
# Any two without the third tested at zero, so that scores 50-55 = HOLD. WATCH
# (60-79) means two are met and the third is close, i.e. one move from a BUY.
# Removed because they added nothing in any universe or period: DFV V3 (the gate
# did as well or better without it), PFD, Triple, RSI timing, both penalties.
# v2 needed gate + DFV V3 + an overlay to reach 80, which happened on 0.01% of
# stock-days; the gate alone is ~1% on this watch-list.
MODEL_VERSION = 'v3'
NEAR_DIST_T = -0.15   # "approaching" a dip
NEAR_TREND_T = -0.05  # within 5% of the 200d MA, either side
UP_TREND_T = 0.05     # a dip that leaves less than 5% of cushion over the 200d MA tested at zero
NEAR_QUALITY_T = 0.10 # 12m return/vol 0.3-0.6
DEEP_DIST_T = -0.25   # deep dip
OVERLAY_CAP = 25


def technical_buy_score(last):
    """Price-derived part of the buy score (v3). Used for direct tickers AND
    Thai proxy funds so the two can never drift apart."""
    buy = 0
    buy += 25 if last['qual'] else 15 if last['qualc'] else 0
    buy += 25 if last['up']   else 15 if last['upc']   else 0
    buy += 30 if last['dip']  else 15 if last['near']  else 0
    if last['deepdip']: buy += 10
    return buy


def technical_buy_frame(df):
    """Vectorised technical_buy_score() over a whole compute_signals() frame.
    Used by validate_signals.py so the backtest scores history with exactly the
    weights that run live. Keep the two functions in lockstep (tested)."""
    b = lambda c: df[c].astype(bool)
    return (25 * b('qual') + 15 * (b('qualc') & ~b('qual')) + 25 * b('up') + 15 * (b('upc') & ~b('up'))
            + 30 * b('dip') + 15 * (b('near') & ~b('dip')) + 10 * b('deepdip')).clip(0, 100)


def technical_buy_score_v2(last):
    """The previous model, kept only so the payload can show both scores side by
    side during the transition (`buy_score_v2`). It labels nothing."""
    buy = 0
    if last['f']:      buy += 40
    if last['fdfv3']:  buy += 25
    if last['pfd']:    buy += 8
    if last['triple']: buy += 10
    if last['dfv3'] and not last['f']: buy += 5
    if last['banker_weak']: buy -= 20
    if last['rbear']:       buy -= 10
    return max(0, min(100, buy))


def cap_overlays(insider, pead, sector, cap=OVERLAY_CAP):
    """Insider, PEAD and sector boosts share one cap, applied in that order.
    The README always said the combined boost was capped; the code let it reach 43."""
    out = []; room = cap
    for v in (insider, pead, sector):
        v = max(0, min(v or 0, room)); out.append(v); room -= v
    return out


def classify_buy(buy, gate, near, fs, fw_min=FW_MIN_BUY):
    """Buy-side state before caps: BUY | WEAK_BUY | WATCH | HOLD.
    BUY needs the full gate (strong run + 5% over the 200d MA + 20% dip). WATCH needs at
    least a 15% dip. Overlay boosts (insider/PEAD/sector) cannot create either
    on their own."""
    if gate and buy >= BUY_T:
        return 'WEAK_BUY' if (fs is not None and fs < fw_min) else 'BUY'
    if near and buy >= WATCH_T:
        return 'WATCH'
    return 'HOLD'


def display_label(row):
    """The one label every surface shows for a row. Sell actions on held
    positions win over buy-side states (risk first)."""
    if row.get('signal') == 'NO_DATA':
        return LABEL_NO_DATA
    if row.get('is_holding') and row.get('sell_action') in SELL_LABELS:
        return row['sell_action']
    sig = row.get('signal')
    if sig == 'BUY':
        return LABEL_STRONG_BUY if row.get('signal_tier') == 'STRONG' else LABEL_BUY
    if sig == 'WEAK_BUY':
        return LABEL_FW_BLOCKED
    if sig == 'WATCH':
        return LABEL_WATCH
    return LABEL_HOLD


def buy_breakdown(last, insider_boost=0, pead_boost=0, sector_boost=0):
    """[name, points] pairs that sum to the (pre-clamp) buy score, so a card
    can show exactly why a score is what it is."""
    parts = []
    if last['qual']:    parts.append(['Strong 12m run', 25])
    elif last['qualc']: parts.append(['Fair 12m run', 15])
    if last['up']:      parts.append(['5%+ above 200d MA', 25])
    elif last['upc']:   parts.append(['Within 5% of 200d MA', 15])
    if last['dip']:     parts.append(['Dip 20%+', 30])
    elif last['near']:  parts.append(['Dip 15-20%', 15])
    if last['deepdip']: parts.append(['Deep dip 25%+', 10])
    if insider_boost and insider_boost > 0: parts.append(['Insider/Politician', insider_boost])
    if pead_boost and pead_boost > 0:       parts.append(['PEAD', pead_boost])
    if sector_boost and sector_boost > 0:   parts.append(['Sector', sector_boost])
    return parts

# ── INSIDER & POLITICIAN SIGNALS ──────────────────────────────────────
def load_insider_signals():
    """Load pre-fetched insider/politician signals. Gracefully returns {} if missing/stale."""
    try:
        with open('insider_signals.json') as f:
            data = json.load(f)
        health = data.get('health', {})
        ts_str = health.get('timestamp', '')
        if ts_str:
            try:
                age_days = (datetime.now() - datetime.strptime(ts_str[:16], '%Y-%m-%d %H:%M')).days
                if age_days > 14:
                    # Two missed weekly fetches. Boosts from a 60d lookback that
                    # is itself 2+ weeks old are no longer evidence — drop them.
                    print(f'insider_signals.json is {age_days} days old — ignoring (no boosts applied)')
                    return {}
                elif age_days > 8:
                    print(f'Warning: insider_signals.json is {age_days} days old')
                elif health.get('retry_required'):
                    print('Warning: insider_signals.json marked retry_required')
                else:
                    n = health.get('n_combined_signals', 0)
                    print(f'Insider signals loaded: {n} tickers (age: {age_days}d)')
            except: pass
        return data.get('signals', {})
    except FileNotFoundError:
        print('insider_signals.json not found — run weekly_insider_signals workflow')
        return {}
    except Exception as e:
        print(f'insider_signals.json load error: {e}')
        return {}


def load_pead_signals():
    """Load PEAD signals, filtering out expired ones."""
    try:
        with open('pead_signals.json') as f:
            data = json.load(f)
        today   = datetime.now().strftime('%Y-%m-%d')
        signals = data.get('signals', {})
        active  = {t: s for t, s in signals.items()
                   if s.get('expires_at', '2000-01-01') >= today}
        if active:
            print(f'PEAD signals loaded: {len(active)} active signals')
        return active
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f'pead_signals.json load error: {e}')
        return {}

# ── CONFIGURATION ─────────────────────────────────────────────────────
FINNHUB_TOKEN = os.environ.get('FINNHUB_TOKEN', '')

MY_HOLDINGS = [
    # Direct US stocks
    'NVDA','AVGO','TSLA','MELI','AMAT','MSFT','AAPL','AMZN','META','GOOG',
    'NFLX','BKNG','SHOP','RACE','AMD','CRWV','ASML','ANET','DDOG','CRDO',
    'NBIS','TSM','TM','MU','INTU','CPRT','PGR','O','TEAM',
    'BRKB','NVO','RELX','DELL','BABA','KLAC',
    'NOW','IREN','GDX','ORCL',
    'QCOM','MSTR','GME','CCJ','UNH',
    # European stocks
    'RMS.PA','MC.PA','ITX.MC',
    # US ETFs
    'VOO','VTI','QQQ','JEPI','VXUS','VSS','IEV','URTH','GLD','XLG',
    'XLB','CQQQ',
    # Thematic ETFs
    'AIQG','QNTM.L','QTUM',
    # Thai mutual funds — SCB unchanged, KT all closed
    'SCB_SP500','SCB_NDQ','SCB_SEMI','SCB_WORLD','SCB_GOLD','SCB_NK225',
    'SCB_SET50','SCB_DJ','SCB_AIEM','SCB_FINTECH','SCB_AUTO','SCB_INNOV',
    'SCB_GENO','SCB_CHINA','SCB_EV','SCB_BUSAA',
]

CANDIDATES = [
    # Original candidates
    'PANW','AXON','CEG','CELH','DECK','ENPH','HIMS',
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
    'HON','BIDU','PDD','JD','T',
    'VZ','WMT','IBM','CSCO','INTC','ON',
    'INFY','SONY','HDB','HSBC','AER','CB',
    'HPE','SOUN','FIVN','ONDS','CBRS','NXT',
    'BULL','SOLS','RDDT','KEEL','SEDG',
    'SNDK','SYM','FORM','VICR','RIO','FCX',
    'SLV','LIT','BEP','DNN','MSTR','AAL','OWL',
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
# ── holdings.json override (written by the dashboard editor) ─────────
# If holdings.json exists in the repo root, its ticker list replaces the
# direct-equity portion of MY_HOLDINGS. Thai proxy funds stay defined above,
# since they are managed through TICKER_PROXY_MAP rather than the editor.
try:
    import json as _json, os as _os
    if _os.path.exists('holdings.json'):
        with open('holdings.json') as _f:
            _hj = _json.load(_f)
        _editor_tickers = [s['t'].strip().upper() for s in _hj.get('stocks', [])
                           if s.get('t') and s['t'].strip()]
        if _editor_tickers:
            _thai = [t for t in MY_HOLDINGS if t in TICKER_PROXY_MAP]
            MY_HOLDINGS = list(dict.fromkeys(_editor_tickers + _thai))
            print(f"holdings.json: {len(_editor_tickers)} tickers loaded "
                  f"(updated {_hj.get('updated_at','?')})")
except Exception as _e:
    print(f"holdings.json not applied ({_e}) — using built-in MY_HOLDINGS")

# Funds whose proxy is only a placeholder. They stay in the holdings list but get
# no buy/sell signal: a SPY-derived "buy" on a Thai SET50 fund is not information.
NO_SIGNAL_FUNDS = {'SCB_SET50'}

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

# Framework scores are no longer a hand-typed table in this file.
# Inputs live in framework_scores.json (moat judgement, hand sub-scores, the old
# totals) and fundamentals.json (weekly Finnhub fetch); framework_scorer.py turns
# them into a 0-100 score per ticker. See FRAMEWORK.md.
FRAMEWORK_DETAIL = framework_scorer.score_all()
FRAMEWORK_SCORES = {t: d['score'] for t, d in FRAMEWORK_DETAIL.items()}


# Benchmarks for fund track records that are not otherwise in the universe.
FUND_BENCHMARKS = ['SPY', 'QQQ', 'URTH', 'GLD', 'AGG']
ADJ_CLOSES = {}   # filled by the price fetch: dividend-adjusted closes for fund track records


def refresh_framework_scores(closes):
    """Re-score with today's closes so the valuation axis moves with price, and
    funds' track records use the latest dividend-adjusted history.
    Mutates the two dicts in place; every later lookup sees the fresh values."""
    try:
        prices = {t: float(closes[t].dropna().iloc[-1]) for t in closes.columns if closes[t].notna().any()}
    except Exception:
        prices = {}
    adj = ADJ_CLOSES.get('df')
    src = adj if adj is not None else closes
    try:
        series = {t: src[t].dropna() for t in src.columns}
    except Exception:
        series = {}
    FRAMEWORK_DETAIL.clear(); FRAMEWORK_DETAIL.update(framework_scorer.score_all(prices=prices, series=series))
    FRAMEWORK_SCORES.clear(); FRAMEWORK_SCORES.update({t: d['score'] for t, d in FRAMEWORK_DETAIL.items()})


def framework_row(t):
    """Compact per-row framework detail for the payload."""
    d = FRAMEWORK_DETAIL.get(t)
    if not d:
        return None
    return {'band': d['band'], 'parts': d['parts'], 'sources': d['sources'], 'legacy': d['legacy'], 'kind': d['kind'],
            'provisional': d['provisional'], 'moat_date': d['moat_date'], 'moat_stale': d['moat_stale'], 'pe': d['pe'],
            'industry': d.get('industry'), 'scaling': d.get('scaling', False),
            'ter': d.get('ter'), 'benchmark': d.get('benchmark'), 'excess_3y': d.get('excess_3y'),
            'beat_share': d.get('beat_share')}

# ── SIGNAL ENGINE ─────────────────────────────────────────────────────
def calc_rsi(series, period):
    delta    = series.diff()
    avg_gain = delta.clip(lower=0).ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = (-delta).clip(lower=0).ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float('nan'))
    return 100 - (100 / (1 + rs))

def calc_quality(series, window=252):
    """
    Legacy Sharpe-proxy — retained for backward compatibility but no longer
    used as primary quality gate. Replaced by gross_profitability_proxy below.
    """
    ret = series.pct_change(window)
    vol = series.pct_change().rolling(window).std() * np.sqrt(252)
    return (ret / vol.replace(0, float('nan')) / 3).clip(0, 1)

def calc_gross_profitability_proxy(series, window=252):
    """
    Novy-Marx (2013) gross profitability proxy using price-series only.
    Fully vectorised — no Python loops (50-100x faster than loop version).
    Components:
      - Return consistency: fraction of up-days (earnings stability proxy)
      - Drawdown resilience: inverse of max drawdown (safety proxy)
      - Trend smoothness: R² of log-price vs time (steady compounder proxy)
    Weighted 0.40 / 0.35 / 0.25 → quality score in [0, 1].
    """
    cl = series.dropna()
    if len(cl) < window:
        return pd.Series(np.nan, index=series.index)

    daily_ret = cl.pct_change()

    # Component 1: return consistency
    consistency = daily_ret.gt(0).rolling(window).mean()

    # Component 2: drawdown resilience
    # Component 2: drawdown resilience
    roll_max   = cl.rolling(window, min_periods=20).max()
    dd         = ((cl - roll_max) / roll_max.replace(0, float('nan'))).clip(lower=-1.0)
    safety     = (1.0 + dd / 0.5).clip(0, 1)

    # Component 3: trend smoothness — vectorised rolling R²
    log_cl  = np.log(cl.replace(0, float('nan')))
    lp_std  = log_cl.rolling(window).std()
    t_mean  = (window - 1) / 2.0
    t_std   = np.sqrt(((window - 1) * (2 * window - 1)) / 6.0 - t_mean ** 2)
    roll_cov = log_cl.rolling(window).apply(
        lambda x: np.dot(x - x.mean(), (np.arange(len(x)) - t_mean)) / (len(x) * t_std + 1e-9),
        raw=True
    )
    smoothness = (roll_cov / (lp_std + 1e-9)).clip(-1, 1) ** 2

    quality = (0.40 * consistency + 0.35 * safety + 0.25 * smoothness)
    result  = pd.Series(np.nan, index=series.index)
    result.loc[quality.index] = quality.values
    return result

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
    # ── v3 buy-score inputs ──────────────────────────────────────────
    df['up']          = df['trend'] > UP_TREND_T
    df['qual']        = df['quality'] > QUALITY_T
    df['upc']         = df['trend'] > NEAR_TREND_T
    df['qualc']       = df['quality'] > NEAR_QUALITY_T
    df['dip']         = df['dist'] < DIST_T
    df['near']        = df['dist'] < NEAR_DIST_T
    df['gate']        = df['dip'] & df['up'] & df['qual']
    df['deepdip']     = df['gate'] & (df['dist'] < DEEP_DIST_T)
    return df.dropna(subset=['dist', 'hm_lift'])

# ── TICKER ALIASES — yfinance uses different symbols for some tickers ──
TICKER_ALIASES = {
    'BRKB':   'BRK-B',      # Berkshire B — yfinance requires hyphen
    '005930': '005930.KS',  # Samsung Electronics — KRX listing needs the .KS suffix
}
ALIAS_REVERSE = {v: k for k, v in TICKER_ALIASES.items()}

# ── FETCH DATA ────────────────────────────────────────────────────────
def fetch_history():
    end   = datetime.today()
    start = end - timedelta(days=YEARS*365 + 60)

    # Apply aliases for batch fetch
    all_t = list(dict.fromkeys([BENCHMARK] + UNIVERSE + FUND_BENCHMARKS))
    fetch_t = [TICKER_ALIASES.get(t, t) for t in all_t]
    fetch_t = list(dict.fromkeys(fetch_t))  # dedupe after alias

    print(f'Fetching {len(fetch_t)} tickers ({YEARS}yr daily)...')
    # auto_adjust=False → split-adjusted but NOT dividend-adjusted.
    # Matches TradingView's default chart. Critical for high-yield holdings
    # (JEPI ~9%, O ~5%): dividend adjustment depresses historical prices, which
    # makes current price look like a new 252d high and inflates ATR extension.
    raw = yf.download(fetch_t, start=start, end=end, interval='1d',
                      auto_adjust=False, progress=False, threads=True)

    if isinstance(raw.columns, pd.MultiIndex):
        closes = raw['Close'].dropna(how='all')
        if 'Adj Close' in raw.columns.get_level_values(0):
            ADJ_CLOSES['df'] = raw['Adj Close'].dropna(how='all')
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
    if 'df' in ADJ_CLOSES:
        ADJ_CLOSES['df'] = ADJ_CLOSES['df'].rename(columns=ALIAS_REVERSE)
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
                                     auto_adjust=False, progress=False)
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
FINNHUB_SYMBOLS = {'BRKB': 'BRK.B'}

def finnhub_symbol(t):
    """Map a scanner ticker to its Finnhub US symbol, or None if there isn't one.
    The free Finnhub tier only quotes US listings. Stripping an exchange suffix
    silently returns a DIFFERENT company (MC.PA -> 'MC' is Moelis & Co, not
    LVMH), so non-US tickers are skipped rather than guessed."""
    if t in FINNHUB_SYMBOLS:
        return FINNHUB_SYMBOLS[t]
    if '.' in t or t.isdigit() or '_' in t:
        return None
    return t

def fetch_live_prices(tickers):
    if not FINNHUB_TOKEN:
        print('No FINNHUB_TOKEN — using yfinance last close')
        return {}
    live = {}
    print(f'Fetching live prices from Finnhub for {len(tickers)} tickers...')
    for t in tickers:
        sym = finnhub_symbol(t)
        if sym is None:
            continue   # non-US listing — keep the yfinance close in local currency
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
    trend = float(last['trend']); dist = float(last['dist']); q = float(last['quality']) if last['quality'] == last['quality'] else 0.0
    side = 'above' if trend >= 0 else 'below'
    facts = f"{abs(dist)*100:.0f}% below the 252d high, {abs(trend)*100:.1f}% {side} the 200d MA, 12m return/vol {q*3:.1f}"
    if last['gate']:
        parts.append(("Deep dip" if last['deepdip'] else "Dip") + f" in a strong stock: {facts}. All three buy conditions are met.")
    elif last['near']:
        missing = []
        if not last['dip']:  missing.append(f"dip is not yet {abs(DIST_T)*100:.0f}%")
        if not last['up']:   missing.append(f"price is not {UP_TREND_T*100:.0f}%+ above the 200d MA")
        if not last['qual']: missing.append(f"12m return/vol is below {QUALITY_T*3:.1f}")
        parts.append(f"Not a buy: {facts}. Missing: {'; '.join(missing)}.")
    else:
        parts.append(f"No dip: {facts}. The score needs a 20%+ drop from the high in a stock that is still strong.")
    if fs:
        verdict = {"Core": "Core holding.", "Good": "Good business; monitor."}.get(framework_scorer.band_of(fs), "Below the framework minimum for a buy.")
        parts.append(f"Framework: {fs}/100. {verdict}")
    return " ".join(parts)

# ── SCORE + BUILD PAYLOAD ─────────────────────────────────────────────
def update_weekly_signal_log(payload):
    """
    Append new buy/watch/sell signals from this run to weekly_signals_log.json.
    Prunes entries older than 7 days. Each entry records:
      - ticker, signal type, score, timestamp, price, dist_252h, key flags
    On the next run, existing entries are cross-checked against current signals
    to determine if the signal is still active or has expired (and why).
    """
    log_path = 'weekly_signals_log.json'
    now      = datetime.now()
    cutoff   = now - timedelta(days=7)

    # Load existing log
    try:
        with open(log_path) as f:
            log = json.load(f)
    except FileNotFoundError:
        log = {'entries': []}
    except Exception:
        log = {'entries': []}

    entries = log.get('entries', [])

    # Prune entries older than 7 days
    entries = [e for e in entries
               if datetime.strptime(e['timestamp'][:16], '%Y-%m-%d %H:%M') > cutoff]

    # Current signal state for cross-referencing
    current_signals = {s['ticker']: s for s in payload.get('analytics', {}).get('signals', [])}

    # Update existing entries — mark expired ones with reason
    for entry in entries:
        if entry.get('status') != 'active':
            continue
        t   = entry['ticker']
        cur = current_signals.get(t, {})
        if not cur:
            entry['status']       = 'expired'
            entry['expired_at']   = now.strftime('%Y-%m-%d %H:%M')
            entry['expire_reason'] = 'ticker_missing'
            continue

        was_buy   = entry.get('signal_type') in ('BUY', 'WATCH')
        was_sell  = entry.get('signal_type') == 'SELL'
        cur_score = cur.get('buy_score', 0)
        cur_sell  = cur.get('sell_score', 0)
        cur_sig   = cur.get('signal', '')

        if was_buy:
            if cur_sig == 'BUY':
                entry['current_score'] = cur_score
                entry['current_price'] = cur.get('price')
                # stays active
            elif cur_score < entry.get('score', 80) - 10:
                # Score dropped materially — say which part of the setup went away.
                if not cur.get('trend_up', True):
                    reason, detail = 'trend_broken', 'closed below the 200d MA'
                elif not cur.get('dip', False):
                    reason, detail = 'dip_recovered', f'back within {abs(DIST_T)*100:.0f}% of the 252d high · score {cur_score}'
                else:
                    reason, detail = 'score_dropped', f'score {cur_score}'
                entry['status']        = 'expired'
                entry['expired_at']    = now.strftime('%Y-%m-%d %H:%M')
                entry['expire_reason'] = reason
                entry['expire_detail'] = detail
            else:
                entry['current_score'] = cur_score
                entry['current_price'] = cur.get('price')

        elif was_sell:
            if cur_sell < 30:
                entry['status']       = 'expired'
                entry['expired_at']   = now.strftime('%Y-%m-%d %H:%M')
                entry['expire_reason'] = 'sell_cleared'
                entry['expire_detail'] = f'sell score dropped to {cur_sell:.0f}'

    # Add new signals from this run (if not already logged today)
    logged_today = {e['ticker'] for e in entries
                    if e['timestamp'][:10] == now.strftime('%Y-%m-%d')}

    for s in payload.get('analytics', {}).get('signals', []):
        t      = s.get('ticker', '')
        sig    = s.get('signal', '')
        buy_sc = s.get('buy_score', 0)
        sell_sc= s.get('sell_score', 0)

        # Log exactly what the dashboard labels: buy-side states and sell
        # actions on held positions. Driven by `label`, never re-derived.
        label = s.get('label') or display_label(s)
        is_notable_buy  = label in (LABEL_STRONG_BUY, LABEL_BUY, LABEL_WATCH)
        is_notable_sell = label in ('EXIT', 'REDUCE')

        if not (is_notable_buy or is_notable_sell):
            continue
        if t in logged_today:
            continue
        # Don't re-log while an entry on the same side is still active.
        # (Entries are stored as BUY/WATCH/SELL, so compare by side — comparing
        # 'WATCH' to 'BUY' never matched and re-logged the ticker every run.)
        side_types = ('BUY', 'WATCH') if is_notable_buy else ('SELL',)
        already_active = any(e['ticker'] == t and e.get('status') == 'active'
                             and e.get('signal_type') in side_types
                             for e in entries)
        if already_active:
            continue

        entry = {
            'ticker':        t,
            'signal_type':   ('BUY' if label in (LABEL_STRONG_BUY, LABEL_BUY)
                              else 'WATCH' if is_notable_buy else 'SELL'),
            'label':         label,
            'score':         buy_sc if is_notable_buy else sell_sc,
            'sell_score':    sell_sc,
            'timestamp':     now.strftime('%Y-%m-%d %H:%M'),
            'price':         s.get('price'),
            'dist_252h':     s.get('dist_252h'),
            'framework_score': s.get('framework_score'),
            'fdfv3':         s.get('fdfv3', False),
            'is_holding':    s.get('is_holding', False),
            'status':        'active',
            'current_score': buy_sc if is_notable_buy else sell_sc,
            'current_price': s.get('price'),
            'expire_reason': None,
            'expire_detail': None,
            'expired_at':    None,
        }
        entries.append(entry)
        print(f'  Signal log: +{t} ({entry["signal_type"]} {entry["score"]})')

    log = {
        'last_updated': now.strftime('%Y-%m-%d %H:%M UTC'),
        'entries': entries,
    }
    with open(log_path, 'w') as f:
        json.dump(log, f, indent=2, default=str)
    active = sum(1 for e in entries if e.get('status') == 'active')
    print(f'Weekly signal log: {len(entries)} entries ({active} active) → weekly_signals_log.json')


def build_payload(all_signals, ticker_alpha, live_prices, closes, highs, lows, volumes,
                  sell_market, sell_sectors, failed_tickers=None):
    signals_list = []
    if failed_tickers is None: failed_tickers = {}
    refresh_framework_scores(closes)   # valuation axis at today's prices

    # Load weekly alternative data signals
    insider_signals = load_insider_signals()
    pead_signals    = load_pead_signals()

    # Extract macro_ok once before the ticker loop — it's market-wide
    macro_ok = True
    for sector_data in sell_sectors.values():
        if isinstance(sector_data, dict) and 'macro_ok' in sector_data:
            macro_ok = sector_data['macro_ok']
            break

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
        # Proposed model (CPCV validated 2026-05-24):
        # - Gross-profitability quality gate (DSR 23.5, ann 33.1%)
        # - DFV V3 full weight +25pts (DSR 8.84, ann 38.5% — do not demote)
        # - PFD reduced +20 → +8pts (valid but weaker: DSR 33.5, ann 11.3%)
        # - Triple unchanged +10pts
        buy = technical_buy_score(last)

        # ── INSIDER / POLITICIAN BOOST ────────────────────────────────
        ins_data        = insider_signals.get(t, {})
        insider_boost   = ins_data.get('buy_score_boost', 0)
        insider_score   = ins_data.get('insider_score', 0)
        pol_score       = ins_data.get('politician_score', 0)
        insider_cluster = ins_data.get('insider_cluster', False)
        pol_cluster     = ins_data.get('politician_cluster', False)
        reporting_lag   = ins_data.get('reporting_lag_days', None)

        # ── PEAD BOOST ────────────────────────────────────────────────
        pead_data  = pead_signals.get(t, {})
        pead_boost = pead_data.get('buy_score_boost', 0)
        pead_sue   = pead_data.get('sue', None)

        # ── SECTOR SENTIMENT BOOST ────────────────────────────────────
        # Uses the four-condition hierarchy from fetch_sector_signals():
        # macro_ok + trend_ok + dip_ok + breadth_ok → entry_signal
        # entry_signal (all 4): +8pts — high-confidence sector tailwind
        # trend_ok only: +3pts — sector outperforming SPY, mild tailwind
        sector_buy_boost = 0
        ticker_sectors   = TICKER_SECTORS.get(t, [])
        for sector in ticker_sectors:
            sector_data = sell_sectors.get(sector, {})
            if not isinstance(sector_data, dict):
                continue
            if sector_data.get('entry_signal'):
                sector_buy_boost = 8   # all four conditions — strong tailwind
                break
            elif sector_data.get('trend_ok') and sector_data.get('macro_ok'):
                sector_buy_boost = max(sector_buy_boost, 3)  # trend only — mild
        # Overlays can lift a score within its band but never across a label
        # boundary: without the gate the score stops at 79 (WATCH), without at
        # least a 15% dip at 59 (HOLD). Score and label can then never disagree.
        ceiling = 100 if last['gate'] else (BUY_T - 1 if last['near'] else WATCH_T - 1)
        insider_boost, pead_boost, sector_buy_boost = cap_overlays(
            insider_boost, pead_boost, sector_buy_boost, cap=max(0, min(OVERLAY_CAP, ceiling - buy)))
        buy += insider_boost + pead_boost + sector_buy_boost

        buy = max(0, min(100, buy))

        # ── SELL SCORE ────────────────────────────────────────────────
        # sell_score_raw must be reset per ticker: it used to be assigned only
        # on success, so a failed ticker silently inherited the previous one's.
        sell_score = 0; sell_score_raw = 0; sell_action = 'HOLD'
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
                sell_score, sell_score_raw, sell_action, sell_flags, sell_caution = score_ticker(
                    t, sell_sigs_data, sell_market, sell_sectors, fs)
            except Exception as sell_err:
                sell_caution = f'err:{str(sell_err)[:40]}'

        # ── SIGNAL CLASSIFICATION ─────────────────────────────────────
        # Buy-side state only. SELL and the final display `label` are assigned
        # in finalize_signals() AFTER the sell/buy caps, so a capped signal can
        # never be shown as live on one surface and capped on another.
        signal = classify_buy(buy, bool(last['gate']), bool(last['near']), fs, FW_MIN_BUY)

        guidance = build_guidance(t, last, ta, fs)

        row = {
            'ticker':          t,
            'price':           price,
            'change_pct':      change,
            'signal':          signal,
            'guidance':        guidance,
            'buy_score':       buy,
            'sell_score':      sell_score,
            'sell_score_raw':  sell_score_raw,
            'sell_action':     sell_action,
            'sell_flags':      sell_flags,
            'sell_caution':    sell_caution,
            'sell_dist':       sell_sigs_data.get('dist'),
            'sell_cmf':        sell_sigs_data.get('cmf_20'),
            'sell_rv_z':       sell_sigs_data.get('rv_z'),
            'sell_weekly_rsi': sell_sigs_data.get('weekly_rsi'),
            'near_high':       sell_sigs_data.get('near_high', False),
            'sell_atr_dist':   sell_sigs_data.get('sma_atr_dist'),
            'sell_ceiling':    sell_ceiling(fs),   # strongest action this FW score can ever reach
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
            'rbear':           bool(last['rbear']),
            'gate':            bool(last['gate']),
            'trend_up':        bool(last['up']),
            'strong_run':      bool(last['qual']),
            'dip':             bool(last['dip']),
            'dip_near':        bool(last['near']),
            'trend_near':      bool(last['upc']),
            'run_near':        bool(last['qualc']),
            'deep_dip':        bool(last['deepdip']),
            'run_12m':         None if last['quality'] != last['quality'] else round(float(last['quality']) * 3, 2),
            'rsi14':           round(float(last['rsi14']), 1),
            'buy_score_v2':    technical_buy_score_v2(last),
            'factor_sep':      None if not fa_valid else round(float(fa)*100, 1),
            'framework_score': fs,
            'fw_min_buy':      FW_MIN_BUY,
            'framework':       framework_row(t),
            'buy_breakdown':   buy_breakdown(last, insider_boost, pead_boost, sector_buy_boost),
            # Insider & politician signals
            'insider_boost':     insider_boost,
            'insider_score':     insider_score,
            'politician_score':  pol_score,
            'insider_cluster':   insider_cluster,
            'politician_cluster':pol_cluster,
            'reporting_lag_days':reporting_lag,
            # PEAD signals
            'pead_boost':        pead_boost,
            'pead_sue':          pead_sue,
            'pead_expires':      pead_data.get('expires_at', None),
            # Sector sentiment
            'sector_boost':      sector_buy_boost,
            'macro_ok':          macro_ok,
        }

        signals_list.append(row)

    # ── Add Thai fund rows using proxy signal data ───────────────────
    for fund, proxy in TICKER_PROXY_MAP.items():
        # Only funds still held. Closed funds (all KT_*) used to be emitted
        # with is_holding=True, which showed sold positions as HELD and
        # inflated the held count that sizes the sell cap.
        if fund not in MY_HOLDINGS: continue
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

        # Same technical score as direct tickers. This block had drifted:
        # PFD was still +20 (direct: +8) and the RBear -10 penalty was missing,
        # so a fund and its own proxy ETF could show different scores.
        buy = max(0, min(100, technical_buy_score(last)))
        no_signal = fund in NO_SIGNAL_FUNDS
        if no_signal:
            buy = 0

        sell_score = 0; sell_action = 'HOLD'; sell_flags = '—'; sell_caution = '—'
        sell_score_raw = 0
        sell_sigs_data = {}
        # Thai mutual funds use US ETF proxies for buy signals (directional correlation)
        # but sell signals based on proxy prices are meaningless — SET50 ≠ SPY price action.
        # Sell scoring is skipped for all proxy-mapped funds.
        sell_caution = 'proxy_fund — sell scoring disabled'

        signal = classify_buy(buy, bool(last['gate']), bool(last['near']), fs, FUND_FW_MIN_BUY)

        guidance = build_guidance(proxy, last, ta, fs)
        guidance = (f"No signal: {proxy} is a placeholder, not a valid proxy for this fund."
                    if no_signal else f"[via {proxy}] {guidance}")

        row = {
            'ticker': fund, 'price': price, 'change_pct': 0.0,
            'signal': signal, 'guidance': guidance,
            'buy_score': buy, 'sell_score': sell_score,
            'sell_score_raw': sell_score_raw,
            'sell_action': sell_action, 'sell_flags': sell_flags,
            'sell_caution': sell_caution,
            'sell_dist': None,
            'sell_cmf': None,
            'sell_rv_z': None,
            'sell_weekly_rsi': None,
            'near_high': False,
            'sell_atr_dist': None,
            'is_holding': True, 'proxy': proxy,
            'dist_252h': round(float(last['dist'])*100, 1),
            'vs_200ma':  round(float(last['trend'])*100, 1),
            'dfv_lift':  round(float(last['hm_lift']), 1),
            'factor': bool(last['f']), 'dfv3': bool(last['dfv3']),
            'fdfv3': bool(last['fdfv3']), 'pfd': bool(last['pfd']),
            'triple': bool(last['triple']), 'banker_weak': bool(last['banker_weak']),
            'rbear': bool(last['rbear']),
            'gate':            bool(last['gate']),
            'trend_up':        bool(last['up']),
            'strong_run':      bool(last['qual']),
            'dip':             bool(last['dip']),
            'dip_near':        bool(last['near']),
            'trend_near':      bool(last['upc']),
            'run_near':        bool(last['qualc']),
            'deep_dip':        bool(last['deepdip']),
            'run_12m':         None if last['quality'] != last['quality'] else round(float(last['quality']) * 3, 2),
            'rsi14':           round(float(last['rsi14']), 1),
            'buy_score_v2':    technical_buy_score_v2(last),
            'factor_sep': None if not fa_valid else round(float(fa)*100, 1),
            'framework_score': fs,
            'fw_min_buy': FUND_FW_MIN_BUY,
            'framework': framework_row(fund),
            'buy_breakdown': [] if no_signal else buy_breakdown(last, 0, 0, 0),
            'sell_ceiling': 'HOLD',   # sell scoring is disabled for proxy funds
        }
        if no_signal:
            row.update(factor=False, dfv3=False, fdfv3=False, pfd=False,
                       triple=False, banker_weak=False, rbear=False,
                       gate=False, trend_up=False, strong_run=False, dip=False,
                       dip_near=False, trend_near=False, run_near=False,
                       deep_dip=False, buy_score_v2=0)
        signals_list.append(row)

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
            'sell_score_raw':  0,
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
            'framework': framework_row(t),
        })

    signals_list.sort(key=lambda x: -x['buy_score'])

    cnn   = sell_market.get('cnn_score', 50)
    vix_z = sell_market.get('vix_zscore', 0.0) or 0.0
    n_held = len([r for r in signals_list if r.get('is_holding')])

    if SELL_SCORER_AVAILABLE:
        signals_list = apply_portfolio_cap(signals_list, cnn_score=cnn, held_count=n_held)

    # Sell cap: CNN-aware (display/count only — actual score uses VIX multiplier)
    sell_cap_pct = 0.20 if cnn > 80 else 0.15 if cnn > 60 else 0.10 if cnn > 40 else 0.08 if cnn > 20 else 0.05
    sell_cap     = max(1, round(n_held * sell_cap_pct))

    # ── BUY CAP: VIX z-score aware ────────────────────────────────────
    # HIGH VIX (fear/panic) → expand buys (stocks are cheap, more signals warranted)
    # LOW VIX (complacency) → tighten buys (market extended, be selective)
    # Mirrored from sell logic: VIX panic = more buys, VIX greed = fewer buys
    if   vix_z > 2:   strong_cap = 6; regular_cap = 6   # extreme fear — expand
    elif vix_z > 1:   strong_cap = 4; regular_cap = 5   # fear
    elif vix_z > -1:  strong_cap = 3; regular_cap = 3   # neutral
    elif vix_z > -2:  strong_cap = 2; regular_cap = 2   # greed
    else:             strong_cap = 1; regular_cap = 1   # extreme greed — tighten

    # Macro filter: tighten caps if SPY below 200d SMA
    if not macro_ok:
        strong_cap  = max(1, strong_cap - 1)
        regular_cap = max(1, regular_cap - 1)
        print(f'Macro filter: SPY below 200d SMA — buy caps tightened to {strong_cap}/{regular_cap}')

    # A held position with a live sell action (after the sell cap) is a sell,
    # not a buy: EXIT becomes signal=SELL, and none of them use a buy slot.
    for row in signals_list:
        if row.get('is_holding') and row.get('sell_action') == 'EXIT':
            row['signal'] = 'SELL'

    strong_buy_count = 0; regular_buy_count = 0
    for row in signals_list:
        if row['signal'] == 'BUY' and not (row.get('is_holding') and row.get('sell_action') in SELL_LABELS):
            if row.get('gate') and row['buy_score'] >= STRONG_T:
                if strong_buy_count >= strong_cap:
                    row['signal'] = 'WATCH'; row['buy_capped'] = True
                else:
                    row['signal_tier'] = 'STRONG'; strong_buy_count += 1
            else:
                if regular_buy_count >= regular_cap:
                    row['signal'] = 'WATCH'; row['buy_capped'] = True
                else:
                    row['signal_tier'] = 'BUY'; regular_buy_count += 1

    print(f"VIX z={vix_z:.1f} → buy cap: {strong_buy_count}/{strong_cap} strong + {regular_buy_count}/{regular_cap} regular | CNN {cnn:.0f} → sell cap: {sell_cap}")

    # ── FINAL LABEL — computed once, after every cap ──────────────────
    for row in signals_list:
        row['label'] = display_label(row)

    def n_label(*labels):
        return sum(1 for r in signals_list if r['label'] in labels)

    buy_ideas = sorted(
        [r for r in signals_list
         if r['label'] in (LABEL_STRONG_BUY, LABEL_BUY, LABEL_FW_BLOCKED, LABEL_WATCH)],
        key=lambda x: -x['buy_score'])
    sell_guidance = sorted(
        [r for r in signals_list if r['label'] in SELL_LABELS],
        key=lambda x: -x['sell_score'])

    return {
        'generated_at':  datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'run_date':      datetime.now(BANGKOK).strftime('%A %d %b %Y'),
        'universe_size': len(signals_list),
        'market': {
            'cnn_score':       sell_market.get('cnn_score', 50),
            'cnn_label':       sell_market.get('cnn_label', 'Neutral'),
            'cnn_ok':          bool(sell_market.get('cnn_ok', False)),   # False = default 50, fetch failed
            'vix_ok':          bool(sell_market.get('vix_ok', False)),
            'vix_current':     sell_market.get('vix_current', 20.0),
            'vix_zscore':      sell_market.get('vix_zscore', 0.0),
            'vix_label':       sell_market.get('vix_label', 'Neutral'),
            'sp500_extended':  sell_market.get('sp500_extended', False),
            'buffett_extended':sell_market.get('buffett_extended', False),
            'macro_ok':        macro_ok,          # SPY above its 200d SMA
            'strong_cap':      strong_cap,
            'regular_cap':     regular_cap,
            'sell_cap':        sell_cap,
        },
        # Every surface reads thresholds from here — no hard-coded copies.
        'schema_version': 3,
        'model_version': MODEL_VERSION,
        'framework': (lambda rows, fa: {
            'fundamentals_as_of': fa,
            'axes': framework_scorer.AXES,
            'fund_axes': framework_scorer.FUND_AXES,
            'bands': [list(b) for b in framework_scorer.BANDS],
            'stale_days': framework_scorer.STALE_DAYS,
            'scored': len(rows),
            'with_data': sum(1 for r in rows if 'data' in r['framework']['sources'].values()),
            'provisional': sum(1 for r in rows if r['framework']['provisional']),
            'moat_needs_review': sum(1 for r in rows if r['framework']['moat_stale'] and r['framework']['sources'].get('moat') not in (None, 'legacy')),
            'moat_never_judged': sum(1 for r in rows if r['framework']['sources'].get('moat') == 'legacy'),
            'funds_scored_from_prices': sum(1 for r in rows if r['framework']['sources'].get('track') == 'data'),
        })([r for r in signals_list if r.get('framework')], framework_scorer.load_fundamentals()[1]),
        'thresholds': {
            'buy': BUY_T, 'watch': WATCH_T, 'strong': STRONG_T,
            'fw_min_buy': FW_MIN_BUY, 'fund_fw_min_buy': FUND_FW_MIN_BUY,
            'trim': TRIM_T, 'reduce': REDUCE_T, 'exit': EXIT_T,
        },
        # All counts are counts of `label` — the same thing each row displays.
        'summary': {
            'strong_buy':  n_label(LABEL_STRONG_BUY),
            'buy':         n_label(LABEL_BUY),
            'buy_total':   n_label(LABEL_STRONG_BUY, LABEL_BUY),
            'watch':       n_label(LABEL_WATCH),
            'weak_buy':    n_label(LABEL_FW_BLOCKED),
            'exit':        n_label('EXIT'),
            'reduce':      n_label('REDUCE'),
            'trim':        n_label('TRIM'),
            'sell_flags':  n_label(*SELL_LABELS),   # held positions with a live sell action
            'sell':        n_label('EXIT'),
            'gates':       sum(1 for s in signals_list if s.get('gate')),
            'dips':        sum(1 for s in signals_list if s.get('dip')),
            'factor_zone': sum(1 for s in signals_list if s['factor']),   # v2, reference only
            'fdfv3':       sum(1 for s in signals_list if s['fdfv3']),
            'banker_weak': sum(1 for s in signals_list if s['banker_weak']),
            'held':        n_held,
        },
        'analytics': {
            'signals':       signals_list,
            'buy_ideas':     buy_ideas,
            'sell_guidance': sell_guidance,
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
        if t not in UNIVERSE: continue   # fetched only as a fund benchmark
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

    # Write live_prices.json — committed to repo so dashboard reads
    # prices from GitHub on open without needing to call Finnhub itself
    prices_out = {
        'timestamp':   datetime.now().strftime('%Y-%m-%d %H:%M UTC'),
        'run_date':    payload.get('run_date', ''),
        # fetch_live_prices() stores the day move under 'change'. Reading
        # 'change_pct' here wrote null for every ticker, so the dashboard
        # showed +0.00% whenever prices came from this file.
        'prices':      {t: {'price': v.get('price'), 'change_pct': v.get('change')}
                        for t, v in live_prices.items() if v.get('price')},
    }
    with open('live_prices.json', 'w') as f:
        json.dump(prices_out, f, indent=2, default=str)
    print(f'Written: live_prices.json ({len(prices_out["prices"])} tickers)')

    # ── Write weekly_signals_log.json ────────────────────────────────
    # Appends any NEW signals from this run to a rolling 7-day log.
    # Used by the weekly email to show "signals that fired this week"
    # with freshness status (still active vs expired + reason).
    update_weekly_signal_log(payload)

    # ── Live out-of-sample ledger ────────────────────────────────────
    # Every label the model issues is recorded with its date and price, then
    # scored against the average stock in the same universe at 21/63/126/252
    # trading days. This is the only evidence that cannot be contaminated by
    # hindsight: the universe and the rule are both fixed before the outcome.
    try:
        from signal_ledger import update_ledger
        update_ledger(payload, closes, list(all_signals.keys()))
    except Exception as e:
        print(f'signal ledger not updated: {str(e)[:120]}')

    print(f'Written: {out} ({os.path.getsize(out):,} bytes)')
    print(f"Summary: {payload['summary']}")
    for b in payload['analytics']['buy_ideas'][:8]:
        print(f"  {b['label']:<11} {b['ticker']:<8} buy={b['buy_score']} {'(held)' if b['is_holding'] else ''}")
    for b in payload['analytics']['sell_guidance'][:8]:
        print(f"  {b['label']:<11} {b['ticker']:<8} sell={b['sell_score']} (held)")

if __name__ == '__main__':
    main()
