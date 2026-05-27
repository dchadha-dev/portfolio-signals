"""
insider_signals_fetcher.py
Weekly fetcher:
1. Corporate insider buying — SEC EDGAR Form 4 (code P, open market, ≥$15K)
2. Politician buying       — Finnhub congressional trading (fallback only)

Capitol Trades api.capitoltrades.com does not resolve from GitHub Actions.
Politician signals disabled pending paid source (backlog: Quiver Quant $25/mo).
"""

import json, math, sys, os, time, re
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

LOOKBACK_INSIDER    = 60
LOOKBACK_POLITICIAN = 45
MIN_TRANSACTION_USD = 15_000

POLITICIAN_KEYWORDS = {
    'senator', 'representative', 'congress', 'senate',
    'member of congress', 'u.s. senator', 'u.s. representative',
}


# ── CIK MAP ───────────────────────────────────────────────────────────
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


# ── FORM 4 ACCESSIONS ────────────────────────────────────────────────
def fetch_form4_accessions(cik, cutoff_str):
    try:
        time.sleep(0.12)
        r = requests.get(f'https://data.sec.gov/submissions/CIK{cik}.json',
                         headers=SEC_HEADERS, timeout=20)
        if r.status_code != 200:
            return [], []
        data    = r.json()
        recent  = data.get('filings', {}).get('recent', {})
        forms   = recent.get('form', [])
        dates   = recent.get('filingDate', [])
        accnums = recent.get('accessionNumber', [])
        prim    = recent.get('primaryDocument', [])
        results = []
        for i, form in enumerate(forms):
            if form != '4':
                continue
            fd = dates[i] if i < len(dates) else ''
            if fd < cutoff_str:
                break
            acc  = accnums[i] if i < len(accnums) else ''
            pdoc = prim[i]    if i < len(prim)    else ''
            if acc:
                results.append((acc, fd, pdoc))
        return results
    except Exception:
        return []


# ── XML URL ───────────────────────────────────────────────────────────
def get_xml_url(cik, accession, primary_doc=''):
    """
    Get primary Form 4 XML content.
    Strategy 1: primaryDocument if .xml
    Strategy 2: Full submission .txt file (always exists, contains XML inside <XML> tags)
    Strategy 3: Common .xml filename patterns
    """
    acc_nodash = accession.replace('-', '')
    cik_int    = str(int(cik))
    base       = f'https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}'

    candidates = []
    if primary_doc and primary_doc.lower().endswith('.xml'):
        candidates.append(primary_doc)
    # Full submission text always exists and contains embedded XML
    candidates.append(f'{accession}.txt')
    candidates += ['4.xml', 'ownership.xml', 'primary_doc.xml']

    for fname in candidates:
        url = f'{base}/{fname}'
        try:
            time.sleep(0.12)
            r = requests.get(url, headers=SEC_HEADERS, timeout=20)
            if r.status_code != 200:
                continue
            content = r.content
            if b'<ownershipDocument' in content or b'<XML>' in content:
                return url, content
        except Exception:
            continue

    return None, None


# ── PARSE FORM 4 XML ─────────────────────────────────────────────────
def parse_form4_xml(xml_url, ticker, filing_date, prefetched_content=None):
    """Parse Form 4 XML for open-market purchases."""
    if not xml_url and prefetched_content is None:
        return [], {}
    buys       = []
    code_tally = {}
    try:
        if prefetched_content is not None:
            content = prefetched_content
        else:
            time.sleep(0.12)
            r = requests.get(xml_url, headers=SEC_HEADERS, timeout=20)
            if r.status_code != 200:
                return [], {}
            content = r.content

        # Extract Form 4 XML from SGML submission wrapper
        # .txt files are SGML with multiple <DOCUMENT> sections.
        # The Form 4 XML is inside <XML>...</XML> tags.
        # Must extract this before attempting ET.fromstring.
        if b'<XML>' in content:
            # Find the XML block containing ownershipDocument
            xml_start = content.find(b'<XML>')
            xml_end   = content.find(b'</XML>', xml_start)
            if xml_start >= 0 and xml_end > xml_start:
                content = content[xml_start + 5 : xml_end].strip()
        elif b'<ownershipDocument' not in content:
            return [], {}

        # One-time diagnostic
        if not getattr(parse_form4_xml, '_diagnosed', False):
            parse_form4_xml._diagnosed = True
            preview = content[:200].decode('utf-8', errors='replace').replace('\n', ' ')
            print(f'  XML DIAG: {preview}')

        # Strip namespaces
        text = content.decode('utf-8', errors='replace')
        text = re.sub(r' xmlns[^=]*="[^"]*"', '', text)
        text = re.sub(r'<(/?)[A-Za-z0-9_-]+:', lambda m: '<' + m.group(1), text)
        root = ET.fromstring(text.encode('utf-8'))

        # Owner info
        owner_name = owner_title = ''
        el = root.find('.//rptOwnerName')
        if el is not None and el.text:
            owner_name = el.text.strip()
        rel = root.find('.//reportingOwnerRelationship')
        if rel is not None:
            parts = []
            for tag in ['isDirector', 'isOfficer', 'isTenPercentOwner']:
                e2 = rel.find(tag)
                if e2 is not None and e2.text in ('1', 'true', 'True'):
                    parts.append(tag.replace('is', ''))
            t2 = rel.find('officerTitle')
            if t2 is not None and t2.text:
                parts.append(t2.text.strip())
            owner_title = ', '.join(parts)

        n_pass = 0
        for txn in root.findall('.//nonDerivativeTransaction'):
            # X0609 schema: <transactionCoding><transactionCode>P</transactionCode>
            # Older schema: <transactionCodes><transactionCode>P</transactionCode>
            # Try all known paths
            code = None
            for path in [
                './/transactionCoding/transactionCode',   # X0609 schema
                'transactionCoding/transactionCode',
                './/transactionCodes/transactionCode',    # older schema
                'transactionCodes/transactionCode',
                './/transactionCode',                     # fallback
            ]:
                e2 = txn.find(path)
                if e2 is not None and e2.text:
                    code = e2.text.strip()
                    break
            code_tally[code] = code_tally.get(code, 0) + 1
            if code != 'P':
                continue
            # Skip 10b5-1 plans — X0609 uses equitiesSwapInvolved, older uses rule10b5-1PlanFlag
            plan = None
            for path in [
                './/transactionCoding/equitiesSwapInvolved',
                'transactionCoding/equitiesSwapInvolved',
                './/transactionCodes/rule10b5-1PlanFlag',
                'transactionCodes/rule10b5-1PlanFlag',
            ]:
                e2 = txn.find(path)
                if e2 is not None:
                    plan = e2.text
                    break
            if plan in ('Y', '1', 'true', '1'):
                continue
            # Shares
            shares = 0.0
            for path in [
                './/transactionAmounts/transactionShares/value',
                'transactionAmounts/transactionShares/value',
                './/transactionShares/value',
            ]:
                e2 = txn.find(path)
                if e2 is not None and e2.text:
                    try: shares = float(e2.text); break
                    except: pass
            # Price
            price = 0.0
            for path in [
                './/transactionAmounts/transactionPricePerShare/value',
                'transactionAmounts/transactionPricePerShare/value',
                './/transactionPricePerShare/value',
            ]:
                e2 = txn.find(path)
                if e2 is not None and e2.text:
                    try: price = float(e2.text); break
                    except: pass
            if shares * price < MIN_TRANSACTION_USD:
                continue
            n_pass += 1
            buys.append({
                'ticker':    ticker,
                'date':      filing_date,
                'value_usd': round(shares * price),
                'shares':    int(shares),
                'price':     round(price, 2),
                'name':      owner_name,
                'title':     owner_title,
            })
        if n_pass > 0:
            print(f'    + {ticker}: {n_pass} buy(s) — {owner_name[:30]}')
    except Exception as e:
        print(f'    parse error {ticker}: {type(e).__name__}: {e}')
    return buys, code_tally


# ── FETCH ALL EDGAR SIGNALS ───────────────────────────────────────────
def fetch_edgar_signals(cik_map):
    cutoff = (datetime.now() - timedelta(days=LOOKBACK_INSIDER)).date().strftime('%Y-%m-%d')
    all_insider  = {}
    global_codes = {}
    n = len(cik_map)
    xml_found = xml_tried = 0
    print(f'  Fetching Form 4 for {n} tickers (cutoff {cutoff})...')

    for i, (ticker, cik) in enumerate(cik_map.items()):
        if i % 15 == 0:
            print(f'    {i}/{n} (XML found: {xml_found}/{xml_tried})...')
        for acc, fd, pdoc in fetch_form4_accessions(cik, cutoff)[:8]:
            xml_tried += 1
            xml_url, content = get_xml_url(cik, acc, pdoc)
            if xml_url:
                xml_found += 1
            ins_b, codes = parse_form4_xml(xml_url, ticker, fd, content)
            for c, count in codes.items():
                global_codes[c] = global_codes.get(c, 0) + count
            all_insider.setdefault(ticker, []).extend(ins_b)

    print(f'  XML resolution: {xml_found}/{xml_tried} successful')
    top_codes = dict(sorted(global_codes.items(), key=lambda x: -x[1])[:8])
    print(f'  Transaction codes seen: {top_codes}')
    total_buys = sum(len(v) for v in all_insider.values())
    print(f'  Insider buys (code P, >=${MIN_TRANSACTION_USD:,}): {total_buys}')
    return all_insider


# ── BUILD RESULTS ─────────────────────────────────────────────────────
def build_insider_results(all_insider):
    results = {}
    for ticker, buys in all_insider.items():
        if not buys:
            continue
        unique = len(set(b['name'] for b in buys))
        is_cl  = unique >= 2
        results[ticker] = {
            'buys':       [{**b, 'is_cluster': is_cl} for b in buys[:5]],
            'score':      20 if is_cl else 10,
            'n_insiders': unique,
            'is_cluster': is_cl,
            'as_of':      str(date.today()),
        }
    return results


# ── POLITICIAN SCORE ─────────────────────────────────────────────────
def _politician_score(trades_for_ticker):
    today = date.today()
    valid = []
    for t in trades_for_ticker:
        try:
            disc = datetime.strptime(t['disclosure_date'][:10], '%Y-%m-%d').date()
            txn  = datetime.strptime(t['transaction_date'][:10], '%Y-%m-%d').date()
        except:
            continue
        lag = (disc - txn).days
        if lag > 45:
            continue
        t['reporting_lag_days'] = lag
        valid.append(t)
    if not valid:
        return 0, valid
    dates = sorted([datetime.strptime(t['transaction_date'][:10], '%Y-%m-%d').date()
                    for t in valid])
    cluster = len(set(t.get('politician', '') for t in valid)) >= 2
    if not cluster:
        for i in range(len(dates) - 1):
            if (dates[i+1] - dates[i]).days <= 7:
                cluster = True
                break
    score = 10 if cluster else 5
    return score, valid


# ── CAPITOL TRADES (disabled — DNS fails from GitHub Actions) ─────────
def fetch_capitol_trades(universe_set):
    print('  Capitol Trades: api.capitoltrades.com not accessible from GitHub Actions')
    print('  Backlog: add Quiver Quantitative ($25/mo) for congressional trading data')
    return {}


# ── FINNHUB CONGRESSIONAL (fallback) ─────────────────────────────────
def fetch_finnhub_congressional(finnhub_token, universe_set):
    if not finnhub_token:
        print('  FINNHUB_TOKEN not set')
        return {}
    cutoff_str = (datetime.now() - timedelta(days=LOOKBACK_POLITICIAN)).date().strftime('%Y-%m-%d')
    today_str  = date.today().strftime('%Y-%m-%d')
    results    = {}
    us_tickers = [t for t in universe_set if '.' not in t]
    print(f'  Finnhub congressional: {len(us_tickers)} tickers...')
    for ticker in us_tickers:
        try:
            time.sleep(0.15)
            r = requests.get(
                'https://finnhub.io/api/v1/stock/congressional-trading',
                params={'symbol': ticker, 'from': cutoff_str,
                        'to': today_str, 'token': finnhub_token},
                timeout=15,
            )
            if r.status_code == 429:
                time.sleep(10); continue
            if r.status_code != 200: continue
            trades = r.json().get('data', [])
            for t in trades:
                tx = (t.get('transactionType') or '').lower()
                if 'purchase' not in tx and 'buy' not in tx: continue
                results.setdefault(ticker, []).append({
                    'ticker':           ticker,
                    'transaction_date': t.get('transactionDate', ''),
                    'disclosure_date':  t.get('filingDate', t.get('transactionDate', '')),
                    'politician':       t.get('name', 'Unknown'),
                    'chamber':          t.get('chamber', ''),
                    'party':            t.get('party', ''),
                    'tx_type':          'buy',
                    'amount_minimum':   t.get('amountMin', 0) or 0,
                })
        except Exception:
            continue
    n = sum(len(v) for v in results.values())
    print(f'  Finnhub: {n} buys across {len(results)} tickers')
    return results


# ── BUILD POLITICIAN RESULTS ──────────────────────────────────────────
def build_politician_results(raw_by_ticker):
    results = {}
    for ticker, trades in raw_by_ticker.items():
        score, valid = _politician_score(trades)
        if not valid: continue
        best = sorted(valid, key=lambda t: t.get('reporting_lag_days', 99))
        results[ticker] = {
            'trades':             best[:5],
            'score':              score,
            'n_politicians':      len(set(t.get('politician', '') for t in valid)),
            'is_cluster':         len(set(t.get('politician', '') for t in valid)) >= 2,
            'reporting_lag_days': best[0].get('reporting_lag_days'),
            'as_of':              str(date.today()),
        }
    return results


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
            'politician_cluster':pol.get('is_cluster', False),
            'reporting_lag_days':pol.get('reporting_lag_days'),
            'insider_buys':      ins.get('buys', []),
            'politician_trades': pol.get('trades', []),
            'as_of':             str(date.today()),
        }
    return combined


# ── MAIN ──────────────────────────────────────────────────────────────
def main():
    start = datetime.now()
    print('=' * 60)
    print(f'Insider & Politician Fetcher — {start.strftime("%Y-%m-%d %H:%M UTC")}')
    print('EDGAR Form 4 (corporate) + Capitol Trades (politician)')
    print('=' * 60)

    edgar_ok = pol_ok = True
    insider_signals = politician_signals = {}

    print('\n[1/2] EDGAR Form 4 — Corporate Insider Buying')
    try:
        cik_map = get_cik_map()
        if cik_map:
            all_insider     = fetch_edgar_signals(cik_map)
            insider_signals = build_insider_results(all_insider)
        else:
            edgar_ok = False
    except Exception as e:
        print(f'  EDGAR failed: {type(e).__name__}: {e}')
        edgar_ok = False

    print('\n[2/2] Politician Signals')
    try:
        print('  Trying Capitol Trades API...')
        raw = fetch_capitol_trades(UNIVERSE_SET)
        if not raw:
            finnhub_token = os.environ.get('FINNHUB_TOKEN', '')
            raw = fetch_finnhub_congressional(finnhub_token, UNIVERSE_SET)
        politician_signals = build_politician_results(raw)
        pol_ok = True
    except Exception as e:
        print(f'  Politician fetch failed: {type(e).__name__}: {e}')
        pol_ok = False

    combined = combine(insider_signals, politician_signals)
    elapsed  = round((datetime.now() - start).total_seconds(), 1)

    health = {
        'timestamp':            datetime.now().strftime('%Y-%m-%d %H:%M UTC'),
        'insider_fetch_ok':     edgar_ok,
        'politician_fetch_ok':  pol_ok,
        'n_insider_signals':    sum(1 for v in combined.values() if v['insider_score'] > 0),
        'n_politician_signals': sum(1 for v in combined.values() if v['politician_score'] > 0),
        'n_combined_signals':   len(combined),
        'elapsed_seconds':      elapsed,
        'retry_required':       not edgar_ok,
        'retry_reason':         ['edgar_failed'] if not edgar_ok else [],
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
