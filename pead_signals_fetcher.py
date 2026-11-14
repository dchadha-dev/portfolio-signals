"""
pead_signals_fetcher.py
══════════════════════════════════════════════════════════════════════
Weekly PEAD (Post-Earnings Announcement Drift) signal fetcher.

Methodology (Bernard-Thomas 1989, Daniel-Hirshleifer-Sun 2020):
  1. Find universe tickers that reported earnings in the last 7 days
     using Finnhub earnings calendar endpoint.
  2. For active tickers only, pull historical EPS surprises (last 8Q).
  3. Compute SUE = (actual - estimate) / std_dev_of_surprises
     - If std_dev < 0.02: use 0.02 floor to prevent zero-division.
  4. Signal fires if SUE > 1.5 (absolute threshold).
  5. Export to pead_signals.json with expires_at = announce_date + 60d.

Signal weight: +10pts to buy score (additive with insider/politician).
Combined cap with insider: max 35pts total alternative data boost.

Rate limit: 1 second between Finnhub calls (free tier: 60 calls/min).
"""

import json, math, sys, os, time
import requests
from datetime import datetime, timedelta, date

# ── UNIVERSE ──────────────────────────────────────────────────────────
UNIVERSE = [
    'NVDA','AVGO','TSLA','MELI','AMAT','MSFT','AAPL','AMZN','META','GOOG',
    'NFLX','BKNG','SHOP','RACE','AMD','ASML','ANET','DDOG','CRDO','NBIS',
    'TSM','TM','MU','INTU','CPRT','PGR','TTD','UNH','FISV','TEAM',
    'BRKB','NVO','RELX','DELL','BABA','COIN',
    'LLY','MA','V','MSCI','NOW','ISRG','SPGI','KLAC','TMO','CRWD',
    'PANW','ADBE','CRM','SNOW','AXON','RCL','UBER','CMG','PG','WM',
    'ENPH','CSCO','HPE','NVTS','HIMX','TSEM','RKLB','IREN','RGTI',
    'QBTS','IONQ','LMT','CCJ','KEEL','DNN','APLD','ONDS','SOUN',
    'FIVN','SOFI','SE','GRAB','TCOM','EXPE','INFY','HDB','SYM',
    'LRCX','ALAB','VRT','SERV','SEDG','KWEB','FINX','BOTZ','AIQ','BLOK',
    'QQQ','TQQQ','GLD','SCHD','JEPI','VOO','VTI','IVV','SMH','INDA',
]
UNIVERSE_SET = set(UNIVERSE)

# ── CONFIG ────────────────────────────────────────────────────────────
CALENDAR_LOOKBACK_DAYS = 14     # extended from 7 — Finnhub calendar has gaps on free tier
HISTORY_QUARTERS       = 8      # quarters of EPS history for SUE std_dev
SUE_THRESHOLD          = 1.5    # absolute SUE trigger
STD_DEV_FLOOR          = 0.02   # minimum std_dev to prevent zero-division
REL_STD_FLOOR          = 0.05   # ...and at least 5% of |estimate|: Finnhub's free tier gives
                                #    only 3 prior quarters, too few for a stable std_dev
MIN_HISTORY_QUARTERS   = 3      # fewer prior quarters than this: no signal
MAX_REL_SURPRISE       = 0.50   # a beat above 50% of the estimate is usually a GAAP vs adjusted
                                #    mismatch or a one-off gain, not an earnings surprise
PEAD_HOLD_DAYS         = 60     # signal expires 60 days after announce date
SIGNAL_BUY_SCORE_BOOST = 10     # pts added to buy score when SUE > threshold
RATE_LIMIT_SLEEP       = 1.0    # seconds between Finnhub calls (free tier)


def get_finnhub_token():
    token = os.environ.get('FINNHUB_TOKEN', '')
    if not token:
        print('ERROR: FINNHUB_TOKEN environment variable not set')
        sys.exit(1)
    return token


# ── STEP 1: EARNINGS CALENDAR — find who reported in last 7 days ──────

def fetch_recent_reporters(token):
    """
    Find universe tickers that reported in the last CALENDAR_LOOKBACK_DAYS days.

    Two-pass approach to handle Finnhub free-tier calendar gaps:
    Pass 1: Finnhub earnings calendar endpoint (fast, broad)
    Pass 2: Direct EPS history check for all universe tickers not found in Pass 1
            — checks if most recent quarter date falls within lookback window.
    This ensures tickers like CRM, DELL, COST aren't missed due to calendar gaps.
    """
    end_date   = date.today()
    start_date = end_date - timedelta(days=CALENDAR_LOOKBACK_DAYS)
    start_str  = start_date.strftime('%Y-%m-%d')
    end_str    = end_date.strftime('%Y-%m-%d')

    # ── Pass 1: Finnhub earnings calendar ────────────────────────────
    print(f'  Pass 1: Finnhub calendar {start_str} → {end_str}...')
    calendar_tickers = set()
    reporters = []
    try:
        time.sleep(RATE_LIMIT_SLEEP)
        r = requests.get(
            'https://finnhub.io/api/v1/calendar/earnings',
            params={'from': start_str, 'to': end_str, 'token': token},
            timeout=20,
        )
        if r.status_code == 200:
            for item in r.json().get('earningsCalendar', []):
                ticker = (item.get('symbol') or '').upper().strip()
                if ticker not in UNIVERSE_SET:
                    continue
                announce_date = item.get('date', '')
                if not announce_date:
                    continue
                calendar_tickers.add(ticker)
                reporters.append((ticker, announce_date))
            print(f'  Pass 1: {len(reporters)} universe tickers from calendar')
        else:
            print(f'  Pass 1: HTTP {r.status_code}')
    except Exception as e:
        print(f'  Pass 1 error: {e}')

    # ── Pass 2: per-ticker calendar for tickers the broad calendar missed ─
    # The free-tier broad calendar has gaps, so each remaining ticker is asked
    # for its own announcements in the window. The old fallback used the EPS
    # history's fiscal "period" (e.g. 2026-09-30) as if it were the announcement
    # date, which fired signals on quarter-ends rather than on the report.
    us_tickers_remaining = [
        t for t in UNIVERSE if '.' not in t and t not in calendar_tickers
    ]
    print(f'  Pass 2: per-ticker calendar for {len(us_tickers_remaining)} remaining tickers...')
    pass2_found = 0
    for ticker in us_tickers_remaining:
        try:
            time.sleep(RATE_LIMIT_SLEEP)
            r = requests.get(
                'https://finnhub.io/api/v1/calendar/earnings',
                params={'from': start_str, 'to': end_str, 'symbol': ticker, 'token': token},
                timeout=15,
            )
            if r.status_code != 200:
                continue
            items = [i for i in r.json().get('earningsCalendar', [])
                     if (i.get('symbol') or '').upper() == ticker and i.get('date')
                     and i.get('epsActual') is not None]
            if items:
                d = max(i['date'] for i in items)
                if start_str <= d[:10] <= end_str:
                    reporters.append((ticker, d[:10]))
                    calendar_tickers.add(ticker)
                    pass2_found += 1
        except Exception:
            continue

    print(f'  Pass 2: {pass2_found} additional tickers found')
    print(f'  Total: {len(reporters)} universe tickers with recent earnings: '
          f'{[t for t, _ in reporters]}')
    return reporters


# ── STEP 2: HISTORICAL EPS — compute SUE for active tickers ──────────

def fetch_eps_history(ticker, token):
    """
    Fetch historical quarterly EPS surprises from Finnhub.
    Returns list of {period, actual, estimate, surprise} dicts,
    newest first.
    """
    url = 'https://finnhub.io/api/v1/stock/earnings'
    params = {'symbol': ticker, 'token': token}
    try:
        time.sleep(RATE_LIMIT_SLEEP)
        r = requests.get(url, params=params, timeout=20)
        if r.status_code != 200:
            return []
        data = r.json()
        if not isinstance(data, list):
            return []
        # Filter to quarters with both actual and estimate
        history = []
        for q in data:
            actual   = q.get('actual')
            estimate = q.get('estimate')
            if actual is None or estimate is None:
                continue
            try:
                actual   = float(actual)
                estimate = float(estimate)
            except:
                continue
            history.append({
                'period':   q.get('period', ''),
                'actual':   actual,
                'estimate': estimate,
                'surprise': actual - estimate,
            })
        # Newest first (Finnhub returns newest first already)
        return history[:HISTORY_QUARTERS]
    except Exception as e:
        return []


def compute_sue(history, current_actual, current_estimate):
    """
    Compute Standardised Unexpected Earnings (SUE).

    SUE = (actual_EPS - consensus_EPS) / std_dev_of_historical_surprises

    Safety check: if std_dev < STD_DEV_FLOOR, use the floor value.
    This prevents zero-division when a company always beats by exactly
    the same amount (std_dev = 0) or when history is very short.

    Args:
        history:          list of historical surprise dicts (past quarters)
        current_actual:   EPS actual for the most recent quarter
        current_estimate: EPS consensus estimate for the most recent quarter

    Returns:
        (sue, std_dev, surprise) tuple
    """
    surprise = current_actual - current_estimate

    if len(history) < 2:
        # Not enough history — use floor std_dev
        std_dev = max(STD_DEV_FLOOR, REL_STD_FLOOR * abs(current_estimate))
    else:
        surprises = [q['surprise'] for q in history]
        n         = len(surprises)
        mean      = sum(surprises) / n
        variance  = sum((s - mean) ** 2 for s in surprises) / (n - 1)
        std_dev   = math.sqrt(variance)

        # Safety check: apply floor if std_dev is too small. With three prior
        # quarters a company that beat by 1-2 cents each time has a std_dev near
        # zero, and the next ordinary beat looks like a 10-sigma event (CRM 12,
        # NBIS 34). The relative floor (5% of the estimate, so a signal needs a beat of at least 7.5%) keeps the scale sensible.
        std_dev = max(std_dev, STD_DEV_FLOOR, REL_STD_FLOOR * abs(current_estimate))

    sue = surprise / std_dev
    return round(sue, 3), round(std_dev, 4), round(surprise, 4)


# ── STEP 3: BUILD SIGNALS ─────────────────────────────────────────────

def reject_reason(history_n, actual, estimate, date_source='calendar'):
    """Why a surprise should not become a signal, or None if it is usable.

    PEAD is documented for companies with real earnings, measured against a
    stable history, on the actual announcement date. Anything else is noise."""
    if date_source != 'calendar':
        return 'no confirmed announcement date'
    if history_n < MIN_HISTORY_QUARTERS:
        return f'only {history_n} prior quarters'
    if estimate is None or estimate <= 0 or actual is None or actual <= 0:
        return 'loss-making (EPS estimate or actual not positive)'
    if abs(actual - estimate) > MAX_REL_SURPRISE * abs(estimate):
        return f'surprise {abs(actual - estimate) / abs(estimate):.0%} of estimate: likely accounting-basis mismatch or one-off'
    return None



def process_ticker(ticker, announce_date, token, date_source='calendar'):
    """
    Fetch EPS history, compute SUE, return signal dict if SUE > threshold.
    Returns None if no signal.
    """
    history = fetch_eps_history(ticker, token)
    if not history:
        print(f'  {ticker}: no EPS history available')
        return None

    # Most recent quarter is history[0] — this is the announcement
    latest   = history[0]
    actual   = latest['actual']
    estimate = latest['estimate']

    # Use remaining history (quarters 1+) as the std_dev base
    historical_surprises = history[1:]

    why = reject_reason(len(historical_surprises), actual, estimate, date_source)
    if why:
        print(f'  {ticker}: skipped, {why}')
        return None

    sue, std_dev, surprise = compute_sue(historical_surprises, actual, estimate)

    direction = 'beat' if surprise > 0 else 'miss'
    print(f'  {ticker}: actual={actual:.3f} est={estimate:.3f} '
          f'surprise={surprise:+.3f} std={std_dev:.4f} SUE={sue:+.3f} → {direction}')

    if sue <= SUE_THRESHOLD:
        print(f'    SUE {sue:.3f} ≤ threshold {SUE_THRESHOLD} — no signal')
        return None

    # Compute expiry
    try:
        ann_dt     = datetime.strptime(announce_date[:10], '%Y-%m-%d').date()
        expires_at = (ann_dt + timedelta(days=PEAD_HOLD_DAYS)).strftime('%Y-%m-%d')
    except:
        expires_at = (date.today() + timedelta(days=PEAD_HOLD_DAYS)).strftime('%Y-%m-%d')

    print(f'    ✓ SIGNAL: SUE={sue:.3f} > {SUE_THRESHOLD} — expires {expires_at}')

    return {
        'ticker':          ticker,
        'announce_date':   announce_date[:10],
        'expires_at':      expires_at,
        'sue':             sue,
        'surprise':        surprise,
        'actual_eps':      actual,
        'estimate_eps':    estimate,
        'std_dev':         std_dev,
        'std_dev_floored': std_dev <= max(STD_DEV_FLOOR, REL_STD_FLOOR * abs(estimate)) + 1e-4,
        'date_source':     date_source,
        'history_quarters':len(historical_surprises),
        'buy_score_boost': SIGNAL_BUY_SCORE_BOOST,
        'as_of':           str(date.today()),
    }


# ── STEP 4: LOAD EXISTING + MERGE ────────────────────────────────────

def load_existing_signals():
    """
    Load existing pead_signals.json and remove expired signals.
    This preserves signals from prior weeks that haven't expired yet.
    """
    try:
        with open('pead_signals.json') as f:
            data = json.load(f)
        signals = data.get('signals', {})
        today   = str(date.today())
        active  = {t: s for t, s in signals.items()
                   if s.get('expires_at', '2000-01-01') >= today}
        # Re-check signals written before the quality rules existed.
        kept = {}
        for t, s in active.items():
            why = reject_reason(s.get('history_quarters', 0), s.get('actual_eps'), s.get('estimate_eps'),
                                s.get('date_source', 'period'))
            if why:
                print(f'  Dropped existing {t}: {why}')
                continue
            if 'date_source' not in s or s.get('std_dev', 0) < REL_STD_FLOOR * abs(s.get('estimate_eps') or 0):
                continue   # pre-fix SUE scale; will be recomputed on its next report
            kept[t] = s
        active = kept
        expired = len(signals) - len(active)
        if expired > 0:
            print(f'  Removed {expired} expired PEAD signals')
        return active
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f'  Could not load existing signals: {e}')
        return {}


# ── MAIN ──────────────────────────────────────────────────────────────

def main():
    start = datetime.now()
    print('=' * 60)
    print(f'PEAD Signal Fetcher — {start.strftime("%Y-%m-%d %H:%M UTC")}')
    print(f'SUE threshold: >{SUE_THRESHOLD} | Hold: {PEAD_HOLD_DAYS}d | '
          f'Std floor: {STD_DEV_FLOOR}')
    print('=' * 60)

    token = get_finnhub_token()

    # Step 1: Find who reported in last 7 days
    print('\n[1/3] Earnings Calendar — recent reporters in universe')
    reporters = fetch_recent_reporters(token)

    # Step 2: Compute SUE for each reporter
    print(f'\n[2/3] Computing SUE for {len(reporters)} tickers...')
    new_signals = {}
    for ticker, announce_date in reporters:
        result = process_ticker(ticker, announce_date, token)
        if result:
            new_signals[ticker] = result

    # Step 3: Merge with existing (unexpired) signals
    print('\n[3/3] Merging with existing unexpired signals...')
    existing = load_existing_signals()
    # New signals override existing for same ticker
    merged = {**existing, **new_signals}

    elapsed = round((datetime.now() - start).total_seconds(), 1)

    # Build output
    health = {
        'timestamp':         datetime.now().strftime('%Y-%m-%d %H:%M UTC'),
        'tickers_checked':   len(reporters),
        'new_signals':       len(new_signals),
        'total_signals':     len(merged),
        'expired_removed':   len(existing) - (len(merged) - len(new_signals)),
        'sue_threshold':     SUE_THRESHOLD,
        'hold_days':         PEAD_HOLD_DAYS,
        'elapsed_seconds':   elapsed,
    }

    output = {'health': health, 'signals': merged}

    def sanitize(obj):
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return None
        if isinstance(obj, dict):
            return {k: sanitize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [sanitize(v) for v in obj]
        return obj

    with open('pead_signals.json', 'w') as f:
        json.dump(sanitize(output), f, indent=2, default=str)

    print(f'\nDone in {elapsed}s')
    print(f'Reporters checked:  {len(reporters)}')
    print(f'New PEAD signals:   {len(new_signals)}')
    print(f'Total active:       {len(merged)}')
    if new_signals:
        for t, s in new_signals.items():
            print(f'  {t}: SUE={s["sue"]:+.3f} expires {s["expires_at"]}')
    print('pead_signals.json written')


if __name__ == '__main__':
    main()
