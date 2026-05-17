# Portfolio Signal Dashboard

Automated daily stock signal scanner connected to a portfolio dashboard.

## How it works

1. **GitHub Actions** runs `signal_scanner.py` every weekday at 6am Bangkok time
2. Script fetches 5yr daily price history via `yfinance`, runs 10 validated models
3. Calls Finnhub for live prices
4. Writes `signals_payload.json` and commits it to this repo
5. **Netlify** detects the commit and auto-deploys
6. Dashboard loads fresh signals on open — no manual steps

## Setup (one-time)

### 1. Add Finnhub secret to GitHub
- Go to repo → Settings → Secrets and variables → Actions
- New repository secret: `FINNHUB_TOKEN` = your Finnhub API key

### 2. Connect Netlify to this repo
- Netlify dashboard → Add new site → Import an existing project
- Connect to GitHub → select this repo
- Build command: (leave empty)
- Publish directory: `.` (root)
- Deploy

### 3. Enable GitHub Actions
- Go to repo → Actions tab
- Click "Enable Actions" if prompted
- To test immediately: Actions → Daily Signal Scanner → Run workflow

## Manual run
From the repo's Actions tab → Daily Signal Scanner → Run workflow

## Models (validated on 10yr data)
| Model | 504d separation | Consistency |
|---|---|---|
| Factor Value | +65.7% | 4/4 |
| Factor+DFV V3 | +62.0% | 4/4 |
| PFD Buy | +17.4% | 4/4 |
| DFV V1 standalone | -34.9% | 0/4 (removed) |

## Files
- `index.html` — portfolio dashboard
- `signal_scanner.py` — signal engine
- `signals_payload.json` — generated daily (auto-committed)
- `.github/workflows/daily_signals.yml` — scheduler
