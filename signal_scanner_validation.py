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

    # ── Extended sell signals ─────────────────────────────────────────
    # RSI14 bearish divergence (rbear)
    rsi14            = calc_rsi(df['close'], 14)
    df['rsi14']      = rsi14
    rsi_above        = (rsi14 - 60).clip(lower=0).ewm(span=3, adjust=False).mean()
    rsi_pk           = rsi_above.rolling(20).max().shift(1)
    price_pk         = df['close'].rolling(20).max().shift(1)
    df['rbear']      = (
        (df['close'] > price_pk * 1.01) &
        (rsi_above   < rsi_pk  * 0.85) &
        (rsi_above   > 0)
    )

    # WB + divergence confluence
    df['wb_div']     = df['wb'] & df['rbear']

    # Momentum exhaustion
    df['mom20']      = df['close'].pct_change(63) > 0.20
    df['mom30']      = df['close'].pct_change(63) > 0.30

    # MFI proxy bearish divergence (using RSI14 above 65 as MFI proxy)
    mfi_proxy        = (rsi14 - 65).clip(lower=0).ewm(span=3, adjust=False).mean()
    mfi_pk           = mfi_proxy.rolling(20).max().shift(1)
    df['mfbear']     = (
        (df['close'] > price_pk * 1.01) &
        (mfi_proxy   < mfi_pk  * 0.85) &
        (mfi_proxy   > 0)
    )

    # BRED: 2+ bearish divergence components
    macd             = df['close'].ewm(span=12,adjust=False).mean() - df['close'].ewm(span=26,adjust=False).mean()
    dmacd            = macd.clip(lower=0).ewm(span=3, adjust=False).mean()
    dmacd_pk         = dmacd.rolling(20).max().shift(1)
    macd_bear        = (
        (df['close'] > price_pk * 1.01) &
        (dmacd       < dmacd_pk * 0.85) &
        (dmacd       > 0)
    )
    bred_count       = df['rbear'].astype(int) + df['mfbear'].astype(int) + macd_bear.astype(int)
    df['bred']       = bred_count >= 2

    # Quality deterioration: Sharpe declining over 63d
    qual63           = calc_quality(df['close'], window=63)
    qual63_prev      = qual63.shift(21)
    df['qual_det']   = (qual63 < qual63_prev - 0.10) & qual63.notna() & qual63_prev.notna()

    # RVI bearish div proxy (using MACD as RVI substitute — needs OHLCV for real RVI)
    df['rvbear']     = macd_bear

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
    SIGS = ['f','fdfv3','pfd','triple','dfv3','dfv1','wb','near_high','rand','rbear','wb_div','mom20','mom30','mfbear','bred','qual_det','rvbear']
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
    BUY_SIGS  = ['f','fdfv3','pfd','triple','dfv3','dfv1']
    SELL_SIGS = ['near_high','wb','rbear','wb_div','mom20','mom30','mfbear','bred','qual_det','rvbear']

    def n_sig(sig, direction='buy'):
        count = 0
        for fw in HORIZONS:
            r = all_hr[fw].get(sig)
            if r is None: continue
            if direction == 'buy'  and r['p_value'] < P_THRESHOLD and r['mean_sep'] > 0: count += 1
            if direction == 'sell' and r['p_value'] < P_THRESHOLD and r['mean_sep'] < 0: count += 1
        return count

    SELL_SIG_NAMES = {
        'near_high': 'Near 252d high',
        'wb':        'Banker Weak',
        'rbear':     'RSI14 bearish div',
        'wb_div':    'WB + div combo',
        'mom20':     'Momentum exhaust 20%',
        'mom30':     'Momentum exhaust 30%',
        'mfbear':    'MFI bearish div',
        'bred':      'BRED (2+ components)',
        'qual_det':  'Quality deterioration',
        'rvbear':    'RVI bearish div proxy',
    }

    raw_weights = {}
    for s in BUY_SIGS:
        r = all_hr[252].get(s)
        if r and r['p_value'] < P_THRESHOLD and r['mean_sep'] > 0:
            nc = n_sig(s, 'buy')
            raw_weights[s] = r['mean_sep'] * (nc / 4)

    total_raw = sum(raw_weights.values()) or 1
    BUY_WEIGHTS = {s: max(1, round(v/total_raw*100)) for s, v in raw_weights.items()}

    # Sell weights — computed from walk-forward results
    # Only signals with p < P_THRESHOLD AND negative mean_sep get weight
    raw_sell = {}
    for s in SELL_SIGS:
        r = all_hr[252].get(s)
        if r and r['p_value'] < P_THRESHOLD and r['mean_sep'] < 0:
            nc = n_sig(s, 'sell')
            raw_sell[s] = abs(r['mean_sep']) * (nc / 4)

    if raw_sell:
        total_sell_raw = sum(raw_sell.values())
        SELL_WEIGHTS = {s: max(1, round(v/total_sell_raw*100)) for s,v in raw_sell.items()}
    else:
        SELL_WEIGHTS = {'near_high': 60}  # fallback

    print(f'\nValidated SELL signals (negative sep, p<{P_THRESHOLD}):')
    for s in SELL_SIGS:
        r = all_hr[252].get(s)
        if r:
            direction = '✓ SELL' if r['mean_sep'] < 0 and r['p_value'] < P_THRESHOLD else '✗ not sell'
            print(f'  {SELL_SIG_NAMES.get(s,s):<28} sep={r["mean_sep"]*100:+.2f}% p={r["p_value"]:.3f} {direction}')

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
        'sell_signal_ranking': [
            {
                'signal': s,
                'name': SELL_SIG_NAMES.get(s, s),
                'mean_sep_252d': round(all_hr[252][s]['mean_sep']*100, 2) if all_hr[252].get(s) else None,
                'p_value': round(all_hr[252][s]['p_value'], 4) if all_hr[252].get(s) else None,
                'consistency': n_sig(s, 'sell'),
                'validated': bool(all_hr[252].get(s) and all_hr[252][s]['p_value'] < P_THRESHOLD and all_hr[252][s]['mean_sep'] < 0),
                'weight': SELL_WEIGHTS.get(s, 0),
            }
            for s in SELL_SIGS
        ],
    }

    out = 'validated_params.json'
    with open(out, 'w') as f:
        json.dump(payload, f, indent=2, default=str)
    print(f'\nExported: {out} ({os.path.getsize(out):,} bytes)')
    # write_html_report disabled until function is fixed
    # write_html_report(payload, all_hr, best_params, cv_windows, ok)
    print(f'Buy weights: {BUY_WEIGHTS}')
    print(f'Sell weights: {SELL_WEIGHTS}')

if __name__ == '__main__':
    main()


def write_html_report(payload, all_hr, best_params, cv_windows, ok):
    """Write a human-readable validation report to validation_report.html."""
    HORIZONS = [63, 126, 252, 504]
    BUY_SIGS  = ['f','fdfv3','pfd','triple','dfv3','dfv1']
    SELL_SIGS = ['near_high','wb']
    P_THRESHOLD = payload['p_threshold']

    SIGNAL_NAMES = {
        'f':         'Factor Value',
        'fdfv3':     'Factor+DFV V3 ★',
        'pfd':       'PFD Buy',
        'triple':    'Triple Composite',
        'dfv3':      'DFV V3 standalone',
        'dfv1':      'DFV V1 standalone',
        'wb':        'Banker Weak',
        'near_high': 'Near 252d High',
        'rand':      'Random 10%',
    }

    def cell(r, is_sell=False):
        if r is None: return '<td style="color:#aaa;text-align:right;padding:6px 10px">—</td>'
        sep = r['mean_sep']; p = r['p_value']; std = r['std']
        wins = r['n_wins']; n_w = r['n_windows']
        good = sep < 0 if is_sell else sep > 0
        sig  = p < P_THRESHOLD and good
        bg = ('#d4edda' if sig else '#f0fff4') if good else ('#f8d7da' if abs(sep)>0.01 else '#fff8f0')
        tc = '#155724' if sig else '#721c24' if not good and abs(sep)>0.01 else '#856404'
        return (f'<td style="text-align:right;background:{bg};color:{tc};padding:6px 10px;font-size:13px">'
                f'<b>{sep*100:+.1f}%</b>{"✓" if sig else ""}<br>'
                f'<span style="font-size:11px;opacity:.75">±{std*100:.1f}% · {wins}/{n_w} wins · p={p:.2f}</span>'
                f'</td>')

    def n_sig(sig, direction='buy'):
        count = 0
        for fw in HORIZONS:
            r = all_hr[fw].get(sig)
            if not r: continue
            if direction == 'buy'  and r['p_value'] < P_THRESHOLD and r['mean_sep'] > 0: count += 1
            if direction == 'sell' and r['p_value'] < P_THRESHOLD and r['mean_sep'] < 0: count += 1
        return count

    regimes = [w['regime'] for w in cv_windows]

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Model Validation Report</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f8f9fa;color:#212529;margin:0;padding:20px}}
.wrap{{max-width:960px;margin:0 auto}}
h1{{font-size:24px;font-weight:700;margin:0 0 4px}}
h2{{font-size:17px;font-weight:600;margin:24px 0 10px;padding-bottom:6px;border-bottom:2px solid #dee2e6}}
.meta{{font-size:13px;color:#6c757d;margin:0 0 20px;line-height:1.8}}
.params{{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px;margin-bottom:20px}}
.param{{background:#fff;border:1px solid #dee2e6;border-radius:8px;padding:12px 14px}}
.param-label{{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:#6c757d;margin-bottom:4px}}
.param-val{{font-size:20px;font-weight:700;font-family:monospace}}
table{{width:100%;border-collapse:collapse;background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.1);margin-bottom:20px}}
th{{background:#f0f0f0;padding:8px 10px;text-align:right;font-size:12px;font-weight:600;border-bottom:2px solid #dee2e6}}
th:first-child,th:nth-child(2){{text-align:left}}
td:first-child{{padding:6px 10px;font-weight:500}}
td:nth-child(2){{padding:6px 10px;font-size:12px;color:#6c757d}}
tr:hover td{{background:#f8f9fa}}
.group-row td{{background:#f4f4f4;font-size:11px;font-weight:600;letter-spacing:.06em;color:#555;padding:5px 10px}}
.windows{{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px;margin-bottom:20px}}
.window{{background:#fff;border:1px solid #dee2e6;border-radius:6px;padding:10px 12px;font-size:12px}}
.window-regime{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600;margin-bottom:5px}}
.bull{{background:#d4edda;color:#155724}}.bear{{background:#f8d7da;color:#721c24}}.sideways{{background:#fff3cd;color:#856404}}
.weights{{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:20px}}
.weight{{background:#fff;border:1px solid #dee2e6;border-radius:6px;padding:8px 12px;font-size:13px}}
.weight b{{font-family:monospace;font-size:16px}}
.footer{{font-size:12px;color:#6c757d;margin-top:20px;text-align:center}}
</style>
</head>
<body>
<div class="wrap">
<h1>📊 Model Validation Report</h1>
<p class="meta">
Generated: <b>{payload['generated_at']}</b> &nbsp;·&nbsp;
Universe: <b>{payload['universe_size']} tickers</b> &nbsp;·&nbsp;
CV windows: <b>{len(cv_windows)}</b> (random walk-forward) &nbsp;·&nbsp;
p-threshold: <b>{P_THRESHOLD}</b> &nbsp;·&nbsp;
Regime mix: <b>{regimes.count("bull")} bull / {regimes.count("sideways")} sideways / {regimes.count("bear")} bear</b>
</p>

<h2>Optimal Parameters</h2>
<div class="params">
  <div class="param"><div class="param-label">DIST_T</div><div class="param-val">{best_params['dist_t']}</div><div style="font-size:11px;color:#6c757d">dist from 252d high</div></div>
  <div class="param"><div class="param-label">DFV_LIFT</div><div class="param-val">{best_params['dfv_lift']}</div><div style="font-size:11px;color:#6c757d">hm_rsi floor lift</div></div>
  <div class="param"><div class="param-label">QUALITY_T</div><div class="param-val">{best_params['quality_t']}</div><div style="font-size:11px;color:#6c757d">Sharpe/3 threshold</div></div>
  <div class="param"><div class="param-label">p-threshold</div><div class="param-val">{P_THRESHOLD}</div><div style="font-size:11px;color:#6c757d">significance cutoff</div></div>
</div>

<h2>Validated Weights</h2>
<div class="weights">'''

    bw = payload['buy_weights']
    for sig, w in sorted(bw.items(), key=lambda x: -x[1]):
        html += f'<div class="weight">{SIGNAL_NAMES.get(sig,sig)}: <b>+{w}</b></div>'
    html += f'<div class="weight" style="border-color:#dc3545;color:#dc3545">Banker Weak: <b>{payload["buy_wb_penalty"]}</b></div>'
    html += f'<div class="weight" style="border-color:#0d6efd;color:#0d6efd">Fundamental boost: <b>0–{payload["fundamental_boost_max"]}</b></div>'
    html += '</div>'

    html += '''<h2>Signal Separation — All Horizons</h2>
<p style="font-size:13px;color:#6c757d;margin-bottom:10px">
✓ = statistically significant at p&lt;''' + str(P_THRESHOLD) + '''. 
Buy signals: green = positive sep (good). Sell signals: green = negative sep (good).<br>
Format: <b>mean sep</b> / ±std / wins/windows / p-value
</p>
<table>
<tr><th style="text-align:left">Signal</th><th>Category</th>'''
    for fw in HORIZONS:
        html += f'<th>{fw}d ({fw//21}mo)</th>'
    html += '<th>Consistent</th></tr>'

    for group, sigs, is_sell in [('BUY SIGNALS', BUY_SIGS, False), ('SELL SIGNALS', SELL_SIGS, True)]:
        html += f'<tr class="group-row"><td colspan="6">{group}</td></tr>'
        for sig in sigs:
            name = SIGNAL_NAMES.get(sig, sig)
            is_best = sig == 'fdfv3'
            w = bw.get(sig, 0)
            weight_str = f' (+{w})' if w and not is_sell else ''
            html += f'<tr style="{"font-weight:600" if is_best else ""}">'
            html += f'<td>{name}{weight_str}</td>'
            html += f'<td>{"Combined ★" if is_best else "Sell" if is_sell else "Buy"}</td>'
            nc = n_sig(sig, 'sell' if is_sell else 'buy')
            for fw in HORIZONS:
                r = all_hr[fw].get(sig)
                html += cell(r, is_sell=is_sell)
            nc_col = '#155724' if nc >= 3 else '#856404' if nc >= 2 else '#721c24'
            html += f'<td style="text-align:right;color:{nc_col};font-weight:700;padding:6px 10px">{nc}/4</td>'
            html += '</tr>'

    html += '</table>'

    html += '<h2>CV Windows Used</h2><div class="windows">'
    for i, w in enumerate(cv_windows):
        rc = {'bull':'bull','bear':'bear','sideways':'sideways'}.get(w['regime'],'sideways')
        html += (f'<div class="window">'
                 f'<span class="window-regime {rc}">{w["regime"].upper()}</span> '
                 f'VOO {w["voo_ret"]:+.1f}%<br>'
                 f'<b>Train:</b> {str(w["train_start"])[:10]} → {str(w["train_end"])[:10]}<br>'
                 f'<b>Test:</b> {str(w["test_start"])[:10]} → {str(w["test_end"])[:10]}'
                 f'</div>')
    html += '</div>'

    html += f'<p class="footer">Next quarterly validation scheduled automatically via GitHub Actions · portfolio-signals</p>'
    html += '</div></body></html>'

    with open('validation_report.html', 'w') as f:
        f.write(html)
    print(f'Written: validation_report.html ({len(html):,} chars)')
    print(f'View at: https://portfolio-signals.netlify.app/validation_report.html')
