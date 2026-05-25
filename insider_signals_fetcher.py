"""
insider_signals_fetcher.py
Weekly fetcher for corporate insider buying (EDGAR Form 4 XML)
and politician trading (House/Senate Stock Watcher JSON).

Signal weights:
  Single insider buy (30d):       +10 pts
  Cluster buy (2+ insiders, 30d): +20 pts
  Politician buy (45d):           +8 pts
  Combined max boost:             +25 pts (cannot create signal alone)
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

HEADERS = {'User-Agent': 'portfolio-signals-bot contact@dchadha.dev'}
LOOKBACK_INSIDER    = 30
LOOKBACK_POLITICIAN = 45
MIN_TRANSACTION_USD = 50_000

def get_cik_map():
    try:
        r = requests.get('https://www.sec.gov/files/company_tickers.json',
                         headers=HEADERS, timeout=30)
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

def fetch_recent_form4_accessions(cik, cutoff_str, ticker):
    url = f'https://data.sec.gov/submissions/CIK{cik}.json'
    try:
        time.sleep(0.12)
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            return []
        data    = r.json()
        recent  = data.get('filings', {}).get('recent', {})
        forms   = recent.get('form', [])
        dates   = recent.get('filingDate', [])
        accnums = recent.get('accessionNumber', [])
        results = []
        for i, form in enumerate(forms):
            if form != '4': continue
            fd = dates[i] if i < len(dates) else ''
            if fd < cutoff_str: break
            acc = accnums[i] if i < len(accnums) else ''
            if acc:
                results.append((acc, fd))
        return results
    except Exception as e:
        return []

def parse_form4_xml(cik, accession, filing_date, ticker):
    acc_nodash = accession.replace('-', '')
    cik_int    = str(int(cik))
    xml_url    = f'https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{accession}.xml'
    buys = []
    try:
        time.sleep(0.12)
        r = requests.get(xml_url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.content)
        owner_rel = ''
        owner_el  = root.find('.//reportingOwner')
        if owner_el is not None:
            rel_el = owner_el.find('.//reportingOwnerRelationship')
            if rel_el is not None:
                parts = []
                for tag in ['isDirector','isOfficer','isTenPercentOwner']:
                    el = rel_el.find(tag)
                    if el is not None and el.text == '1':
                        parts.append(tag.replace('is',''))
                title_el = rel_el.find('officerTitle')
                if title_el is not None and title_el.text:
                    parts.append(title_el.text.strip())
                owner_rel = ', '.join(parts)

        for txn in root.findall('.//nonDerivativeTransaction'):
            code_el = txn.find('.//transactionCodes/transactionCode')
            if code_el is None or code_el.text != 'P':
                continue
            plan_el = txn.find('.//transactionCodes/rule10b5-1PlanFlag')
            if plan_el is not None and plan_el.text in ('Y','1','true'):
                continue
            shares_el = txn.find('.//transactionAmounts/transactionShares/value')
            price_el  = txn.find('.//transactionAmounts/transactionPricePerShare/value')
            shares = float(shares_el.text) if shares_el is not None and shares_el.text else 0
            price  = float(price_el.text)  if price_el  is not None and price_el.text  else 0
            value  = shares * price
            if value < MIN_TRANSACTION_USD:
                continue
            buys.append({
                'ticker':        ticker,
                'date':          filing_date,
                'value_usd':     round(value),
                'shares':        int(shares),
                'price':         round(price, 2),
                'insider_title': owner_rel,
                'is_cluster':    False,
            })
    except Exception:
        pass
    return buys

def fetch_edgar_insider_signals(cik_map):
    cutoff_str = (datetime.now() - timedelta(days=LOOKBACK_INSIDER)).date().strftime('%Y-%m-%d')
    all_buys   = []
    n          = len(cik_map)
    print(f'  Fetching Form 4 for {n} tickers (cutoff: {cutoff_str})...')
    for i, (ticker, cik) in enumerate(cik_map.items()):
        if i % 20 == 0:
            print(f'    {i}/{n}...')
        accessions = fetch_recent_form4_accessions(cik, cutoff_str, ticker)
        for acc, fd in accessions[:10]:
            all_buys.extend(parse_form4_xml(cik, acc, fd, ticker))
    print(f'  EDGAR: {len(all_buys)} qualifying transactions')
    results = {}
    by_ticker = {}
    for buy in all_buys:
        by_ticker.setdefault(buy['ticker'], []).append(buy)
    for ticker, buys in by_ticker.items():
        unique = len(set(b['insider_title'] for b in buys))
        is_cl  = unique >= 2
        for b in buys:
            b['is_cluster'] = is_cl
        results[ticker] = {
            'buys':       buys[:5],
            'score':      20 if is_cl else 10,
            'n_insiders': unique,
            'is_cluster': is_cl,
            'as_of':      str(date.today()),
        }
    return results

def fetch_politician_signals():
    cutoff     = (datetime.now() - timedelta(days=LOOKBACK_POLITICIAN)).date()
    cutoff_str = cutoff.strftime('%Y-%m-%d')
    all_trades = []
    ok         = True
    sources    = [
        ('House',  'https://house-stock-watcher-data.s3-us-west-2.amazonaws.com/data/all_transactions.json'),
        ('Senate', 'https://senate-stock-watcher-data.s3-us-west-2.amazonaws.com/aggregate/all_transactions.json'),
    ]
    for chamber, url in sources:
        try:
            time.sleep(0.5)
            r = requests.get(url, timeout=30, headers={'User-Agent': 'Mozilla/5.0'})
            if r.status_code != 200:
                print(f'  {chamber}: HTTP {r.status_code}')
                ok = False
                continue
            data = r.json()
            if not isinstance(data, list):
                data = data.get('data', data.get('transactions', []))
            count = 0
            for trade in data:
                td_str = trade.get('transaction_date') or trade.get('date', '')
                if not td_str or td_str == 'Not disclosed':
                    continue
                try:
                    td = datetime.strptime(td_str[:10], '%Y-%m-%d').date()
                except:
                    try:
                        td = datetime.strptime(td_str[:10], '%m/%d/%Y').date()
                    except:
                        continue
                if td < cutoff:
                    continue
                raw_ticker = (trade.get('ticker') or '').upper().strip()
                ticker     = re.sub(r'[^A-Z]', '', raw_ticker)
                if not ticker or ticker not in UNIVERSE_SET:
                    continue
                tx_type = (trade.get('type') or trade.get('transaction_type', '')).lower()
                if not any(w in tx_type for w in ['purchase', 'buy', 'bought']):
                    continue
                politician = (trade.get('representative') or
                              trade.get('senator') or
                              trade.get('name', 'Unknown'))
                all_trades.append({
                    'ticker':     ticker,
                    'date':       str(td),
                    'politician': politician,
                    'chamber':    chamber,
                    'type':       'buy',
                    'amount':     trade.get('amount', ''),
                })
                count += 1
            print(f'  {chamber}: {count} qualifying buys')
        except Exception as e:
            print(f'  {chamber} failed: {e}')
            ok = False
    results = {}
    by_ticker = {}
    for t in all_trades:
        by_ticker.setdefault(t['ticker'], []).append(t)
    for ticker, trades in by_ticker.items():
        results[ticker] = {
            'trades':        trades[:5],
            'score':         8,
            'n_politicians': len(set(t['politician'] for t in trades)),
            'as_of':         str(date.today()),
        }
    print(f'  Politician signals: {len(results)} tickers')
    return results, ok

def combine_signals(insider, politician):
    combined = {}
    for ticker in set(list(insider.keys()) + list(politician.keys())):
        ins = insider.get(ticker, {})
        pol = politician.get(ticker, {})
        combined[ticker] = {
            'buy_score_boost':   min(25, ins.get('score',0) + pol.get('score',0)),
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

def main():
    start = datetime.now()
    print('='*60)
    print(f'Insider & Politician Signal Fetcher — {start.strftime("%Y-%m-%d %H:%M UTC")}')
    print('='*60)

    insider_ok = politician_ok = True
    insider_signals = politician_signals = {}

    try:
        print('\n[1/2] EDGAR Form 4')
        cik_map = get_cik_map()
        if cik_map:
            insider_signals = fetch_edgar_insider_signals(cik_map)
    except Exception as e:
        print(f'  EDGAR failed: {e}')
        insider_ok = False

    try:
        print('\n[2/2] House/Senate Stock Watcher')
        politician_signals, politician_ok = fetch_politician_signals()
    except Exception as e:
        print(f'  Politician fetch failed: {e}')
        politician_ok = False

    combined = combine_signals(insider_signals, politician_signals)
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
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return None
        if isinstance(obj, dict):  return {k: sanitize(v) for k, v in obj.items()}
        if isinstance(obj, list):  return [sanitize(v) for v in obj]
        return obj

    with open('insider_signals.json', 'w') as f:
        json.dump(sanitize({'health': health, 'signals': combined}), f, indent=2, default=str)

    print(f'\nDone in {elapsed}s')
    print(f'Insider:    {health["n_insider_signals"]} tickers')
    print(f'Politician: {health["n_politician_signals"]} tickers')
    print(f'Retry:      {health["retry_required"]}')
    print('insider_signals.json written')

    if health['retry_required']:
        sys.exit(1)

if __name__ == '__main__':
    main()
