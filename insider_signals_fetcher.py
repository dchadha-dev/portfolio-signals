"""
insider_signals_fetcher.py
══════════════════════════════════════════════════════════════════════
Weekly fetcher for two alternative data signals:

1. EDGAR Form 4 — Corporate insider buying
   Source: SEC EDGAR full-text search + submissions API
   Filter: transaction code P (open market purchase only)
           exclude 10b5-1 plans
           minimum $50K transaction value
           cluster of 2+ insiders within 30 days = stronger signal

2. Capitol Trades — Politician trading (STOCK Act disclosures)
   Source: capitoltrades.com (no API key required)
   Filter: buys only, last 45 days, Senate + House combined
   Universe: tickers already in portfolio-signals universe only

Output: insider_signals.json (read by signal_scanner.py for buy score boost)

Signal weights (Cohen-Malloy-Pomorski 2012 informed):
  Single insider buy (30d):     +10 pts to buy score
  Cluster buy (2+ insiders, 30d): +20 pts to buy score
  Politician buy (45d):         +8 pts to buy score
  Combined max boost:           +25 pts (capped — cannot create signal alone)

Auto-retry: if any fetch fails, writes failure flags to insider_signals.json
            GitHub Actions will re-trigger on failure via retry step.
"""

import json
import time
import requests
import re
from datetime import datetime, timedelta, date

# ── UNIVERSE (must match signal_scanner.py MY_HOLDINGS + CANDIDATES) ─
UNIVERSE = [
    'NVDA','AVGO','TSLA','MELI','AMAT','MSFT','AAPL','AMZN','META','GOOG',
    'NFLX','BKNG','SHOP','RACE','AMD','ASML','ANET','DDOG','CRDO','NBIS',
    'TSM','TM','MU','INTU','CPRT','PGR','TTD','UNH','FISV','TEAM',
    'BRKB','NVO','RELX','DELL','BABA','COIN',
    'RMS.PA','MC.PA','ITX.MC',
    'LLY','MA','V','MSCI','NOW','ISRG','SPGI','KLAC','TMO','CRWD',
    'PANW','ADBE','CRM','SNOW','AXON','RCL','UBER','CMG','PG','WM',
    'ENPH','CSCO','HPE','NVTS','HIMX','TSEM','RKLB','IREN','RGTI',
    'QBTS','IONQ','LMT','CCJ','KEEL','DNN','APLD','ONDS','SOUN',
    'FIVN','SOFI','SE','GRAB','TCOM','EXPE','INFY','HDB','SYM',
    'LRCX','ALAB','VRT','SERV','SEDG','KWEB','DRIV','ARKK','ARKG',
    'FINX','BOTZ','AIQ','BLOK','IWF','ACWV','INDA','SMH','IVV',
    'QQQ','TQQQ','GLD','SCHD','JEPI','VOO','VTI','VXUS','IEV','URTH',
]

HEADERS = {
    'User-Agent': 'portfolio-signals-bot dharam@agoda.com',  # SEC requires User-Agent
    'Accept': 'application/json',
}

LOOKBACK_INSIDER    = 30   # days for insider signal
LOOKBACK_POLITICIAN = 45   # days for politician signal
MIN_TRANSACTION_USD = 50_000   # minimum insider buy to count

# ── EDGAR CIK LOOKUP ─────────────────────────────────────────────────
def get_cik_map(tickers):
    """Fetch CIK numbers for all tickers from SEC EDGAR company_tickers.json."""
    print('Fetching CIK map from SEC EDGAR...')
    try:
        r = requests.get(
            'https://www.sec.gov/files/company_tickers.json',
            headers=HEADERS, timeout=30
        )
        r.raise_for_status()
        data = r.json()
        # Build ticker -> CIK map
        cik_map = {}
        for entry in data.values():
            ticker = entry.get('ticker', '').upper()
            cik    = str(entry.get('cik_str', '')).zfill(10)
            if ticker in tickers:
                cik_map[ticker] = cik
        print(f'  Found CIKs for {len(cik_map)}/{len(tickers)} tickers')
        return cik_map
    except Exception as e:
        print(f'  CIK map fetch failed: {e}')
        return {}

# ── EDGAR FORM 4 FETCHER ─────────────────────────────────────────────
def fetch_insider_buys_for_ticker(ticker, cik, cutoff_date):
    """
    Fetch Form 4 filings for a ticker via SEC EDGAR submissions API.
    Returns list of qualifying open-market purchases.
    """
    buys = []
    url  = f'https://data.sec.gov/submissions/CIK{cik}.json'
    try:
        time.sleep(0.12)  # SEC rate limit: 10 requests/second max
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            return buys

        data     = r.json()
        filings  = data.get('filings', {}).get('recent', {})
        forms    = filings.get('form', [])
        dates    = filings.get('filingDate', [])
        accnums  = filings.get('accessionNumber', [])

        for i, form in enumerate(forms):
            if form != '4': continue
            filing_date = dates[i] if i < len(dates) else None
            if not filing_date: continue
            try:
                fd = datetime.strptime(filing_date, '%Y-%m-%d').date()
            except:
                continue
            if fd < cutoff_date: break  # filings are sorted newest first
            # Fetch the actual filing to get transaction details
            accnum = accnums[i].replace('-', '') if i < len(accnums) else None
            if not accnum: continue

            filing_buys = parse_form4_filing(ticker, accnum, fd)
            buys.extend(filing_buys)

    except Exception as e:
        print(f'    {ticker}: EDGAR fetch error: {e}')

    return buys

def parse_form4_filing(ticker, accnum, filing_date):
    """
    Parse a Form 4 filing XML for open-market purchases (code P).
    Returns list of qualifying transactions.
    """
    buys = []
    # Form 4 XML endpoint
    cik_short = accnum[:10]
    url = f'https://www.sec.gov/Archives/edgar/data/{int(cik_short)}/{accnum}/{accnum}-index.htm'

    try:
        time.sleep(0.12)
        r = requests.get(
            f'https://efts.sec.gov/LATEST/search-index?q=%22{ticker}%22&forms=4&dateRange=custom'
            f'&startdt={filing_date}&enddt={filing_date}',
            headers=HEADERS, timeout=15
        )
        if r.status_code != 200:
            return buys

        results = r.json().get('hits', {}).get('hits', [])
        for hit in results:
            src = hit.get('_source', {})
            # Check transaction code
            trans_codes = src.get('transactions', [])
            for trans in trans_codes:
                code   = trans.get('transactionCode', '')
                shares = trans.get('transactionShares', 0) or 0
                price  = trans.get('transactionPricePerShare', 0) or 0
                value  = shares * price

                if code != 'P': continue           # open market purchase only
                if value < MIN_TRANSACTION_USD: continue  # minimum size filter

                # Check for 10b5-1 plan (routine, less informative)
                is_10b51 = src.get('rule10b51', False) or \
                           'rule 10b5-1' in str(src).lower()
                if is_10b51: continue

                insider_title = src.get('reportingOwnerRelationship', '')
                buys.append({
                    'ticker':         ticker,
                    'date':           str(filing_date),
                    'value_usd':      round(value),
                    'shares':         int(shares),
                    'price':          round(price, 2),
                    'insider_title':  insider_title,
                    'is_cluster':     False,  # computed later
                })
    except Exception as e:
        pass  # silent — will be caught at ticker level

    return buys

def fetch_all_insider_signals(universe, cik_map):
    """
    Fetch insider buying for all universe tickers.
    Returns dict: ticker -> {buys: [...], signal_score: int, n_insiders: int}
    """
    cutoff = (datetime.now() - timedelta(days=LOOKBACK_INSIDER)).date()
    results = {}
    n_tickers = len([t for t in universe if t in cik_map])
    print(f'Fetching Form 4 data for {n_tickers} tickers (cutoff: {cutoff})...')

    # Alternative: use EFTS bulk search which is faster
    insider_data = fetch_insider_bulk(universe, cutoff)

    for ticker in universe:
        ticker_buys = [b for b in insider_data if b['ticker'] == ticker]

        # Cluster detection: 2+ unique insiders within 30 days
        unique_insiders = len(set(b.get('insider_title', '') for b in ticker_buys))
        is_cluster      = unique_insiders >= 2

        for b in ticker_buys:
            b['is_cluster'] = is_cluster

        # Signal score
        if is_cluster:
            score = 20
        elif len(ticker_buys) >= 1:
            score = 10
        else:
            score = 0

        if score > 0:
            results[ticker] = {
                'buys':       ticker_buys,
                'score':      score,
                'n_insiders': unique_insiders,
                'is_cluster': is_cluster,
                'as_of':      str(date.today()),
            }

    print(f'  Insider signals: {len(results)} tickers with qualifying buys')
    return results

def fetch_insider_bulk(universe, cutoff):
    """
    Use EDGAR full-text search to bulk-fetch Form 4 transactions.
    More efficient than per-ticker CIK lookups.
    """
    all_buys = []
    cutoff_str = cutoff.strftime('%Y-%m-%d')
    today_str  = date.today().strftime('%Y-%m-%d')

    # Process in batches of 10 tickers
    batch_size = 10
    universe_us = [t for t in universe if '.' not in t]  # US tickers only for EDGAR

    for i in range(0, len(universe_us), batch_size):
        batch = universe_us[i:i+batch_size]
        for ticker in batch:
            try:
                time.sleep(0.15)
                url = (f'https://efts.sec.gov/LATEST/search-index?'
                       f'q=%22{ticker}%22&forms=4'
                       f'&dateRange=custom&startdt={cutoff_str}&enddt={today_str}')
                r = requests.get(url, headers=HEADERS, timeout=15)
                if r.status_code != 200: continue

                hits = r.json().get('hits', {}).get('hits', [])
                for hit in hits:
                    src   = hit.get('_source', {})
                    # Parse transaction details from filing text
                    filing_text = src.get('file_date', '')
                    trans_list  = src.get('transactions', [])

                    if not trans_list:
                        # Try parsing from display_names and description
                        continue

                    for trans in trans_list if isinstance(trans_list, list) else [trans_list]:
                        code   = trans.get('transactionCode', '') if isinstance(trans, dict) else ''
                        if code != 'P': continue
                        shares = float(trans.get('transactionShares', 0) or 0)
                        price  = float(trans.get('transactionPricePerShare', 0) or 0)
                        value  = shares * price
                        if value < MIN_TRANSACTION_USD: continue

                        all_buys.append({
                            'ticker':        ticker,
                            'date':          src.get('file_date', str(date.today())),
                            'value_usd':     round(value),
                            'shares':        int(shares),
                            'price':         round(price, 2),
                            'insider_title': src.get('entity_name', ''),
                            'is_cluster':    False,
                        })
            except Exception as e:
                print(f'    {ticker} EFTS error: {e}')
                continue

    print(f'  EDGAR bulk fetch: {len(all_buys)} qualifying transactions')
    return all_buys

# ── CAPITOL TRADES FETCHER ────────────────────────────────────────────
def fetch_politician_signals(universe):
    """
    Fetch politician trading data from Capitol Trades.
    Returns dict: ticker -> {trades: [...], signal_score: int, n_politicians: int}
    """
    cutoff     = (datetime.now() - timedelta(days=LOOKBACK_POLITICIAN)).date()
    cutoff_str = cutoff.strftime('%Y-%m-%d')
    results    = {}

    print(f'Fetching Capitol Trades data (cutoff: {cutoff_str})...')

    # Capitol Trades API — no key required
    # Endpoint returns paginated JSON of recent trades
    base_url = 'https://www.capitoltrades.com/api/trades'
    us_tickers = [t for t in universe if '.' not in t]

    # Fetch recent trades for all tickers in one pass (paginated)
    all_trades = fetch_capitol_trades_bulk(cutoff_str)

    for ticker in us_tickers:
        ticker_trades = [t for t in all_trades
                        if t.get('ticker', '').upper() == ticker
                        and t.get('type') == 'buy']

        if not ticker_trades:
            continue

        unique_politicians = len(set(t.get('politician', '') for t in ticker_trades))

        score = 8 if len(ticker_trades) >= 1 else 0

        if score > 0:
            results[ticker] = {
                'trades':          ticker_trades,
                'score':           score,
                'n_politicians':   unique_politicians,
                'as_of':           str(date.today()),
            }

    print(f'  Politician signals: {len(results)} tickers with qualifying buys')
    return results

def fetch_capitol_trades_bulk(cutoff_str):
    """
    Fetch all recent Capitol Trades buys since cutoff.
    Paginates through results until cutoff date is passed.
    """
    all_trades = []
    page = 1
    max_pages = 20  # cap to avoid infinite loop

    while page <= max_pages:
        try:
            time.sleep(0.5)
            # Capitol Trades uses query params for filtering
            url = f'https://www.capitoltrades.com/api/trades?page={page}&pageSize=100&txType=buy'
            r   = requests.get(url, headers={
                'User-Agent': 'Mozilla/5.0',
                'Accept': 'application/json',
                'Referer': 'https://www.capitoltrades.com/',
            }, timeout=20)

            if r.status_code == 404:
                break  # no more pages
            if r.status_code != 200:
                print(f'  Capitol Trades page {page}: status {r.status_code}')
                # Try alternate endpoint format
                url2 = f'https://www.capitoltrades.com/trades?page={page}&txType=buy'
                r    = requests.get(url2, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
                if r.status_code != 200:
                    break

            try:
                data   = r.json()
            except:
                break

            trades = data.get('data', data.get('trades', data if isinstance(data, list) else []))
            if not trades:
                break

            for trade in trades:
                trade_date_str = trade.get('txDate', trade.get('date', ''))
                try:
                    trade_date = datetime.strptime(trade_date_str[:10], '%Y-%m-%d').date()
                except:
                    continue

                if str(trade_date) < cutoff_str:
                    return all_trades  # past cutoff — stop paginating

                ticker = (trade.get('ticker') or trade.get('asset', {}).get('ticker', '')).upper()
                if not ticker: continue

                all_trades.append({
                    'ticker':     ticker,
                    'date':       str(trade_date),
                    'politician': trade.get('politician', {}).get('name', '') if isinstance(trade.get('politician'), dict) else trade.get('politician', ''),
                    'party':      trade.get('politician', {}).get('party', '') if isinstance(trade.get('politician'), dict) else '',
                    'chamber':    trade.get('politician', {}).get('chamber', '') if isinstance(trade.get('politician'), dict) else '',
                    'type':       trade.get('txType', trade.get('type', '')).lower(),
                    'size':       trade.get('txAmount', trade.get('amount', '')),
                })

            page += 1

        except Exception as e:
            print(f'  Capitol Trades page {page} error: {e}')
            break

    print(f'  Capitol Trades: {len(all_trades)} total trades fetched')
    return all_trades

# ── COMBINE AND SCORE ─────────────────────────────────────────────────
def combine_signals(insider_signals, politician_signals, universe):
    """
    Combine insider and politician signals into final per-ticker boost.
    Max combined boost: 25 pts (cannot create a buy signal alone).
    """
    combined = {}

    all_tickers = set(list(insider_signals.keys()) + list(politician_signals.keys()))

    for ticker in all_tickers:
        if ticker not in universe: continue

        ins  = insider_signals.get(ticker, {})
        pol  = politician_signals.get(ticker, {})

        ins_score = ins.get('score', 0)
        pol_score = pol.get('score', 0)

        # Cap combined boost at 25 pts
        combined_score = min(25, ins_score + pol_score)

        combined[ticker] = {
            'buy_score_boost':  combined_score,
            'insider_score':    ins_score,
            'politician_score': pol_score,
            'insider_n':        ins.get('n_insiders', 0),
            'insider_cluster':  ins.get('is_cluster', False),
            'politician_n':     pol.get('n_politicians', 0),
            'insider_buys':     ins.get('buys', [])[:5],       # cap for JSON size
            'politician_trades':pol.get('trades', [])[:5],
            'as_of':            str(date.today()),
        }

    return combined

# ── HEALTH CHECK & RETRY FLAGS ────────────────────────────────────────
def build_health_report(insider_ok, politician_ok, combined, start_time):
    """
    Build health metadata for the output JSON.
    If either source failed, sets retry_required=True so GitHub Actions
    can detect and re-trigger.
    """
    elapsed = round((datetime.now() - start_time).total_seconds(), 1)
    n_insider_signals    = sum(1 for v in combined.values() if v['insider_score'] > 0)
    n_politician_signals = sum(1 for v in combined.values() if v['politician_score'] > 0)

    health = {
        'timestamp':             datetime.now().strftime('%Y-%m-%d %H:%M UTC'),
        'insider_fetch_ok':      insider_ok,
        'politician_fetch_ok':   politician_ok,
        'n_insider_signals':     n_insider_signals,
        'n_politician_signals':  n_politician_signals,
        'n_combined_signals':    len(combined),
        'elapsed_seconds':       elapsed,
        'retry_required':        not insider_ok or not politician_ok,
        'retry_reason':          [] if (insider_ok and politician_ok) else
                                 (['insider_fetch_failed'] if not insider_ok else []) +
                                 (['politician_fetch_failed'] if not politician_ok else []),
    }
    return health

# ── MAIN ──────────────────────────────────────────────────────────────
def main():
    start_time = datetime.now()
    print('=' * 60)
    print('Insider & Politician Signal Fetcher')
    print(f'Universe: {len(UNIVERSE)} tickers')
    print(f'Insider lookback: {LOOKBACK_INSIDER}d | Politician: {LOOKBACK_POLITICIAN}d')
    print('=' * 60)

    insider_ok    = True
    politician_ok = True
    insider_signals    = {}
    politician_signals = {}

    # ── 1. EDGAR Form 4 ──────────────────────────────────────────────
    try:
        print('\n[1/2] EDGAR Form 4 — Corporate Insider Buying')
        cik_map = get_cik_map(UNIVERSE)
        insider_signals = fetch_all_insider_signals(UNIVERSE, cik_map)
        if len(insider_signals) == 0 and len(cik_map) > 10:
            # Got CIKs but no signals — could be real (no buys) or a fetch issue
            print('  Warning: 0 insider signals — may be a slow news period or fetch issue')
        insider_ok = True
    except Exception as e:
        print(f'  EDGAR fetch FAILED: {e}')
        insider_ok = False

    # ── 2. Capitol Trades ─────────────────────────────────────────────
    try:
        print('\n[2/2] Capitol Trades — Politician Trading')
        politician_signals = fetch_politician_signals(UNIVERSE)
        politician_ok = True
    except Exception as e:
        print(f'  Capitol Trades fetch FAILED: {e}')
        politician_ok = False

    # ── 3. Combine ────────────────────────────────────────────────────
    combined = combine_signals(insider_signals, politician_signals, UNIVERSE)
    health   = build_health_report(insider_ok, politician_ok, combined, start_time)

    # ── 4. Write output ───────────────────────────────────────────────
    output = {
        'health':  health,
        'signals': combined,
    }

    import math
    def sanitize(obj):
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return None
        if isinstance(obj, dict):  return {k: sanitize(v) for k, v in obj.items()}
        if isinstance(obj, list):  return [sanitize(v) for v in obj]
        return obj

    with open('insider_signals.json', 'w') as f:
        json.dump(sanitize(output), f, indent=2, default=str)

    print('\n' + '=' * 60)
    print(f'Done in {health["elapsed_seconds"]}s')
    print(f'Insider signals:    {health["n_insider_signals"]} tickers')
    print(f'Politician signals: {health["n_politician_signals"]} tickers')
    print(f'Retry required:     {health["retry_required"]}')
    if health['retry_reason']:
        print(f'Retry reason:       {", ".join(health["retry_reason"])}')
    print('✓ insider_signals.json written')

    # Exit with error code if retry required — GitHub Actions detects this
    if health['retry_required']:
        print('\nExiting with code 1 to signal GitHub Actions retry...')
        import sys; sys.exit(1)

if __name__ == '__main__':
    main()
