# Portfolio Signal & Framework Reference

## Table of Contents
1. [Overview](#1-overview)
2. [System Performance Summary](#2-performance)
3. [Framework Score — 5 Axes](#3-framework-score)
4. [Current Framework Scores](#4-current-scores)
5. [Buy-Side Signal Models](#5-buy-side)
6. [Sell-Side Signal Models](#6-sell-side)
7. [How the Systems Work Together](#7-combined)
8. [Exact Model Logic](#8-model-logic)
9. [Backtesting Methodology — V1 vs V2](#9-backtesting)
10. [Fundamental Layer](#10-fundamentals)
11. [Thai Fund Proxies](#11-thai-funds)
12. [Scoring Any New Ticker](#12-new-ticker)

---

## 1. Overview

Two systems work together on every position. Neither is sufficient alone.

| | Framework Score | Signal Models |
|---|---|---|
| Question | Is this business worth owning? | Is now a good time to act? |
| Inputs | Qualitative — moat, quality, growth, valuation, fit | Price, volume, RSI, momentum + yfinance fundamentals |
| Updates | Quarterly | Daily (6am + 8pm Bangkok via GitHub Actions) |
| Output | 0–100 conviction score | Buy score 0–100 + Sell score 0–100 |
| NVDA example | 93/100 — outstanding business | Near highs → sell score elevated |

Framework score prevents selling great businesses on temporary weakness. Signals prevent buying bad businesses because they look cheap. The combination of value zone entry (factor gate) + institutional momentum (DFV) + near-high exit is the complete cycle.

**Pipeline:**
```
GitHub Actions (daily 6am + 8pm Bangkok)
  → signal_scanner.py + sell_side_scorer.py
  → signals_payload.json committed to repo
  → Netlify auto-deploys
  → Dashboard loads fresh signals

GitHub Actions (monthly, first Monday)
  → signal_scanner_validation.py
  → Walk-forward CV across 24 random windows
  → validated_params.json committed
  → Daily scanner reads new params automatically
```

---

## 2. System Performance Summary

### Buy-Side (validated, 24 walk-forward windows, dual-seed)

| Signal | 252d mean sep | p-value | Win rate | Weight |
|---|---|---|---|---|
| Factor Value | +23.7% | 0.024 | 10/12 | 40 |
| Factor+DFV V3 ★ | +22.8% | 0.034 | 9/12 | 38 |
| Triple Composite | +9.1% | 0.088 | 7/12 | 15 |
| PFD Buy | +5.1% | 0.112 | 7/12 | 6 |
| DFV V3 standalone | −0.3% | 0.586 | — | **0 (removed)** |
| DFV V1 standalone | −1.3% | 0.716 | — | **0 (removed)** |

**Separation** = avg excess return vs VOO when signal fires minus when silent. Positive = signal correctly identifies outperformers.

### Sell-Side (validated, 24 walk-forward windows, dual-seed)

| Signal | 252d sep | p-value | Win rate | Weight |
|---|---|---|---|---|
| near_high | −15.8% | **0.000** | 11/12 | 35 |
| CMF dist @ high | −9.1% | **0.000** | — | 30 |
| SMA/ATR > 3.5 | −13.4% | 0.001 | — | 20 |
| rv_z2 (30d) | −1.4% at 30d | **0.000** | 17/22 | 10 |
| Banker Weak | +3.2% | 0.451 | — | **0 (wrong direction)** |
| Weekly RSI >75/80 | positive | — | — | **0 (buy continuation)** |
| Momentum exhaust | positive | — | — | **0 (buy continuation)** |

For sell signals: **negative separation = good** (flagged stocks underperformed VOO after signal).

### Optimal parameters (from grid search, validated 2026-05-17)

| Parameter | Value | Tested range |
|---|---|---|
| DIST_T | −0.15 | −0.08, −0.10, −0.12, −0.15 |
| DFV_LIFT | 2.5 | 2.5, 4.0, 5.5, 7.0 |
| QUALITY_T | 0.20 | 0.10, 0.15, 0.20 |
| p_threshold | 0.15 | — |

---

## 3. Framework Score — 5 Axes

### Axis 1 — Competitive Moat (30 pts)

What makes this business structurally unassailable over 5–10 years?

| Score | Evidence |
|---|---|
| 25–30 | Network effects compounding at scale OR switching costs making migration prohibitive OR unique technology with multi-year R&D lead time |
| 15–24 | Real competitive advantage but replicable with capital + time |
| 0–14 | Commodity or scale-only moat |

Examples: NVDA 28 (CUDA ecosystem — 4M+ developers), ASML 28 (EUV monopoly), BKNG 23 (network effects but OTA competition), TSLA 14 (EV moat eroding)

### Axis 2 — Financial Quality (25 pts)

Can the business convert revenue into durable profit efficiently?

| Score | Evidence |
|---|---|
| 20–25 | Gross margin >60% + FCF conversion >80% + ROIC >20% + minimal debt |
| 12–19 | Good margins but capital-intensive or moderate FCF conversion |
| 0–11 | Low margins, poor FCF, or leveraged balance sheet |

Distinct from growth — AAPL scores 22 (high quality, moderate growth); CRDO scores 15 (quality still scaling but growing fast).

### Axis 3 — Growth & Runway (20 pts)

Is there a large, expanding market AND a credible path to capture it?

| Score | Evidence |
|---|---|
| 16–20 | TAM expanding faster than company + multi-year backlog + structural AI/cloud/semiconductor tailwind |
| 10–15 | Good growth but cyclical risk OR mature market with share gains only |
| 0–9 | Single-digit growth or structurally declining |

### Axis 4 — Valuation Risk (20 pts)

*Raised from 15pts in previous version. Price paid matters.*

| Score | Evidence |
|---|---|
| 16–20 | PEG <1.5 OR >15% below own historical median multiple OR clear re-rating catalyst |
| 10–15 | Fair value — reasonable multiple relative to growth |
| 0–9 | Priced for perfection — PSR >20x with slowing growth, or consensus already pricing bull case |

### Axis 5 — Portfolio Fit (5 pts)

Does this add unique exposure or duplicate an existing position?

| Score | Situation |
|---|---|
| 4–5 | Unique sector/geographic exposure not replicated elsewhere |
| 2–3 | Moderate overlap |
| 0–1 | Heavy overlap — duplicates theme without adding diversification |

*Portfolio-dependent — changes when you add/remove positions.*

---

## 4. Current Framework Scores

| Ticker | Moat /30 | Quality /25 | Growth /20 | Val /20 | Fit /5 | **Total** |
|---|---|---|---|---|---|---|
| NVDA | 28 | 23 | 18 | 13 | 11 | **93** |
| ASML | 28 | 21 | 18 | 15 | 11 | **93** |
| CRDO | 27 | 15 | 19 | 14 | 18 | **93** |
| AVGO | 26 | 22 | 17 | 14 | 13 | **92** |
| TSM | 25 | 20 | 18 | 13 | 13 | **89** |
| ANET | 26 | 21 | 18 | 14 | 12 | **91** |
| DDOG | 24 | 20 | 18 | 13 | 15 | **90** |
| MELI | 23 | 18 | 19 | 14 | 16 | **90** |
| NBIS | 22 | 16 | 18 | 17 | 14 | **87** |
| AMD | 23 | 18 | 17 | 14 | 14 | **86** |
| BKNG | 23 | 20 | 15 | 13 | 14 | **85** |
| AMAT | 21 | 18 | 17 | 13 | 13 | **82** |
| MSFT | 26 | 22 | 15 | 11 | 8 | **82** |
| META | 24 | 20 | 16 | 12 | 10 | **82** |
| AMZN | 24 | 19 | 17 | 13 | 9 | **82** |
| GOOG | 24 | 20 | 17 | 12 | 10 | **83** |
| AAPL | 23 | 22 | 14 | 11 | 10 | **80** |
| CPRT | 21 | 19 | 16 | 13 | 11 | **80** |
| SHOP | 19 | 16 | 16 | 13 | 14 | **78** |
| INTU | 21 | 19 | 16 | 12 | 11 | **79** |
| RACE | 21 | 19 | 14 | 13 | 12 | **79** |
| NVO | 22 | 20 | 17 | 12 | 9 | **80** |
| PGR | 19 | 17 | 15 | 13 | 14 | **78** |
| NFLX | 20 | 18 | 14 | 12 | 12 | **76** |
| MU | 18 | 16 | 16 | 14 | 12 | **76** |
| TTD | 19 | 17 | 16 | 13 | 12 | **77** |
| UNH | 20 | 17 | 14 | 12 | 14 | **77** |
| BRKB | 22 | 19 | 13 | 13 | 11 | **78** |
| RELX | 20 | 18 | 15 | 13 | 11 | **77** |
| TEAM | 19 | 17 | 15 | 12 | 12 | **75** |
| FISV | 19 | 16 | 14 | 12 | 13 | **74** |
| CRWV | 17 | 13 | 17 | 10 | 15 | **72** |
| RMS.PA | 24 | 21 | 14 | 12 | 11 | **82** |
| MC.PA | 23 | 20 | 14 | 12 | 11 | **80** |
| ITX.MC | 19 | 17 | 14 | 14 | 12 | **76** |
| TM | 17 | 13 | 11 | 14 | 9 | **64** |
| O | 15 | 14 | 11 | 14 | 11 | **65** |
| ADBE | 17 | 17 | 11 | 8 | 6 | **59** |
| COIN | 14 | 10 | 16 | 8 | 10 | **58** |
| TSLA | 14 | 11 | 13 | 7 | 5 | **50** |
| TQQQ | 7 | 5 | 9 | 4 | 9 | **34** |

**Thresholds:** ≥85 = core keeper. 70–84 = good, monitor. 55–69 = marginal, consider rotating. <55 = sell candidate.

---

## 5. Buy-Side Signal Models

### Validated weights (from walk-forward CV, 24 windows)

```
Factor gate active         +40   backbone — 4/4 horizons
Factor+DFV V3 combined     +38   best entry signal — 4/4 horizons
Triple Composite (63d>20%) +15   momentum confirmation — 4/4
PFD Buy                    +6    supporting — 3/4 horizons
─────────────────────────────────────────────────────
Sum if all active         = 99   (clean — no arbitrary cap)
+ Fundamental boost        0–15  (when factor gate active)
- Banker Weak penalty       −15  (institutional exit reduces conviction)
Capped 0–100
```

**Buy signal thresholds:** Score ≥60 = BUY. Score 30–59 = watchlist. Score <30 = HOLD.

---

## 6. Sell-Side Signal Models

### Validated weights (walk-forward CV, 24 windows, dual-seed)

```
near_high (dist > -5%)     +35   stock within 5% of 252d high — value thesis exhausted
CMF_dist @ high            +30   institutional distribution while near highs — strongest signal
SMA/ATR > 3.5              +20   price >3.5 ATRs above 200d MA — extreme extension
rv_z2 (short-term)         +10   vol climax: valid 30-126d, flips at 252d
────────────────────────────────────────────────────────────────────
Max stock-only score:       95
+ CNN greed >75            +15   (>90 = +25 instead)
+ S&P500 >15% above 200d   +10
+ Buffett proxy extended    +10
+ Sector extension          +10   (max 1 per ticker)
Capped 0–100
```

**Sell thresholds:** TRIM ≥35 · REDUCE ≥55 · EXIT ≥70

**Portfolio cap:** Max 10% of positions flagged at TRIM+ at any time. Prevents mass sell signals in bull markets.

### Removed signals (wrong direction — stocks outperform after these fire)

| Signal | 252d sep | Reason removed |
|---|---|---|
| Weekly RSI >75/80 | +8.2% | Buy continuation — stocks keep rising |
| Momentum exhaust 20%/30% | +15.3% | Same — momentum continues |
| Banker Weak (as sell) | +3.2% | Stocks outperform after WB fires |
| BRED (2+ components) | +3.0% | Wrong direction |
| Framework score <55 | +12.7% | Cheap stocks outperform |

---

## 7. How the Systems Work Together

### Buy: signal × framework

| Factor+DFV V3 | Fundamentals | Framework | Action |
|---|---|---|---|
| ✓ active | Strong | ≥80 | **Full conviction add** |
| ✓ active | Weak | ≥80 | Buy — trust framework |
| Factor only | Strong | ≥80 | Watchlist — wait for DFV |
| ✓ active | Any | <55 | **Do not buy — poor business** |
| No signal | — | ≥80 | Hold — not an entry point |

### Sell: signal × framework

| near_high | CMF dist | Framework | Action |
|---|---|---|---|
| — | — | ≥80 | Hold |
| ✓ | — | ≥80 | TRIM — value thesis exhausted, business strong |
| ✓ | ✓ | ≥80 | REDUCE |
| ✓ | ✓ | 60–79 | EXIT |
| ✓ | ✓ | <55 | **EXIT immediately** |

---

## 8. Exact Model Logic

### Factor Value (`f`)

```python
high252  = close.rolling(252).max().shift(1)   # shift avoids lookahead
dist     = (close - high252) / high252
ma200    = close.rolling(200).mean()
trend    = (close - ma200) / ma200
quality  = (ret_252d / vol_252d / 3).clip(0, 1)  # Sharpe/3

f = (dist < -0.15) AND (trend > 0) AND (quality > 0.20)
```

DIST_T=−0.15: stock must be >15% below 252d high. QUALITY_T=0.20: Sharpe must exceed 0.60 (=0.20×3). shift(1) on high252 prevents the current bar from being included in its own high calculation.

### DFV V3 (`dfv3`) — Pine Script exact match

```python
rsi40    = wilder_rsi(close, 40)
hm_rsi   = (0.7 * (rsi40 - 30)).clip(0, 20)      # hot money RSI: 0=oversold, 20=max
hm_floor = hm_rsi.rolling(10).min().shift(1)      # 10-day floor
hm_lift  = hm_rsi - hm_floor
dfv3     = hm_lift > 2.5                          # validated: DFV_LIFT=2.5
```

### DFV V1 (`dfv1`) — Pine Script original buy signal

```python
dfv1 = (hm_rsi > hm_prev) AND (hm_prev >= 0) AND (hm_prev <= 5)
# Fires on single bar when hm_rsi turns up from 0-5 zone (RSI40 was 30-37)
```

### Factor+DFV V3 combined (`fdfv3`)

```python
fdfv3 = f AND dfv3
# Best entry: value zone + quality + institutional re-entry momentum
```

### Banker Weak (`wb`) — Pine Script exact match

```python
rsi47      = wilder_rsi(close, 47)
banker_rsi = (1.5 * (rsi47 - 51)).clip(0, 20)
wb         = (banker_rsi_prev >= 20) AND (banker_rsi < 20)
# Fires when institutional RSI drops from max — used as BUY penalty only (-15)
# Validated as SELL signal: p=0.451, +3.2% sep → wrong direction, not a sell trigger
```

### PFD Buy (`pfd`)

```python
pfd = ((ret252 - 2*ret126) > 0.05) AND (quality > QUALITY_T * 2)
# Fires when 1yr return compressed vs recent 6mo — accumulation pattern
```

### Near 252d High — primary sell signal

```python
near_high = dist > -0.05   # within 5% of 252d high
# Exact inverse of factor gate. Validated: -15.8% at 252d, p=0.000, 11/12 windows
```

### SMA/ATR Distance — sell signal

```python
tr       = max(high-low, |high-prev_close|, |low-prev_close|)
atr14    = tr.rolling(14).mean()
sma_atr  = (close - sma200) / atr14
# sell when sma_atr > 3.5: price >3.5 ATRs above 200d MA
# Validated: -13.4% at 252d, p=0.001
```

### CMF (Chaikin Money Flow) — sell signal

```python
mfm      = ((close - low) - (high - close)) / (high - low)
cmf_20   = (mfm * volume).rolling(20).sum() / volume.rolling(20).sum()
# Negative CMF while near_high = institutions distributing at highs
# Validated: -9.1% at 252d, p=0.000, most consistent sell signal (2/4 horizons)
```

### RV Z-Score — short-term sell signal

```python
rv20     = log_returns.rolling(20).std() * sqrt(252)  # annualised 20d vol
rv_mean  = rv20.rolling(756).mean()                   # 3yr average
rv_std   = rv20.rolling(756).std()
rv_z     = (rv20 - rv_mean) / rv_std
rv_z2    = rv_z > 2.0
# Validated SHORT-TERM: -1.4% at 30d p=0.000, 17/22 windows, CI=[-2.2%,-0.8%]
# Flips to +9.7% at 252d — only actionable when cash held 1-3 months
```

---

## 9. Backtesting Methodology — V1 vs V2

### V1 (deprecated) — single fixed split

One fixed 7yr train / 3yr test window. Single result per signal with no uncertainty estimate. Entirely determined by which 3 years happened to be the test window. Factor+DFV V3 showed +62% at 504d — plausible but one data point.

### V2 (current) — walk-forward cross-validation

| | V1 | V2 |
|---|---|---|
| Windows | 1 fixed split | 24 random (seed=42 + seed=99) |
| Train length | Fixed 7yr | Random 1.5–4yr |
| Test length | Fixed 3yr | Random 6–18mo (min 30d) |
| Purge gap | None | 20 days (prevents lookahead) |
| Uncertainty | None | Mean ± std, 95% bootstrap CI |
| p-value | Not reported | t-test vs zero, p<0.15 required |
| Parameters | Fixed arbitrary | Grid searched |
| Factor+DFV V3 504d | +62% (one window) | +54.6% ±92.9% (24 windows) |

**How windows are generated:** Random non-overlapping test windows across 10yr history. Each tagged bull/bear/sideways by VOO return. Current mix: 8 bull / 3 sideways / 1 bear — known limitation.

**Parameter grid search:** DIST_T × DFV_LIFT × QUALITY_T across 36 combinations. Best = highest mean separation on Factor+DFV V3 at 252d, statistically significant only. Re-runs monthly.

**Metrics per signal:** Mean separation · std · 95% bootstrap CI · p-value (t-test) · window win rate · consistency (horizons significant in correct direction).

**p-value threshold p<0.15:** Relaxed from standard p<0.05. Appropriate for financial signal research — low signal-to-noise ratio means p<0.05 rejects genuinely real signals. Walk-forward CV already controls false positive rate by averaging across 24 independent windows.

### Limitations

1. Survivorship bias — universe excludes delisted stocks
2. Transaction costs ignored — low-frequency signals limit this concern
3. Regime concentration — 8/12 windows are bull markets
4. High std at 504d (±92.9%) — 252d is the most reliable horizon
5. ~76 direct tickers — meaningful but not a full market cross-section

---

## 10. Fundamental Layer

Added May 2026 via yfinance `.info`. Adds up to +15 to buy score when factor gate is active.

| Component | Max weight | Full score trigger |
|---|---|---|
| Revenue growth (TTM YoY) | 0.30 | >50% |
| Gross margin | 0.25 | >60% |
| Earnings growth (quarterly) | 0.20 | >50% |
| Analyst upside (≥3 analysts) | 0.15 | >30% vs current |
| PEG ratio | 0.10 | <0.75 |

Fundamental boost only applies when factor gate is active — fundamentals confirm the value thesis, they don't replace timing discipline. No penalty if yfinance has no data (ETFs, foreign-listed).

---

## 11. Thai Fund Proxies

| Thai Fund | Proxy | Sectors |
|---|---|---|
| SCB S&P500 (A) | VOO | us_equity, broad |
| SCB Nasdaq (A) | QQQ | us_equity, tech |
| SCB Semiconductor (A) | SMH | semiconductor |
| SCB World (A) | URTH | global, broad |
| SCB Gold (A) | GLD | gold, commodity |
| SCB Nikkei 225 (A) | EWJ | japan |
| SCB Dow Jones (A) | DIA | us_equity, dividend |
| SCB Asian EM (A) | AAXJ | asia, em |
| SCB Fintech (A) | QQQ | fintech |
| SCB Innovation (A) | QQQ | innovation |
| SCB Genomic Rev (A) | XLV | healthcare, biotech |
| SCB China Tech (A) | QQQ | china, tech |
| SCB EV & Mobility (A) | QQQ | ev, auto |
| SCB US Business (A) | SPY | us_equity |
| KTAM India (A) | INDA | india, em |
| KTAM World Equity (A) | URTH | global, broad |
| KTAM World Tech AI (A) | QQQ | ai, tech |
| KTAM Blockchain (A) | QQQ | crypto, blockchain |
| KTAM Technology (A) | QQQ | tech, global |
| KTAM Global ESG (A) | URTH | esg, global |

Signal on proxy = signal applies to Thai fund. KTAM India (INDA) is the most uniquely positioned — genuine diversification. Most others overlap significantly with direct holdings.

---

## 12. Scoring Any New Ticker

1. **Moat /30** — Network effects, switching costs, proprietary tech, regulatory barriers
2. **Financial Quality /25** — Gross margin, FCF conversion, ROIC, debt level
3. **Growth & Runway /20** — Revenue CAGR, TAM expansion, secular vs cyclical
4. **Valuation Risk /20** — PEG vs peers, distance from historical median multiple
5. **Portfolio Fit /5** — Unique exposure vs what you already hold

Score ≥85: core keeper. 70–84: good. 55–69: marginal. <55: sell candidate.

Then overlay signal models for entry/exit timing. Strongest setup: framework ≥80 + Factor+DFV V3 active + strong fundamentals = full conviction add.
