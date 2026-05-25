"""
insider_signals_fetcher.py
══════════════════════════════════════════════════════════════════════
Weekly fetcher using SEC EDGAR as the single source for both:

1. Corporate insider buying — Form 4 (executives/directors)
2. Politician buying — Form 4 (Members of Congress file same form)

Both file Form 4 with SEC. EDGAR EFTS full-text search lets us find
filings by ticker. We parse the XML to extract transactions.

Key fix: use the filing index to find the actual XML filename,
not a guessed pattern.

Signal weights:
  Single insider buy (30d):       +10 pts
  Cluster buy (2+ insiders, 30d): +20 pts
  Politician buy (45d):           +8 pts
  Combined max:                   +25 pts
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
LOOKBACK_INSIDER    = 60   # extended from 30 — open-market purchases are rare
LOOKBACK_POLITICIAN = 45
MIN_TRANSACTION_USD = 15_000  # lowered from 50K — catches more signals while filtering trivial transactions

# Known Congress member keywords in owner titles
POLITICIAN_KEYWORDS = {
    'senator', 'representative', 'congress', 'senate', 'house of rep',
    'member of congress', 'u.s. senator', 'u.s. representative',
}

def get_cik_map():
    """Map tickers to CIK numbers."""
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

def fetch_form4_accessions(cik, cutoff_str):
    """Get recent Form 4 accession numbers."""
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

def get_xml_url_from_index(cik, accession):
    """
    Fetch the filing index JSON (not HTML) to get the primary XML document URL.
    EDGAR provides a structured index at accession-index.json.
    """
    acc_nodash = accession.replace('-', '')
    cik_int    = str(int(cik))

    # Try the JSON index first — most reliable
    idx_url = f'https://data.sec.gov/submissions/CIK{cik}.json'  # already fetched above

    # Use the direct filing index JSON
    json_idx = f'https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{accession}-index.json'
    try:
        time.sleep(0.12)
        r = requests.get(json_idx, headers=SEC_HEADERS, timeout=15)
        if r.status_code == 200:
            data = r.json()
            for item in data.get('directory', {}).get('item', []):
                name = item.get('name', '')
                # Form 4 primary document is typically .xml and not the full submission txt
                if name.endswith('.xml') and 'xbrl' not in name.lower():
                    return f'https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{name}'
    except Exception:
        pass

    # Fallback: try EFTS to get the filing documents list
    try:
        time.sleep(0.12)
        efts_url = f'https://efts.sec.gov/LATEST/search-index?q=%22{accession}%22&forms=4'
        r = requests.get(efts_url, headers=SEC_HEADERS, timeout=15)
        if r.status_code == 200:
            hits = r.json().get('hits', {}).get('hits', [])
            for hit in hits:
                src = hit.get('_source', {})
                period = src.get('period_of_report', '')
                # Get the filing document URLs from _id (format: accession:filename)
                doc_id = hit.get('_id', '')
                if '.xml' in doc_id:
                    parts = doc_id.split(':')
                    if len(parts) == 2:
                        fname = parts[1]
                        return f'https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{fname}'
    except Exception:
        pass

    # Last resort: try common Form 4 XML filename patterns
    for suffix in ['4.xml', 'primary_doc.xml', 'form4.xml']:
        url = f'https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{suffix}'
        try:
            time.sleep(0.08)
            r = requests.head(url, headers=SEC_HEADERS, timeout=8)
            if r.status_code == 200:
                return url
        except Exception:
            pass

    return None

def parse_form4_xml(xml_url, ticker, filing_date):
    """Parse Form 4 XML - handles namespaced and non-namespaced formats."""
    if not xml_url: return [], []
    buys = []
    pol_buys = []
    try:
        time.sleep(0.12)
        r = requests.get(xml_url, headers=SEC_HEADERS, timeout=20)
        if r.status_code != 200: return [], []

        content = r.content

        # Extract XML from full submission text wrapper if needed
        if b'<ownershipDocument' not in content and b'<XML>' in content:
            start = content.find(b'<ownershipDocument')
            end   = content.find(b'</ownershipDocument>') + len(b'</ownershipDocument>')
            if start >= 0 and end > start:
                content = content[start:end]

        # Strip XML namespaces — handles both old and new SEC Form 4 schemas
        text = content.decode('utf-8', errors='replace')
        text = re.sub(r' xmlns[^=]*="[^"]*"', '', text)
        text = re.sub(r'<(/?)[A-Za-z0-9_-]+:', lambda m: '<' + m.group(1), text)
        root = ET.fromstring(text.encode('utf-8'))

        # Debug: count transactions found
        txn_count = len(root.findall('.//nonDerivativeTransaction'))
        if txn_count > 0:
            print(f'    Found {txn_count} transactions in {xml_url.split("/")[-1]}')

        # Get reporter info
        owner_name  = ''
        owner_title = ''
        owner_el    = root.find('.//reportingOwner')
        if owner_el is not None:
            name_el = owner_el.find('.//rptOwnerName')
            if name_el is not None and name_el.text:
                owner_name = name_el.text.strip()
            rel_el = owner_el.find('.//reportingOwnerRelationship')
            if rel_el is not None:
                parts = []
                for tag in ['isDirector', 'isOfficer', 'isTenPercentOwner']:
                    el = rel_el.find(tag)
                    if el is not None and el.text == '1':
                        parts.append(tag.replace('is', ''))
                title_el = rel_el.find('officerTitle')
                if title_el is not None and title_el.text:
                    parts.append(title_el.text.strip())
                owner_title = ', '.join(parts)

        # Check if this is a politician filer
        name_lower  = owner_name.lower()
        title_lower = owner_title.lower()
        is_politician = any(kw in name_lower or kw in title_lower
                           for kw in POLITICIAN_KEYWORDS)

        # Parse transactions — with rejection counters for debugging
        n_total = n_not_p = n_10b51 = n_size = n_pass = 0
        code_counts = {}
        for txn in root.findall('.//nonDerivativeTransaction'):
            n_total += 1
            code = None
            for path in ['.//transactionCodes/transactionCode',
                         'transactionCodes/transactionCode',
                         './/transactionCode']:
                el = txn.find(path)
                if el is not None and el.text:
                    code = el.text.strip()
                    break
            code_counts[code] = code_counts.get(code, 0) + 1

            if code != 'P':
                n_not_p += 1
                continue

            # 10b5-1 plan check
            plan = None
            for path in ['.//transactionCodes/rule10b5-1PlanFlag',
                         'transactionCodes/rule10b5-1PlanFlag',
                         './/rule10b5-1PlanFlag']:
                el = txn.find(path)
                if el is not None:
                    plan = el.text
                    break
            if plan in ('Y', '1', 'true'):
                n_10b51 += 1
                continue

            shares_el = txn.find('.//transactionAmounts/transactionShares/value')
            price_el  = txn.find('.//transactionAmounts/transactionPricePerShare/value')
            shares = float(shares_el.text) if shares_el is not None and shares_el.text else 0
            price  = float(price_el.text)  if price_el  is not None and price_el.text  else 0
            if shares * price < MIN_TRANSACTION_USD:
                n_size += 1
                continue

            n_pass += 1
            entry = {
                'ticker':     ticker,
                'date':       filing_date,
                'value_usd':  round(shares * price),
                'shares':     int(shares),
                'price':      round(price, 2),
                'name':       owner_name,
                'title':      owner_title,
            }
            if is_politician:
                pol_buys.append(entry)
            else:
                buys.append(entry)

        if n_total > 0 and n_pass == 0:
            print(f'    FILTERED: {n_total} txns — not_P:{n_not_p} 10b51:{n_10b51} size:{n_size} codes:{code_counts}')
    except Exception:
        pass
    return buys, pol_buys

def fetch_all_signals(cik_map):
    """Fetch Form 4 for all tickers, separate into insider and politician."""
    ins_cutoff = (datetime.now() - timedelta(days=LOOKBACK_INSIDER)).date().strftime('%Y-%m-%d')
    pol_cutoff = (datetime.now() - timedelta(days=LOOKBACK_POLITICIAN)).date().strftime('%Y-%m-%d')

    all_insider = {}   # ticker -> list of buys
    all_pol     = {}   # ticker -> list of buys
    n = len(cik_map)
    print(f'  Fetching Form 4 for {n} tickers...')
    xml_found = 0; xml_tried = 0

    for i, (ticker, cik) in enumerate(cik_map.items()):
        if i % 15 == 0: print(f'    {i}/{n} (XML found: {xml_found}/{xml_tried})...')

        # Use longer lookback for politician check
        accessions = fetch_form4_accessions(cik, pol_cutoff)
        for acc, fd in accessions[:8]:
            xml_tried += 1
            xml_url = get_xml_url_from_index(cik, acc)
            if xml_url:
                xml_found += 1
            insider_buys, pol_buys = parse_form4_xml(xml_url, ticker, fd)

            # Apply appropriate cutoff
            if fd >= ins_cutoff:
                all_insider.setdefault(ticker, []).extend(insider_buys)
            all_pol.setdefault(ticker, []).extend(pol_buys)

    print(f'  XML resolution: {xml_found}/{xml_tried} successful')
    print(f'  Insider buys: {sum(len(v) for v in all_insider.values())} transactions')
    print(f'  Politician buys: {sum(len(v) for v in all_pol.values())} transactions')
    return all_insider, all_pol

def build_insider_results(all_insider):
    results = {}
    for ticker, buys in all_insider.items():
        if not buys: continue
        unique  = len(set(b['name'] for b in buys))
        is_cl   = unique >= 2
        results[ticker] = {
            'buys':       [{**b, 'is_cluster': is_cl} for b in buys[:5]],
            'score':      20 if is_cl else 10,
            'n_insiders': unique,
            'is_cluster': is_cl,
            'as_of':      str(date.today()),
        }
    return results

def build_politician_results(all_pol):
    results = {}
    for ticker, buys in all_pol.items():
        if not buys: continue
        results[ticker] = {
            'trades':        buys[:5],
            'score':         8,
            'n_politicians': len(set(b['name'] for b in buys)),
            'as_of':         str(date.today()),
        }
    return results

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

def main():
    start = datetime.now()
    print('='*60)
    print(f'Insider & Politician Fetcher — {start.strftime("%Y-%m-%d %H:%M UTC")}')
    print('Single source: SEC EDGAR Form 4 (corporate + politician)')
    print('='*60)

    ok = True
    insider_signals = politician_signals = {}

    try:
        cik_map = get_cik_map()
        if cik_map:
            # ── Diagnostic: sample one XML to verify parser ──
            _diag_ticker = list(cik_map.keys())[0]
            _diag_cik    = cik_map[_diag_ticker]
            _diag_cutoff = (datetime.now() - timedelta(days=30)).date().strftime("%Y-%m-%d")
            _diag_accs   = fetch_form4_accessions(_diag_cik, _diag_cutoff)
            if _diag_accs:
                _acc, _fd = _diag_accs[0]
                _url = get_xml_url_from_index(_diag_cik, _acc)
                print("DIAG: sample ticker=" + _diag_ticker + " acc=" + _acc[:20])
                if _url:
                    _r = requests.get(_url, headers=SEC_HEADERS, timeout=15)
                    if _r.status_code == 200:
                        _t = _r.content.decode("utf-8", errors="replace")
                        print("DIAG raw txn count:", _t.count("nonDerivativeTransaction>"))
                        print("DIAG first 300 chars:", _t[:300].replace("\n"," "))
            
            all_insider, all_pol = fetch_all_signals(cik_map)
            insider_signals    = build_insider_results(all_insider)
            politician_signals = build_politician_results(all_pol)
        else:
            ok = False
    except Exception as e:
        print(f'  Fetch failed: {e}')
        ok = False

    combined = combine(insider_signals, politician_signals)
    elapsed  = round((datetime.now() - start).total_seconds(), 1)

    health = {
        'timestamp':            datetime.now().strftime('%Y-%m-%d %H:%M UTC'),
        'insider_fetch_ok':     ok,
        'politician_fetch_ok':  ok,
        'n_insider_signals':    sum(1 for v in combined.values() if v['insider_score'] > 0),
        'n_politician_signals': sum(1 for v in combined.values() if v['politician_score'] > 0),
        'n_combined_signals':   len(combined),
        'elapsed_seconds':      elapsed,
        'retry_required':       not ok,
        'retry_reason':         ['edgar_failed'] if not ok else [],
    }

    def sanitize(obj):
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)): return None
        if isinstance(obj, dict):  return {k: sanitize(v) for k, v in obj.items()}
        if isinstance(obj, list):  return [sanitize(v) for v in obj]
        return obj

    with open('insider_signals.json', 'w') as f:
        json.dump(sanitize({'health': health, 'signals': combined}), f, indent=2, default=str)

    print(f'\nDone in {elapsed}s')
    print(f'Insider signals:    {health["n_insider_signals"]} tickers')
    print(f'Politician signals: {health["n_politician_signals"]} tickers')
    print(f'Retry required:     {health["retry_required"]}')
    print('insider_signals.json written')

    if health['retry_required']:
        sys.exit(1)

if __name__ == '__main__':
    main()
