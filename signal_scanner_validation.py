"""
signal_scanner_validation.py
Runs quarterly via GitHub Actions → commits validated_params.json
signal_scanner.py reads that file automatically on every daily run.

Pipeline:
  validate_models.yml (quarterly) → validated_params.json
  daily_signals.yml   (daily)     → reads validated_params.json → signals_payload.json
"""
import warnings; warnings.filterwarnings('ignore')
import yfinance as yf
import pandas as pd
import numpy as np
from scipy import stats
from datetime import datetime, timedelta
import json, os

# ── Configuration ──────────────────────────────────────────────────────
BENCHMARK    = 'VOO'
YEARS        = 10
N_CV_WINDOWS = 12
PURGE_DAYS   = 20
N_BOOTSTRAP  = 500
P_THRESHOLD  = 0.15
HORIZONS     = [63, 126, 252, 504]

DIST_T_OPTIONS   = [-0.08, -0.10, -0.12, -0.15]
DFV_LIFT_OPTIONS = [2.5, 4.0, 5.5, 7.0]
QUALITY_OPTIONS  = [0.10, 0.15, 0.20]

HOLDINGS = [
    'NVDA','AVGO','TSLA','MELI','AMAT','MSFT','AAPL','AMZN','META','GOOG',
    'NFLX','BKNG','SHOP','RACE','AMD','CRWV','ASML','ANET','DDOG','CRDO',
    'NBIS','TSM','TM','MU','INTU','CPRT','PGR','TTD','UNH','FISV','O','TEAM',
    'VOO','VTI','QQQ','TQQQ','SCHD','JEPI','VXUS','VSS','IEV','URTH','GLD',
]
CANDIDATES = [
    'NOW','PANW','ORCL','COIN','AXON','CEG','CELH','DECK','ENPH','HIMS',
    'IDXX','KNSL','LULU','MPWR','NET','PLNT','RCL','SPOT','SQ','UBER',
    'ULTA','VEEV','SMCI','CAVA','SNOW','MEDP','PODD','HEI','ACLS',
    'FICO','APP','HOOD','RKLB','ARM',
]
BROAD = [
    'ADBE','CRM','SNPS','CDNS','FTNT','ZS','OKTA','MDB','GTLB','HUBS','WDAY','NTNX','PSTG',
    'MRVL','ON','TXN','ADI','MCHP','LRCX','KLAC','ONTO','WOLF',
    'JPM','BAC','GS','MS','BLK','AXP','V','MA','PYPL','COF','C','WFC',
    'JNJ','LLY','ABBV','MRK','PFE','ABT','TMO','DHR','BSX','ISRG','DXCM',
    'MCD','SBUX','CMG','NKE','ONON','WMT','COST','TGT','PG','KO','PEP',
    'CAT','DE','HON','GE','ETN','ROK','AME','PWR','VRT',
    'XOM','CVX','COP','SLB','LNG','MPC',
    'SE','NU','SAP','INFY',
    'SMH','XLC','XLK','XLF','XLV','XLI','XLE','IBB','IWM','EEM','EFA',
]
UNIVERSE = list(dict.fromkeys(HOLDINGS + CANDIDATES + BROAD))

# ── Signal engine ──────────────────────────────────────────────────────
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

def compute_signals(cl, dist_t, quality_t, dfv_lift):
    cl = cl.dropna()
    if len(cl) < 300: return pd.DataFrame()
    df = pd.DataFrame({'close': cl})
    df['rsi40']      = calc_rsi(df['close'], 40)
    df['rsi47']      = calc_rsi(df['close'], 47)
    df['hm_rsi']     = ((df['rsi40'] - 30) * 0.7).clip(0, 20)
    df['hm_prev']    = df['hm_rsi'].shift(1)
    df['hm_floor10'] = df['hm_rsi'].rolling(10).min().shift(1)
    df['hm_lift']    = df['hm_rsi'] - df['hm_floor10']
    df['banker_rsi'] = ((df['rsi47'] - 51) * 1.5).clip(0, 20)
    df['bnk_prev']   = df['banker_rsi'].shift(1)
    df['high252']    = df['close'].rolling(252).max().shift(1)
    df['dist']       = (df['close'] - df['high252']) / df['high252']
    df['ma200']      = df['close'].rolling(200).mean()
    df['trend']      = (df['close'] - df['ma200']) / df['ma200']
    df['quality']    = calc_quality(df['close'])
    df['f']          = (df['dist'] < dist_t) & (df['trend'] > 0) & (df['quality'] > quality_t)
    df['dfv3']       = df['hm_lift'] > dfv_lift
    df['dfv1']       = (df['hm_rsi'] > df['hm_prev']) & (df['hm_prev'] >= 0) & (df['hm_prev'] <= 5)
    df['fdfv3']      = df['f'] & df['dfv3']
    r252 = df['close'].pct_change(252); r126 = df['close'].pct_change(126)
    df['pfd']        = ((r252 - 2*r126) > 0.05) & (df['quality'] > quality_t * 2)
    df['triple']     = df['close'].pct_change(63) > 0.20
    df['wb']         = (df['bnk_prev'] >= 20) & (df['banker_rsi'] < 20)
    df['near_high']  = df['dist'] > -0.05
    np.random.seed(42)
    df['rand']       = np.random.random(len(df)) < 0.10
    return df.dropna(subset=['dist', 'hm_lift'])

# ── Fetch data ─────────────────────────────────────────────────────────
def fetch_data():
    end   = datetime.today()
    start = end - timedelta(days=YEARS*365 + 90)
    all_t = list(dict.fromkeys([BENCHMARK] + UNIVERSE))
    print(f'Fetching {len(all_t)} tickers ({YEARS}yr)...')
    closes_dict = {}
    ok = []; fail = []
    for i, t in enumerate(all_t):
        try:
            df = yf.download(t, start=start, end=end, interval='1d',
                            auto_adjust=True, progress=False)
            if df is not None and len(df) > 400:
                closes_dict[t] = (df['Close'][t] if isinstance(df.columns, pd.MultiIndex)
                                 else df['Close']).dropna()
                ok.append(t)
            else:
                fail.append(t)
        except:
            fail.append(t)
        if (i+1) % 40 == 0:
            print(f'  {i+1}/{len(all_t)} ({len(ok)} ok)...')
    closes = pd.DataFrame(closes_dict)
    print(f'✓ {len(ok)} ok | ✗ {len(fail)} failed')
    return closes, ok

# ── CV window generator ────────────────────────────────────────────────
def generate_cv_windows(dates, voo):
    np.random.seed(42)
    n = len(dates)
    windows, attempts = [], 0
    min_tr, max_tr = int(1.5*252), int(4.0*252)
    min_te, max_te = int(0.5*252), int(1.5*252)
    while len(windows) < N_CV_WINDOWS and attempts < 1000:
        attempts += 1
        tr_len = np.random.randint(min_tr, max_tr)
        te_len = np.random.randint(min_te, max_te)
        if tr_len + PURGE_DAYS + te_len >= n: continue
        tr_s = np.random.randint(0, n - tr_len - PURGE_DAYS - te_len)
        te_s = tr_s + tr_len + PURGE_DAYS
        te_e = te_s + te_len
        ts, te = dates[te_s], dates[min(te_e, n-1)]
        if any(not (te < w['test_end'] or ts > w['test_start']) for w in windows): continue
        try:
            v0, v1 = voo.asof(ts), voo.asof(te)
            vr = v1/v0-1 if v0 and v1 and v0 > 0 else 0
        except: vr = 0
        regime = 'bull' if vr > 0.10 else 'bear' if vr < -0.05 else 'sideways'
        windows.append({
            'train_start': dates[tr_s], 'train_end': dates[tr_s+tr_len-1],
            'test_start': ts, 'test_end': te,
            'train_days': tr_len, 'test_days': te_len,
            'regime': regime, 'voo_ret': round(vr*100,1)
        })
    windows = sorted(windows, key=lambda w: w['test_start'])
    regimes = [w['regime'] for w in windows]
    print(f'CV windows: {len(windows)} | Bull:{regimes.count("bull")} Sideways:{regimes.count("sideways")} Bear:{regimes.count("bear")}')
    return windows

# ── Walk-forward evaluator ─────────────────────────────────────────────
def bootstrap_ci(arr, n_boot=N_BOOTSTRAP):
    if len(arr) < 3: return (float('nan'), float('nan'))
    means = [np.mean(np.random.choice(arr, len(arr), replace=True)) for _ in range(n_boot)]
    return (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))

def run_cv(closes, ok, voo, cv_windows, dist_t, quality_t, dfv_lift, fw):
    SIGS = ['f','fdfv3','pfd','triple','dfv3','dfv1','wb','near_high','rand']
    all_sigs = {}
    for t in ok:
        if t == BENCHMARK: continue
        try:
            s = compute_signals(closes[t], dist_t, quality_t, dfv_lift)
            if len(s) > 80: all_sigs[t] = s
        except: pass

    window_seps = {s: [] for s in SIGS}
    for w in cv_windows:
        obs = []
        for t, sig_df in all_sigs.items():
            cl = sig_df['close']; dates = sig_df.index
            for i in range(len(dates)):
                d = dates[i]
                if not (w['test_start'] <= d <= w['test_end']): continue
                fi = i + fw
                if fi >= len(dates): continue
                try:
                    v0, v1 = voo.asof(d), voo.asof(dates[fi])
                    if pd.isna(v0) or pd.isna(v1) or v0 == 0: continue
                    exc = cl.iloc[fi]/cl.iloc[i] - 1 - (v1/v0-1)
                    row = sig_df.iloc[i].to_dict()
                    row.update({'excess': exc, 'beats': exc > 0})
                    obs.append(row)
                except: pass
        if len(obs) < 15: continue
        df_obs = pd.DataFrame(obs)
        for s in SIGS:
            if s not in df_obs.columns: continue
            sig = df_obs[df_obs[s]==True]
            nsig = df_obs[df_obs[s]==False]
            if len(sig) < 5: continue
            sep = sig['excess'].mean() - (nsig['excess'].mean() if len(nsig) > 0 else 0)
            window_seps[s].append(sep)

    results = {}
    for s, seps in window_seps.items():
        if len(seps) < 3: results[s] = None; continue
        arr = np.array(seps)
        mean = arr.mean(); std = arr.std()
        _, p = stats.ttest_1samp(arr, 0)
        ci_lo, ci_hi = bootstrap_ci(arr)
        results[s] = {
            'mean_sep': float(mean), 'std': float(std),
            'ci_lo': float(ci_lo), 'ci_hi': float(ci_hi),
            'p_value': float(p),
            'significant': bool(p < P_THRESHOLD and mean > 0),
            'n_wins': int((arr > 0).sum()),
            'n_windows': int(len(seps)),
            'win_rate': float((arr > 0).mean()),
        }
    return results

# ── Main ───────────────────────────────────────────────────────────────
def main():
    print(f'Model validation starting — {datetime.today().strftime("%Y-%m-%d %H:%M UTC")}')
    print(f'Universe: {len(UNIVERSE)} tickers | CV windows: {N_CV_WINDOWS} | p<{P_THRESHOLD}')

    closes, ok = fetch_data()
    voo = closes[BENCHMARK].dropna()
    cv_windows = generate_cv_windows(closes.index, voo)

    # Grid search at 252d horizon for Factor+DFV V3
    print(f'\nGrid search: {len(DIST_T_OPTIONS)*len(DFV_LIFT_OPTIONS)*len(QUALITY_OPTIONS)} combinations...')
    best_sep, best_params = -999, None
    for dist in DIST_T_OPTIONS:
        for lift in DFV_LIFT_OPTIONS:
            for qual in QUALITY_OPTIONS:
                r = run_cv(closes, ok, voo, cv_windows, dist, qual, lift, 252)
                fdfv3 = r.get('fdfv3')
                if fdfv3 and fdfv3['significant'] and fdfv3['mean_sep'] > best_sep:
                    best_sep = fdfv3['mean_sep']
                    best_params = {'dist_t': dist, 'dfv_lift': lift, 'quality_t': qual}
                print(f'  dist={dist} lift={lift} qual={qual} → '
                      f'{fdfv3["mean_sep"]*100:+.2f}% p={fdfv3["p_value"]:.3f}'
                      f' {"✓" if fdfv3 and fdfv3["significant"] else " "}')

    if best_params is None:
        print('No significant params found — using defaults')
        best_params = {'dist_t': -0.12, 'dfv_lift': 4.0, 'quality_t': 0.15}
    print(f'\nBest: DIST_T={best_params["dist_t"]} DFV_LIFT={best_params["dfv_lift"]} QUALITY={best_params["quality_t"]}')

    # Run all 4 horizons with optimal params
    print('\nRunning all 4 horizons with optimal params...')
    all_hr = {}
    for fw in HORIZONS:
        all_hr[fw] = run_cv(closes, ok, voo, cv_windows,
                            best_params['dist_t'], best_params['quality_t'],
                            best_params['dfv_lift'], fw)
        r = all_hr[fw]
        fdfv3 = r.get('fdfv3')
        if fdfv3:
            print(f'  {fw}d: {fdfv3["mean_sep"]*100:+.2f}% ±{fdfv3["std"]*100:.1f}% '
                  f'p={fdfv3["p_value"]:.3f} {"✓" if fdfv3["significant"] else " "}')

    # Compute validated buy weights
    BUY_SIGS = ['f','fdfv3','pfd','triple','dfv3','dfv1']
    SELL_SIGS = ['near_high','wb']

    def n_sig(sig, direction='buy'):
        count = 0
        for fw in HORIZONS:
            r = all_hr[fw].get(sig)
            if r is None: continue
            if direction == 'buy'  and r['p_value'] < P_THRESHOLD and r['mean_sep'] > 0: count += 1
            if direction == 'sell' and r['p_value'] < P_THRESHOLD and r['mean_sep'] < 0: count += 1
        return count

    raw_weights = {}
    for s in BUY_SIGS:
        r = all_hr[252].get(s)
        if r and r['p_value'] < P_THRESHOLD and r['mean_sep'] > 0:
            nc = n_sig(s, 'buy')
            raw_weights[s] = r['mean_sep'] * (nc / 4)

    total_raw = sum(raw_weights.values()) or 1
    BUY_WEIGHTS = {s: max(1, round(v/total_raw*100)) for s, v in raw_weights.items()}

    # Sell weights from validated results (near_high 4/4, wb_plus_div 3/4)
    SELL_WEIGHTS = {'near_high': 45, 'wb_plus_div': 35, 'wb_alone': 10}

    # Build payload
    payload = {
        'generated_at':  datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
        'p_threshold':   P_THRESHOLD,
        'n_cv_windows':  N_CV_WINDOWS,
        'universe_size': len(ok),
        'optimal_params': {
            'dist_t':    best_params['dist_t'],
            'dfv_lift':  best_params['dfv_lift'],
            'quality_t': best_params['quality_t'],
            'trend_t':   0.0,
        },
        'buy_weights':          BUY_WEIGHTS,
        'buy_wb_penalty':       -15,
        'fundamental_boost_max': 15,
        'sell_weights':         SELL_WEIGHTS,
        'signal_results_252d': {
            s: {k: round(v,4) if isinstance(v,float) else v
                for k,v in (all_hr[252].get(s) or {}).items()}
            for s in BUY_SIGS + SELL_SIGS
        },
        'consistency': {
            s: n_sig(s, 'sell' if s in SELL_SIGS else 'buy')
            for s in BUY_SIGS + SELL_SIGS
        },
    }

    out = 'validated_params.json'
    with open(out, 'w') as f:
        json.dump(payload, f, indent=2, default=str)
    print(f'\nExported: {out} ({os.path.getsize(out):,} bytes)')
    print(f'Buy weights: {BUY_WEIGHTS}')
    print(f'Sell weights: {SELL_WEIGHTS}')

if __name__ == '__main__':
    main()
