"""
framework_scorer.py
═══════════════════
Framework score = "is this business worth owning?", 0-100.

Four axes, 95 points, rescaled to 100:

    Moat               30   judgement — five yes/no questions, 6 points each
    Financial quality  25   computed  — gross margin, FCF margin, ROIC, leverage
    Growth             20   computed  — 3y revenue CAGR, TTM revenue growth, 3y EPS growth
    Valuation          20   computed  — P/E against the stock's own 5y median, and P/E to growth

Portfolio Fit is no longer part of the score. It describes the portfolio, not
the business, and in the old table it had become a balancing figure (documented
maximum 5, actual values up to 18).

Each axis is taken from the best source available, in this order:

    data    computed from fundamentals.json (written weekly by fundamentals_fetcher.py)
    hand    a hand sub-score recorded in framework_scores.json
    legacy  the old single total, spread pro rata — a placeholder, flagged as provisional

The old totals are first moved onto the new scale (see LEGACY_A/B below), so
every ticker is comparable, and every score says where each part came from.
"""

import json, os
from datetime import date, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
SCORES_FILE = os.path.join(HERE, 'framework_scores.json')
FUNDAMENTALS_FILE = os.path.join(HERE, 'fundamentals.json')

AXES = {'moat': 30, 'quality': 25, 'growth': 20, 'valuation': 20}
AXIS_TOTAL = sum(AXES.values())          # 95
STALE_DAYS = 120                          # a moat judgement older than this is flagged
FUNDAMENTALS_MAX_AGE_DAYS = 45            # older fundamentals are ignored, not trusted

# The old totals carried about 8 points of "portfolio fit" padding. Across the 40
# tickers that have hand sub-scores, the four real axes relate to the old total as
#     new = -3.6 + 0.94 x old      (r = 0.97, residual sd 2.1)
# That line is used to (a) place tickers that only have an old total on the new
# scale, and (b) carry the old band cut-offs across: 85 -> 76, 70 -> 62, 55 -> 48.
LEGACY_A, LEGACY_B = -3.6, 0.94
BANDS = (('Core', 76), ('Good', 62), ('Marginal', 48), ('Avoid', 0))
BUY_MIN = 62          # "Good" or better: the BUY gate (was 70 on the old scale)
FUND_BUY_MIN = 48     # funds and ETFs (was 55)


def legacy_on_new_scale(legacy):
    return max(0.0, min(100.0, LEGACY_A + LEGACY_B * legacy))

MOAT_QUESTIONS = (
    'Would a typical customer face real cost, risk or disruption in switching away?',
    'Does the product get better or cheaper as more customers use it (network or scale effects)?',
    'Does it own technology, IP, data or licences a well-funded rival could not replicate in five years?',
    'Has it held or gained market share and pricing power over the last five years?',
    'Is it number one or two in its core market?',
)


def band_of(score):
    if score is None:
        return None
    for name, floor in BANDS:
        if score >= floor:
            return name
    return 'Avoid'


def _num(x):
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if x == x and abs(x) != float('inf') else None


def _lin(x, lo, hi, pts):
    """0 at `lo`, `pts` at `hi`, linear between, clipped. lo > hi means lower is better."""
    x = _num(x)
    if x is None:
        return None
    f = (x - lo) / (hi - lo)
    return pts * max(0.0, min(1.0, f))


def _blend(parts, axis_max, need):
    """parts: list of (points or None, max points). Rescales what is available to
    the axis maximum. Returns None when fewer than `need` inputs are present."""
    have = [(p, m) for p, m in parts if p is not None]
    if len(have) < need:
        return None
    return round(axis_max * sum(p for p, _ in have) / sum(m for _, m in have), 1)


# ── COMPUTED AXES ─────────────────────────────────────────────────────
# All margin / return / growth inputs are in percent (45.0 = 45%).

def quality_points(m):
    """Financial quality, 0-25. Thresholds follow the written rubric: gross margin
    60%+, ROIC 20%+, strong cash generation, little debt."""
    return _blend([
        (_lin(m.get('gross_margin'), 20, 60, 7), 7),
        (_lin(m.get('fcf_margin'),    0, 25, 6), 6),
        (_lin(m.get('roic'),          5, 20, 7), 7),
        (_lin(m.get('net_debt_to_equity'), 1.5, 0.0, 5), 5),
    ], AXES['quality'], need=2)


def growth_points(m):
    """Growth, 0-20. Realised growth only — it cannot see the size of the market ahead."""
    rev3, rev1 = m.get('rev_growth_3y'), m.get('rev_growth_ttm')
    if _num(rev3) is None and _num(rev1) is None:
        return None
    return _blend([
        (_lin(rev3, 0, 25, 10), 10),
        (_lin(rev1, 0, 30, 6), 6),
        (_lin(m.get('eps_growth_3y'), 0, 25, 4), 4),
    ], AXES['growth'], need=1)


def current_pe(m, price=None):
    eps = _num(m.get('eps_ttm')); price = _num(price)
    if price and eps and eps > 0:
        return price / eps
    pe = _num(m.get('pe_ttm'))
    return pe if pe and pe > 0 else None


def valuation_points(m, price=None):
    """Valuation, 0-20; higher = cheaper. Half from P/E against the stock's own
    5-year median, half from P/E divided by growth. Loss-makers fall back to
    price/sales against its own history."""
    pe = current_pe(m, price)
    rel = peg = None
    if pe:
        med = _num(m.get('pe_5y_median'))
        if med and med > 0:
            rel = _lin(pe / med, 1.3, 0.7, 10)
        g = _num(m.get('eps_growth_3y'))
        if g is None or g <= 0:
            g = _num(m.get('rev_growth_3y'))
        if g and g > 0:
            peg = _lin(pe / g, 3.0, 1.0, 10)
    else:
        ps, med = _num(m.get('ps_ttm')), _num(m.get('ps_5y_median'))
        if ps and med and med > 0:
            rel = _lin(ps / med, 1.3, 0.7, 10)
    return _blend([(rel, 10), (peg, 10)], AXES['valuation'], need=1)


COMPUTED = {'quality': lambda m, p: quality_points(m),
            'growth': lambda m, p: growth_points(m),
            'valuation': valuation_points}


# ── MOAT ──────────────────────────────────────────────────────────────
def moat_points(entry):
    ans = entry.get('moat_answers')
    if isinstance(ans, list) and len(ans) == len(MOAT_QUESTIONS):
        return 6.0 * sum(1 for a in ans if a), 'hand'
    hand = _num((entry.get('hand') or {}).get('moat'))
    if hand is not None:
        return hand, 'hand'
    return None, None


def _age_days(d, today):
    try:
        return (today - datetime.strptime(str(d)[:10], '%Y-%m-%d').date()).days
    except (TypeError, ValueError):
        return None


# ── ONE TICKER ────────────────────────────────────────────────────────
def score_ticker(entry, metrics=None, price=None, today=None):
    """entry: one record from framework_scores.json. Returns None if there is
    nothing to score from, else a dict with score, band, parts and sources."""
    if not entry:
        return None
    today = today or date.today()
    legacy = _num(entry.get('legacy'))
    hand = entry.get('hand') or {}
    metrics = metrics or {}
    is_fund = entry.get('kind') == 'fund'  # ETFs and funds: no business to analyse
    if is_fund:
        metrics, hand, entry = {}, {}, dict(entry, moat_answers=None, hand={})

    parts, sources = {}, {}
    for axis, axis_max in AXES.items():
        val = src = None
        if axis == 'moat':
            val, src = moat_points(entry)
        else:
            val = COMPUTED[axis](metrics, price)
            if val is not None:
                src = 'data'
            elif _num(hand.get(axis)) is not None:
                val, src = _num(hand.get(axis)), 'hand'
        if val is None:
            if legacy is None:
                return None
            val, src = axis_max * legacy_on_new_scale(legacy) / 100.0, 'legacy'
        parts[axis] = round(max(0.0, min(float(axis_max), val)), 1)
        sources[axis] = src

    score = int(round(100.0 * sum(parts.values()) / AXIS_TOTAL))
    moat_age = _age_days(entry.get('moat_date'), today)
    return {
        'score': score,
        'band': band_of(score),
        'parts': parts,
        'sources': sources,
        'legacy': None if legacy is None else int(legacy),
        'kind': 'fund' if is_fund else 'stock',
        'provisional': (not is_fund) and 'legacy' in sources.values(),   # some axis is still a placeholder
        'moat_date': entry.get('moat_date'),
        'moat_stale': (not is_fund) and (sources['moat'] != 'hand' or moat_age is None or moat_age > STALE_DAYS),
        'moat_reason': entry.get('moat_reason'),
        'pe': (lambda v: None if v is None else round(v, 1))(current_pe(metrics, price)),
    }


# ── ALL TICKERS ───────────────────────────────────────────────────────
def load_entries(path=SCORES_FILE):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f).get('tickers', {})
    except (OSError, ValueError) as e:
        print(f'framework_scores.json not readable ({e}) — no framework scores')
        return {}


def load_fundamentals(path=FUNDAMENTALS_FILE, today=None):
    """Returns ({ticker: metrics}, as_of). Stale or unreadable files give ({}, None)
    so the scorer falls back to hand/legacy values and says so."""
    today = today or date.today()
    try:
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}, None
    as_of = d.get('as_of')
    age = _age_days(as_of, today)
    if age is None or age > FUNDAMENTALS_MAX_AGE_DAYS:
        print(f'fundamentals.json is {age} days old — ignored')
        return {}, as_of
    return d.get('tickers', {}), as_of


def score_all(prices=None, today=None, entries=None, fundamentals=None):
    """{ticker: detail}. prices: optional {ticker: last close} so the valuation
    axis uses today's price rather than last week's P/E."""
    entries = load_entries() if entries is None else entries
    if fundamentals is None:
        fundamentals, _ = load_fundamentals(today=today)
    prices = prices or {}
    out = {}
    for t, e in entries.items():
        d = score_ticker(e, fundamentals.get(t), prices.get(t), today)
        if d:
            out[t] = d
    return out


if __name__ == '__main__':
    det = score_all()
    fund, as_of = load_fundamentals()
    n = len(det)
    by_src = {a: {} for a in AXES}
    for d in det.values():
        for a, s in d['sources'].items():
            by_src[a][s] = by_src[a].get(s, 0) + 1
    print(f'{n} tickers scored · fundamentals as of {as_of} ({len(fund)} tickers)')
    for a in AXES:
        print(f'  {a:<10} {by_src[a]}')
    moved = sorted(((d['score'] - round(legacy_on_new_scale(d['legacy'])), t) for t, d in det.items() if d['legacy'] is not None), key=lambda x: x[0])
    print('largest moves vs old score on the new scale:', [(t, f'{m:+d}') for m, t in moved[:8]], '...', [(t, f'{m:+d}') for m, t in moved[-8:]])
    crossed = [(t, d['legacy'], d['score']) for t, d in det.items()
               if d['legacy'] is not None and d['kind'] == 'stock' and (d['legacy'] >= 70) != (d['score'] >= BUY_MIN)]
    print(f'{len(crossed)} stocks change side of the BUY gate (old 70 / new {BUY_MIN}):', crossed)
