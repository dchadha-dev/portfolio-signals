"""
fundamentals_fetcher.py
═══════════════════════
Weekly fetch of company fundamentals from Finnhub's basic-financials endpoint
(/stock/metric?metric=all), normalised into the handful of inputs that
framework_scorer.py needs. Writes fundamentals.json.

One call per ticker, ~1.1s apart (free tier: 60 calls/min) — about five minutes
for the full universe. Tickers Finnhub cannot serve (most non-US listings on the
free tier, ETFs, funds) are simply absent from the output; the scorer then falls
back to hand or legacy values for them and labels the score accordingly.

Field names are matched through alias lists because Finnhub's metric keys vary
by plan and have changed over time. The run prints a coverage table and a sanity
check on AAPL so a silent mismatch is visible in the Actions log.
"""

import json, os, statistics, sys, time
from datetime import datetime, timezone

import requests

RATE_LIMIT_SLEEP = 1.1
OUT_FILE = 'fundamentals.json'
HISTORY_YEARS = 5

# normalised name -> (metric-block aliases [percent / plain], series aliases [fractions for margins])
PCT = 'pct'; RATIO = 'ratio'
FIELDS = {
    'gross_margin':       (PCT,   ['grossMarginTTM', 'grossMarginAnnual', 'grossMargin5Y'],                 ['grossMargin']),
    'fcf_margin':         (PCT,   [],                                                                       ['fcfMargin']),
    'roic':               (PCT,   ['roicTTM', 'roiTTM', 'roiAnnual', 'roi5Y'],                              ['roicTTM', 'roic']),
    'net_debt_to_equity': (RATIO, ['netDebtToTotalEquityQuarterly', 'netDebtToTotalEquityAnnual',
                                   'totalDebt/totalEquityQuarterly', 'totalDebt/totalEquityAnnual'],        ['netDebtToTotalEquity', 'totalDebtToEquity']),
    'rev_growth_3y':      (PCT,   ['revenueGrowth3Y', 'revenueGrowth5Y'],                                   []),
    'rev_growth_ttm':     (PCT,   ['revenueGrowthTTMYoy', 'revenueGrowthQuarterlyYoy'],                     []),
    'eps_growth_3y':      (PCT,   ['epsGrowth3Y', 'epsGrowth5Y'],                                           []),
    'eps_ttm':            (RATIO, ['epsTTM', 'epsBasicExclExtraItemsTTM', 'epsExclExtraItemsTTM',
                                   'epsInclExtraItemsTTM'],                                                 []),
    'pe_ttm':             (RATIO, ['peTTM', 'peBasicExclExtraTTM', 'peExclExtraTTM', 'peInclExtraTTM'],     []),
    'ps_ttm':             (RATIO, ['psTTM', 'psAnnual'],                                                    []),
}
HISTORY = {'pe_5y_median': ['peTTM', 'pe'], 'ps_5y_median': ['psTTM', 'ps']}


def _num(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float('inf') else None


def _series_values(series, names, years=HISTORY_YEARS):
    """Most recent `years` annual values for the first alias present."""
    annual = (series or {}).get('annual') or {}
    for n in names:
        rows = annual.get(n) or []
        vals = [(r.get('period', ''), _num(r.get('v'))) for r in rows if isinstance(r, dict)]
        vals = [v for _, v in sorted(vals, reverse=True)[:years] if v is not None]
        if vals:
            return vals
    return []


def normalise(raw):
    """Finnhub response -> flat dict of scorer inputs. Missing inputs are omitted."""
    metric = (raw or {}).get('metric') or {}
    series = (raw or {}).get('series') or {}
    out = {}
    for name, (kind, m_keys, s_keys) in FIELDS.items():
        val = next((v for v in (_num(metric.get(k)) for k in m_keys) if v is not None), None)
        if val is None and s_keys:
            sv = _series_values(series, s_keys, years=1)
            if sv:
                val = sv[0] * 100.0 if kind == PCT else sv[0]     # series margins are fractions
        if val is not None:
            out[name] = round(val, 4)
    for name, s_keys in HISTORY.items():
        vals = [v for v in _series_values(series, s_keys) if v > 0]
        if len(vals) >= 3:
            out[name] = round(statistics.median(vals), 2)
    return out


def fetch_one(symbol, token, session):
    r = session.get('https://finnhub.io/api/v1/stock/metric',
                    params={'symbol': symbol, 'metric': 'all', 'token': token}, timeout=20)
    if r.status_code == 429:
        time.sleep(30)
        r = session.get('https://finnhub.io/api/v1/stock/metric',
                        params={'symbol': symbol, 'metric': 'all', 'token': token}, timeout=20)
    if r.status_code != 200:
        return None, f'http {r.status_code}'
    try:
        return r.json(), None
    except ValueError:
        return None, 'bad json'


def sanity_check(tickers):
    """Known-value check: catches unit or alias mistakes that would otherwise
    produce plausible-looking nonsense."""
    a = tickers.get('AAPL')
    if not a:
        return ['AAPL missing — cannot sanity-check units']
    problems = []
    for key, lo, hi in (('gross_margin', 30, 60), ('roic', 15, 200), ('pe_ttm', 10, 60)):
        v = a.get(key)
        if v is None:
            problems.append(f'AAPL {key} missing')
        elif not lo <= v <= hi:
            problems.append(f'AAPL {key}={v} outside expected {lo}-{hi} — check units/aliases')
    return problems


def main():
    token = os.environ.get('FINNHUB_TOKEN', '')
    if not token:
        print('ERROR: FINNHUB_TOKEN environment variable not set'); sys.exit(1)
    import framework_scorer as fw
    # US listings only: Finnhub's free tier does not serve most foreign listings, and
    # a suffix-stripped symbol can resolve to a different company (MC.PA -> Moelis).
    finnhub_symbol = lambda t: {'BRKB': 'BRK.B'}.get(t, t)
    entries = fw.load_entries()
    universe = [t for t, e in entries.items() if e.get('kind') != 'fund' and '_' not in t and '.' not in t and not t.isdigit()]
    session = requests.Session()
    tickers, errors, seen_keys = {}, {}, set()
    for i, t in enumerate(universe):
        raw, err = fetch_one(finnhub_symbol(t), token, session)
        if err:
            errors[t] = err
        else:
            seen_keys.update((raw.get('metric') or {}).keys())
            m = normalise(raw)
            if m:
                tickers[t] = m
            else:
                errors[t] = 'no usable fields'
        if (i + 1) % 50 == 0:
            print(f'  {i + 1}/{len(universe)} fetched, {len(tickers)} usable')
        time.sleep(RATE_LIMIT_SLEEP)

    coverage = {k: sum(1 for m in tickers.values() if k in m) for k in list(FIELDS) + list(HISTORY)}
    problems = sanity_check(tickers)
    unmatched = {name: FIELDS[name][1] for name in FIELDS
                 if FIELDS[name][1] and not any(k in seen_keys for k in FIELDS[name][1])}
    out = {
        'as_of': datetime.now(timezone.utc).strftime('%Y-%m-%d'),
        'source': 'finnhub /stock/metric?metric=all',
        'health': {'requested': len(universe), 'usable': len(tickers), 'coverage': coverage,
                   'sanity_problems': problems, 'aliases_never_seen': unmatched,
                   'errors': dict(list(errors.items())[:80])},
        'tickers': tickers,
    }
    # Never overwrite a good file with an empty or implausible one.
    if len(tickers) < 20 or any('outside expected' in p for p in problems):
        print(f'NOT WRITING {OUT_FILE}: usable={len(tickers)}, problems={problems}')
        print('metric keys seen (sample):', sorted(seen_keys)[:120])
        sys.exit(1)
    with open(OUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(out, f, indent=1, sort_keys=True)
    print(f'{OUT_FILE}: {len(tickers)}/{len(universe)} tickers')
    print('coverage:', coverage)
    if problems:  print('sanity:', problems)
    if unmatched: print('aliases never seen (field came from series or is missing):', unmatched)


if __name__ == '__main__':
    main()
