import { getStore } from '@netlify/blobs';

/**
 * Returns the last price snapshot written by trigger_refresh.
 * Instant and unauthenticated — safe for the dashboard to call on load,
 * and readable by any scheduled job that wants JSON rather than a screenshot.
 */
export default async (req) => {
  const headers = {
    'Access-Control-Allow-Origin': '*',
    'Content-Type':                'application/json',
    // Short CDN cache: fresh enough to be useful, cheap enough to hammer.
    'Cache-Control':               'public, max-age=60, stale-while-revalidate=600',
  };
  if (req.method === 'OPTIONS') return new Response('', { status: 204, headers });

  try {
    const store = getStore('portfolio');
    const snap  = await store.get('latest', { type: 'json' });

    if (!snap) {
      return new Response(JSON.stringify({
        error: 'no snapshot yet — call /.netlify/functions/trigger_refresh once to populate',
        prices: {},
      }), { status: 404, headers });
    }

    const ageMs = Date.now() - new Date(snap.timestamp).getTime();
    return new Response(JSON.stringify({
      ...snap,
      age_minutes: Math.round(ageMs / 60000),
      stale:       ageMs > 6 * 60 * 60 * 1000,
    }), { status: 200, headers });

  } catch (e) {
    return new Response(JSON.stringify({ error: e.message, prices: {} }),
                        { status: 500, headers });
  }
};
