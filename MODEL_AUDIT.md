# Model audit — November 2026

What the signal model can and cannot be shown to do, how that was tested, and what changed as a result.

## Summary

1. **The published validation numbers are artefacts.** 70% annual excess, t-statistics of 40–120 and deflated Sharpe ratios in the dozens come from five defects in `signal_scanner_validation_cpcv.py`, not from the signals.
2. **On unbiased data no buy or sell component shows a detectable edge** — not on ~2,300 liquid US stocks (2010–2017), not on point-in-time S&P 500 members (1996–2026). On this repo's own tickers the factor gate does look good over 2011–2026, but at least half of that is hindsight in the ticker list (section 7).
3. **What the backtests were really measuring is the ticker list.** The stocks in this repo's universe beat the average liquid stock by about 6–7 points a year over 2010–2017 *before any signal is applied*.
4. **The sell model is close to switched off.** Framework damping makes EXIT mathematically unreachable for 56 of 63 held stocks, and REDUCE for 28.

None of this says the portfolio's stock selection is bad. It says the timing layer on top of it has no evidence behind it, and the tooling was reporting evidence that did not exist.

## 1. Why the old statistics were wrong

| Defect | Effect |
|---|---|
| 66 "CPCV paths" re-slice the same days and 2 "seeds" change nothing; no parameter is ever fitted on a train split | every observation counted 22 times |
| Daily observations of 252-day forward returns treated as independent | adjacent days share 251/252 of their window; effective sample is ~250x smaller |
| Sharpe computed from 252-day returns, then multiplied by sqrt(252) | Sharpe overstated ~16x |
| `compute_pbo` returns the share of paths ranked below the median | 0.50 by construction; it measures nothing |
| Excess return measured against VOO on a hand-picked universe | the universe's own outperformance is credited to the signal |

The duplication and overlap alone inflate a t-statistic by roughly sqrt(22 x 250) ≈ 74. The reported t of 41.9 for the current model corresponds to about 0.6.

The one honest number already in `validation_cpcv_results.json` was `signal_vs_nosignal_spread`: **-18.16**. Inside the model's own universe, days with a buy signal did worse than days without one.

## 2. How it was re-tested

Data: every US-listed stock from January 2009 to March 2017 (the `pystock-data` archive, 6,246 symbols, including names that later delisted), with split and dividend adjustments rebuilt, bad ticks removed and a liquidity filter (price ≥ $5, median dollar volume ≥ $2M). Point-in-time S&P 500 membership from `fja05680/sp500`.

Method: the production signal formulas, applied to every stock every day. For each signal a **calendar-time portfolio**: enter the day after the signal, hold 63 or 252 trading days, equal weight. That gives an ordinary daily return series, so significance is not inflated by overlapping windows. Returns are measured against the equal-weight average of the same universe on the same days.

Scripts are in `research/`. The data is public but not committed.

## 3. Results

Excess return of each signal portfolio over its own universe, % per year, 252-day hold, 2010–2017:

| Signal | All liquid US stocks | Point-in-time S&P 500 | This repo's tickers (hindsight) |
|---|---|---|---|
| Factor gate (the +40 anchor) | -1.7 (t -0.5) | +2.5 (t +0.5) | +4.1 (t +0.8) |
| More than 20% below 252d high | -1.2 (t -0.9) | -1.0 (t -0.6) | +2.9 (t +1.7) |
| Near 252d high (the sell trigger) | +0.7 (t +1.3) | +0.1 (t +0.2) | +0.5 (t +0.7) |

Other components on the neutral universe, 63-day and 252-day holds:

| Signal | 63d | 252d |
|---|---|---|
| Factor + DFV V3 | -4.7 (t -0.9) | -1.9 (t -0.4) |
| Buy score ≥ 60 | -4.5 (t -0.8) | -1.7 (t -0.4) |
| Triple (up 20% in 63d) | -1.5 (t -0.9) | +0.4 (t +0.4) |
| PFD | +1.5 (t +0.9) | +0.6 (t +0.7) |
| Sell score ≥ 52 | +0.8 (t +0.5) | +1.0 (t +1.4) |

Nothing reaches |t| = 2. The sign of the factor gate flips from negative on the neutral universe to positive on the hand-picked one — the footprint of selection bias: "buy the dip" looks good when every name on the list is known to have recovered.

On 16 mega-cap winners with data through 2026, the factor gate earned -0.6%/yr (t -0.1) over the 16-name average in 2018–2026.

Pre-specified alternatives (12-1 momentum, nearness to the 52-week high, 12-month Sharpe, low volatility, an equal-weight composite) were also tested. None was significant either, and inside the S&P 500 all were near zero. **No replacement score was installed**, because nothing earned it.

Limits of this test: the sample is 2010–2017, a long bull market with no 2008, 2020 or 2022. Dip-buying does best in sharp rebounds, which the sample lacks. Seven years is also little data for a 252-day horizon. The right reading is "no evidence of an edge", not "proof of none".

## 4. Sell-side reach

The sell score is 0–100 and framework damping multiplies it by `1 - 0.5 x (FW/100)^1.5`. With every input at its maximum:

| Framework score | Highest reachable score | Strongest possible action |
|---|---|---|
| ≤ 57 | ≥ 78 | EXIT |
| 58–78 | 65–77 | REDUCE |
| ≥ 79 | ≤ 64 | TRIM |

On the current book of 63 directly held stocks, 56 can never reach EXIT and 28 can never reach REDUCE, under any market conditions. In practice even TRIM is out of reach: across 2012–2017, a stock with FW 68 would have scored TRIM on 0.009% of stock-days and one with FW ≥ 80 on none. The model also has no input for position size.

## 5. What changed in the code

- `validate_signals.py` — the test described above, built on the production functions, run monthly in CI. Writes `validation_honest.json` with a verdict per signal (SUPPORTED needs |t| ≥ 2 with both halves of the sample agreeing).
- `signal_ledger.py` — every label the scanner issues is logged with date and price and later scored against the scanner universe at 21/63/126/252 trading days. This is forward evidence, which hindsight cannot contaminate. `signal_ledger.json` carries a scoreboard by label.
- `sell_ceiling` per ticker in the payload, and a line on the Sell Guidance tab stating how many holdings can never reach REDUCE.
- CNN Fear & Greed: a failed fetch used to become a silent "50 / Neutral". The payload now carries `cnn_ok`, and the dashboard says when the value is a placeholder.
- SCB SET50 no longer receives a buy signal derived from SPY.
- Guidance text no longer calls a 63-day run-up a "Caution" while the score adds 10 points for it.
- `signal_scanner_validation_cpcv.py` is marked deprecated for decisions.

The buy score was then rebuilt around the one rule that held up — see section 7. Sell weights and thresholds are unchanged.

## 6. Open decisions

1. **Whether the buy score works going forward.** Section 7 is the best available evidence and it is not conclusive. `signal_ledger.json` will answer it; expect 12–24 months before the sample means anything.
2. **The sell model.** Either accept that it is informational, or replace it with rules that do not depend on forecasting — position-size bands and a thesis check.
3. **The framework score.** It is the only input that could carry real information and it has never been tested. The ledger will start measuring it; hand scores should be dated when changed.

## 7. Buy score v3 (12 November 2026)

**Data.** Survivorship-free S&P 500 daily prices and membership, 1996 to 10 Nov 2026 (`Johnbrick123/sp500-data`), which closes the 2017–2026 gap in the first pass. Method as in section 2: enter the day after a signal, hold 63 or 252 trading days, equal weight, compare with the equal-weight average of the same list. "Vol-matched" compares each signal name only with names in the same volatility third, because dipped stocks are volatile stocks and volatile stocks on a hand-picked list are mostly its winners.

**What was tested.** Twelve dip rules (fixed and volatility-scaled drawdowns, with and without the 200d MA, RSI and 12-month-run filters), a "pullback in a leader" score, and seventeen variants of the existing factor gate, on four lists: this repo's tickers, the 150 best S&P performers of each period (pure hindsight), 150 random survivors, and point-in-time S&P members.

**Result.** The existing gate — 20%+ below the 252d high, above the 200d MA, 12-month return/volatility above 0.6 — was the best rule tested. Nothing designed to replace it beat it.

| Gate, 252d hold, vol-matched excess %/yr (t) | 2011–2018 | 2019–2026 | 1997–2010 |
|---|---|---|---|
| This repo's tickers | +5.9 (1.0) | +17.0 (3.4) | n/a |
| Hindsight top 150 | +2.9 (0.7) | +11.4 (2.5) | +4.8 (1.6) |
| Random survivors | +2.3 (0.4) | +18.3 (3.2) | +2.6 (0.7) |
| Point-in-time S&P 500 | +0.9 (0.2) | +5.6 (1.1) | +0.9 (0.3) |

Read honestly: positive in every cell, significant only on lists built with hindsight and only in 2019–2026, small and not significant on the unbiased universe. On this repo's tickers it was positive in 12 of 16 calendar years, but 51% of the total came from five names (MRNA, GME, ENPH, BE, AMD). The rule is worth keeping as a timing aid for names already chosen; it is not proof of a tradable edge.

**What the refinement tests showed** (repo tickers, 2011–2026, 252d, vol-matched):

- The conjunction is the signal. Dip + uptrend without the strong run: +2.6 (t 1.3). Dip + strong run below the 200d MA: +0.4 (0.2). Strong run + uptrend with no dip: +0.1.
- Depth helps: 15% dip +2.9, 20% +9.2, 25% +28.0 (t 4.2). Same ordering on random survivors.
- DFV V3 adds nothing: gate with DFV V3 +8.7, gate without +10.2. v2 gave it 25 of the 80 points needed for a BUY.
- RSI timing, "first 21 days only", PFD, Triple, Banker Weak and RSI Bear: no effect or too rare to measure.
- Volatility-scaled drawdowns and the pullback-in-a-leader score were worse than the fixed 20% rule everywhere.

**The score.** Strong run +25, 5%+ above the 200d MA +25, Dip 20%+ +30: all three = 80 = BUY. Deep dip (25%+) +10. Near-misses score 15 instead (run 0.3–0.6, within 5% of the 200d MA either side, dip of 15–20%), so WATCH (60–79) means two conditions met and the third close. Insider/PEAD/sector overlays share a +25 cap and cannot move a score across a label boundary. v2 required gate + DFV V3 + an overlay to reach 80 and fired on 0.01% of S&P stock-days; the gate alone is open on about 1% of stock-days on this list.

**Second pass: five more features, tested inside the gate** (13 Nov). Each splits gate days in two; a feature earns a place only if the split points the same way on this repo's tickers, on random survivors and on point-in-time S&P members. 252d hold, vol-matched excess %/yr.

| Split of gate days | Repo tickers | Random survivors | PIT S&P 1997–2026 | Verdict |
|---|---|---|---|---|
| 5%+ above 200d MA vs 0–5% | +11.9 vs +3.5 | +11.4 vs +5.0 | +5.6 vs −0.7 | **Adopted** |
| Market 10%+ off its high vs not | +13.8 vs +9.2 | +2.5 vs +14.9 | +2.9 vs +1.3 | Inconsistent |
| Heavy volume vs normal | +6.2 vs +8.6 | +10.9 vs +8.2 | +2.1 vs +1.9 | Nothing |
| 1-day drop of 10%+ vs none | +19.3 vs +7.4 | +26.7 vs +4.9 | +0.6 vs +0.8 | Hindsight only |
| Fast dip (high < 63d ago) vs slow | +10.7 vs +8.3 | +7.9 vs +7.9 | not run | Nothing |

The one adopted change: the uptrend condition now needs the close to be 5% or more above the 200d MA. A stock that has dipped 20% and sits within 5% of its 200d MA tested at zero in all three lists and is now a WATCH. That removes roughly a quarter of the old gate's signals. The 1-day-shock result is the instructive failure: buying after an earnings-style gap looks excellent on any list of survivors and is worthless on the unbiased list, because the names that gapped down and never recovered are missing from the first two.

Extreme readings (25%+ dip together with 10%+ cushion) returned +37.5 on the repo's tickers and −4.6 on PIT members. The deeper the setup, the more the outcome depends on the stock being a long-run winner, which is the framework score's job, not the signal's.

**Not tested: insider, politician and PEAD overlays.** There is no historical archive of those inputs in the repo, so they could not be backtested. They remain in the live score on the strength of the published literature only, which is why they are capped. Two data issues to fix before trusting them: PEAD surprise scores are computed from as few as three quarters (CRM showed an SUE of 12), and the insider feed contains at least one implausible record (a TSM purchase at $71).


## 8. Framework score rebuild (13–14 November 2026)

**What was wrong.** The framework score gates every BUY and damps every sell, and was a hand-typed number per ticker.

- Sub-scores existed for 41 of 315 tickers. Their Portfolio Fit column, documented as 5 points, ran from 5 to 18 and made the parts add up to a total chosen first.
- The scanner and the table disagreed on 7 of those 41.
- Moat and quality correlated 0.88; valuation correlated +0.63 with moat, the opposite of what a valuation measure should do.
- 81% of scores were even numbers, clumped at 68 and 72 on either side of the gate at 70.
- Valuation was hand-scored quarterly, and no score carried a date.

**What changed.** Moat 40 + Quality 30 + Growth 20 + Valuation 10 = 100. Quality, growth and valuation are computed from Finnhub fundamentals (weekly `fundamentals_fetcher.py`); valuation is repriced on every scanner run. Moat is five dated yes/no questions. Fit is removed. Every score records the source of each part. Method and every threshold: `FRAMEWORK.md`.

**First live fetch (14 Nov): what it exposed.** Finnhub returned data for all 227 US stocks and Apple's figures passed the sanity check. The first scoring was wrong in ways the data made obvious, and each fix is now in the scorer and tested:

| Problem seen | Example | Fix |
|---|---|---|
| Missing inputs were rescaled away, so loss-makers scored on their good figures only | IONQ 36 → 65 | Missing inputs score zero; loss-makers capped at 60% on quality and growth |
| Price/sales below a bubble-era median scored as cheap | IONQ at 51× sales vs a 122× median | Loss-makers get at most a quarter of valuation, zero above 15× sales |
| Banks scored on gross margin and leverage, which they do not have | JPM quality 0 | Financials fall back to hand scores for quality and growth |
| Gross margin punished low-margin, high-turnover models | COST | Return on capital weighted 10, gross margin 4 |
| Debt/equity punished buyback companies with tiny book equity | MCD 41×, LOW 52× | Half points when return on capital and cash flow are strong |
| P/E against its own history punished cyclicals at a trough | DE P/E 37 vs median 17 | P/S against its own history added as a third valuation part |
| EPS reported in home currency for some listings | EPS 59,668 | Live P/E used only within 2× of Finnhub's P/E |
| Free-cash-flow margin unit errors | NBIS 3,571% | Above 100% treated as unknown |
| Valuation at 20 points scored most large tech near zero | AAPL, AMD, KLAC | Valuation cut to 10, moat raised to 40 |

**Scale.** Cut-offs carried across with the fitted line `new = −6.92 + 0.994 × old` (r 0.95 on the 40 hand-scored tickers): gate 70 → 63, Core 85 → 78, Marginal 55 → 48.

**Effect with live data.** 120 stocks passed the old gate; 87 pass the new one. 50 fall below it and 17 rise above it. Holdings that fall below: NBIS (87 → 52), AMAT (82 → 59), CPRT (80 → 61), MRVL (79 → 61), BRKB (78 → 49), TEAM (75 → 54), CRWV (72 → 40). The largest falls are slow growers and cyclicals near a trough (DE, TMO, HD); the largest rises are profitable fast growers the old hand scores had underrated (RDDT, SNDK).

**Not done.** The score is still unvalidated against returns. 190 stocks have no real moat judgement, so 40% of their score is still the old number. Growth rewards growth companies by design (full marks at 25% a year). Sell thresholds were not re-tuned for the new scale.

**Second revision (14 Nov), after review of NBIS and DELL.**

- *Losses were penalised three times* (quality cap, growth cap, valuation cap). The growth cap is removed. Loss-makers growing 30%+ on 50%+ gross margins are now judged on gross margin, growth and balance sheet instead of return on capital and cash flow, still capped at 60% of quality. NBIS 52 → 67, CRWV 40 → 61, NET 49 → 59; IONQ 34 → 42 and QUBT 25 → 33 stay well below the gate.
- *One template for every business model.* Gross and free-cash-flow margins are now scored half against industry peers (Finnhub industry, with an override for hardware makers that Finnhub files beside software).
- *Debt/equity is distorted by buybacks.* Interest coverage now leads where Finnhub reports it (209 of 227 stocks); a negative figure on a profitable company is read as net interest income.
- DELL moves only 47 → 49. Against hardware peers its margins are still well below the median (gross 20% against 49%, free cash flow 8% against 25%), and revenue grew 3.5% a year over three years. That is the data, not the template.

**Moat questions and funds (14 Nov).** The moat now asks the four sources of competitive advantage (switching costs, network effects, intangibles, cost advantage) plus durability: whether the company is on the right side of its market's technology shift. Market share and pricing power were dropped from the moat because the data axes already measure them. Answers for all 230 stocks were drafted by Claude and accepted; an "uncertain" answer scores half, and an unreadable one is rejected rather than counted as yes (previously any non-empty text counted as yes).

Funds no longer reuse the old single total. They have their own score (cost 25, track record against a benchmark 35, consistency 15, concentration 10, conviction 15), computed from dividend-adjusted prices on every scan. Expense ratios for Thai feeder funds are estimates until checked against factsheets.

