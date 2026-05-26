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
CALENDAR_LOOKBACK_DAYS = 7      # how far back to look for recent earnings
HISTORY_QUARTERS       = 8      # quarters of EPS history for SUE std_dev
SUE_THRESHOLD          = 1.5    # absolute SUE trigger
STD_DEV_FLOOR          = 0.02   # minimum std_dev to prevent zero-division
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
    Use Finnhub earnings calendar to find universe tickers
    that reported in the last CALENDAR_LOOKBACK_DAYS days.
    Returns list of (ticker, announce_date) tuples.
    """
    end_date   = date.today()
    start_date = end_date - timedelta(days=CALENDAR_LOOKBACK_DAYS)
    url = 'https://finnhub.io/api/v1/calendar/earnings'
    params = {
        'from':  start_date.strftime('%Y-%m-%d'),
        'to':    end_date.strftime('%Y-%m-%d'),
        'token': token,
    }
    print(f'  Fetching earnings calendar {start_date} → {end_date}...')
    try:
        time.sleep(RATE_LIMIT_SLEEP)
        r = requests.get(url, params=params, timeout=20)
        if r.status_code != 200:
            print(f'  Calendar fetch failed: HTTP {r.status_code}')
            return []
        data     = r.json()
        earnings = data.get('earningsCalendar', [])
        reporters = []
        for item in earnings:
            ticker = (item.get('symbol') or '').upper().strip()
            if ticker not in UNIVERSE_SET:
                continue
            announce_date = item.get('date', '')
            if not announce_date:
                continue
            reporters.append((ticker, announce_date))
        print(f'  Found {len(reporters)} universe tickers with recent earnings: '
              f'{[t for t, _ in reporters]}')
        return reporters
    except Exception as e:
        print(f'  Calendar fetch error: {e}')
        return []


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
        std_dev = STD_DEV_FLOOR
    else:
        surprises = [q['surprise'] for q in history]
        n         = len(surprises)
        mean      = sum(surprises) / n
        variance  = sum((s - mean) ** 2 for s in surprises) / (n - 1)
        std_dev   = math.sqrt(variance)

        # Safety check: apply floor if std_dev is too small
        if std_dev < STD_DEV_FLOOR:
            std_dev = STD_DEV_FLOOR

    sue = surprise / std_dev
    return round(sue, 3), round(std_dev, 4), round(surprise, 4)


# ── STEP 3: BUILD SIGNALS ─────────────────────────────────────────────

def process_ticker(ticker, announce_date, token):
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
        'std_dev_floored': std_dev == STD_DEV_FLOOR,
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
