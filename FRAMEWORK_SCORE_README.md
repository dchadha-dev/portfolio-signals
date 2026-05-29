# Portfolio Signal System — README
**Last updated:** 2026-05-29
**Dashboard:** https://portfolio-signals1.netlify.app
**Repo:** https://github.com/dchadha-dev/portfolio-signals

---

## System Overview

A daily quantitative signal scanner running on ~260 tickers (held + candidates + proxy ETFs). Signals are written to `signals_payload.json`, committed to GitHub, and fetched directly by the dashboard (no Netlify redeploy required for data updates).

---

## Architecture

```
GitHub Actions (6am + 6pm Bangkok)
    └── signal_scanner.py
            ├── fetch_history()          — yfinance 5yr OHLCV
            ├── compute_signals()        — buy signals per ticker
            ├── sell_side_scorer.py      — continuous sell scoring + sector sentiment
            ├── insider_signals.json     — weekly alternative data overlay
            ├── pead_signals.json        — weekly PEAD/SUE overlay
            └── signals_payload.json → committed to GitHub → Dashboard fetches via API

GitHub Actions (Monday 8am Bangkok)
    └── weekly_insider_signals.yml
            ├── pead_signals_fetcher.py  — Finnhub earnings calendar + SUE
            └── insider_signals_fetcher.py — EDGAR Form 4 + Finnhub congressional
```

Dashboard fetches `signals_payload.json` directly from GitHub (raw URL → API → Netlify fallback). Netlify only redeploys when `index.html` changes, saving ~900 credits/month.

---

## File Reference

| File | Purpose | Triggered by |
|---|---|---|
| `signal_scanner.py` | Daily engine — fetches prices, computes signals, writes payload | Schedule + push |
| `sell_side_scorer.py` | Sell-side scoring + sector sentiment model | Called by scanner |
| `insider_signals_fetcher.py` | Weekly EDGAR Form 4 + Finnhub congressional | Monday schedule |
| `pead_signals_fetcher.py` | Weekly PEAD/SUE signal fetcher | Monday schedule |
| `signal_scanner_validation_cpcv.py` | CPCV model validation harness | Manual |
| `index.html` | Dashboard (static shell — data from GitHub) | Push only |
| `signals_payload.json` | Scanner output, read by dashboard | Written by scanner |
| `insider_signals.json` | Weekly insider/politician data | Written by fetcher |
| `pead_signals.json` | Weekly PEAD signals with expiry dates | Written by fetcher |
| `validation_cpcv_report.html` | CPCV HTML report | Written by validator |
| `validation_cpcv_results.json` | CPCV JSON results | Written by validator |
| `FRAMEWORK_SCORE_README.md` | Framework score documentation | Manual |

### GitHub Actions Workflows

| Workflow | Schedule | Purpose |
|---|---|---|
| `daily_scanner.yml` | 6am + 6pm Bangkok | Main scanner run |
| `weekly_insider_signals.yml` | Monday 8am Bangkok | PEAD + EDGAR + Finnhub fetch |
| `cpcv_validation.yml` | Manual only | Model validation |

---

## Buy Signal Model

### Signal Architecture (current deployed model)

| Signal | Points | CPCV Validated | Notes |
|---|---|---|---|
| Factor gate (dist + trend + quality) | +40 | +29.2% ann, DSR 13.7 | Core entry gate |
| Factor + DFV V3 (fdfv3) | +25 | +38.5% ann, DSR 8.84 | Strongest — do not demote |
| PFD buy | +8 | +11.3% ann, DSR 33.5 | Reduced from +20 |
| Triple composite (63d >20%) | +10 | +18.6% ann, DSR 42.1 | Momentum |
| DFV V3 alone (no factor) | +5 | — | Tie-breaker only |
| Insider single buy (≥$15K, code P) | +10 | Literature CMF 2012 | 60d lookback |
| Insider cluster (2+ insiders) | +20 | Literature CMF 2012 | 60d lookback |
| Politician single buy | +5 | Literature | 45d hard cap on lag |
| Politician cluster (2+ within 7d) | +10 | Literature | Committee relevance considered |
| PEAD (SUE > 1.5) | +10 | Bernard-Thomas 1989 | 60d expiry from announce date |
| Sector entry signal (all 4 conditions) | +8 | — | Macro+trend+dip+breadth |
| Sector trend only | +3 | — | RS above 200d SMA only |
| Banker Weak penalty | -20 | — | Institutional RSI divergence |
| RBear penalty | -10 | — | Price high, RSI not confirming |

**Buy threshold:** score ≥ 80 → BUY | score ≥ 60 → WATCH

### Factor Gate Components

```
dist    = (close - 252d_high.shift(1)) / 252d_high  < -0.20
trend   = (close - 200d_MA) / 200d_MA               >  0.00
quality = sharpe_proxy(close, window=252)            >  0.20
```

### VIX-Gated Buy Cap (replaces CNN)

| VIX z-score | Strong cap | Regular cap |
|---|---|---|
| z > 2 (Extreme Fear) | 6 | 6 |
| z > 1 (Fear) | 4 | 5 |
| -1 to 1 (Neutral) | 3 | 3 |
| z < -1 (Greed) | 2 | 2 |
| z < -2 (Extreme Greed) | 1 | 1 |

**Macro filter:** if SPY < 200d SMA, all caps reduced by 1.

---

## Sell Signal Model

### Continuous Scoring (0–100), FW-damped

| Signal | Max pts | Validated |
|---|---|---|
| Near 252d high (linear, dist > -10%) | 35 | -15.8% at 252d, p=0.000 |
| CMF distribution at high | 30 | -9.1% at 252d, p=0.000 |
| ATR extension (>2 ATRs above 200d MA) | 20 | -13.4% at 252d, p=0.001 |
| RV z-score >1 | 10 | -1.4% at 30d, p=0.000 |

### Action Thresholds (post-FW damping)

| Damped score | Action |
|---|---|
| <52 | HOLD |
| 52–64 | TRIM |
| 65–77 | REDUCE |
| ≥78 | EXIT |

### Framework Score Damping

```
fw_damp = 1.0 - 0.50 * (fw/100)^1.5
```

| FW score | Damping | Raw 100 → damped |
|---|---|---|
| 68 (DELL) | ×0.720 | 72 → REDUCE |
| 80 (AAPL) | ×0.642 | 64 → TRIM |
| 90 (DDOG) | ×0.573 | 57 → TRIM |
| 93 (NVDA) | ×0.552 | 55 → TRIM |

### VIX Sell Multiplier (inverted — high fear dampens sells)

| VIX z-score | Multiplier |
|---|---|
| z > 2 (panic) | ×0.75 |
| z > 1 (fear) | ×0.90 |
| neutral | ×1.00 |
| z < -1 (greed) | ×1.15 |
| z < -2 (extreme greed) | ×1.30 |

---

## Sector Sentiment Model

Four-condition hierarchy — ALL must be true for entry signal (+8pts buy):

1. **Macro filter:** SPY > 200d SMA (regime gate)
2. **Trend check:** Sector_ETF / SPY RS line > its 200d SMA
3. **Dip detection:** Sector price Z-score < -2.0 (20d rolling)
4. **Confidence check:** Sector 10d EMA slope > 0 (breadth proxy)

All conditions use `shift(1)` — no look-ahead bias. Trend-only (conditions 1+2) → +3pts.

---

## Framework Score (0–100)

| Axis | Weight | Notes |
|---|---|---|
| Competitive Moat | /30 | Qualitative — largest weight |
| Financial Quality | /25 | Earnings consistency, margins |
| Growth & Runway | /20 | TAM, reinvestment opportunity |
| Valuation Risk | /20 | Overvaluation penalty |
| Portfolio Fit | /5 | Redundancy check |

Key scores: NVDA/ASML/CRDO: 93 · AVGO: 92 · ANET: 91 · DDOG: 90 · LLY/MA/V: 88 · NBIS: 87 · BKNG: 85 · NOW: 86

---

## Alternative Data Signals (live, not backtested)

### EDGAR Form 4 — Corporate Insider Buying
- Source: SEC EDGAR submissions API (free, no key required)
- Method: `.txt` full submission file → extract `<XML>` block → parse X0609 + older schemas
- Filter: Transaction code P, non-10b5-1, min $15K, 60d lookback
- Signal: Single insider +10pts, cluster (2+ insiders same ticker, 60d) +20pts
- Literature: Cohen-Malloy-Pomorski (2012): 9.8% VW / 21.6% EW annualised alpha
- Latest: SPGI cluster (3 buyers $2.5M), MSCI CEO $1.97M, SOFI CEO $498K, TSM VP $140K

### Politician Trading — Finnhub Congressional
- Source: Finnhub `/stock/congressional-trading` (existing FINNHUB_TOKEN)
- Filter: Buys only, 45-day hard cap on reporting lag, universe tickers only
- Signal: Single politician +5pts, cluster (2+ within 7d) +10pts
- Note: Capitol Trades `api.capitoltrades.com` does not resolve from GitHub Actions (DNS failure). Backlog: Quiver Quantitative ($25/mo) for reliable congressional data.

### PEAD/SUE — Post-Earnings Announcement Drift
- Source: Finnhub earnings calendar + EPS history (free tier)
- Method: Two-pass — calendar endpoint + direct EPS check for remaining tickers (14d lookback)
- SUE = (actual - estimate) / std_dev_surprises, floor std_dev = 0.02
- Signal: SUE > 1.5 → +10pts, expires 60 days from announce date
- Literature: Bernard-Thomas (1989), Daniel-Hirshleifer-Sun (2020)
- Latest: NVDA SUE=3.89 (expires 2026-07-19), AMAT SUE=2.92 (expires 2026-07-13)

---

## CPCV Validation Framework

### Methodology
- C(12,2) = 66 combinatorial paths × 2 seeds = 132 total paths per ticker
- Purge gap: 20 trading days
- Forward return horizon: 252 days
- Signal inclusion: p < 0.15, DSR > 0 required

### Deploy Decision Gates
1. Proposed DSR ≥ current DSR × 0.90
2. Proposed annualised excess ≥ current × 0.90
3. Proposed PBO ≤ current PBO × 1.10

Raw Sharpe excluded — inflated by Sharpe-proxy quality gate in current model.

### Latest Results (2026-05-25, full run)

| Metric | Current | Proposed (GP-proxy) |
|---|---|---|
| Ann. excess vs VOO | 69.72% | 15.21% |
| DSR | 52.660 | 6.082 |
| Hit rate | 62.9% | 44.7% |

**Status: Proposed model HOLD — do not deploy.** GP proxy fires 3× more signals but at much lower quality individually. Current model every BUY score≥80 requires fdfv3.

New metrics added: Information Ratio (per-decision risk-adj return) and IC (correlation of score with forward excess return).

---

## Dashboard Signal Dots

| Dot | Meaning |
|---|---|
| ★★ | Factor + DFV V3 (strongest buy) |
| ★ | Factor gate only |
| ◆ | PFD buy signal |
| 3x | Triple (63d >20%) |
| 🔑 | Single insider buy |
| 🔑🔑 | Cluster insider buy (2+ insiders) |
| 🏛 | Politician buy (lag shown in tooltip) |
| 🏛🏛 | Cluster politician buy |
| ⚡ | PEAD: SUE >1.5 (tooltip shows score + expiry) |
| 📡 | Sector entry (all 4 conditions, bright = full, dim = trend only) |
| ⚠H | Within 5% of 252d high |
| ⚠C | CMF distribution at high |
| ⚠A | ATR overextension |
| WB | Banker Weak |

---

## Known Issues

| Issue | Status | Notes |
|---|---|---|
| AIQG / QNTM.L | Permanent NO DATA | LSE-listed, yfinance gap. SKIP_SIGNAL set. |
| CRWV | Occasional NO DATA | IPO March 2025. Retry logic in place. |
| FLAX | NO DATA | LSE-listed, no yfinance support |
| BRKB | Fixed | Alias BRK-B applied |
| Capitol Trades DNS | Disabled | api.capitoltrades.com fails from GitHub Actions |
| raw.githubusercontent.com | CORS in browser | Private repo — GitHub API fallback works |

---

## Backlog (prioritised)

| Item | Priority | Notes |
|---|---|---|
| Politician signals via Quiver Quant | HIGH | $25/mo, reliable congressional data |
| Make repo public | HIGH | Eliminates PAT requirement, raw URL works |
| QUALITY_T threshold tuning | MEDIUM | GP proxy fires 3× more — raise from 0.20 if deploying proposed |
| Framework reweight | MEDIUM | Quality 30 / Valuation 25 / Growth 20 / Moat 20 / Fit 5 |
| Insider buying backtest | MEDIUM | Monitor live 6mo first, then reassess |
| Earnings revision momentum | MEDIUM | Chan-Jegadeesh-Lakonishok (1996) |
| Short-interest filter | LOW | Asquith-Pathak-Ritter (2005) |

---

## Key References

- Jegadeesh-Titman (1993): momentum, JF 48:65-91
- George-Hwang (2004): 52-week high momentum, JF 59:2145-2176
- Novy-Marx (2013): gross profitability, JFE 108:1-28
- Asness-Frazzini-Pedersen (2019): QMJ, RAS 24:34-112
- Cohen-Malloy-Pomorski (2012): insider trading, JF 67:1009-1043
- Bernard-Thomas (1989): PEAD, JAR
- Daniel-Hirshleifer-Sun (2020): PEAD revisited
- Harvey-Liu-Zhu (2016): multiple testing, RFS 29:5-68
- López de Prado (2018): CPCV, JPM 44:120-133
- Bailey-López de Prado (2014): deflated Sharpe, JPM 40:94-107
