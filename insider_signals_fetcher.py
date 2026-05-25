"""
insider_signals_fetcher.py
Weekly fetcher for corporate insider buying (EDGAR Form 4)
and politician trading (House Stock Watcher API + Senate Stock Watcher GitHub).

Fixes from v1:
- EDGAR: now fetches filing index to get actual XML filename
- Politician: House uses housestockwatcher.com/api/transactions endpoint
              Senate uses raw GitHub JSON (timothycarambat/senate-stock-watcher-data)
"""

import json, time, re, math, sys
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, date

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
SEC_HEADERS  = {'User-Agent': 'portfolio-signals-bot contact@dchadha.dev'}
LOOKBACK_INSIDER    = 30
LOOKBACK_POLITICIAN = 45
MIN_TRANSACTION_USD = 50_000

# ── EDGAR: CIK MAP ────────────────────────────────────────────────────
def get_cik_map():
    try:
        r = requests.get('https://www.sec.gov/files/company_tickers.json',
                         headers=SEC_HEADERS, timeout=30)
        r.raise_for_status()
        cik_map = {}
        for entry in r.json().values():
            t = entry.get('ticker', '').upper()
            if t in UNIVERSE_SET:
                cik_map[t] = str(entry.get('cik_str', '')).zfill(10)
        print(f'  CIK map: {len(cik_map)} tickers matched')
        return cik_map
    except Exception as e:
        print(f'  CIK map failed: {e}')
        return {}

# ── EDGAR: FORM 4 ACCESSIONS ──────────────────────────────────────────
def fetch_form4_accessions(cik, cutoff_str):
    """Get recent Form 4 accession numbers from submissions API."""
    try:
        time.sleep(0.12)
        r = requests.get(f'https://data.sec.gov/submissions/CIK{cik}.json',
                         headers=SEC_HEADERS, timeout=20)
        if r.status_code != 200: return []
        recent  = r.json().get('filings', {}).get('recent', {})
        forms   = recent.get('form', [])
        dates   = recent.get('filingDate', [])
        accnums = recent.get('accessionNumber', [])
        results = []
        for i, form in enumerate(forms):
            if form != '4': continue
            fd = dates[i] if i < len(dates) else ''
            if fd < cutoff_str: break
            acc = accnums[i] if i < len(accnums) else ''
            if acc: results.append((acc, fd))
        return results
    except Exception:
        return []

# ── EDGAR: GET XML FILENAME FROM FILING INDEX ─────────────────────────
def get_form4_xml_url(cik, accession):
    """
    Fetch the filing index to find the actual XML filename.
    Form 4 files are NOT named accession.xml — must look up in index.
    """
    acc_nodash = accession.replace('-', '')
    cik_int    = str(int(cik))
    index_url  = f'https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{accession}-index.htm'
    try:
        time.sleep(0.12)
        r = requests.get(index_url, headers=SEC_HEADERS, timeout=15)
        if r.status_code != 200:
            return None
        # Find the primary XML document — usually ends in .xml and is form4 type
        for line in r.text.splitlines():
            if '.xml' in line.lower() and ('form4' in line.lower() or '4/' in line.lower()
                                            or 'xbrl' not in line.lower()):
                match = re.search(r'href="([^"]+\.xml)"', line, re.IGNORECASE)
                if match:
                    fname = match.group(1).split('/')[-1]
                    return f'https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{fname}'
        # Fallback: try common patterns
        for suffix in ['.xml', '-primary.xml']:
            url = f'https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{accession}{suffix}'
            return url  # try first fallback
    except Exception:
        pass
    return None

# ── EDGAR: PARSE FORM 4 XML ───────────────────────────────────────────
def parse_form4_xml(xml_url, ticker, filing_date):
    """Parse Form 4 XML for open-market purchases (code P, non-10b5-1, ≥$50K)."""
    if not xml_url: return []
    buys = []
    try:
        time.sleep(0.12)
        r = requests.get(xml_url, headers=SEC_HEADERS, timeout=20)
        if r.status_code != 200: return []
        root = ET.fromstring(r.content)

        # Get insider title
        owner_rel = ''
        rel_el = root.find('.//reportingOwnerRelationship')
        if rel_el is not None:
            parts = []
            for tag in ['isDirector', 'isOfficer', 'isTenPercentOwner']:
                el = rel_el.find(tag)
                if el is not None and el.text == '1':
                    parts.append(tag.replace('is', ''))
            title_el = rel_el.find('officerTitle')
            if title_el is not None and title_el.text:
                parts.append(title_el.text.strip())
            owner_rel = ', '.join(parts)

        for txn in root.findall('.//nonDerivativeTransaction'):
            # Transaction code must be P (open market purchase)
            code_el = txn.find('.//transactionCodes/transactionCode')
            if code_el is None or code_el.text != 'P': continue

            # Exclude 10b5-1 plans
            plan_el = txn.find('.//transactionCodes/rule10b5-1PlanFlag')
            if plan_el is not None and plan_el.text in ('Y', '1', 'true'): continue

            shares_el = txn.find('.//transactionAmounts/transactionShares/value')
            price_el  = txn.find('.//transactionAmounts/transactionPricePerShare/value')
            shares = float(shares_el.text) if shares_el is not None and shares_el.text else 0
            price  = float(price_el.text)  if price_el  is not None and price_el.text  else 0
            if shares * price < MIN_TRANSACTION_USD: continue

            buys.append({
                'ticker':        ticker,
                'date':          filing_date,
                'value_usd':     round(shares * price),
                'shares':        int(shares),
                'price':         round(price, 2),
                'insider_title': owner_rel,
            })
    except Exception:
        pass
    return buys

# ── EDGAR: MAIN FETCH ─────────────────────────────────────────────────
def fetch_edgar_signals(cik_map):
    cutoff_str = (datetime.now() - timedelta(days=LOOKBACK_INSIDER)).date().strftime('%Y-%m-%d')
    all_buys   = []
    n          = len(cik_map)
    print(f'  Fetching Form 4 for {n} tickers (cutoff: {cutoff_str})...')

    for i, (ticker, cik) in enumerate(cik_map.items()):
        if i % 20 == 0: print(f'    {i}/{n}...')
        for acc, fd in fetch_form4_accessions(cik, cutoff_str)[:5]:
            xml_url = get_form4_xml_url(cik, acc)
            all_buys.extend(parse_form4_xml(xml_url, ticker, fd))

    print(f'  EDGAR: {len(all_buys)} qualifying transactions')
    by_ticker = {}
    for b in all_buys:
        by_ticker.setdefault(b['ticker'], []).append(b)
    results = {}
    for ticker, buys in by_ticker.items():
        unique  = len(set(b['insider_title'] for b in buys))
        is_cl   = unique >= 2
        results[ticker] = {
            'buys':       [{**b, 'is_cluster': is_cl} for b in buys[:5]],
            'score':      20 if is_cl else 10,
            'n_insiders': unique,
            'is_cluster': is_cl,
            'as_of':      str(date.today()),
        }
    return results

# ── POLITICIAN: HOUSE ─────────────────────────────────────────────────
def fetch_house_trades(cutoff):
    """Fetch House trades from housestockwatcher.com API."""
    trades = []
    # Try the website API endpoint
    urls = [
        'https://housestockwatcher.com/api/transactions',
        'https://housestockwatcher.com/api/transactions_by_date',
    ]
    for url in urls:
        try:
            time.sleep(1)
            r = requests.get(url, timeout=30, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/json',
            })
            print(f'  House ({url.split("/")[-1]}): HTTP {r.status_code}')
            if r.status_code != 200: continue
            data = r.json()
            if not isinstance(data, list): data = data.get('data', [])
            for trade in data:
                td_str = trade.get('transaction_date', '')
                if not td_str or td_str == 'Not Disclosed': continue
                try:
                    td = datetime.strptime(td_str[:10], '%Y-%m-%d').date()
                except: continue
                if td < cutoff: continue
                raw = (trade.get('ticker', '') or '').upper().strip()
                ticker = re.sub(r'[^A-Z]', '', raw)
                if not ticker or ticker not in UNIVERSE_SET: continue
                tx = (trade.get('type', '') or '').lower()
                if 'purchase' not in tx and 'buy' not in tx: continue
                trades.append({
                    'ticker':     ticker,
                    'date':       str(td),
                    'politician': trade.get('representative', 'Unknown'),
                    'chamber':    'House',
                    'amount':     trade.get('amount', ''),
                })
            print(f'  House: {len(trades)} qualifying buys found')
            return trades
        except Exception as e:
            print(f'  House error: {e}')
    return trades

# ── POLITICIAN: SENATE ────────────────────────────────────────────────
def fetch_senate_trades(cutoff):
    """Fetch Senate trades from GitHub JSON (timothycarambat/senate-stock-watcher-data)."""
    trades = []
    urls = [
        'https://raw.githubusercontent.com/timothycarambat/senate-stock-watcher-data/master/data/all_transactions.json',
        'https://raw.githubusercontent.com/timothycarambat/senate-stock-watcher-data/main/data/all_transactions.json',
    ]
    for url in urls:
        try:
            time.sleep(0.5)
            r = requests.get(url, timeout=30, headers={'User-Agent': 'Mozilla/5.0'})
            print(f'  Senate GitHub: HTTP {r.status_code}')
            if r.status_code != 200: continue
            data = r.json()
            if not isinstance(data, list): data = data.get('data', [])
            for trade in data:
                td_str = trade.get('transaction_date', '')
                if not td_str: continue
                try:
                    td = datetime.strptime(td_str[:10], '%Y-%m-%d').date()
                except: continue
                if td < cutoff: continue
                raw    = (trade.get('ticker', '') or '').upper().strip()
                ticker = re.sub(r'[^A-Z]', '', raw)
                if not ticker or ticker not in UNIVERSE_SET: continue
                tx = (trade.get('type', '') or '').lower()
                if 'purchase' not in tx and 'buy' not in tx: continue
                trades.append({
                    'ticker':     ticker,
                    'date':       str(td),
                    'politician': trade.get('senator', 'Unknown'),
                    'chamber':    'Senate',
                    'amount':     trade.get('amount', ''),
                })
            print(f'  Senate: {len(trades)} qualifying buys found')
            return trades
        except Exception as e:
            print(f'  Senate error: {e}')
    return trades

# ── POLITICIAN: MAIN FETCH ────────────────────────────────────────────
def fetch_politician_signals():
    cutoff  = (datetime.now() - timedelta(days=LOOKBACK_POLITICIAN)).date()
    trades  = []
    ok      = True

    house_trades  = fetch_house_trades(cutoff)
    senate_trades = fetch_senate_trades(cutoff)
    trades        = house_trades + senate_trades

    if not trades:
        ok = False

    by_ticker = {}
    for t in trades:
        by_ticker.setdefault(t['ticker'], []).append(t)
    results = {}
    for ticker, tlist in by_ticker.items():
        results[ticker] = {
            'trades':        tlist[:5],
            'score':         8,
            'n_politicians': len(set(t['politician'] for t in tlist)),
            'as_of':         str(date.today()),
        }
    print(f'  Politician signals: {len(results)} tickers total')
    return results, ok

# ── COMBINE ───────────────────────────────────────────────────────────
def combine(insider, politician):
    combined = {}
    for ticker in set(list(insider) + list(politician)):
        ins = insider.get(ticker, {})
        pol = politician.get(ticker, {})
        combined[ticker] = {
            'buy_score_boost':   min(25, ins.get('score', 0) + pol.get('score', 0)),
            'insider_score':     ins.get('score', 0),
            'politician_score':  pol.get('score', 0),
            'insider_n':         ins.get('n_insiders', 0),
            'insider_cluster':   ins.get('is_cluster', False),
            'politician_n':      pol.get('n_politicians', 0),
            'insider_buys':      ins.get('buys', []),
            'politician_trades': pol.get('trades', []),
            'as_of':             str(date.today()),
        }
    return combined

# ── MAIN ──────────────────────────────────────────────────────────────
def main():
    start = datetime.now()
    print('='*60)
    print(f'Insider & Politician Fetcher — {start.strftime("%Y-%m-%d %H:%M UTC")}')
    print('='*60)

    insider_ok = politician_ok = True
    insider_signals = politician_signals = {}

    try:
        print('\n[1/2] EDGAR Form 4')
        cik_map = get_cik_map()
        if cik_map:
            insider_signals = fetch_edgar_signals(cik_map)
    except Exception as e:
        print(f'  EDGAR failed: {e}')
        insider_ok = False

    try:
        print('\n[2/2] Politician Trades')
        politician_signals, politician_ok = fetch_politician_signals()
    except Exception as e:
        print(f'  Politician failed: {e}')
        politician_ok = False

    combined = combine(insider_signals, politician_signals)
    elapsed  = round((datetime.now() - start).total_seconds(), 1)

    health = {
        'timestamp':            datetime.now().strftime('%Y-%m-%d %H:%M UTC'),
        'insider_fetch_ok':     insider_ok,
        'politician_fetch_ok':  politician_ok,
        'n_insider_signals':    sum(1 for v in combined.values() if v['insider_score'] > 0),
        'n_politician_signals': sum(1 for v in combined.values() if v['politician_score'] > 0),
        'n_combined_signals':   len(combined),
        'elapsed_seconds':      elapsed,
        'retry_required':       not insider_ok or not politician_ok,
        'retry_reason':         (['insider_failed'] if not insider_ok else []) +
                                (['politician_failed'] if not politician_ok else []),
    }

    def sanitize(obj):
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)): return None
        if isinstance(obj, dict):  return {k: sanitize(v) for k, v in obj.items()}
        if isinstance(obj, list):  return [sanitize(v) for v in obj]
        return obj

    with open('insider_signals.json', 'w') as f:
        json.dump(sanitize({'health': health, 'signals': combined}), f, indent=2, default=str)

    print(f'\nDone in {elapsed}s | Insider: {health["n_insider_signals"]} | Politician: {health["n_politician_signals"]}')
    print('insider_signals.json written')
    if health['retry_required']:
        sys.exit(1)

if __name__ == '__main__':
    main()
