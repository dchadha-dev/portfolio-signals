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

**The score.** Strong run +25, Uptrend +25, Dip 20%+ +30: all three = 80 = BUY. Deep dip (25%+) +10. Near-misses score 15 instead (run 0.3–0.6, within 5% below the 200d MA, dip of 15–20%), so WATCH (60–79) means two conditions met and the third close. Insider/PEAD/sector overlays share a +25 cap and cannot move a score across a label boundary. v2 required gate + DFV V3 + an overlay to reach 80 and fired on 0.01% of S&P stock-days; the gate alone is open on about 1% of stock-days on this list.

**Not tested: insider, politician and PEAD overlays.** There is no historical archive of those inputs in the repo, so they could not be backtested. They remain in the live score on the strength of the published literature only, which is why they are capped. Two data issues to fix before trusting them: PEAD surprise scores are computed from as few as three quarters (CRM showed an SUE of 12), and the insider feed contains at least one implausible record (a TSM purchase at $71).

