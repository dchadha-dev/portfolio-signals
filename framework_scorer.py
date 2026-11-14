"""
framework_scorer.py
═══════════════════
Framework score = "is this business worth owning?", 0-100.

Four axes, 100 points:

    Moat               40   judgement — five yes/no questions, 8 points each
    Financial quality  30   computed  — return on capital, FCF and gross margin (half vs industry), interest cover
    Growth             20   computed  — 3y revenue CAGR, TTM revenue growth, 3y EPS growth
    Valuation          10   computed  — P/E and P/S against the stock's own 5y medians, P/E to growth

Valuation is deliberately small. This score asks whether a business is worth
owning; whether today's price is a good entry is the buy score's job. At 20
points, valuation scored almost every large tech company near zero and made the
framework score track the share price.

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

AXES = {'moat': 40, 'quality': 30, 'growth': 20, 'valuation': 10}
AXIS_TOTAL = sum(AXES.values())          # 100
# Hand sub-scores in framework_scores.json were written on the original rubric's
# scale; they are converted pro rata (a hand moat of 24/30 becomes 32/40).
HAND_MAX = {'moat': 30, 'quality': 25, 'growth': 20, 'valuation': 20}
STALE_DAYS = 120                          # a moat judgement older than this is flagged
FUNDAMENTALS_MAX_AGE_DAYS = 45            # older fundamentals are ignored, not trusted

# The old totals carried about 8 points of "portfolio fit" padding. Across the 40
# tickers with hand sub-scores, the four real axes (on the weights above) relate
# to the old total by the line below. It (a) places tickers that only have an old
# total on the new scale and (b) carries the old cut-offs across (85 / 70 / 55).
# Refit with `python framework_scorer.py --calibrate` if the weights change.
LEGACY_A, LEGACY_B = -6.92, 0.994
BANDS = (('Core', 78), ('Good', 63), ('Marginal', 48), ('Avoid', 0))
BUY_MIN = 63          # "Good" or better: the BUY gate (was 70 on the old scale)
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

def _profitable(m):
    eps = _num(m.get('eps_ttm'))
    return eps is not None and eps > 0


def _is_financial(m):
    """Banks, brokers and insurers report no gross margin or free-cash-flow margin;
    their leverage is the business model and their 'revenue' is mostly interest.
    Neither the quality nor the growth rubric applies, so both fall back to hand
    or legacy values."""
    return _num(m.get('gross_margin')) is None and _num(m.get('fcf_margin')) is None


def _fcf_margin(m):
    """Above 100% is a unit or tiny-revenue artefact (NBIS showed 3,571%), not a
    real margin. Treated as unknown."""
    v = _num(m.get('fcf_margin'))
    return v if v is not None and v <= 100 else None


INDUSTRY_MIN_PEERS = 5   # industry comparison only when at least this many peers report the figure
SCALING_MIN_GROWTH = 30  # loss-maker growing revenue at least this fast (TTM, %) ...
SCALING_MIN_GM = 50      # ... with at least this gross margin is judged as "scaling", not "failing"


def _margin_points(m, key, value, lo, hi, pts):
    """A margin scored half on its absolute level and half against the median of
    its industry peers (zero at half the median, full at 1.5x). Hardware, retail
    and industrials run structurally lower margins than software; judging them
    only against one absolute scale marks them down for their business model
    rather than for being worse at it. Without enough peers: absolute only."""
    absolute = _lin(value, lo, hi, pts)
    med = _num(m.get('_ind_' + key))
    if absolute is None or med is None or med <= 0:
        return absolute
    relative = _lin(value / med, 0.5, 1.5, pts)
    return (absolute + relative) / 2


def _leverage_points(m, pts):
    """Balance-sheet risk.

    Preferred: interest coverage (operating profit / interest), zero at 2x, full
    at 12x. It measures whether the debt can be serviced and, unlike debt/equity,
    is not distorted when buybacks shrink book equity.

    Fallback: debt / equity, full at 0, zero at 1.5. Companies that have bought
    back most of their equity (MCD 41x, LOW 52x) look insolvent on this measure,
    so when the ratio is above 3 but return on capital is 15%+ and free cash flow
    10%+, it gets half points."""
    cov = _num(m.get('interest_coverage'))
    if cov is not None and cov >= 0:
        return _lin(cov, 2, 12, pts)
    if cov is not None and _profitable(m):
        # Negative on a profitable company means it earns more interest on its
        # cash than it pays on its debt (PANW, LULU, SNDK): no debt risk.
        return float(pts)
    # Negative on a loss-maker means operating losses, which says nothing about
    # debt: judge the balance sheet on debt/equity instead.
    de = _num(m.get('net_debt_to_equity'))
    if de is None:
        return None
    roic, fcf = _num(m.get('roic')), _fcf_margin(m)
    if de > 3 and roic is not None and roic >= 15 and fcf is not None and fcf >= 10:
        return pts / 2
    return _lin(de, 1.5, 0.0, pts)


def is_scaling(m):
    """Loss-making by choice: fast revenue growth on high gross margins, i.e. the
    unit economics work and the losses are spending ahead of revenue (NBIS, NET).
    Return on capital and free cash flow describe that stage badly."""
    gm, r1 = _num(m.get('gross_margin')), _num(m.get('rev_growth_ttm'))
    return (not _profitable(m)) and gm is not None and gm >= SCALING_MIN_GM and r1 is not None and r1 >= SCALING_MIN_GROWTH


def quality_points(m):
    """Financial quality, 0-30.

    Profitable companies (and loss-makers that are not scaling):
        return on capital 10, free-cash-flow margin 6, gross margin 4, balance sheet 5
    Return on capital carries the most weight because it compares across industries.
    The two margins are scored half against their industry. Missing inputs score
    zero rather than being rescaled away.

    Scaling loss-makers (see is_scaling): gross margin 12, revenue growth 8
    (30% -> 0, 100% -> full), balance sheet 5.

    Every loss-maker keeps at most 60% of the axis. This is the only place losses
    are penalised (plus the separate rule for valuing a company with no earnings);
    growth is not capped, so one fact is not punished three times."""
    if not m or _is_financial(m):
        return None
    if is_scaling(m):
        parts = [
            (_margin_points(m, 'gross_margin', _num(m.get('gross_margin')), 40, 75, 12), 12),
            (_lin(min(_num(m.get('rev_growth_ttm')), 100.0), SCALING_MIN_GROWTH, 100, 8), 8),
            (_leverage_points(m, 5), 5),
        ]
    else:
        parts = [
            (_lin(m.get('roic'), 5, 25, 10), 10),
            (_margin_points(m, 'fcf_margin', _fcf_margin(m), 0, 20, 6), 6),
            (_margin_points(m, 'gross_margin', _num(m.get('gross_margin')), 20, 60, 4), 4),
            (_leverage_points(m, 5), 5),
        ]
        if sum(p is not None for p, _ in parts) < 3:
            return None
    pts = AXES['quality'] * sum((p or 0.0) for p, _ in parts) / 25.0
    if not _profitable(m):
        pts = min(pts, 0.6 * AXES['quality'])
    return round(pts, 1)


def growth_points(m):
    """Growth, 0-20. Realised growth only — it cannot see the size of the market
    ahead, and it judges a cyclical at the point of the cycle it is in. Not capped
    for loss-makers: their losses are already counted in quality and valuation."""
    if not m or _is_financial(m):
        return None
    rev3, rev1 = m.get('rev_growth_3y'), m.get('rev_growth_ttm')
    if _num(rev3) is None and _num(rev1) is None:
        return None
    return _blend([
        (_lin(rev3, 0, 25, 10), 10),
        (_lin(rev1, 0, 30, 6), 6),
        (_lin(m.get('eps_growth_3y'), 0, 25, 4), 4),
    ], AXES['growth'], need=1)


def industry_medians(fundamentals):
    """{industry: {'gross_margin': median, 'fcf_margin': median}} over industries
    with at least INDUSTRY_MIN_PEERS reporting companies."""
    import statistics
    groups = {}
    for m in fundamentals.values():
        ind = m.get('industry')
        if not ind or _is_financial(m):
            continue
        g = groups.setdefault(ind, {'gross_margin': [], 'fcf_margin': []})
        if _num(m.get('gross_margin')) is not None:
            g['gross_margin'].append(_num(m['gross_margin']))
        if _fcf_margin(m) is not None:
            g['fcf_margin'].append(_fcf_margin(m))
    return {ind: {k: statistics.median(v) for k, v in g.items() if len(v) >= INDUSTRY_MIN_PEERS}
            for ind, g in groups.items()}


def with_industry(m, medians):
    """Copy of m with its industry's medians attached as _ind_* keys."""
    if not m or not m.get('industry'):
        return m
    meds = medians.get(m['industry']) or {}
    return dict(m, **{'_ind_' + k: v for k, v in meds.items()}) if meds else m


def current_pe(m, price=None):
    """Finnhub's weekly P/E, moved to today's price when that is safe. price/EPS
    is only trusted when it lands within a factor of two of Finnhub's own P/E:
    for ADRs EPS is often reported in the home currency (one showed EPS 59,668),
    which would make the stock look absurdly cheap."""
    pe = _num(m.get('pe_ttm'))
    pe = pe if pe and pe > 0 else None
    eps = _num(m.get('eps_ttm')); price = _num(price)
    if price and eps and eps > 0:
        live = price / eps
        if pe is None or 0.5 <= live / pe <= 2.0:
            return live
    return pe


def current_ps(m, price=None, pe_used=None):
    """P/S moved by the same price change as P/E, when P/E was repriced."""
    ps = _num(m.get('ps_ttm'))
    base = _num(m.get('pe_ttm'))
    if ps and pe_used and base and base > 0:
        return ps * pe_used / base
    return ps


def valuation_points(m, price=None):
    """Valuation, 0-10; higher = cheaper. Three equal parts:
      - P/E against the stock's own 5-year median (1.3x -> 0, 0.7x -> full)
      - P/S against its own 5-year median, same scale. Sales move far less than
        earnings through a cycle, so this keeps a cyclical at a trough (DE: P/E
        37 against a median of 17 on depressed earnings) from looking dear only
        because profits are temporarily low
      - P/E divided by growth (3 -> 0, 1 -> full), growth capped at 50%

    Loss-makers have no earnings to value: at most a quarter of the axis, from
    P/S against its own history, and nothing above 15x sales. A stock that has
    fallen from a bubble valuation is not cheap just because it is cheaper."""
    pe = current_pe(m, price)
    ps = current_ps(m, price, pe)
    ps_med = _num(m.get('ps_5y_median'))
    rel_ps = _lin(ps / ps_med, 1.3, 0.7, 10) if ps and ps_med and ps_med > 0 else None
    if pe:
        rel = peg = None
        med = _num(m.get('pe_5y_median'))
        if med and med > 0:
            rel = _lin(pe / med, 1.3, 0.7, 10)
        g = _num(m.get('eps_growth_3y'))
        if g is None or g <= 0:
            g = _num(m.get('rev_growth_3y'))
        if g and g > 0:
            peg = _lin(pe / min(g, 50.0), 3.0, 1.0, 10)
        return _blend([(rel, 10), (rel_ps, 10), (peg, 10)], AXES['valuation'], need=1)
    if ps is None:
        return None
    if ps > 15 or rel_ps is None:
        return 0.0
    return round(rel_ps / 10 * 0.25 * AXES['valuation'], 1)


COMPUTED = {'quality': lambda m, p: quality_points(m),
            'growth': lambda m, p: growth_points(m),
            'valuation': valuation_points}


# ── MOAT ──────────────────────────────────────────────────────────────
def moat_points(entry):
    ans = entry.get('moat_answers')
    if isinstance(ans, list) and len(ans) == len(MOAT_QUESTIONS):
        return AXES['moat'] / len(MOAT_QUESTIONS) * sum(1 for a in ans if a), 'hand'
    hand = _num((entry.get('hand') or {}).get('moat'))
    if hand is not None:
        return hand * AXES['moat'] / HAND_MAX['moat'], 'hand'
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
                val, src = _num(hand.get(axis)) * axis_max / HAND_MAX[axis], 'hand'
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
        'industry': metrics.get('industry'),
        'scaling': bool(metrics) and is_scaling(metrics),
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
    # Finnhub's industry labels are coarse ("Technology" holds DELL beside ADBE);
    # an "industry" field in framework_scores.json overrides it.
    fundamentals = {t: (dict(m, industry=entries[t]['industry']) if entries.get(t, {}).get('industry') else m)
                    for t, m in fundamentals.items()}
    meds = industry_medians(fundamentals)
    out = {}
    for t, e in entries.items():
        d = score_ticker(e, with_industry(fundamentals.get(t), meds), prices.get(t), today)
        if d:
            out[t] = d
    return out


def calibrate(entries=None):
    """Refit LEGACY_A/B on tickers with hand sub-scores and print the old cut-offs
    on the new scale. Run after changing AXES."""
    entries = load_entries() if entries is None else entries
    xs, ys = [], []
    for e in entries.values():
        h = e.get('hand') or {}
        if e.get('legacy') is not None and all(_num(h.get(a)) is not None for a in AXES):
            xs.append(float(e['legacy']))
            ys.append(sum(_num(h[a]) * AXES[a] / HAND_MAX[a] for a in AXES) * 100.0 / AXIS_TOTAL)
    n = len(xs); mx = sum(xs) / n; my = sum(ys) / n
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    a = my - b * mx
    r = (sum((x - mx) * (y - my) for x, y in zip(xs, ys))
         / (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5)
    print(f"{n} tickers: new = {a:.2f} + {b:.3f} x old  (r = {r:.2f})")
    for old in (85, 70, 55):
        print(f'  old {old} -> new {a + b * old:.1f}')
    return a, b


if __name__ == '__main__':
    import sys
    if '--calibrate' in sys.argv:
        calibrate(); sys.exit(0)
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
