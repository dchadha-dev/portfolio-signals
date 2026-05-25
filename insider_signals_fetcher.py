"""
insider_signals_fetcher.py
Weekly fetcher for two alternative data signals:
1. Corporate insider buying  — SEC EDGAR Form 4 XML parsing
2. Politician buying         — Finnhub congressional trading API

Signal weights (Cohen-Malloy-Pomorski 2012):
  Single insider buy (60d):       +10 pts
  Cluster buy (2+ insiders, 60d): +20 pts
  Politician buy (45d):           +8 pts
  Combined max boost:             +25 pts (cannot create signal alone)
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

LOOKBACK_INSIDER    = 60    # days — open-market purchases are rare, wider net
LOOKBACK_POLITICIAN = 45    # days — STOCK Act 45-day filing window
MIN_TRANSACTION_USD = 15_000

POLITICIAN_KEYWORDS = {
    'senator', 'representative', 'congress', 'senate', 'house of rep',
    'member of congress', 'u.s. senator', 'u.s. representative',
}

# ─────────────────────────────────────────────────────────────────────
# PART 1: EDGAR Form 4 — corporate insider buying
# ─────────────────────────────────────────────────────────────────────

def get_cik_map():
    """Map universe tickers to SEC CIK numbers."""
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
    """Get recent Form 4 accession numbers from SEC submissions API."""
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
                break   # sorted newest-first
            acc = accnums[i] if i < len(accnums) else ''
            if acc:
                results.append((acc, fd))
        return results
    except Exception:
        return []


def get_xml_url_from_index(cik, accession):
    """
    Fetch the filing index JSON to find the primary Form 4 XML filename.
    Form 4 XML files are NOT named accession.xml — must look up in index.
    """
    acc_nodash = accession.replace('-', '')
    cik_int    = str(int(cik))
    idx_url    = (f'https://www.sec.gov/Archives/edgar/data/'
                  f'{cik_int}/{acc_nodash}/{accession}-index.json')
    try:
        time.sleep(0.12)
        r = requests.get(idx_url, headers=SEC_HEADERS, timeout=15)
        if r.status_code == 200:
            for item in r.json().get('directory', {}).get('item', []):
                name = item.get('name', '')
                if name.endswith('.xml') and 'xbrl' not in name.lower():
                    return (f'https://www.sec.gov/Archives/edgar/data/'
                            f'{cik_int}/{acc_nodash}/{name}')
    except Exception:
        pass
    # Fallback: try common Form 4 XML filename patterns
    for suffix in ['4.xml', 'ownership.xml', 'primary_doc.xml']:
        url = (f'https://www.sec.gov/Archives/edgar/data/'
               f'{cik_int}/{acc_nodash}/{suffix}')
        try:
            time.sleep(0.08)
            if requests.head(url, headers=SEC_HEADERS, timeout=8).status_code == 200:
                return url
        except Exception:
            pass
    return None


def parse_form4_xml(xml_url, ticker, filing_date):
    """
    Download and parse Form 4 XML.
    Handles both namespaced and non-namespaced SEC Form 4 schemas.
    Returns (insider_buys, politician_buys).
    """
    if not xml_url:
        return [], []
    buys = []
    pol_buys = []
    try:
        time.sleep(0.12)
        r = requests.get(xml_url, headers=SEC_HEADERS, timeout=20)
        if r.status_code != 200:
            return [], []

        content = r.content
        # Extract XML from full submission text wrapper if present
        if b'<ownershipDocument' not in content and b'<XML>' in content:
            start = content.find(b'<ownershipDocument')
            end   = content.find(b'</ownershipDocument>') + len(b'</ownershipDocument>')
            if start >= 0 and end > start:
                content = content[start:end]

        # Strip XML namespace declarations and prefixes
        # This handles both old (no namespace) and new (namespaced) Form 4 schemas
        text = content.decode('utf-8', errors='replace')
        text = re.sub(r' xmlns[^=]*="[^"]*"', '', text)
        text = re.sub(r'<(/?)[A-Za-z0-9_-]+:', lambda m: '<' + m.group(1), text)
        root = ET.fromstring(text.encode('utf-8'))

        # Get reporting owner info
        owner_name  = ''
        owner_title = ''
        for name_path in ['.//rptOwnerName']:
            el = root.find(name_path)
            if el is not None and el.text:
                owner_name = el.text.strip()
                break
        rel_el = root.find('.//reportingOwnerRelationship')
        if rel_el is not None:
            parts = []
            for tag in ['isDirector', 'isOfficer', 'isTenPercentOwner']:
                el = rel_el.find(tag)
                if el is not None and el.text in ('1', 'true', 'True'):
                    parts.append(tag.replace('is', ''))
            title_el = rel_el.find('officerTitle')
            if title_el is not None and title_el.text:
                parts.append(title_el.text.strip())
            owner_title = ', '.join(parts)

        is_politician = any(
            kw in owner_name.lower() or kw in owner_title.lower()
            for kw in POLITICIAN_KEYWORDS
        )

        # Parse non-derivative transactions
        n_pass = 0
        for txn in root.findall('.//nonDerivativeTransaction'):
            # Transaction code — must be P (open market purchase)
            code = None
            for path in ['.//transactionCodes/transactionCode',
                         'transactionCodes/transactionCode',
                         './/transactionCode']:
                el = txn.find(path)
                if el is not None and el.text:
                    code = el.text.strip()
                    break
            if code != 'P':
                continue

            # Exclude 10b5-1 pre-scheduled plans
            plan = None
            for path in ['.//transactionCodes/rule10b5-1PlanFlag',
                         'transactionCodes/rule10b5-1PlanFlag']:
                el = txn.find(path)
                if el is not None:
                    plan = el.text
                    break
            if plan in ('Y', '1', 'true'):
                continue

            # Transaction amounts
            shares = 0.0
            price  = 0.0
            for path in ['.//transactionAmounts/transactionShares/value',
                         './/transactionShares/value']:
                el = txn.find(path)
                if el is not None and el.text:
                    try: shares = float(el.text); break
                    except: pass
            for path in ['.//transactionAmounts/transactionPricePerShare/value',
                         './/transactionPricePerShare/value']:
                el = txn.find(path)
                if el is not None and el.text:
                    try: price = float(el.text); break
                    except: pass

            if shares * price < MIN_TRANSACTION_USD:
                continue

            n_pass += 1
            entry = {
                'ticker':    ticker,
                'date':      filing_date,
                'value_usd': round(shares * price),
                'shares':    int(shares),
                'price':     round(price, 2),
                'name':      owner_name,
                'title':     owner_title,
            }
            if is_politician:
                pol_buys.append(entry)
            else:
                buys.append(entry)

        if n_pass > 0:
            print(f'    + {ticker}: {n_pass} qualifying buy(s) — {owner_name[:30]}')

    except Exception:
        pass
    return buys, pol_buys


def fetch_all_signals(cik_map):
    """Fetch Form 4 for all tickers, split into insider and politician."""
    ins_cutoff = (datetime.now() - timedelta(days=LOOKBACK_INSIDER)).date().strftime('%Y-%m-%d')
    pol_cutoff = (datetime.now() - timedelta(days=LOOKBACK_POLITICIAN)).date().strftime('%Y-%m-%d')
    all_insider = {}
    all_pol     = {}
    n = len(cik_map)
    xml_found = xml_tried = 0
    print(f'  Fetching Form 4 for {n} tickers...')

    for i, (ticker, cik) in enumerate(cik_map.items()):
        if i % 15 == 0:
            print(f'    {i}/{n} (XML found: {xml_found}/{xml_tried})...')
        for acc, fd in fetch_form4_accessions(cik, pol_cutoff)[:8]:
            xml_tried += 1
            xml_url = get_xml_url_from_index(cik, acc)
            if xml_url:
                xml_found += 1
            ins_b, pol_b = parse_form4_xml(xml_url, ticker, fd)
            if fd >= ins_cutoff:
                all_insider.setdefault(ticker, []).extend(ins_b)
            all_pol.setdefault(ticker, []).extend(pol_b)

    print(f'  XML resolution: {xml_found}/{xml_tried} successful')
    print(f'  Insider buys: {sum(len(v) for v in all_insider.values())} transactions')
    print(f'  Politician buys (EDGAR): {sum(len(v) for v in all_pol.values())} transactions')
    return all_insider, all_pol


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


def build_politician_results(all_pol):
    results = {}
    for ticker, buys in all_pol.items():
        if not buys:
            continue
        results[ticker] = {
            'trades':        buys[:5],
            'score':         8,
            'n_politicians': len(set(b['name'] for b in buys)),
            'as_of':         str(date.today()),
        }
    return results


# ─────────────────────────────────────────────────────────────────────
# PART 2: Finnhub congressional trading API
# ─────────────────────────────────────────────────────────────────────

def fetch_politician_signals_finnhub(finnhub_token):
    """
    Fetch congressional trading via Finnhub /stock/congressional-trading.
    Uses existing FINNHUB_TOKEN GitHub secret — no new key needed.
    Free tier: 60 calls/minute. 89 US tickers at 0.15s = ~13s total.
    """
    if not finnhub_token:
        print('  FINNHUB_TOKEN not set — skipping congressional signals')
        return {}, False

    cutoff_str = (datetime.now() - timedelta(days=LOOKBACK_POLITICIAN)).date().strftime('%Y-%m-%d')
    today_str  = date.today().strftime('%Y-%m-%d')
    results    = {}
    n_found = n_tickers = 0
    us_tickers = [t for t in UNIVERSE if '.' not in t]
    print(f'  Finnhub congressional: {len(us_tickers)} tickers, cutoff {cutoff_str}...')

    for ticker in us_tickers:
        try:
            time.sleep(0.15)
            url = (f'https://finnhub.io/api/v1/stock/congressional-trading'
                   f'?symbol={ticker}&from={cutoff_str}&to={today_str}'
                   f'&token={finnhub_token}')
            r = requests.get(url, timeout=15)
            if r.status_code == 429:
                print('  Rate limited — sleeping 10s')
                time.sleep(10)
                r = requests.get(url, timeout=15)
            if r.status_code != 200:
                continue
            data   = r.json()
            trades = data.get('data', [])
            if not trades:
                continue
            buys = []
            for t in trades:
                tx = (t.get('transactionType') or '').lower()
                if 'purchase' not in tx and 'buy' not in tx:
                    continue
                buys.append({
                    'ticker':     ticker,
                    'date':       t.get('transactionDate', ''),
                    'politician': t.get('name', 'Unknown'),
                    'chamber':    t.get('chamber', ''),
                    'party':      t.get('party', ''),
                    'amount':     t.get('amount', ''),
                })
            if buys:
                n_tickers += 1
                n_found   += len(buys)
                results[ticker] = {
                    'trades':        buys[:5],
                    'score':         8,
                    'n_politicians': len(set(b['politician'] for b in buys)),
                    'as_of':         str(date.today()),
                }
                print(f'    + {ticker}: {len(buys)} congressional buy(s)')
        except Exception:
            continue

    print(f'  Finnhub congressional: {n_found} trades, {n_tickers} tickers')
    return results, True


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
    print('EDGAR Form 4 (corporate) + Finnhub (congressional)')
    print('=' * 60)

    edgar_ok      = True
    politician_ok = True
    insider_signals    = {}
    politician_signals = {}

    # ── 1. EDGAR Form 4 — corporate insider buying ───────────────────
    print('\n[1/2] EDGAR Form 4 — Corporate Insider Buying')
    try:
        cik_map = get_cik_map()
        if cik_map:
            all_insider, _ = fetch_all_signals(cik_map)
            insider_signals = build_insider_results(all_insider)
        else:
            edgar_ok = False
    except Exception as e:
        print(f'  EDGAR failed: {e}')
        edgar_ok = False

    # ── 2. Finnhub — congressional trading ───────────────────────────
    print('\n[2/2] Finnhub — Congressional Trading')
    try:
        finnhub_token = os.environ.get('FINNHUB_TOKEN', '')
        politician_signals, politician_ok = fetch_politician_signals_finnhub(finnhub_token)
    except Exception as e:
        print(f'  Finnhub failed: {e}')
        politician_ok = False

    # ── Combine + write ───────────────────────────────────────────────
    combined = combine(insider_signals, politician_signals)
    elapsed  = round((datetime.now() - start).total_seconds(), 1)

    health = {
        'timestamp':            datetime.now().strftime('%Y-%m-%d %H:%M UTC'),
        'insider_fetch_ok':     edgar_ok,
        'politician_fetch_ok':  politician_ok,
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
