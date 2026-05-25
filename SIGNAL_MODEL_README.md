# Portfolio Signal System — README
**Last updated:** 2026-05-25  
**Dashboard:** https://portfolio-signals1.netlify.app  
**Repo:** https://github.com/dchadha-dev/portfolio-signals

---

## System Overview

A daily quantitative signal scanner running on 289 tickers (78 held + 197 candidates + proxy ETFs). Signals are written to `signals_payload.json`, deployed via Netlify, and displayed on a live dashboard.

---

## Architecture

```
GitHub Actions (6am + 6pm Bangkok)
    └── signal_scanner.py
            ├── fetch_history() — yfinance 5yr OHLCV
            ├── compute_signals() — buy signals per ticker
            ├── sell_side_scorer.py — continuous sell scoring
            ├── insider_signals.json — weekly alternative data overlay
            └── signals_payload.json → Netlify → Dashboard
```

---

## File Reference

| File | Purpose | Triggered by |
|---|---|---|
| `signal_scanner.py` | Daily engine — fetches prices, computes signals, writes payload | Schedule + push |
| `sell_side_scorer.py` | Sell-side scoring module | Called by scanner |
| `signal_scanner_validation_cpcv.py` | CPCV model validation harness | Manual |
| `insider_signals_fetcher.py` | Weekly EDGAR + Capitol Trades fetcher | Schedule (Monday) |
| `index.html` | Netlify dashboard | Push (no scanner trigger) |
| `signals_payload.json` | Scanner output, read by dashboard | Written by scanner |
| `insider_signals.json` | Weekly alternative data, read by scanner | Written by fetcher |
| `validation_cpcv_report.html` | CPCV validation HTML report | Written by validator |
| `validation_cpcv_results.json` | CPCV validation JSON results | Written by validator |
| `validated_params.json` | Monthly walk-forward parameter refit | Monthly workflow |

### GitHub Actions Workflows (`.github/workflows/`)

| Workflow | Schedule | Purpose |
|---|---|---|
| `daily_scanner.yml` | 6am + 6pm Bangkok | Main scanner run |
| `weekly_insider_signals.yml` | Monday 8am Bangkok | EDGAR + Capitol Trades fetch |
| `cpcv_validation.yml` | Manual only | Model validation |
| `signal_scanner_validation.py` | First Monday monthly | Parameter refit |

---

## Buy Signal Model

### Signal Architecture (current deployed model)

| Signal | Points | Validated | Notes |
|---|---|---|---|
| Factor gate (dist + trend + quality) | +40 | +29.2% ann, DSR 13.7 | Core entry signal |
| Factor + DFV V3 (fdfv3) | +25 | +38.5% ann, DSR 8.84 | Strongest signal — do not demote |
| PFD buy | +8 | +11.3% ann, DSR 33.5 | Reduced from +20, still valid |
| Triple composite (63d >20%) | +10 | +18.6% ann, DSR 42.1 | 3-month momentum confirmation |
| DFV V3 alone (no factor) | +5 | — | Weak, tie-breaker only |
| Banker Weak penalty | -20 | — | RSI divergence |
| RBear penalty | -10 | — | Price high, RSI not confirming |
| Insider buying boost | +10/+20 | Literature (CMF 2012) | Single/cluster, capped at +25 combined |
| Politician buy boost | +8 | Literature | Capitol Trades, 45d lookback |
| **Max combined boost** | **+25** | — | Cannot create signal alone |

### Factor Gate Components

```python
dist     = (close - 252d_high.shift(1)) / 252d_high  < -0.20
trend    = (close - 200d_MA) / 200d_MA                > 0.00
quality  = gross_profitability_proxy(close, window=252) > 0.20
```

### Gross Profitability Proxy (proposed model — pending CPCV deploy decision)

Replaces the Sharpe-proxy quality gate. Three vectorised components:
- **Return consistency** (0.40 weight): fraction of up-days in 252d window
- **Drawdown resilience** (0.35 weight): current drawdown from rolling high, normalised
- **Trend smoothness** (0.25 weight): R² of log-price vs linear time index

Validated: factor_gate_proposed DSR 23.541 vs current 13.712 — materially better.

### CNN Fear & Greed — Buy Cap

| CNN Score | Strong buy cap | Regular buy cap |
|---|---|---|
| <20 Extreme Fear | 6 | 6 |
| 20–40 Fear | 4 | 5 |
| 40–60 Neutral | 3 | 3 |
| 60–80 Greed | 2 | 2 |
| >80 Extreme Greed | 1 | 1 |

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

```python
fw_damp = 1.0 - 0.50 * (fw/100)^1.5
```

| FW score | Damping | Raw 100 → |
|---|---|---|
| 68 (DELL) | ×0.720 | 72 → EXIT |
| 80 (AAPL) | ×0.642 | 64 → TRIM |
| 90 (DDOG) | ×0.573 | 57 → TRIM |
| 93 (NVDA) | ×0.552 | 55 → TRIM |

### CNN-Aware Sell Cap (% of held positions)

| CNN Score | Cap % | ~Max signals (78 held) |
|---|---|---|
| <20 Extreme Fear | 5% | ~4 |
| 20–40 Fear | 8% | ~6 |
| 40–60 Neutral | 10% | ~8 |
| 60–80 Greed | 15% | ~12 |
| >80 Extreme Greed | 20% | ~15 |

---

## Framework Score (0–100)

Five-axis scoring used to gate buys (FW ≥70 required) and damp sells.

| Axis | Weight | Notes |
|---|---|---|
| Competitive Moat | /30 | Qualitative — largest weight by design |
| Financial Quality | /25 | Earnings consistency, margins |
| Growth & Runway | /20 | TAM, reinvestment opportunity |
| Valuation Risk | /20 | Overvaluation penalty |
| Portfolio Fit | /5 | Redundancy check |

**Key scores:** NVDA/ASML/CRDO: 93 · AVGO: 92 · ANET: 91 · DDOG: 90 · NBIS: 87 · LLY/MA/V: 88 · BKNG: 85

**Reweight note (backlog):** Research suggests Quality 30 / Valuation 25 / Growth 20 / Moat 20 / Fit 5 better aligns with Novy-Marx (2013) and AQR QMJ factor evidence. Not yet deployed.

---

## Alternative Data Signals (live, not backtested)

### EDGAR Form 4 — Corporate Insider Buying
- **Source:** SEC EDGAR EFTS API (free, no key required)
- **Filter:** Transaction code P (open market purchase), non-10b5-1, min $50K
- **Lookback:** 30 days
- **Signal:** Single insider +10pts, cluster (2+ insiders) +20pts
- **Literature:** Cohen-Malloy-Pomorski (2012): 9.8% VW / 21.6% EW annualised alpha on opportunistic insider trades
- **Backtest status:** Skipped — survivorship bias on pre-selected universe, low event frequency (~200-400 events over 5yr). Monitoring live instead.

### Capitol Trades — Politician Trading
- **Source:** capitoltrades.com (no API key required)
- **Filter:** Buys only, last 45 days, universe tickers only
- **Signal:** +8pts to buy score
- **Literature:** STOCK Act disclosures — signal quality debated; treating as weak confirmation
- **Backtest status:** Skipped — data only reliable from ~2019, insufficient history. Monitoring live.

### PEAD/SUE — Post-Earnings Announcement Drift (backlog)
- **Planned source:** Finnhub earnings calendar + EPS surprise data (free tier, 5yr history)
- **Signal:** Top-quintile SUE → +10pts to buy score, ~60-day hold signal
- **Literature:** Bernard-Thomas (1989), Daniel-Hirshleifer-Sun (2020): dominant short-horizon anomaly
- **Backtest status:** In backlog — sufficient data (5,500 earnings events), low look-ahead risk. Worth building.

---

## CPCV Validation Framework

### Methodology
- **C(12,2) = 66 combinatorial paths × 2 seeds = 132 total paths per ticker**
- **Purge gap:** 20 trading days on each test boundary
- **Forward return horizon:** 252 days
- **Signal inclusion threshold:** p < 0.15 (operational), DSR > 0 (required)

### Decision Gates (deploy proposed model if all pass)
1. Proposed DSR ≥ current DSR × 0.90 (primary — multiple-testing adjusted)
2. Proposed annualised excess ≥ current × 0.90
3. Proposed PBO ≤ current PBO × 1.10

Raw Sharpe deliberately excluded from deploy gates — inflated by Sharpe-proxy quality gate in current model.

### Latest Results (2026-05-25, partial run — 119/277 tickers)

| Metric | Current | Proposed |
|---|---|---|
| Ann. excess vs VOO | 70.97% | 61.93% |
| DSR | 127.603 | 171.755 |
| t-stat | 93.925 | 126.332 |
| PBO | 0.499 | 0.501 |
| Hit rate | 61.8% | 64.0% |

**Status:** Partial run (crashed on `agg` NameError — fixed). Re-run pending on 277 tickers.

### Key Findings
- DFV V3 validated: +38.5% ann excess, DSR 8.84 — **do not demote**
- Gross-profitability quality gate fires 3× more than Sharpe-proxy — threshold may need raising
- PFD validated at +11.3% ann excess, DSR 33.5 — keep at reduced weight (+8pts)

---

## Known Limitations & Issues

| Issue | Status | Notes |
|---|---|---|
| AIQG / QNTM.L | Permanent NO DATA | LSE-listed, yfinance coverage gap. In SKIP_SIGNAL set. |
| CRWV | Occasional NO DATA | IPO March 2025, yfinance sometimes fails batch fetch. Retry logic in place. |
| BRKB | Fixed | yfinance requires `BRK-B` — alias applied in TICKER_ALIASES |
| SCB_SET50 | Proxy = SPY | No liquid ETF proxy for Thai SET50. SPY used as directional fallback. |
| FINNHUB day % | Shows +0.00% on weekends | Expected — Finnhub returns null when markets closed |
| PBO ~0.50 | Expected at high t-stat | With t=94-126, nearly all paths "win" — path-level Sharpe rankings become noisy |

---

## Ticker Aliases (yfinance)

| Scanner ticker | yfinance symbol |
|---|---|
| BRKB | BRK-B |

---

## Thai Fund Proxy Map

| Fund | Proxy ETF | Source |
|---|---|---|
| SCB_SP500 | IVV | iShares S&P 500 |
| SCB_NDQ | QQQ | Invesco QQQM |
| SCB_SEMI | SMH | VanEck Semiconductor |
| SCB_WORLD | URTH | iShares MSCI World |
| SCB_GOLD | GLD | SPDR Gold |
| SCB_NK225 | EWJ | iShares Nikkei |
| SCB_SET50 | SPY | Fallback (no SET50 ETF on yfinance) |
| SCB_DJ | DIA | SPDR Dow Jones |
| SCB_AIEM | AAXJ | Asian EM |
| SCB_FINTECH | FINX | Global X Fintech |
| SCB_AUTO | BOTZ | Global X Robotics |
| SCB_INNOV | ARKK | ARK Innovation |
| SCB_GENO | ARKG | ARK Genomic |
| SCB_CHINA | KWEB | KraneShares China Internet |
| SCB_EV | DRIV | Global X EV & Mobility |
| SCB_BUSAA | IWF | iShares Russell 1000 Growth |
| KT_INDIA | INDA | iShares India |
| KT_WORLD | ACWV | AB Low Vol Global |
| KT_WTAI | AIQ | KTAM World Tech AI |
| KT_BLOCK | BLOK | KTAM Blockchain |
| KT_TECH | IWF | AB American Growth |
| KT_ESG | ACWV | KTAM Global ESG |

---

## Backlog

| Item | Priority | Notes |
|---|---|---|
| PEAD/SUE signal fetcher | HIGH | Finnhub earnings data, ~5,500 events, worth backtesting |
| PEAD/SUE backtest | HIGH | Build after fetcher — sufficient data, low look-ahead risk |
| Insider buying backtest | MEDIUM | Deferred — survivorship bias, low frequency (~200-400 events). Monitor live 6 months first |
| Politician signal backtest | LOW | Deferred — insufficient history (<2019 unreliable). Monitor live |
| Framework reweight | MEDIUM | Quality 30 / Valuation 25 / Growth 20 / Moat 20 / Fit 5 — literature-aligned but not urgent |
| Quality threshold tuning | HIGH | GP proxy fires 3× more than Sharpe proxy — consider raising QUALITY_T from 0.20 |
| Capitol Trades historical data | LOW | Only reliable post-2019, too thin for backtest |
| Earnings revision momentum | MEDIUM | Chan-Jegadeesh-Lakonishok (1996) — needs consensus EPS API |
| Short-interest filter | LOW | Avoid top-decile SI stocks — Asquith-Pathak-Ritter (2005) |

---

## Key References

- Jegadeesh-Titman (1993): momentum, JF 48:65-91
- George-Hwang (2004): 52-week high momentum, JF 59:2145-2176
- Faber (2007): 200d MA timing, JWM Spring 2007
- Antonacci (2014): dual momentum — absolute + relative
- Novy-Marx (2013): gross profitability, JFE 108:1-28
- Asness-Frazzini-Pedersen (2019): QMJ, RAS 24:34-112
- Cohen-Malloy-Pomorski (2012): insider trading, JF 67:1009-1043
- Bernard-Thomas (1989): PEAD, JAR
- Harvey-Liu-Zhu (2016): multiple testing, RFS 29:5-68
- López de Prado (2018): CPCV, JPM 44:120-133
- Bailey-López de Prado (2014): deflated Sharpe, JPM 40:94-107
