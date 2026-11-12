"""
signal_ledger.py — live, out-of-sample scorekeeping for the signal model.

Why this exists
---------------
Backtests on this repo's universe are biased: the tickers were chosen with
knowledge of how they performed. The only clean evidence is forward evidence.
Each time a ticker ENTERS a label (BUY, WATCH, TRIM, ...) the event is logged
with its date and close. Later runs fill in what happened next, measured against
the equal-weight average of every ticker the scanner covers over the same days.

  excess > 0 after a BUY/WATCH  -> the label added value
  excess < 0 after a TRIM/REDUCE/EXIT -> the label added value

`summary` aggregates by label and horizon. Treat it as noise until a label has
a few dozen completed events; with fewer, a 10-point average excess is ordinary luck.
"""
import json
from datetime import datetime

import numpy as np
import pandas as pd

LEDGER_PATH = 'signal_ledger.json'
HORIZONS    = (21, 63, 126, 252)                       # trading days
TRACKED     = ('STRONG BUY', 'BUY', 'FW BLOCKED', 'WATCH', 'TRIM', 'REDUCE', 'EXIT')


def _load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {'state': {}, 'events': []}


def _window_return(series, start_date, h):
    """Close-to-close return over h trading days starting at the first bar on/after start_date."""
    s = series.dropna()
    pos = s.index.searchsorted(pd.Timestamp(start_date))
    if pos >= len(s) or pos + h >= len(s):
        return None
    return float(s.iloc[pos + h] / s.iloc[pos] - 1)


def update_ledger(payload, closes, universe, path=LEDGER_PATH):
    led    = _load(path)
    state  = led.get('state', {})
    events = led.get('events', [])
    rows   = payload.get('analytics', {}).get('signals', [])
    universe = [t for t in universe if t in closes.columns]

    # 1. fill in outcomes that have matured since the last run
    for ev in events:
        t = ev['ticker']
        if t not in closes.columns:
            continue
        for h in HORIZONS:
            k = str(h)
            if k in ev.get('outcomes', {}):
                continue
            r = _window_return(closes[t], ev['date'], h)
            if r is None:
                continue
            peers = [x for x in (_window_return(closes[u], ev['date'], h) for u in universe) if x is not None]
            if len(peers) < 20:
                continue
            u = float(np.mean(peers))
            ev.setdefault('outcomes', {})[k] = {'ret': round(r * 100, 2), 'universe': round(u * 100, 2),
                                               'excess': round((r - u) * 100, 2)}

    # 2. log tickers that ENTERED a tracked label on this run
    added = 0
    for r in rows:
        t, label = r.get('ticker'), r.get('label')
        if not t or label is None or label == 'NO DATA' or r.get('proxy'):
            continue                                   # proxy funds: price is the ETF's, not the fund's
        prev = state.get(t)
        state[t] = label
        if label == prev or label not in TRACKED or t not in closes.columns:
            continue
        s = closes[t].dropna()
        if s.empty:
            continue
        events.append({
            'ticker': t, 'label': label, 'from': prev,
            'date': s.index[-1].strftime('%Y-%m-%d'), 'price': round(float(s.iloc[-1]), 4),
            'buy_score': r.get('buy_score'), 'sell_score': r.get('sell_score'),
            'framework_score': r.get('framework_score'), 'is_holding': bool(r.get('is_holding')),
            'outcomes': {},
        })
        added += 1

    # 3. scoreboard
    summary = {}
    for label in TRACKED:
        for h in HORIZONS:
            ex = [e['outcomes'][str(h)]['excess'] for e in events
                  if e['label'] == label and str(h) in e.get('outcomes', {})]
            if ex:
                summary.setdefault(label, {})[str(h)] = {
                    'n': len(ex), 'mean_excess': round(float(np.mean(ex)), 2),
                    'median_excess': round(float(np.median(ex)), 2),
                    'hit_rate': round(float(np.mean([x > 0 for x in ex])) * 100, 1)}

    out = {'updated': datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC'),
           'note': 'excess = ticker return minus equal-weight scanner universe over the same trading days, in %',
           'summary': summary, 'state': state, 'events': events}
    with open(path, 'w') as f:
        json.dump(out, f, indent=1)
    print(f'Signal ledger: {len(events)} events (+{added} new), '
          f'{sum(1 for e in events if e.get("outcomes"))} with outcomes → {path}')
    return out
