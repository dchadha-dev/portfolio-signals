# Framework score

Answers one question: **is this business worth owning?** It gates every BUY and damps every sell signal. The buy and sell scores answer a different question (is now a good time), and are documented in `MODEL_AUDIT.md`.

## The score

| Axis | Points | Source | Inputs |
|---|---|---|---|
| Moat | 30 | Judgement | Five yes/no questions, 6 points each |
| Financial quality | 25 | Computed weekly | Gross margin (20%→60%), free-cash-flow margin (0→25%), return on invested capital (5%→20%), net debt to equity (1.5→0) |
| Growth | 20 | Computed weekly | 3-year revenue growth (0→25% a year), last-12-month revenue growth (0→30%), 3-year EPS growth (0→25%) |
| Valuation | 20 | Computed on every scanner run | P/E against the stock's own 5-year median (1.3×→0.7×), and P/E divided by growth (3→1) |

Total 95, rescaled to 100. Each input scores zero at the first figure and full marks at the second, linear in between. When an input is missing, the remaining inputs for that axis are rescaled; an axis with too few inputs is not computed at all.

**Bands:** Core 76+ · Good 62–75 · Marginal 48–61 · Avoid below 48. A BUY needs Good or better (48 for funds and ETFs).

## The five moat questions

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

A moat with no date, or a date older than 120 days, is flagged with an asterisk on the dashboard.

## Where each number comes from

For each axis the scorer uses the best source available and records which one it used:

- **data** — computed from `fundamentals.json`.
- **hand** — a hand sub-score in `framework_scores.json` (40 tickers have these from the original table), or the moat answers.
- **legacy** — a placeholder derived from the old single total. The score is marked provisional.

Funds and ETFs (`"kind": "fund"`) have no business to analyse; they keep their old hand score, moved onto the new scale.

Hover any FW number on the dashboard to see the four parts and their sources.

## Why the scale changed

The old score had a fifth axis, Portfolio Fit, documented as 5 points. In the table it ran from 5 to 18 and had become a balancing figure: CRDO's 93 was 75 from the four real axes plus 18 of fit. Fit is removed. It describes the portfolio, not the business.

Across the 40 tickers with sub-scores, the four real axes relate to the old total as `new = −3.6 + 0.94 × old` (correlation 0.97). That line moves old-only scores onto the new scale and carries the old cut-offs across: 85 → 76, 70 → 62, 55 → 48. So a score of 62 today means what 70 meant before.

## Files

| File | Written by | Purpose |
|---|---|---|
| `framework_scores.json` | You | Moat answers and dates, hand sub-scores, old totals |
| `fundamentals.json` | `fundamentals_fetcher.py`, weekly | Normalised Finnhub fundamentals, US listings |
| `framework_scorer.py` | — | Turns the two files into scores; `python framework_scorer.py` prints coverage and the largest moves |

Fundamentals older than 45 days are ignored, and the scorer falls back to hand or legacy values.

## Known limits

- **Not yet validated.** Nothing shows that a higher framework score has led to better returns. `signal_ledger.json` records the score with every label so this can be measured.
- **The Finnhub field names are unverified.** The fetcher was written without access to the live API. It matches fields through alias lists, checks Apple's figures against known ranges, and refuses to write the file if they are implausible. Read the coverage table in the first Actions run.
- **Growth is backward-looking.** It measures realised growth, not the size of the market ahead. A company at an inflection will score low until the numbers arrive.
- **Quality thresholds suit asset-light businesses.** Banks, insurers and capital-heavy industrials score lower on gross margin and leverage by construction.
- **Non-US listings and loss-makers get less data.** They fall back to hand or legacy values, and the dashboard says so.
- **Valuation needs positive earnings and three years of P/E history**; otherwise it uses price-to-sales against its own history, or falls back.
