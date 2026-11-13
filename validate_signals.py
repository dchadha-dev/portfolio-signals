"""
validate_signals.py — does any signal in this model actually predict returns?

This replaces signal_scanner_validation_cpcv.py as the basis for model decisions.
The older script's headline numbers (70% annual excess, t-stats of 40-120, deflated
Sharpe in the dozens) are artefacts, not evidence:

  1. Every observation was counted 22 times (66 "paths" that only re-slice the same
     days, x 2 "seeds" that change nothing). Nothing was ever fitted on a train split.
  2. Daily observations of 252-day forward returns were treated as independent.
     Adjacent days share 251/252 of their window.
  3. A Sharpe built from 252-day returns was annualised again by sqrt(252).
  4. "PBO" was the share of paths ranked below the median — 0.50 by construction.
  5. Returns were measured against VOO on a universe picked with hindsight, so the
     universe's own outperformance was credited to the signal.

What this script does instead
  * Scores history with the production functions (compute_signals, technical_buy_frame,
    sell_frame) — no second copy of the model.
  * Calendar-time portfolios: enter the day AFTER a signal, hold N trading days,
    equal weight. That yields one ordinary daily return series per signal, so the
    t-statistic is not inflated by overlapping windows.
  * Benchmarks each signal against the equal-weight average of the SAME universe
    over the same days. The hindsight premium of the universe cancels out.
  * Reports both halves of the sample separately.

It cannot remove hindsight from the ticker list itself: on your own universe a
"buy the dip" rule is flattered because every name is a known survivor. Read the
results as an upper bound, and trust signal_ledger.json (live, forward) more.

Usage:  python validate_signals.py            # yfinance, scanner universe
        python validate_signals.py --prices-pickle panel.pkl   # offline {close,high,low,volume}
"""
import argparse, json, warnings
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')
import signal_scanner as sc
import sell_side_scorer as ss

HOLDS = (5, 63, 252)   # 5d = entry timing, 63/252d = does it forecast
# name -> (which frame, column/expr, direction the model assumes: +1 buy, -1 sell)
BUY_TESTS = {
    # v3 — the live model
    'v3 gate = BUY (>=80)':     lambda b, s: b['f'] & (b['buy'] >= sc.BUY_T),
    'v3 deep dip (>=90)':       lambda b, s: b['deepdip'],
    'v3 WATCH (60-79)':         lambda b, s: b['near'] & ~b['f'] & (b['buy'] >= sc.WATCH_T),
    'dip, below 200d MA':       lambda b, s: b['dip'] & ~b['up'],
    'dip, weak 12m run':        lambda b, s: b['dip'] & b['up'] & ~b['qual'],
    # retired v2 component, kept as a reference line
    'gate + DFV V3 (v2 +25)':   lambda b, s: b['fdfv3'],
}
PENALTY_TESTS = {}
SELL_TESTS = {
    'near 252d high':           lambda b, s: s['near_high'],
    'CMF<0 near high':          lambda b, s: s['cmf_neg_near_high'],
    'ATR ext >3.5 near high':   lambda b, s: s['sma_atr_gt35'] & s['near_high'],
    'RV z-score > 2':           lambda b, s: s['rv_z2'],
}


def load_prices(years, pickle_path=None):
    if pickle_path:
        d = pd.read_pickle(pickle_path)
        return d['close'], d['high'], d['low'], d['volume'], d.get('benchmark')
    import yfinance as yf
    tickers = [sc.TICKER_ALIASES.get(t, t) for t in sc.DIRECT_HOLDINGS + sc.CANDIDATES]
    tickers = list(dict.fromkeys(tickers + [sc.BENCHMARK]))
    end = datetime.today(); start = end - timedelta(days=years * 365 + 90)
    raw = yf.download(tickers, start=start, end=end, interval='1d', auto_adjust=True,
                      progress=False, threads=True)
    f = lambda k: raw[k].rename(columns=sc.ALIAS_REVERSE).dropna(how='all')
    close = f('Close'); bench = close[sc.BENCHMARK] if sc.BENCHMARK in close else None
    return close, f('High'), f('Low'), f('Volume'), bench


def build_masks(close, high, low, volume):
    """Run the production signal code per ticker; return {test name: wide bool frame}."""
    cols = {}; tests = {**BUY_TESTS, **PENALTY_TESTS, **SELL_TESTS}
    for t in close.columns:
        cl = close[t].dropna()
        if len(cl) < 300: continue
        try:
            b = sc.compute_signals(cl)
            if len(b) < 50: continue
            b['buy'] = sc.technical_buy_frame(b)
            g = lambda w: w[t].reindex(cl.index) if t in w.columns else cl
            s = ss.sell_frame(cl, g(high), g(low), g(volume)).reindex(b.index)
            for name, fn in tests.items():
                cols.setdefault(name, {})[t] = fn(b, s).fillna(False).astype(bool)
            cols.setdefault('_elig', {})[t] = pd.Series(True, index=b.index)
        except Exception as e:
            print(f'  skip {t}: {str(e)[:60]}')
    wide = {k: pd.DataFrame(v).reindex(close.index).fillna(False).astype(bool) for k, v in cols.items()}
    return wide


def calendar_time(mask, elig, ret, hold, min_names=3):
    """Daily excess return of 'hold everything that signalled in the last `hold` days'
    over the equal-weight universe. Entry is the day after the signal."""
    entered = (mask & elig).shift(1, fill_value=False)
    active  = entered.rolling(hold, min_periods=1).max().astype(bool)
    nxt     = ret.shift(-1)
    port    = nxt.where(active).mean(axis=1)
    univ    = nxt.where(elig).mean(axis=1)
    ex      = (port - univ).where(active.sum(axis=1) >= min_names).dropna()
    if len(ex) < 250:
        return None
    half = len(ex) // 2
    ann  = lambda x: float(x.mean() * 252 * 100)
    return {'excess_pct_yr': round(ann(ex), 2),
            't_stat': round(float(ex.mean() / ex.std() * np.sqrt(len(ex))), 2),
            'first_half': round(ann(ex.iloc[:half]), 2), 'second_half': round(ann(ex.iloc[half:]), 2),
            'avg_names': round(float(active.sum(axis=1).loc[ex.index].mean()), 1),
            'days': int(len(ex)), 'from': str(ex.index[0].date()), 'to': str(ex.index[-1].date())}


def verdict(res, direction):
    """Supported only if the effect has the sign the model assumes, |t| >= 2,
    and both halves agree. Anything else is 'no evidence'."""
    if res is None: return 'too few signals'
    signed = [res['excess_pct_yr'] * direction, res['first_half'] * direction, res['second_half'] * direction]
    if all(x > 0 for x in signed) and abs(res['t_stat']) >= 2: return 'SUPPORTED'
    if all(x < 0 for x in signed) and abs(res['t_stat']) >= 2: return 'WRONG SIGN'
    return 'no evidence'


def run(close, high, low, volume, bench=None):
    masks = build_masks(close, high, low, volume)
    elig  = masks.pop('_elig'); ret = close.pct_change(fill_method=None)
    out   = {'generated_at': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
             'universe_size': int(elig.any().sum()), 'method': 'calendar-time, excess vs equal-weight universe',
             'signals': {}}
    uni = ret.where(elig.shift(1, fill_value=False)).mean(axis=1).dropna()
    out['universe_return_pct_yr'] = round(float(uni.mean() * 252 * 100), 1)
    if bench is not None:
        b = bench.pct_change().reindex(uni.index)
        out['benchmark_return_pct_yr'] = round(float(b.mean() * 252 * 100), 1)
        out['hindsight_premium_pct_yr'] = round(out['universe_return_pct_yr'] - out['benchmark_return_pct_yr'], 1)
    for group, tests, direction in (('buy', BUY_TESTS, 1), ('penalty', PENALTY_TESTS, -1), ('sell', SELL_TESTS, -1)):
        for name in tests:
            entry = {'group': group, 'assumed_direction': direction}
            for h in HOLDS:
                r = calendar_time(masks[name], elig, ret, h)
                entry[f'hold_{h}d'] = r; entry[f'verdict_{h}d'] = verdict(r, direction)
            out['signals'][name] = entry
    return out


def report(out):
    print(f"\nUniverse: {out['universe_size']} tickers, equal-weight return {out['universe_return_pct_yr']}%/yr"
          + (f" vs benchmark {out['benchmark_return_pct_yr']}%/yr  -> the ticker list itself is worth "
             f"{out['hindsight_premium_pct_yr']:+}%/yr before any signal (hindsight)" if 'benchmark_return_pct_yr' in out else ''))
    print('Excess = signal portfolio minus equal-weight universe, %/yr. Supported needs |t|>=2 and both halves agreeing.\n')
    print(f"{'signal':<26}{'grp':<8}" + ''.join(f"{'hold '+str(h)+'d: excess':>18}{'t':>7}{'1st/2nd half':>16}{'verdict':>14}" for h in HOLDS))
    for name, e in out['signals'].items():
        line = f"{name:<26}{e['group']:<8}"
        for h in HOLDS:
            r = e[f'hold_{h}d']
            line += (f"{r['excess_pct_yr']:>+18.2f}{r['t_stat']:>+7.2f}{(str(r['first_half'])+' / '+str(r['second_half'])):>16}" if r
                     else f"{'—':>18}{'—':>7}{'—':>16}") + f"{e[f'verdict_{h}d']:>14}"
        print(line)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--years', type=int, default=10)
    ap.add_argument('--prices-pickle', default=None)
    ap.add_argument('--out', default='validation_honest.json')
    a = ap.parse_args()
    out = run(*load_prices(a.years, a.prices_pickle))
    report(out)
    with open(a.out, 'w') as f:
        json.dump(out, f, indent=2)
    print(f'\nWritten: {a.out}')
