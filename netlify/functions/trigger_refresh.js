import { getStore } from '@netlify/blobs';

const FINNHUB_KEY = process.env.FINNHUB_TOKEN;
const GH_OWNER    = 'dchadha-dev';
const GH_REPO     = 'portfolio-signals';

// Finnhub free tier: 60 calls/min. 5 per batch with a 5.2s gap ≈ 57/min.
const BATCH = 5;
const DELAY = 5200;

const sleep = ms => new Promise(r => setTimeout(r, ms));

/** Ticker list = holdings.json in the repo (single source of truth). */
async function loadTickers() {
  try {
    const url = `https://raw.githubusercontent.com/${GH_OWNER}/${GH_REPO}/main/holdings.json?t=${Date.now()}`;
    const r = await fetch(url);
    if (!r.ok) throw new Error(`holdings.json HTTP ${r.status}`);
    const j = await r.json();
    return (j.stocks || [])
      .map(s => (s.t || '').trim().toUpperCase())
      .filter(Boolean);
  } catch (e) {
    console.warn('holdings.json unavailable:', e.message);
    return [];
  }
}

/** Fallback source: live_prices.json committed by the scanner 2x daily.
 *  Lets this function work with no FINNHUB_TOKEN configured at all. */
async function loadScannerPrices() {
  const url = `https://raw.githubusercontent.com/${GH_OWNER}/${GH_REPO}/main/live_prices.json?t=${Date.now()}`;
  const r = await fetch(url);
  if (!r.ok) throw new Error(`live_prices.json HTTP ${r.status}`);
  const j = await r.json();
  const out = {};
  Object.entries(j.prices || {}).forEach(([t, v]) => {
    if (v && v.price) out[t] = { price: v.price, change_pct: v.change_pct || 0, stale: false };
  });
  if (!Object.keys(out).length) throw new Error('live_prices.json empty');
  return { prices: out, source: 'scanner', scanner_timestamp: j.timestamp || j.run_date || null };
}

async function fetchQuote(ticker) {
  const url = `https://finnhub.io/api/v1/quote?symbol=${encodeURIComponent(ticker)}&token=${FINNHUB_KEY}`;
  const ctrl = new AbortController();
  const tid  = setTimeout(() => ctrl.abort(), 10000);
  try {
    const res = await fetch(url, { signal: ctrl.signal });
    if (res.status === 429) throw new Error('RATE_LIMIT');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const d = await res.json();
    if (!d.c || d.c === 0) throw new Error('empty quote');
    return { price: +d.c.toFixed(2), change_pct: +(d.dp ?? 0).toFixed(2) };
  } finally {
    clearTimeout(tid);
  }
}

export default async (req, context) => {
  const cors = {
    'Access-Control-Allow-Origin':  '*',
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
    'Content-Type':                 'application/json',
  };
  if (req.method === 'OPTIONS') return new Response('', { status: 204, headers: cors });

  const store = getStore('portfolio');

  // No Finnhub key? Cache the scanner's committed prices instead. Slightly
  // less fresh, but zero configuration and still serves the crawler use case.
  if (!FINNHUB_KEY) {
    try {
      const fb = await loadScannerPrices();
      const snapshot = {
        timestamp:    new Date().toISOString(),
        source:       'scanner-fallback',
        note:         'FINNHUB_TOKEN not set — served from live_prices.json',
        scanner_timestamp: fb.scanner_timestamp,
        ticker_count: Object.keys(fb.prices).length,
        live_count:   Object.keys(fb.prices).length,
        rate_limited: 0,
        failed:       [],
        prices:       fb.prices,
      };
      await store.setJSON('latest', snapshot);
      return new Response(JSON.stringify(snapshot), { status: 200, headers: cors });
    } catch (e) {
      return new Response(JSON.stringify({
        error: 'no FINNHUB_TOKEN and scanner fallback failed: ' + e.message
      }), { status: 502, headers: cors });
    }
  }

  const tickers = await loadTickers();

  if (!tickers.length) {
    return new Response(JSON.stringify({ error: 'no tickers — holdings.json unreachable' }),
                        { status: 502, headers: cors });
  }

  // Start from the previous snapshot so a failed call never blanks a ticker.
  let prices = {};
  try {
    const prev = await store.get('latest', { type: 'json' });
    if (prev?.prices) prices = { ...prev.prices };
  } catch (e) { /* first run — no prior snapshot */ }

  let live = 0, limited = 0;
  const failed = [];

  for (let i = 0; i < tickers.length; i += BATCH) {
    const batch = tickers.slice(i, i + BATCH);
    await Promise.all(batch.map(async t => {
      try {
        prices[t] = { ...(await fetchQuote(t)), stale: false };
        live++;
      } catch (e) {
        if (e.message === 'RATE_LIMIT') limited++;
        if (prices[t]) prices[t].stale = true;
        else failed.push(t);
      }
    }));
    if (i + BATCH < tickers.length) await sleep(DELAY);
  }

  const snapshot = {
    timestamp:    new Date().toISOString(),
    ticker_count: tickers.length,
    live_count:   live,
    rate_limited: limited,
    failed,
    prices,
  };

  try {
    await store.setJSON('latest', snapshot);
  } catch (e) {
    console.error('blob write failed:', e.message);
    return new Response(JSON.stringify({ ...snapshot, warning: 'cache write failed: ' + e.message }),
                        { status: 200, headers: cors });
  }

  return new Response(JSON.stringify(snapshot), { status: 200, headers: cors });
};

export const config = { path: '/.netlify/functions/trigger_refresh' };
