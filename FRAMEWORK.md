# Framework score

Answers one question: **is this business worth owning?** It gates every BUY (a BUY needs 63+) and damps every sell signal. The buy and sell scores answer a different question, whether now is a good time, and are documented in `MODEL_AUDIT.md`.

## The score: 100 points

| Axis | Points | Source | What earns the points |
|---|---|---|---|
| Moat | 40 | Your judgement | Five yes/no questions, 8 points each |
| Financial quality | 30 | Computed weekly | Return on capital, free-cash-flow margin, gross margin, debt |
| Growth | 20 | Computed weekly | 3-year revenue growth, last-12-month revenue growth, 3-year EPS growth |
| Valuation | 10 | Computed on every scanner run | P/E and P/S against the stock's own 5-year medians, P/E against growth |

**Bands:** Core 78+ · Good 63–77 · Marginal 48–62 · Avoid below 48. A BUY needs Good or better (48 for funds and ETFs). These are the old 85 / 70 / 55 cut-offs moved onto the new scale.

### Why moat is 40 and valuation only 10

Moat is the one axis that decides whether a business stays good, and it is where your judgement adds most; the other three are arithmetic. Valuation is small because whether today's price is right is the buy score's job. At 20 points, valuation scored nearly every large tech company close to zero, so the framework score ended up tracking the share price instead of the business.

## How each axis is computed

Each input scores zero at the first figure and full marks at the second, in a straight line between.

### Financial quality (30)

| Input | Weight | Zero at | Full at | Why |
|---|---|---|---|---|
| Return on capital | 10 of 25 | 5% | 25% | The best single test of a good business, and it works for retailers, software and industrials alike |
| Free-cash-flow margin | 6 of 25 | 0% | 20% | Profits that turn into cash |
| Gross margin | 4 of 25 | 20% | 60% | Pricing power; kept small so high-volume, low-margin models (Costco) are not punished for their business model |
| Debt to equity | 5 of 25 | 1.5× | 0 | Balance-sheet risk |

The four weights add to 25 and are scaled up to 30.

Adjustments:

- **Missing inputs score zero.** A company that does not report free cash flow does not get the benefit of the doubt. If fewer than three inputs exist, the axis is not computed at all.
- **Loss-makers keep at most 60% of the axis (18 of 30).**
- **Buybacks.** Finnhub reports debt over book equity, so companies that have bought back most of their shares look insolvent (McDonald's 41×, Lowe's 52×). If the ratio is above 3 but return on capital is 15%+ and free-cash-flow margin 10%+, the debt input gets half points instead of zero.
- **Implausible figures are ignored.** A free-cash-flow margin above 100% (NBIS showed 3,571%) is treated as unknown.
- **Banks, brokers and insurers** report no gross or free-cash-flow margin, and leverage is their business model. The rubric does not fit, so quality and growth fall back to your hand score.

### Growth (20)

| Input | Weight | Zero at | Full at |
|---|---|---|---|
| 3-year revenue growth, per year | 10 | 0% | 25% |
| Last-12-month revenue growth | 6 | 0% | 30% |
| 3-year EPS growth, per year | 4 | 0% | 25% |

Missing inputs are dropped and the rest rescaled; at least one revenue figure is needed. Loss-makers keep at most 60% of the axis (12 of 20), because fast growth from a tiny, unprofitable base is not the same thing as a growing business.

### Valuation (10)

Three equal parts; higher means cheaper:

- **P/E against its own 5-year median:** zero at 1.3×, full marks at 0.7×.
- **P/S against its own 5-year median,** same scale. Sales swing far less than profits through a cycle, so this stops a cyclical at the bottom of its cycle looking expensive only because its earnings are temporarily low (Deere: P/E 37 against a median of 17).
- **P/E divided by growth:** zero at 3, full marks at 1. Growth is capped at 50% a year, because no stock is cheap on 300% growth.

Other rules:

- **Repricing.** P/E is moved to today's price on every scanner run, but only if the result is within 2× of Finnhub's own figure. For some foreign listings EPS is in the home currency (one showed EPS 59,668), which would otherwise make the stock look absurdly cheap.
- **Loss-makers** have no earnings to value. They get at most 2.5 points, from P/S against their own history, and zero above 15× sales. A stock that has fallen from a bubble valuation is not cheap just because it is cheaper.

### Moat (40): the five questions

1. Would a typical customer face real cost, risk or disruption in switching away?
2. Does the product get better or cheaper as more customers use it (network or scale effects)?
3. Does it own technology, IP, data or licences a well-funded rival could not replicate in five years?
4. Has it held or gained market share and pricing power over the last five years?
5. Is it number one or two in its core market?

Record the answers in `framework_scores.json`:

```json
"ANET": { "legacy": 91, "moat_answers": [true, false, true, true, true], "moat_date": "2026-11-20",
          "moat_reason": "EOS lock-in with hyperscalers; no network effect" }
```

A moat with no date, or a date older than 120 days, is marked with an asterisk on the dashboard.

## Where each number comes from

For each axis the scorer uses the best source available and records which one it used:

- **data:** computed from `fundamentals.json`.
- **hand:** a hand sub-score in `framework_scores.json` (40 tickers have these from the original table), or the moat answers. Hand sub-scores are stored on the original rubric's scale (moat /30, quality /25, growth /20, valuation /20) and converted pro rata: a hand moat of 24/30 counts as 32/40.
- **legacy:** a placeholder derived from the old single total. The score is marked provisional.

Funds and ETFs (`"kind": "fund"`) have no business to analyse; they keep their old hand score, moved onto the new scale.

Hover any FW number on the dashboard to see the four parts and their sources.

## Why the scale changed

The old score had a fifth axis, Portfolio Fit, documented as 5 points. In the table it ran from 5 to 18 and had become a balancing figure: CRDO's 93 was 75 from the four real axes plus 18 of fit. Fit is removed. It describes the portfolio, not the business; use the Overlap tab for that.

Across the 40 tickers with sub-scores, the four real axes (at the weights above) relate to the old total as `new = −6.92 + 0.994 × old` (correlation 0.95). That line puts old-only scores onto the new scale and carries the cut-offs across: 85 → 78, 70 → 63, 55 → 48. A 63 today means what a 70 meant before. If the weights change, run `python framework_scorer.py --calibrate` and update `LEGACY_A`, `LEGACY_B` and the bands; a test checks they agree.

## Files

| File | Written by | Purpose |
|---|---|---|
| `framework_scores.json` | You | Moat answers and dates, hand sub-scores, old totals |
| `fundamentals.json` | `fundamentals_fetcher.py`, weekly | Finnhub fundamentals for US listings |
| `framework_scorer.py` | — | Turns the two files into scores. `python framework_scorer.py` prints coverage, the largest moves and every gate crossing |

Fundamentals older than 45 days are ignored, and the scorer falls back to hand or legacy values.

## Known limits

- **Not validated against returns.** Nothing yet shows that a higher framework score has led to better results. `signal_ledger.json` records the score with every label so this can be measured.
- **Growth is backward-looking.** A company at an inflection scores low until the numbers arrive, and a cyclical is judged at whatever point in the cycle it is in (P/S helps valuation here, but not growth).
- **Growth favours growth companies.** Full marks need 25% a year, so steady compounders (P&G 2%, Coca-Cola 4%) score low on this axis. That is deliberate for a growth-oriented portfolio, but it is a choice.
- **Return on capital is GAAP-based.** Companies with heavy stock compensation or recent large acquisitions (Palo Alto 1.6%) look worse than their cash economics.
- **190 of 230 stocks still have no real moat judgement,** only a placeholder from the old total. Until those are answered, 40% of most scores is the old number.
- **Non-US listings, ETFs and funds stay hand-scored.**
