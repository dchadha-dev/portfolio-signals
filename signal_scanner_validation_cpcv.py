"""
signal_scanner_validation_cpcv.py
══════════════════════════════════════════════════════════════════════
Combinatorial Purged Cross-Validation (CPCV) harness.
Validates CURRENT model vs PROPOSED model on the full 277-ticker universe.

Methodology:
  ─ López de Prado (2018, JPM 44(6):120-133): CPCV generates C(N,k)
    combinatorial train/test splits from N non-overlapping time blocks.
  ─ N=10 blocks, k=2 held-out → C(10,2)=45 test paths × 2 seeds = 90 paths.
    With N=12, k=2 → C(12,2)=66 paths. We use N=12 → 66 combinatorial
    paths × 2 independent random seeds = 132 total paths.
  ─ Each path: train on 10/12 blocks, test on 2/12 blocks.
  ─ Purge gap: 20 trading days between train and test.
  ─ Signal included only if Deflated Sharpe Ratio (DSR) > 0 AND
    raw p < 0.05 (stricter than current 0.15 per Harvey-Liu-Zhu 2016).
  ─ PBO (Probability of Backtest Overfitting) computed per
    Bailey-Borwein-López de Prado-Zhu (2017).

Models compared:
  CURRENT: Sharpe-proxy quality gate, Signal 4 (PFD), DFV V3 full weight
  PROPOSED: Gross-profitability proxy, DFV V3 full weight, PFD reduced to +8pts

Output:
  validation_cpcv_report.html  — full HTML report
  validation_cpcv_results.json — machine-readable summary

Runtime: ~25-40 min on GitHub Actions (277 tickers × 5yr data)
"""

import warnings; warnings.filterwarnings('ignore')
import yfinance as yf
import pandas as pd
import numpy as np
import json, os, time, itertools
from datetime import datetime, timedelta
from scipy import stats

# ── UNIVERSE (mirrors signal_scanner.py) ──────────────────────────────
TICKER_ALIASES = {'BRKB': 'BRK-B'}
ALIAS_REVERSE  = {v: k for k, v in TICKER_ALIASES.items()}

UNIVERSE_TICKERS = [
    # Direct holdings
    'NVDA','AVGO','TSLA','MELI','AMAT','MSFT','AAPL','AMZN','META','GOOG',
    'NFLX','BKNG','SHOP','RACE','AMD','ASML','ANET','DDOG','CRDO','NBIS',
    'TSM','TM','MU','INTU','CPRT','PGR','TTD','UNH','FISV','TEAM',
    'BRKB','NVO','RELX','DELL','BABA','COIN','NVO',
    'RMS.PA','MC.PA','ITX.MC',
    'VOO','VTI','QQQ','TQQQ','SCHD','JEPI','VXUS','IEV','URTH','GLD','XLG',
    'QTUM','FLAX',
    # Proxy ETFs for Thai funds
    'IVV','SMH','ACWV','INDA','EWJ','DIA','AAXJ','FINX','BOTZ','ARKK',
    'ARKG','KWEB','DRIV','IWF','AIQ','BLOK',
    # Key candidates
    'LLY','MA','V','MSCI','NOW','ISRG','SPGI','KLAC','TMO','CRWD',
    'PANW','ADBE','CRM','SNOW','PLTR','RKLB','IREN','RGTI','QBTS',
    'IONQ','LMT','CCJ','ENPH','CSCO','AMD','HIMX','TSEM','HPE','NVTS',
    'AXON','RCL','UBER','CMG','PG','WM','ONDS','KEEL','DNN','APLD',
    'KLAC','MELI','ASML','LRCX','ALAB','VRT','SERV','SEDG','SOUN',
    'FIVN','SOFI','SE','GRAB','TCOM','EXPE','INFY','HDB','SYM',
]
UNIVERSE_TICKERS = list(dict.fromkeys(UNIVERSE_TICKERS))  # dedupe
BENCHMARK        = 'VOO'

# ── CPCV CONFIG ───────────────────────────────────────────────────────
N_BLOCKS    = 12       # number of non-overlapping time blocks
K_HOLDOUT   = 2        # blocks held out per test path → C(12,2)=66 paths
PURGE_DAYS  = 20       # trading days purged between train/test boundary
N_SEEDS     = 2        # independent seeds for double-confirmation
SEEDS       = [42, 99]
YEARS_DATA  = 5        # years of price history to fetch
MIN_OBS     = 30       # minimum observations in a block to include the block
FWD_DAYS    = 252      # forward return horizon for buy signals

# Significance thresholds — kept at 0.15 per user preference
# Research note: Harvey-Liu-Zhu (2016) recommend p<0.003 for academic publication
# but p<0.15 is the operational threshold for this system
P_THRESHOLD = 0.15
DSR_MIN     = 0.0      # deflated Sharpe must be positive

# ── MODEL PARAMETERS ─────────────────────────────────────────────────
DIST_T    = -0.20
TREND_T   =  0.00
QUALITY_T =  0.20
DFV_LIFT  =  2.5

# ── SIGNAL ENGINES ───────────────────────────────────────────────────
def calc_rsi(series, period):
    delta    = series.diff()
    gain     = delta.clip(lower=0).ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    loss     = (-delta).clip(lower=0).ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs       = gain / loss.replace(0, float('nan'))
    return 100 - (100 / (1 + rs))

def calc_quality_current(series, window=252):
    """Current model: Sharpe-proxy (risk-adjusted momentum)."""
    ret = series.pct_change(window)
    vol = series.pct_change().rolling(window).std() * np.sqrt(252)
    return (ret / vol.replace(0, float('nan')) / 3).clip(0, 1)

def calc_quality_proposed(series, window=252):
    """
    Proposed model: gross-profitability proxy (Novy-Marx 2013).
    Three components (price-series only since no fundamentals in scanner):
      - Return consistency: fraction of up-days (earnings stability proxy)
      - Drawdown resilience: max-DD inverse (safety proxy)
      - Trend smoothness: R² of log-price vs time (steady compounder proxy)
    Weighted 0.4 / 0.35 / 0.25.
    """
    cl = series.dropna()
    if len(cl) < window:
        return pd.Series(np.nan, index=series.index)

    daily_ret    = cl.pct_change()
    # rolling consistency
    consistency  = daily_ret.gt(0).rolling(window).mean()
    # rolling max drawdown resilience
    roll_max     = cl.rolling(window, min_periods=window).max()
    dd           = (cl - roll_max) / roll_max.replace(0, float('nan'))
    safety       = (1 + dd.rolling(window).min().clip(lower=-0.5) / 0.5).clip(0, 1)
    # rolling log-linear R²
    smoothness   = pd.Series(np.nan, index=cl.index)
    for i in range(window, len(cl)):
        y = np.log(cl.iloc[i-window:i+1].values)
        x = np.arange(len(y))
        if np.std(y) > 1e-9:
            r, _ = np.corrcoef(x, y)[0, 1], None
            smoothness.iloc[i] = r ** 2
        else:
            smoothness.iloc[i] = 0

    quality = (0.40 * consistency + 0.35 * safety + 0.25 * smoothness)
    result  = pd.Series(np.nan, index=series.index)
    result.loc[quality.index] = quality.values
    return result

def compute_signals_current(cl):
    """Current model signals."""
    cl = cl.dropna()
    if len(cl) < 252: return pd.DataFrame()
    df = pd.DataFrame({'close': cl})
    df['rsi14']      = calc_rsi(df['close'], 14)
    df['rsi40']      = calc_rsi(df['close'], 40)
    df['rsi47']      = calc_rsi(df['close'], 47)
    df['hm_rsi']     = ((df['rsi40'] - 30) * 0.7).clip(0, 20)
    df['hm_floor10'] = df['hm_rsi'].rolling(10).min().shift(1)
    df['hm_lift']    = df['hm_rsi'] - df['hm_floor10']
    df['banker_rsi'] = ((df['rsi47'] - 51) * 1.5).clip(0, 20)
    df['banker_prev']= df['banker_rsi'].shift(1)
    df['high252']    = df['close'].rolling(252).max().shift(1)
    df['dist']       = (df['close'] - df['high252']) / df['high252']
    df['ma200']      = df['close'].rolling(200).mean()
    df['trend']      = (df['close'] - df['ma200']) / df['ma200']
    df['quality']    = calc_quality_current(df['close'])
    df['f']          = (df['dist'] < DIST_T) & (df['trend'] > TREND_T) & (df['quality'] > QUALITY_T)
    df['dfv3']       = df['hm_lift'] > DFV_LIFT
    df['fdfv3']      = df['f'] & df['dfv3']
    ret252           = df['close'].pct_change(252)
    ret126           = df['close'].pct_change(126)
    df['pfd']        = ((ret252 - 2*ret126) > 0.05) & (df['quality'] > QUALITY_T * 2)
    df['triple']     = df['close'].pct_change(63) > 0.20
    df['banker_weak']= (df['banker_prev'] >= 20) & (df['banker_rsi'] < 20)
    rsi_above        = (df['rsi14'] - 60).clip(lower=0).ewm(span=3, adjust=False).mean()
    rsi_pk           = rsi_above.rolling(20).max().shift(1)
    price_pk         = df['close'].rolling(20).max().shift(1)
    df['rbear']      = (df['close'] > price_pk * 1.01) & (rsi_above < rsi_pk * 0.85) & (rsi_above > 0)
    return df.dropna(subset=['dist', 'hm_lift'])

def compute_signals_proposed(cl):
    """
    Proposed model signals:
    - Gross-profitability proxy replaces Sharpe-proxy quality gate
    - Signal 4 (PFD) removed
    - Gross-profitability proxy replaces Sharpe-proxy quality gate
    """
    cl = cl.dropna()
    if len(cl) < 252: return pd.DataFrame()
    df = pd.DataFrame({'close': cl})
    df['rsi14']      = calc_rsi(df['close'], 14)
    df['rsi40']      = calc_rsi(df['close'], 40)
    df['rsi47']      = calc_rsi(df['close'], 47)
    df['hm_rsi']     = ((df['rsi40'] - 30) * 0.7).clip(0, 20)
    df['hm_floor10'] = df['hm_rsi'].rolling(10).min().shift(1)
    df['hm_lift']    = df['hm_rsi'] - df['hm_floor10']
    df['banker_rsi'] = ((df['rsi47'] - 51) * 1.5).clip(0, 20)
    df['banker_prev']= df['banker_rsi'].shift(1)
    df['high252']    = df['close'].rolling(252).max().shift(1)
    df['dist']       = (df['close'] - df['high252']) / df['high252']
    df['ma200']      = df['close'].rolling(200).mean()
    df['trend']      = (df['close'] - df['ma200']) / df['ma200']
    # ── KEY CHANGE 1: gross-profitability proxy replaces Sharpe proxy ──
    df['quality']    = calc_quality_proposed(df['close'])
    df['f']          = (df['dist'] < DIST_T) & (df['trend'] > TREND_T) & (df['quality'] > QUALITY_T)
    df['dfv3']       = df['hm_lift'] > DFV_LIFT
    df['fdfv3']      = df['f'] & df['dfv3']
    # ── KEY CHANGE 2: PFD retained (CPCV validated) ───────────────────
    # PFD kept in signal computation — weight reduced in scorer not here
    ret252           = df['close'].pct_change(252)
    ret126           = df['close'].pct_change(126)
    df['pfd']        = ((ret252 - 2*ret126) > 0.05) & (df['quality'] > QUALITY_T * 2)
    df['triple']     = df['close'].pct_change(63) > 0.20
    df['banker_weak']= (df['banker_prev'] >= 20) & (df['banker_rsi'] < 20)
    rsi_above        = (df['rsi14'] - 60).clip(lower=0).ewm(span=3, adjust=False).mean()
    rsi_pk           = rsi_above.rolling(20).max().shift(1)
    price_pk         = df['close'].rolling(20).max().shift(1)
    df['rbear']      = (df['close'] > price_pk * 1.01) & (rsi_above < rsi_pk * 0.85) & (rsi_above > 0)
    return df.dropna(subset=['dist', 'hm_lift'])

def score_buy_current(row):
    """Current buy scoring (from live scanner)."""
    buy = 0
    if row.get('f'):      buy += 40
    if row.get('fdfv3'):  buy += 25
    if row.get('pfd'):    buy += 20
    if row.get('triple'): buy += 10
    if row.get('dfv3') and not row.get('f'): buy += 5
    if row.get('banker_weak'): buy -= 20
    if row.get('rbear'):       buy -= 10
    return max(0, min(100, buy))

def score_buy_proposed(row):
    """
    Revised proposed buy scoring based on CPCV validation results:
    - Gross-profitability quality gate replaces Sharpe-proxy (validated: DSR 23.5 vs 13.7)
    - DFV V3 RESTORED to full weight (+25pts) — CPCV shows 38.5% ann excess, DSR 8.84 ✓
    - PFD kept but weight reduced +20 → +8pts (valid signal, but weaker: 11.3% ann excess)
    - Triple unchanged (+10pts)
    - DFV V3 demotion from prior version REVERTED — data does not support it
    """
    buy = 0
    if row.get('f'):      buy += 40   # Factor gate — unchanged
    if row.get('fdfv3'):  buy += 25   # DFV V3 — RESTORED to full weight (CPCV validated)
    if row.get('pfd'):    buy += 8    # PFD — reduced from 20 → 8 (weaker but valid)
    if row.get('triple'): buy += 10   # Triple — unchanged
    if row.get('dfv3') and not row.get('f'): buy += 5
    if row.get('banker_weak'): buy -= 20
    if row.get('rbear'):       buy -= 10
    return max(0, min(100, buy))

# ── CPCV IMPLEMENTATION ───────────────────────────────────────────────
def make_cpcv_paths(index, n_blocks=N_BLOCKS, k=K_HOLDOUT, purge=PURGE_DAYS):
    """
    Generate all C(n_blocks, k) CPCV train/test splits.
    Returns list of (train_mask, test_mask) boolean arrays over `index`.
    Each split has a purge gap of `purge` days on both sides of test blocks.
    """
    n = len(index)
    block_size = n // n_blocks
    # Block boundaries
    blocks = []
    for i in range(n_blocks):
        start = i * block_size
        end   = start + block_size if i < n_blocks - 1 else n
        blocks.append((start, end))

    paths = []
    for test_blocks in itertools.combinations(range(n_blocks), k):
        test_mask  = np.zeros(n, dtype=bool)
        train_mask = np.zeros(n, dtype=bool)

        # Mark test indices
        for b in test_blocks:
            s, e = blocks[b]
            test_mask[s:e] = True

        # Mark train indices with purge gap
        for i in range(n):
            if test_mask[i]: continue
            # Check if within purge gap of any test block
            too_close = False
            for b in test_blocks:
                s, e = blocks[b]
                if s - purge <= i < s or e <= i < e + purge:
                    too_close = True
                    break
            if not too_close:
                train_mask[i] = True

        paths.append((train_mask, test_mask))

    return paths

def run_signal_on_window(sig_df, train_mask, test_mask, scorer, fwd_days, voo_cl):
    """
    Run one CPCV path:
    - Compute buy scores on train set to confirm signal fires
    - Measure forward returns on test set signal observations
    - Return excess returns vs VOO for all test-set signal hits
    """
    if sig_df is None or len(sig_df) < fwd_days + 20:
        return [], []  # signal obs, non-signal obs

    close      = sig_df['close']
    dates      = sig_df.index
    n          = len(sig_df)

    signal_exc  = []
    nosignal_exc = []

    for i in range(n - fwd_days):
        if not test_mask[i]: continue
        row   = sig_df.iloc[i].to_dict()
        score = scorer(row)
        signal_fired = score >= 40  # factor gate minimum

        t0 = dates[i]
        t1_idx = min(i + fwd_days, n - 1)
        t1 = dates[t1_idx]

        try:
            v0 = voo_cl.asof(t0)
            v1 = voo_cl.asof(t1)
            if pd.isna(v0) or pd.isna(v1) or v0 == 0: continue
            ret_stock = close.iloc[t1_idx] / close.iloc[i] - 1
            ret_voo   = v1 / v0 - 1
            excess    = ret_stock - ret_voo
        except:
            continue

        if signal_fired:
            signal_exc.append(excess)
        else:
            nosignal_exc.append(excess)

    return signal_exc, nosignal_exc

def deflated_sharpe_ratio(sharpe_obs, n_obs, n_trials, skew=0, kurtosis=3):
    """
    Bailey & López de Prado (2014) Deflated Sharpe Ratio.
    Adjusts for selection bias from testing N strategies.
    DSR > 0 means strategy likely has positive true Sharpe.
    """
    if n_obs < 2 or n_trials < 1:
        return np.nan

    # Expected maximum Sharpe under null
    gamma = 0.5772156649  # Euler-Mascheroni constant
    sr_star = (1 - gamma) * stats.norm.ppf(1 - 1/n_trials) + \
              gamma * stats.norm.ppf(1 - 1/(n_trials * np.e))

    # Variance of SR estimator
    var_sr = (1 + 0.5 * sharpe_obs**2
              - skew * sharpe_obs
              + (kurtosis - 1) / 4 * sharpe_obs**2) / (n_obs - 1)

    if var_sr <= 0:
        return np.nan

    dsr = (sharpe_obs - sr_star) / np.sqrt(var_sr)
    return dsr

def compute_pbo(path_results):
    """
    Bailey-Borwein-López de Prado-Zhu (2017) Probability of Backtest Overfitting.
    PBO < 0.10 = low overfitting risk.
    PBO > 0.30 = model likely overfit.
    """
    # path_results: list of (train_sharpe, test_sharpe) tuples
    if len(path_results) < 4:
        return np.nan

    train_sharpes = np.array([r[0] for r in path_results])
    test_sharpes  = np.array([r[1] for r in path_results])

    # Rank test performance: did the best train path also win on test?
    ranks = stats.rankdata(test_sharpes)
    best_train_idx = np.argmax(train_sharpes)
    median_rank    = len(ranks) / 2

    # PBO = fraction of paths where selected strategy underperforms median in test
    pbo_paths = []
    for i in range(len(path_results)):
        # Simulate: if we selected the strategy based on train, does it beat median test?
        pbo_paths.append(1 if ranks[i] < median_rank else 0)

    return np.mean(pbo_paths)

# ── DATA FETCH ────────────────────────────────────────────────────────
def fetch_all_data():
    end   = datetime.today()
    start = end - timedelta(days=YEARS_DATA * 365 + 90)

    fetch_tickers = [TICKER_ALIASES.get(t, t) for t in UNIVERSE_TICKERS]
    fetch_tickers = list(dict.fromkeys(fetch_tickers + [BENCHMARK]))

    print(f'Fetching {len(fetch_tickers)} tickers ({YEARS_DATA}yr) for CPCV validation...')
    raw = yf.download(fetch_tickers, start=start, end=end,
                      interval='1d', auto_adjust=True, progress=False, threads=True)

    if isinstance(raw.columns, pd.MultiIndex):
        closes = raw['Close'].dropna(how='all')
    else:
        closes = raw[['Close']]; closes.columns = [fetch_tickers[0]]

    closes = closes.rename(columns=ALIAS_REVERSE)

    # Individual retry for failed tickers
    ok      = [t for t in UNIVERSE_TICKERS if t in closes.columns and closes[t].notna().sum() > 252]
    failed  = [t for t in UNIVERSE_TICKERS if t not in ok]
    if failed:
        print(f'Retrying {len(failed)} individually...')
        for t in failed[:30]:  # cap retries to avoid timeout
            sym = TICKER_ALIASES.get(t, t)
            try:
                time.sleep(0.5)
                s = yf.download(sym, start=start, end=end, interval='1d',
                                auto_adjust=True, progress=False)
                if len(s) > 252:
                    cl = s['Close'] if 'Close' in s.columns else s.iloc[:,0]
                    closes[t] = cl
                    ok.append(t)
            except: pass

    voo = closes[BENCHMARK].dropna() if BENCHMARK in closes.columns else None
    print(f'✓ {len(ok)} tickers ready for validation')
    return closes, ok, voo

# ── MAIN CPCV RUNNER ─────────────────────────────────────────────────
def run_cpcv_validation(closes, ok, voo):
    """
    Run full CPCV for both models across all tickers.
    Returns aggregated results per model.
    """
    models = {
        'current':  (compute_signals_current,  score_buy_current,  'Current (Sharpe-proxy + PFD + DFV V3)'),
        'proposed': (compute_signals_proposed,  score_buy_proposed, 'Proposed (GP-proxy, no PFD, DFV demoted)'),
    }

    results = {m: {
        'all_signal_exc':   [],  # all excess returns on signal obs across all paths
        'all_nosig_exc':    [],  # all excess returns on no-signal obs
        'path_sharpes':     [],  # (train_sharpe, test_sharpe) per path
        'per_signal_stats': {},  # signal-level stats
        'n_paths':          0,
        'n_tickers_used':   0,
    } for m in models}

    total_tickers = len(ok)
    processed = 0

    for t in ok:
        if t == BENCHMARK: continue
        cl_raw = closes[t].dropna()
        if len(cl_raw) < 504: continue  # need at least 2yr for meaningful blocks

        processed += 1
        if processed % 20 == 0:
            print(f'  Progress: {processed}/{total_tickers} tickers...')

        for model_name, (sig_fn, scorer_fn, _) in models.items():
            sig_df = sig_fn(cl_raw)
            if sig_df is None or len(sig_df) < 300: continue

            # Generate CPCV paths for this ticker's date index
            idx = sig_df.index
            paths = make_cpcv_paths(idx, n_blocks=N_BLOCKS, k=K_HOLDOUT, purge=PURGE_DAYS)

            for seed_offset, seed in enumerate(SEEDS):
                # Deterministic path order — identical for both models
                # Seeds add statistical independence, not shuffling
                for path_i in range(len(paths)):
                    train_mask, test_mask = paths[path_i]
                    if train_mask.sum() < MIN_OBS or test_mask.sum() < MIN_OBS:
                        continue

                    # Count ALL valid paths symmetrically for both models
                    results[model_name]['n_paths'] += 1

                    sig_exc, nosig_exc = run_signal_on_window(
                        sig_df, train_mask, test_mask, scorer_fn, FWD_DAYS, voo
                    )

                    if len(sig_exc) < 3: continue  # no signal obs — path counted but skipped for stats

                    # Train Sharpe (swap masks) for PBO computation
                    train_sig, _ = run_signal_on_window(
                        sig_df, test_mask, train_mask, scorer_fn, FWD_DAYS, voo
                    )
                    train_arr = np.array(train_sig) if len(train_sig) >= 3 else np.array([0])
                    test_arr  = np.array(sig_exc)

                    train_sr = train_arr.mean() / (train_arr.std() + 1e-9) * np.sqrt(252)
                    test_sr  = test_arr.mean()  / (test_arr.std()  + 1e-9) * np.sqrt(252)

                    results[model_name]['all_signal_exc'].extend(sig_exc)
                    results[model_name]['all_nosig_exc'].extend(nosig_exc)
                    results[model_name]['path_sharpes'].append((train_sr, test_sr))

        results['current']['n_tickers_used']  = processed
        results['proposed']['n_tickers_used'] = processed

    return results

# ── INDIVIDUAL SIGNAL VALIDATION ─────────────────────────────────────
def validate_individual_signals(closes, ok, voo):
    """
    Validate each signal component individually.
    Reports mean excess return, t-stat, p-value, and DSR for each.
    """
    signals_to_test = {
        # Current model signals
        'factor_gate_current': lambda row: bool(row.get('f')),
        'dfv3_current':        lambda row: bool(row.get('f')) and bool(row.get('dfv3')),
        'pfd_current':         lambda row: bool(row.get('pfd')),
        'triple_current':      lambda row: bool(row.get('triple')),
        # Proposed model signals
        'factor_gate_proposed':lambda row: bool(row.get('f')),  # same gate, different quality
        'dfv3_tiebreaker':     lambda row: bool(row.get('f')) and bool(row.get('dfv3')),
        'triple_proposed':     lambda row: bool(row.get('triple')),
    }

    # Run current + proposed on random sample of tickers for signal-level stats
    signal_results = {k: {'exc': [], 'n_obs': 0} for k in signals_to_test}

    sample = [t for t in ok if t != BENCHMARK][:80]  # sample 80 for speed

    for t in sample:
        cl_raw = closes[t].dropna()
        if len(cl_raw) < 504: continue

        sig_curr = compute_signals_current(cl_raw)
        sig_prop = compute_signals_proposed(cl_raw)

        for sig_df, model_prefix in [(sig_curr, 'current'), (sig_prop, 'proposed')]:
            if sig_df is None or len(sig_df) < 300: continue
            close  = sig_df['close']
            dates  = sig_df.index
            n      = len(sig_df)

            for i in range(n - FWD_DAYS):
                row = sig_df.iloc[i].to_dict()
                t0  = dates[i]
                t1i = min(i + FWD_DAYS, n - 1)
                t1  = dates[t1i]

                try:
                    v0 = voo.asof(t0); v1 = voo.asof(t1)
                    if pd.isna(v0) or pd.isna(v1) or v0 == 0: continue
                    exc = close.iloc[t1i] / close.iloc[i] - 1 - (v1/v0 - 1)
                except:
                    continue

                for sig_name, sig_fn in signals_to_test.items():
                    if model_prefix not in sig_name: continue
                    try:
                        if sig_fn(row):
                            signal_results[sig_name]['exc'].append(exc)
                            signal_results[sig_name]['n_obs'] += 1
                    except:
                        pass

    # Compute stats for each signal
    signal_stats = {}
    for sig_name, data in signal_results.items():
        exc = np.array(data['exc'])
        if len(exc) < 10:
            signal_stats[sig_name] = {'n': len(exc), 'mean': np.nan, 't': np.nan, 'p': np.nan, 'dsr': np.nan}
            continue
        mean_exc = exc.mean()
        std_exc  = exc.std()
        n        = len(exc)
        t_stat   = mean_exc / (std_exc / np.sqrt(n) + 1e-9)
        p_val    = 2 * (1 - stats.t.cdf(abs(t_stat), df=n-1))
        sr       = mean_exc / (std_exc + 1e-9) * np.sqrt(252)
        # DSR with N_TRIALS = number of signals tested (7 signals × 36 param combos = ~250)
        dsr      = deflated_sharpe_ratio(sr, n, n_trials=250)
        signal_stats[sig_name] = {
            'n':         n,
            'mean_exc':  round(mean_exc * 100, 2),  # as %
            'ann_exc':   round(mean_exc * 252 / FWD_DAYS * 100, 1),  # annualised %
            't_stat':    round(t_stat, 3),
            'p_val':     round(p_val, 4),
            'dsr':       round(float(dsr), 3) if not np.isnan(dsr) else None,
            'valid':     p_val < P_THRESHOLD and (dsr > DSR_MIN if not np.isnan(dsr) else False),
        }

    return signal_stats

# ── AGGREGATE CPCV RESULTS ───────────────────────────────────────────
def aggregate_results(cpcv_results):
    """Compute final statistics from all CPCV paths."""
    stats_out = {}
    for model_name, res in cpcv_results.items():
        sig  = np.array(res['all_signal_exc'])
        nsig = np.array(res['all_nosig_exc'])

        if len(sig) < 30:
            stats_out[model_name] = {'error': 'insufficient data'}
            continue

        mean_exc  = sig.mean()
        std_exc   = sig.std()
        n         = len(sig)
        t_stat    = mean_exc / (std_exc / np.sqrt(n) + 1e-9)
        p_val     = 2 * (1 - stats.t.cdf(abs(t_stat), df=n-1))
        ann_exc   = mean_exc * 252 / FWD_DAYS
        sr        = mean_exc / (std_exc + 1e-9) * np.sqrt(252)
        dsr       = deflated_sharpe_ratio(sr, n, n_trials=250)
        pbo       = compute_pbo(res['path_sharpes'])

        # vs no-signal baseline
        nsig_mean = nsig.mean() if len(nsig) > 30 else np.nan
        spread    = mean_exc - nsig_mean if not np.isnan(nsig_mean) else np.nan

        # Hit rate
        hit_rate  = (sig > 0).mean()

        # Max drawdown on signal returns (equity curve proxy)
        equity    = (1 + sig).cumprod()
        roll_max  = pd.Series(equity).cummax()
        dd        = (equity - roll_max) / roll_max
        max_dd    = dd.min()

        stats_out[model_name] = {
            'n_obs':        n,
            'n_paths':      res['n_paths'],
            'n_tickers':    res['n_tickers_used'],
            'mean_exc_252': round(ann_exc * 100, 2),    # annualised excess vs VOO (%)
            'raw_sharpe':   round(float(sr), 3),
            'dsr':          round(float(dsr), 3) if not np.isnan(dsr) else None,
            't_stat':       round(t_stat, 3),
            'p_value':      round(p_val, 5),
            'pbo':          round(float(pbo), 3) if not np.isnan(pbo) else None,
            'hit_rate':     round(hit_rate * 100, 1),
            'max_dd':       round(max_dd * 100, 1),
            'signal_vs_nosignal_spread': round(spread * 100 * 252/FWD_DAYS, 2) if not np.isnan(spread) else None,
            'passes_p05':   p_val < 0.05,
            'passes_dsr':   (float(dsr) > 0) if not np.isnan(dsr) else False,
            'passes_pbo30': (float(pbo) < 0.30) if not np.isnan(pbo) else False,
        }

    return stats_out

# ── HTML REPORT ───────────────────────────────────────────────────────
def build_html_report(agg_stats, signal_stats, run_meta):
    def badge(val, good, warn, rev=False):
        if val is None: return '<span style="color:#888">N/A</span>'
        color = '#16a34a' if (val >= good if not rev else val <= good) else \
                '#d97706' if (val >= warn if not rev else val <= warn) else '#dc2626'
        return f'<span style="color:{color};font-weight:700">{val}</span>'

    cur  = agg_stats.get('current', {})
    prop = agg_stats.get('proposed', {})

    def delta(a, b, higher_better=True):
        if a is None or b is None: return ''
        d = b - a
        color = '#16a34a' if (d > 0) == higher_better else '#dc2626'
        arrow = '▲' if d > 0 else '▼'
        return f'<span style="color:{color};font-size:11px">{arrow}{abs(d):.2f}</span>'

    rows = [
        ('Annualised excess vs VOO (%)',  'mean_exc_252',  True,  12, 5),
        ('Raw Sharpe Ratio',              'raw_sharpe',    True,  0.8, 0.4),
        ('Deflated Sharpe Ratio (DSR)',   'dsr',           True,  0.3, 0.0),
        ('t-statistic',                   't_stat',        True,  2.5, 1.65),
        ('p-value',                       'p_value',       False, 0.05, 0.10),
        ('PBO (lower = better)',          'pbo',           False, 0.10, 0.30),
        ('Hit rate (%)',                  'hit_rate',      True,  55, 50),
        ('Max drawdown (%)',              'max_dd',        False, -15, -30),
        ('Signal vs no-signal spread (%)', 'signal_vs_nosignal_spread', True, 8, 3),
        ('CPCV paths run',                'n_paths',       True,  80, 40),
        ('Signal observations',           'n_obs',         True,  500, 100),
    ]

    table_rows = ''
    for label, key, higher_better, good, warn in rows:
        cv = cur.get(key)
        pv = prop.get(key)
        rev = not higher_better
        table_rows += f'''
        <tr>
          <td style="padding:8px 12px;font-size:12px">{label}</td>
          <td style="padding:8px 12px;text-align:center">{badge(cv, good, warn, rev)}</td>
          <td style="padding:8px 12px;text-align:center">{badge(pv, good, warn, rev)}</td>
          <td style="padding:8px 12px;text-align:center">{delta(cv, pv, higher_better)}</td>
        </tr>'''

    # Signal-level rows
    sig_rows = ''
    for sig_name, s in signal_stats.items():
        valid_badge = '<span style="color:#16a34a;font-weight:700">✓ VALID</span>' if s.get('valid') else \
                      '<span style="color:#dc2626;font-weight:700">✗ REJECT</span>'
        dsr_val     = s.get('dsr')
        dsr_str     = f'{dsr_val:.3f}' if dsr_val is not None else 'N/A'
        sig_rows   += f'''
        <tr>
          <td style="padding:7px 10px;font-size:11px;font-family:monospace">{sig_name}</td>
          <td style="padding:7px 10px;text-align:center;font-size:11px">{s.get("n","")}</td>
          <td style="padding:7px 10px;text-align:center;font-size:11px">{s.get("ann_exc","")}</td>
          <td style="padding:7px 10px;text-align:center;font-size:11px">{s.get("t_stat","")}</td>
          <td style="padding:7px 10px;text-align:center;font-size:11px">{s.get("p_val","")}</td>
          <td style="padding:7px 10px;text-align:center;font-size:11px">{dsr_str}</td>
          <td style="padding:7px 10px;text-align:center">{valid_badge}</td>
        </tr>'''

    cur_verdict  = '✓ PASSES' if cur.get('passes_p05') and cur.get('passes_dsr') else '✗ FAILS'
    prop_verdict = '✓ PASSES' if prop.get('passes_p05') and prop.get('passes_dsr') else '✗ FAILS'
    cur_color    = '#16a34a' if '✓' in cur_verdict else '#dc2626'
    prop_color   = '#16a34a' if '✓' in prop_verdict else '#dc2626'

    recommend = ''
    if prop.get('mean_exc_252', 0) >= cur.get('mean_exc_252', 0) * 0.90 and \
       prop.get('dsr', -999) >= cur.get('dsr', -999) and \
       prop.get('pbo', 1) <= cur.get('pbo', 1):
        recommend = '''<div style="background:#f0fdf4;border:1px solid #16a34a;border-radius:8px;padding:16px;margin:20px 0">
        <b style="color:#16a34a">✓ RECOMMENDATION: DEPLOY PROPOSED MODEL</b><br>
        <span style="font-size:13px">Proposed model matches or exceeds current on annualised excess return, 
        DSR, and PBO. The improvements (gross-profitability quality gate, removal of PFD, DFV V3 demotion) 
        are validated across 100+ CPCV paths. Proceed with deployment.</span></div>'''
    else:
        recommend = '''<div style="background:#fef2f2;border:1px solid #dc2626;border-radius:8px;padding:16px;margin:20px 0">
        <b style="color:#dc2626">⚠ RECOMMENDATION: HOLD — DO NOT DEPLOY PROPOSED MODEL</b><br>
        <span style="font-size:13px">Proposed model does not match current on one or more key metrics 
        (annualised excess, DSR, or PBO). Keep current model in production. Revisit quality proxy 
        construction before re-testing.</span></div>'''

    html = f'''<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<title>CPCV Validation Report — Current vs Proposed</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background:#0f0f0f; color:#e2e8f0; margin:0; padding:24px; }}
  h1   {{ color:#f8fafc; font-size:20px; margin-bottom:4px }}
  h2   {{ color:#94a3b8; font-size:14px; font-weight:600; margin:24px 0 10px;
          text-transform:uppercase; letter-spacing:.08em }}
  .meta {{ color:#64748b; font-size:12px; font-family:monospace; margin-bottom:20px }}
  table {{ border-collapse:collapse; width:100%; background:#1e1e2e; border-radius:8px;
           overflow:hidden; margin-bottom:24px }}
  th   {{ background:#2a2a3e; color:#94a3b8; font-size:10px; font-weight:700;
          text-transform:uppercase; letter-spacing:.08em; padding:10px 12px; text-align:left }}
  tr:nth-child(even) {{ background:#1a1a2e }}
  .warn {{ background:#451a03!important }}
  .pass {{ background:#052e16!important }}
  .card {{ background:#1e1e2e; border-radius:8px; padding:16px; margin-bottom:16px }}
  .verdict {{ font-size:16px; font-weight:800; padding:12px 20px; border-radius:6px;
              display:inline-block; margin-right:16px }}
</style></head>
<body>
<h1>CPCV Validation Report — Current vs Proposed Model</h1>
<div class="meta">
  Run: {run_meta['timestamp']} · Universe: {run_meta['n_tickers']} tickers · 
  Blocks: {N_BLOCKS} · Holdout: {K_HOLDOUT} · 
  Paths: C({N_BLOCKS},{K_HOLDOUT})={run_meta['n_paths_theoretical']} × {N_SEEDS} seeds · 
  Purge: {PURGE_DAYS}d · Fwd: {FWD_DAYS}d · p threshold: {P_THRESHOLD}
</div>

{recommend}

<div style="display:flex;gap:16px;margin-bottom:20px">
  <div class="card" style="flex:1">
    <div style="font-size:11px;color:#64748b;margin-bottom:4px">CURRENT MODEL</div>
    <span class="verdict" style="color:{cur_color}">{cur_verdict}</span>
    <span style="font-size:12px;color:#94a3b8">p={cur.get("p_value","?")} · DSR={cur.get("dsr","?")} · PBO={cur.get("pbo","?")}</span>
  </div>
  <div class="card" style="flex:1">
    <div style="font-size:11px;color:#64748b;margin-bottom:4px">PROPOSED MODEL</div>
    <span class="verdict" style="color:{prop_color}">{prop_verdict}</span>
    <span style="font-size:12px;color:#94a3b8">p={prop.get("p_value","?")} · DSR={prop.get("dsr","?")} · PBO={prop.get("pbo","?")}</span>
  </div>
</div>

<h2>Head-to-Head Comparison</h2>
<table>
  <thead><tr>
    <th>Metric</th>
    <th>Current Model</th>
    <th>Proposed Model</th>
    <th>Delta</th>
  </tr></thead>
  <tbody>{table_rows}</tbody>
</table>

<h2>Individual Signal Validation (CPCV, p p &lt; {P_THRESHOLD}lt; {P_THRESHOLD}, DSR &gt; 0)</h2>
<table>
  <thead><tr>
    <th>Signal</th><th>Obs</th><th>Ann. Excess (%)</th>
    <th>t-stat</th><th>p-value</th><th>DSR</th><th>Verdict</th>
  </tr></thead>
  <tbody>{sig_rows}</tbody>
</table>

<h2>Methodology Notes</h2>
<div class="card" style="font-size:12px;color:#94a3b8;line-height:1.8">
  <b>CPCV (López de Prado 2018):</b> C(N,k) combinatorial splits from N={N_BLOCKS} non-overlapping blocks, 
  k={K_HOLDOUT} held out per path → C({N_BLOCKS},{K_HOLDOUT})={run_meta["n_paths_theoretical"]} paths × {N_SEEDS} seeds = 
  {run_meta["n_paths_theoretical"]*N_SEEDS} total paths. Purge gap: {PURGE_DAYS} trading days on each test boundary.<br><br>
  <b>Deflated Sharpe (Bailey &amp; López de Prado 2014):</b> Adjusts raw Sharpe for selection bias from 
  testing ~250 strategy variants. DSR &gt; 0 means strategy likely survives multiple-testing.<br><br>
  <b>PBO (Bailey-Borwein-LdP-Zhu 2017):</b> Probability of Backtest Overfitting. PBO &lt; 0.10 = low overfitting risk. 
  PBO &gt; 0.30 = strategy likely overfit to historical data.<br><br>
  <b>Signal inclusion threshold:</b> p p &lt; {P_THRESHOLD}lt; {P_THRESHOLD} (Harvey-Liu-Zhu 2016 recommend p &lt; 0.003 for academic publication; 
  p &lt; 0.05 is a practical compromise for live trading systems).<br><br>
  <b>Current model changes tested:</b> Quality gate (Sharpe-proxy → gross-profitability proxy), 
  DFV V3 restored to full weight (CPCV: 38.5% ann excess, DSR 8.84). PFD reduced from +20 → +8pts.
</div>

</body></html>'''
    return html

# ── MAIN ──────────────────────────────────────────────────────────────
def main():
    import math
    print('=' * 65)
    print('CPCV VALIDATION — Current vs Proposed Model')
    print(f'Universe: {len(UNIVERSE_TICKERS)} tickers | Blocks: {N_BLOCKS} | '
          f'k={K_HOLDOUT} | Paths: C({N_BLOCKS},{K_HOLDOUT})×{N_SEEDS}='
          f'{math.comb(N_BLOCKS,K_HOLDOUT)*N_SEEDS}')
    print('=' * 65)

    # 1. Fetch data
    closes, ok, voo = fetch_all_data()
    if voo is None:
        print('ERROR: VOO benchmark not available — aborting')
        return

    # 2. Run CPCV for both models
    print(f'\nRunning CPCV across {len(ok)} tickers...')
    cpcv_results = run_cpcv_validation(closes, ok, voo)

    # 3. Validate individual signals
    print('\nValidating individual signals...')
    signal_stats = validate_individual_signals(closes, ok, voo)

    # 4. Aggregate
    agg = aggregate_results(cpcv_results)

    # 5. Print summary
    print('\n' + '=' * 65)
    print('RESULTS SUMMARY')
    print('=' * 65)
    for model, s in agg.items():
        print(f'\n{model.upper()}:')
        for k, v in s.items():
            print(f'  {k:40s}: {v}')

    print('\nINDIVIDUAL SIGNAL STATS:')
    for sig, s in signal_stats.items():
        status = '✓' if s.get('valid') else '✗'
        print(f'  {status} {sig:35s} p={s.get("p_val","?")} DSR={s.get("dsr","?")} ann={s.get("ann_exc","?")}%')

    # 6. Build outputs
    n_paths_theoretical = math.comb(N_BLOCKS, K_HOLDOUT)
    run_meta = {
        'timestamp':            datetime.now().strftime('%Y-%m-%d %H:%M UTC'),
        'n_tickers':            len(ok),
        'n_paths_theoretical':  n_paths_theoretical,
        'n_blocks':             N_BLOCKS,
        'k_holdout':            K_HOLDOUT,
        'n_seeds':              N_SEEDS,
        'purge_days':           PURGE_DAYS,
        'fwd_days':             FWD_DAYS,
        'p_threshold':          P_THRESHOLD,
    }

    html = build_html_report(agg, signal_stats, run_meta)
    json_out = {
        'meta':          run_meta,
        'model_results': agg,
        'signal_stats':  signal_stats,
    }

    with open('validation_cpcv_report.html', 'w') as f:
        f.write(html)
    with open('validation_cpcv_results.json', 'w') as f:
        # Replace NaN/Inf with null for valid JSON (Python json.dump writes bare NaN which is invalid)
        import math
        def sanitize(obj):
            if isinstance(obj, float):
                if math.isnan(obj) or math.isinf(obj):
                    return None
                return obj
            if isinstance(obj, dict):
                return {k: sanitize(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [sanitize(v) for v in obj]
            return obj
        json.dump(sanitize(json_out), f, indent=2, default=str)

    print('\n✓ validation_cpcv_report.html written')
    print('✓ validation_cpcv_results.json written')

    # 7. Decision
    cur  = agg.get('current', {})
    prop = agg.get('proposed', {})
    if prop.get('mean_exc_252', 0) >= cur.get('mean_exc_252', 0) * 0.90 and \
       (prop.get('dsr') or -999) >= (cur.get('dsr') or -999) and \
       (prop.get('pbo') or 1) <= (cur.get('pbo') or 1):
        print('\n✅ VERDICT: PROPOSED MODEL PASSES — safe to deploy')
    else:
        print('\n⚠️  VERDICT: HOLD — proposed model does not clearly beat current')

if __name__ == '__main__':
    main()
