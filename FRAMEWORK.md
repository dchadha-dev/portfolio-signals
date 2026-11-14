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

Two paths, depending on the company's stage.

**Profitable companies, and loss-makers that are not scaling:**

| Input | Weight | Zero at | Full at | Why |
|---|---|---|---|---|
| Return on capital | 10 of 25 | 5% | 25% | The best single test of a good business, and it compares across industries |
| Free-cash-flow margin | 6 of 25 | 0% | 20% | Profits that turn into cash |
| Gross margin | 4 of 25 | 20% | 60% | Pricing power |
| Balance sheet | 5 of 25 | see below | | Can the debt be serviced |

**Scaling loss-makers** lose money while growing revenue 30%+ a year on gross margins of 50%+. The unit economics work and the losses are spending ahead of revenue (NBIS, NET, CRWV), so return on capital and cash flow describe them badly. They are judged on:

| Input | Weight | Zero at | Full at |
|---|---|---|---|
| Gross margin | 12 of 25 | 40% | 75% |
| Revenue growth, last 12 months | 8 of 25 | 30% | 100% |
| Balance sheet | 5 of 25 | see below | |

Both paths are out of 25 and scaled to 30.

**Margins are judged half against the industry.** Each margin scores half on the absolute scale above and half against the median of its industry peers: zero at half the median, full at 1.5×. This stops hardware, retail and industrials being marked down for their business model. Industry labels come from Finnhub. Where those are too coarse, an `industry` field in `framework_scores.json` overrides them; AAPL, DELL, HPE, SMCI, STX, WDC and SNDK are set to "Technology Hardware", because Finnhub files them under "Technology" beside the software companies. An industry needs at least 5 peers with data; otherwise only the absolute scale is used.

**Balance sheet.** Interest coverage (operating profit divided by interest) is used when Finnhub reports it: zero at 2×, full at 12×. Unlike debt-to-equity, it is not distorted when buybacks shrink book equity.

- A negative figure on a profitable company means it earns more interest on its cash than it pays on its debt (PANW, LULU), so it gets full points.
- On a loss-maker, a negative figure only reflects the losses, so the balance sheet is judged on debt-to-equity instead: full at 0, zero at 1.5×. Above 3×, with return on capital 15%+ and free-cash-flow margin 10%+, debt-to-equity gets half points, because there the ratio reflects buybacks rather than risk.

**Other rules:**

- **Missing inputs score zero.** On the profitable path, fewer than three inputs means the axis is not computed.
- **Every loss-maker keeps at most 60% of quality (18 of 30).** This is the only place losses are penalised. Growth is not capped, and valuation has its own rule for companies with no earnings, so one fact is not punished three times.
- **Implausible figures are ignored.** A free-cash-flow margin above 100% (NBIS showed 3,571%) is treated as unknown.
- **Banks, brokers and insurers** report no gross or free-cash-flow margin, and leverage is their business model. Quality and growth fall back to your hand score.

### Growth (20)

| Input | Weight | Zero at | Full at |
|---|---|---|---|
| 3-year revenue growth, per year | 10 | 0% | 25% |
| Last-12-month revenue growth | 6 | 0% | 30% |
| 3-year EPS growth, per year | 4 | 0% | 25% |

Missing inputs are dropped and the rest rescaled; at least one revenue figure is needed. There is no cap for loss-makers, because their losses are already counted in quality.

### Valuation (10)

Three equal parts; higher means cheaper:

- **P/E against its own 5-year median:** zero at 1.3×, full marks at 0.7×.
- **P/S against its own 5-year median,** same scale. Sales swing far less than profits through a cycle, so this stops a cyclical at the bottom of its cycle looking expensive only because its earnings are temporarily low (Deere: P/E 37 against a median of 17).
- **P/E divided by growth:** zero at 3, full marks at 1. Growth is capped at 50% a year, because no stock is cheap on 300% growth.

Other rules:

- **Repricing.** P/E is moved to today's price on every scanner run, but only if the result is within 2× of Finnhub's own figure. For some foreign listings EPS is in the home currency (one showed EPS 59,668), which would otherwise make the stock look absurdly cheap.
- **Loss-makers** have no earnings to value. They get at most 2.5 points, from P/S against their own history, and zero above 15× sales. A stock that has fallen from a bubble valuation is not cheap just because it is cheaper.

### Moat (40): the five questions

The first four are the four sources of a durable competitive advantage (the standard moat framework used in equity research). The fifth asks whether the advantage will last. Market share and pricing power are deliberately absent: they are what a moat produces, and the quality and growth axes already measure them from data, so asking about them again would count the same thing twice.

1. **Switching costs:** would a typical customer face real cost, risk or disruption in leaving?
2. **Network effects:** does the product become more valuable to each user as more users join?
3. **Intangibles:** does it own brands, patents, data, licences or permits a well-funded rival could not replicate in five years?
4. **Cost advantage:** can it make or deliver its product structurally cheaper than rivals, through scale, process know-how, location, or a market that only supports one or two players?
5. **Durability:** is it on the right side of the main technology shift in its market, as the disruptor or clearly immune, rather than the one being disrupted?

Question 5 is where innovation and disruption enter the score. It is two-sided: it rewards being the disruptor and marks down the disrupted. It is worth one question and no more, because a framework that paid extra for being on a hot theme would stop being a check on the themes.

Record the answers in `framework_scores.json`:

```json
"ANET": { "legacy": 91, "moat_answers": [true, false, false, true, true], "moat_date": "2026-11-20",
          "moat_reason": "EOS lock-in with hyperscalers; no network effect" }
```

A moat with no date, or a date older than 120 days, is marked with an asterisk on the dashboard.

## Funds and ETFs: a separate score

Moat, quality and growth describe a single business, not a basket of them. A fund is scored on a different question: is this the best way to own this exposure?

| Axis | Points | Source | Zero at | Full at |
|---|---|---|---|---|
| Cost | 25 | Expense ratio (TER) | 1.5% | 0.1% |
| Track record | 35 | 3-year annualised return against its benchmark, after fees | −6%/yr | +4%/yr |
| Consistency | 15 | Share of rolling 1-year windows (sampled monthly, last 3 years) that beat the benchmark | 30% | 70% |
| Concentration | 10 | Weight of the top 10 holdings | 70% | 30% |
| Conviction | 15 | Your view of the theme: Y 15, ? 7.5, N 0 | | |

The fund gate is 48, as before.

- **Track record** uses dividend-adjusted prices, so income funds (JEPI, SCHD) are not penalised for paying out. A window within half a percentage point of the benchmark counts as half a win, so an index fund tracking its own benchmark scores a neutral 7.5 on consistency.
- **Benchmarks** are the cheap alternative for the same money: SPY for US equity, QQQ for tech and thematic funds, URTH for global and regional equity, GLD for gold and miners, AGG for bonds. A thematic fund that has lagged QQQ for three years scores low on track record. That is the point: it has not paid for its higher fees and risk.
- **Thai feeder funds** are priced through their proxy ETF (SCB_GENO through ARKG, for example), and their own expense ratio is deducted from the proxy's return, because the feeder fund charges it on top.
- **Leveraged products** (TQQQ) score zero on concentration. Single-asset funds (gold, silver, bonds) score half.
- **Expense ratios for Thai funds are estimates** (`ter_source: "estimate"`) from typical SCB and KTAM fees. Replace them with the figure from each factsheet; ETF expense ratios are the published figures.
- **Conviction** starts at "?" for every fund. Set Y or N in `framework_scores.json` for the themes you hold a view on.
- Funds without enough price history (fewer than two years) or not in the scan fall back to the old total for those axes, marked provisional.

## Where each number comes from

For each axis the scorer uses the best source available and records which one it used:

- **data:** computed from `fundamentals.json`.
- **hand:** a hand sub-score in `framework_scores.json` (40 tickers have these from the original table), or the moat answers. Hand sub-scores are stored on the original rubric's scale (moat /30, quality /25, growth /20, valuation /20) and converted pro rata: a hand moat of 24/30 counts as 32/40.
- **legacy:** a placeholder derived from the old single total. The score is marked provisional.

Funds and ETFs (`"kind": "fund"`) use the fund score above.

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
- **Industry comparison is only as good as the peer group.** With 227 stocks, many industries have fewer than 5 peers and fall back to the absolute scale. Peer medians also move with the cycle: in a memory boom, DELL's 20% gross margin is judged against peers near 50%.
- **190 of 230 stocks still have no real moat judgement,** only a placeholder from the old total. Until those are answered, 40% of most scores is the old number.
- **Non-US listings, ETFs and funds stay hand-scored.**
