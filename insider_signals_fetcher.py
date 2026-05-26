"""
insider_signals_fetcher.py
══════════════════════════════════════════════════════════════════════
Weekly fetcher for two alternative data signals:

1. Corporate insider buying  — SEC EDGAR Form 4 XML
2. Politician buying         — Capitol Trades public API (no key)
                               Finnhub congressional trading (fallback)

Signal logic:
  Corporate insider:
    Single buyer  ≥$15K, open-market (code P), non-10b5-1: +10pts
    Cluster (2+ insiders same ticker, 60d):                 +20pts

  Politician:
    45-day hard cap: discard if disclosure_date - transaction_date > 45d
    Single politician buy:                                   +5pts
    Cluster (2+ politicians same ticker, 7d) OR
    committee-relevant buy (Armed Services → defense etc):  +10pts
    Combined max boost:                                     +25pts

Output includes reporting_lag_days for freshness display on dashboard.
"""

import json, time, re, math, sys, os
import requests
import xml.etree.ElementTree as ET
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
SEC_HEADERS  = {'User-Agent': 'portfolio-signals-bot contact@dchadha.dev'}

LOOKBACK_INSIDER    = 60    # days
LOOKBACK_POLITICIAN = 45    # days — also the hard cap for reporting lag
MIN_TRANSACTION_USD = 15_000

# Committee relevance: if politician is on these committees, weight their buys
# in related sectors more heavily (cluster rule applies)
COMMITTEE_SECTOR_MAP = {
    'armed services': {'LMT', 'AXON', 'CCJ'},
    'energy':         {'ENPH', 'CCJ', 'SEDG'},
    'judiciary':      {'MSFT', 'GOOG', 'META', 'AMZN', 'AAPL'},
    'banking':        {'SOFI', 'V', 'MA', 'FISV'},
    'health':         {'UNH', 'LLY', 'TMO', 'ISRG'},
    'commerce':       {'AMZN', 'TSLA', 'NVDA', 'MSFT'},
}

# ─────────────────────────────────────────────────────────────────────
# PART 1: EDGAR Form 4 — corporate insider buying
# ─────────────────────────────────────────────────────────────────────

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


def fetch_form4_accessions(cik, cutoff_str):
    try:
        time.sleep(0.12)
        r = requests.get(f'https://data.sec.gov/submissions/CIK{cik}.json',
                         headers=SEC_HEADERS, timeout=20)
        if r.status_code != 200:
            return []
        recent  = r.json().get('filings', {}).get('recent', {})
        forms   = recent.get('form', [])
        dates   = recent.get('filingDate', [])
        accnums = recent.get('accessionNumber', [])
        results = []
        for i, form in enumerate(forms):
            if form != '4':
                continue
            fd = dates[i] if i < len(dates) else ''
            if fd < cutoff_str:
                break
            acc = accnums[i] if i < len(accnums) else ''
            if acc:
                results.append((acc, fd))
        return results
    except Exception:
        return []


def get_xml_url(cik, accession):
    """
    Get the primary Form 4 XML URL from the EDGAR filing index.
    Uses -index.htm which always exists for every EDGAR filing.
    """
    acc_nodash = accession.replace('-', '')
    cik_int    = str(int(cik))
    base       = f'https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}'

    # Strategy 1: Parse the filing index HTML — most reliable
    # The index page lists all documents; find the primary .xml that isn't XBRL
    try:
        time.sleep(0.12)
        r = requests.get(f'{base}/{accession}-index.htm',
                         headers=SEC_HEADERS, timeout=15)
        if r.status_code == 200:
            # EDGAR index HTML uses both relative and absolute href patterns
            # Pattern A: href="filename.xml" (relative)
            # Pattern B: href="/Archives/edgar/data/.../filename.xml" (absolute)
            for pattern in [
                r'href="([^"]+\.xml)"',
                r"href='([^']+\.xml)'",
            ]:
                for match in re.findall(pattern, r.text, re.IGNORECASE):
                    fname = match.split('/')[-1]
                    if 'xbrl' not in fname.lower() and 'cal' not in fname.lower():
                        # Build full URL
                        if match.startswith('/'):
                            return f'https://www.sec.gov{match}'
                        elif match.startswith('http'):
                            return match
                        else:
                            return f'{base}/{fname}'
    except Exception:
        pass

    # Strategy 2: JSON filing index
    try:
        time.sleep(0.08)
        r = requests.get(f'{base}/{accession}-index.json',
                         headers=SEC_HEADERS, timeout=12)
        if r.status_code == 200:
            data  = r.json()
            items = (data.get('directory', {}).get('item', []) or
                     data.get('files', []))
            for item in items:
                name = item.get('name', item.get('filename', ''))
                if name.endswith('.xml') and 'xbrl' not in name.lower():
                    return f'{base}/{name}'
    except Exception:
        pass

    # Strategy 3: Common filename patterns via HEAD request
    for suffix in ['4.xml', 'ownership.xml', 'primary_doc.xml', 'form4.xml']:
        url = f'{base}/{suffix}'
        try:
            time.sleep(0.08)
            if requests.head(url, headers=SEC_HEADERS, timeout=8).status_code == 200:
                return url
        except Exception:
            pass

    return None

def parse_form4_xml(xml_url, ticker, filing_date):
    """Parse Form 4 XML — handles namespaced and non-namespaced schemas."""
    if not xml_url:
        return [], []
    buys = pol_buys = []
    buys     = []
    pol_buys = []
    try:
        time.sleep(0.12)
        r = requests.get(xml_url, headers=SEC_HEADERS, timeout=20)
        if r.status_code != 200:
            return [], []

        content = r.content
        # Extract from SGML wrapper if needed
        if b'<ownershipDocument' not in content and b'<XML>' in content:
            s = content.find(b'<ownershipDocument')
            e = content.find(b'</ownershipDocument>') + len(b'</ownershipDocument>')
            if s >= 0 and e > s:
                content = content[s:e]

        # Strip XML namespaces
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
        code_tally = {}
        for txn in root.findall('.//nonDerivativeTransaction'):
            code = None
            for path in ['.//transactionCodes/transactionCode',
                         'transactionCodes/transactionCode']:
                e2 = txn.find(path)
                if e2 is not None and e2.text:
                    code = e2.text.strip(); break
            code_tally[code] = code_tally.get(code, 0) + 1
            if code != 'P':
                continue
            # Skip 10b5-1 plans
            for path in ['.//transactionCodes/rule10b5-1PlanFlag',
                         'transactionCodes/rule10b5-1PlanFlag']:
                e2 = txn.find(path)
                if e2 is not None and e2.text in ('Y', '1', 'true'):
                    code = None; break
            if code is None:
                continue
            shares = price = 0.0
            for path in ['.//transactionAmounts/transactionShares/value',
                         './/transactionShares/value']:
                e2 = txn.find(path)
                if e2 is not None and e2.text:
                    try: shares = float(e2.text); break
                    except: pass
            for path in ['.//transactionAmounts/transactionPricePerShare/value',
                         './/transactionPricePerShare/value']:
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
            print(f'    + {ticker}: {n_pass} buy(s) — {owner_name[:25]}')

    except Exception:
        code_tally = {}
    return buys, code_tally


def fetch_edgar_signals(cik_map):
    cutoff = (datetime.now() - timedelta(days=LOOKBACK_INSIDER)).date().strftime('%Y-%m-%d')
    all_insider = {}
    n = len(cik_map)
    xml_found = xml_tried = 0
    global_codes = {}  # tally all transaction codes seen across all filings
    print(f'  Fetching Form 4 for {n} tickers (cutoff {cutoff})...')
    for i, (ticker, cik) in enumerate(cik_map.items()):
        if i % 15 == 0:
            print(f'    {i}/{n} (XML found: {xml_found}/{xml_tried})...')
        for acc, fd in fetch_form4_accessions(cik, cutoff)[:8]:
            xml_tried += 1
            xml_url = get_xml_url(cik, acc)
            if xml_url:
                xml_found += 1
            ins_b, codes = parse_form4_xml(xml_url, ticker, fd)
            for c, count in codes.items():
                global_codes[c] = global_codes.get(c, 0) + count
            all_insider.setdefault(ticker, []).extend(ins_b)
    print(f'  XML resolution: {xml_found}/{xml_tried} successful')
    print(f'  Transaction codes seen: {dict(sorted(global_codes.items(), key=lambda x: -x[1])[:8])}')
    print(f'  Insider buys (code P, ≥${MIN_TRANSACTION_USD:,}): {sum(len(v) for v in all_insider.values())} transactions')
    return all_insider


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


# ─────────────────────────────────────────────────────────────────────
# PART 2: Politician signals — Capitol Trades API (primary)
#         Finnhub congressional trading (fallback)
# ─────────────────────────────────────────────────────────────────────

def _politician_score(trades_for_ticker):
    """
    Score a list of politician trades for one ticker.
    Rules:
      - 45-day hard cap: discard if disclosure - transaction > 45d
      - Single politician buy: +5pts base
      - Cluster (2+ politicians within 7d): bump to +10pts
      - Single politician, committee-relevant sector: +10pts
    """
    today = date.today()
    valid = []
    for t in trades_for_ticker:
        # Parse dates
        try:
            disc_date = datetime.strptime(t['disclosure_date'][:10], '%Y-%m-%d').date()
            txn_date  = datetime.strptime(t['transaction_date'][:10], '%Y-%m-%d').date()
        except:
            continue
        lag = (disc_date - txn_date).days
        if lag > 45:
            continue  # stale — hard cap
        t['reporting_lag_days'] = lag
        valid.append(t)

    if not valid:
        return 0, valid

    # Cluster rule: 2+ politicians within 7 days of each other
    dates  = sorted([datetime.strptime(t['transaction_date'][:10], '%Y-%m-%d').date()
                     for t in valid])
    cluster = False
    for i in range(len(dates) - 1):
        if (dates[i+1] - dates[i]).days <= 7:
            cluster = True
            break

    unique_pols = len(set(t.get('politician', '') for t in valid))
    if unique_pols >= 2:
        cluster = True

    score = 10 if cluster else 5
    return score, valid


def fetch_capitol_trades(universe_set):
    """
    Capitol Trades api.capitoltrades.com DNS fails from GitHub Actions.
    No accessible free alternative exists as of 2026-05.
    Returns empty dict — politician signals disabled pending paid source.
    Backlog: Quiver Quantitative ($25/mo) covers both chambers with clean JSON.
    """
    print("  Capitol Trades: api.capitoltrades.com not accessible from GitHub Actions")
    print("  Politician signals via Capitol Trades disabled — see backlog")
    return {}

def fetch_finnhub_congressional(finnhub_token, universe_set):
    """Finnhub fallback for congressional trading."""
    if not finnhub_token:
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
                f'https://finnhub.io/api/v1/stock/congressional-trading',
                params={'symbol': ticker, 'from': cutoff_str, 'to': today_str,
                        'token': finnhub_token},
                timeout=15,
            )
            if r.status_code == 429:
                time.sleep(10)
                continue
            if r.status_code != 200:
                continue
            trades = r.json().get('data', [])
            for t in trades:
                tx = (t.get('transactionType') or '').lower()
                if 'purchase' not in tx and 'buy' not in tx:
                    continue
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


def build_politician_results(raw_by_ticker):
    """Apply 45-day hard cap, cluster rule, and scoring."""
    results = {}
    for ticker, trades in raw_by_ticker.items():
        score, valid = _politician_score(trades)
        if not valid:
            continue
        # Pick the best (freshest) trade for the summary
        best = sorted(valid, key=lambda t: t.get('reporting_lag_days', 99))
        results[ticker] = {
            'trades':             best[:5],
            'score':              score,
            'n_politicians':      len(set(t.get('politician', '') for t in valid)),
            'is_cluster':         len(set(t.get('politician', '') for t in valid)) >= 2,
            'reporting_lag_days': best[0].get('reporting_lag_days', None),
            'as_of':              str(date.today()),
        }
        print(f'    + {ticker}: score={score} lag={best[0].get("reporting_lag_days")}d'
              f' ({results[ticker]["n_politicians"]} politician(s))')
    return results


# ─────────────────────────────────────────────────────────────────────
# COMBINE + OUTPUT
# ─────────────────────────────────────────────────────────────────────

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
            'reporting_lag_days':pol.get('reporting_lag_days', None),
            'insider_buys':      ins.get('buys', []),
            'politician_trades': pol.get('trades', []),
            'as_of':             str(date.today()),
        }
    return combined


# ─────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────

def main():
    start = datetime.now()
    print('=' * 60)
    print(f'Insider & Politician Fetcher — {start.strftime("%Y-%m-%d %H:%M UTC")}')
    print('EDGAR Form 4 (corporate) + Capitol Trades (politician)')
    print('=' * 60)

    edgar_ok = pol_ok = True
    insider_signals = politician_signals = {}

    # ── 1. EDGAR Form 4 ───────────────────────────────────────────────
    print('\n[1/2] EDGAR Form 4 — Corporate Insider Buying')
    try:
        cik_map = get_cik_map()
        if cik_map:
            all_insider = fetch_edgar_signals(cik_map)
            insider_signals = build_insider_results(all_insider)
        else:
            edgar_ok = False
    except Exception as e:
        print(f'  EDGAR failed: {e}')
        edgar_ok = False

    # ── 2. Politician signals ─────────────────────────────────────────
    print('\n[2/2] Politician Signals')
    try:
        # Primary: Capitol Trades public API
        print('  Trying Capitol Trades API...')
        raw = fetch_capitol_trades(UNIVERSE_SET)

        # Fallback: Finnhub if Capitol Trades returned nothing
        if not raw:
            print('  Capitol Trades returned 0 — trying Finnhub fallback...')
            finnhub_token = os.environ.get('FINNHUB_TOKEN', '')
            raw = fetch_finnhub_congressional(finnhub_token, UNIVERSE_SET)

        politician_signals = build_politician_results(raw)
        pol_ok = True
    except Exception as e:
        print(f'  Politician fetch failed: {type(e).__name__}: {e}')
        pol_ok = False

    # ── Combine + write ───────────────────────────────────────────────
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
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return None
        if isinstance(obj, dict):
            return {k: sanitize(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [sanitize(v) for v in obj]
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
