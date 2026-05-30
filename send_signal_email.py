"""
send_signal_email.py
════════════════════════════════════════════════════════════
Sends portfolio signal digest emails via Gmail SMTP.

Daily (called from daily_scanner.yml after scanner runs):
    python send_signal_email.py --mode daily

Weekly (called from weekly_insider_signals.yml on Monday):
    python send_signal_email.py --mode weekly

Environment variables (GitHub Secrets):
    GMAIL_FROM          sender address  e.g. signals.bot@gmail.com
    GMAIL_APP_PASSWORD  16-char Gmail app password
    GMAIL_TO            recipient address
"""

import json, os, sys, smtplib, argparse
from datetime import datetime, date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ── CONFIG ────────────────────────────────────────────────────────────
GMAIL_FROM = os.environ.get('GMAIL_FROM', '')
GMAIL_PASS = os.environ.get('GMAIL_APP_PASSWORD', '')
GMAIL_TO   = os.environ.get('GMAIL_TO', '')

# ── LOAD DATA ─────────────────────────────────────────────────────────
def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        print(f'Warning: could not load {path}: {e}')
        return {}

def load_payload():
    return load_json('signals_payload.json')

def load_insider():
    return load_json('insider_signals.json')

def load_pead():
    return load_json('pead_signals.json')

# ── HTML HELPERS ──────────────────────────────────────────────────────
NAVY_HDR = 'background:linear-gradient(135deg,#1e3a5f,#1e40af)'
NAVY_STRIP = 'background:#1e3a8a'

BASE_CSS = """
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:-apple-system,'Helvetica Neue',Arial,sans-serif;
       background:#ffffff; color:#1d1d1f; }
.wrap { max-width:600px; margin:0 auto; background:#fff; }
.hdr { """ + NAVY_HDR + """; padding:22px 26px; }
.hdr-meta { display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; }
.hdr-label { color:rgba(255,255,255,.65); font-size:11px; font-weight:600; letter-spacing:.08em; text-transform:uppercase; }
.hdr-date  { color:rgba(255,255,255,.5); font-size:11px; }
.hdr h1    { color:#fff; font-size:20px; font-weight:700; line-height:1.25; margin-bottom:5px; }
.hdr p     { color:rgba(255,255,255,.65); font-size:12px; }
.mkt { """ + NAVY_STRIP + """; padding:12px 26px; display:flex; gap:20px; flex-wrap:wrap; }
.mkt .lbl  { color:rgba(255,255,255,.4); font-size:9px; text-transform:uppercase; letter-spacing:.06em; }
.mkt .val  { color:#fff; font-size:12px; font-weight:600; }
.g { color:#4ade80; } .n { color:#94a3b8; } .a { color:#fbbf24; } .r { color:#f87171; }
.sec { padding:20px 26px; border-bottom:1px solid #f0f4f4; }
.sec:last-child { border-bottom:none; }
.sec-title { font-size:9px; font-weight:700; letter-spacing:.1em; text-transform:uppercase;
             color:#1e40af; margin-bottom:14px; }
.pills { display:flex; gap:8px; flex-wrap:wrap; margin-bottom:14px; }
.pill { padding:4px 12px; border-radius:99px; font-size:11px; font-weight:600; }
.p-navy  { background:#dbeafe; color:#1e40af; }
.p-amber { background:#fef3c7; color:#92400e; }
.p-purple{ background:#f3e8ff; color:#7e22ce; }
.p-gray  { background:#f3f4f6; color:#374151; }
.p-green { background:#dcfce7; color:#15803d; }
.row { display:flex; gap:12px; padding:12px 0; border-bottom:1px solid #f9fafb; }
.row:last-child { border-bottom:none; }
.icon { width:40px; height:40px; border-radius:9px; flex-shrink:0;
        display:flex; align-items:center; justify-content:center; font-size:16px; }
.ic-w { background:#eff6ff; } .ic-s { background:#fef2f2; }
.ic-h { background:#f9fafb; } .ic-i { background:#fefce8; } .ic-p { background:#faf5ff; }
.body { flex:1; min-width:0; }
.row-top { display:flex; align-items:baseline; gap:8px; flex-wrap:wrap; margin-bottom:5px; }
.ticker { font-size:14px; font-weight:700; }
.price  { font-size:12px; color:#6e6e73; }
.tag    { font-size:10px; font-weight:600; padding:2px 7px; border-radius:4px; text-transform:uppercase; }
.t-w  { background:#dbeafe; color:#1e40af; }
.t-h  { background:#f3f4f6; color:#4b5563; }
.t-s  { background:#fee2e2; color:#b91c1c; }
.t-i  { background:#fef3c7; color:#92400e; }
.t-p  { background:#f3e8ff; color:#7e22ce; }
.t-g  { background:#dcfce7; color:#15803d; }
ul.notes { margin:4px 0 6px 0; padding-left:16px; }
ul.notes li { font-size:12px; color:#4b5563; line-height:1.6; margin-bottom:2px; }
ul.notes li strong { color:#1d1d1f; }
.action { font-size:12px; font-weight:600; color:#1e40af;
          background:#eff6ff; padding:4px 10px; border-radius:5px;
          display:inline-block; margin-top:4px; }
.action.hold { color:#374151; background:#f3f4f6; }
.action.warn { color:#92400e; background:#fefce8; }
.action.red  { color:#b91c1c; background:#fee2e2; }
.scores { display:flex; gap:6px; flex-wrap:wrap; margin-top:6px; }
.sp { font-size:10px; padding:2px 7px; border-radius:99px; background:#f5f5f7; color:#3a3a3c; }
.sp.g { background:#dcfce7; color:#15803d; }
.sp.a { background:#fef3c7; color:#92400e; }
.sp.r { background:#fee2e2; color:#b91c1c; }
.tbl { width:100%; border-collapse:collapse; margin-top:8px; font-size:12px; }
.tbl th { text-align:left; font-size:9px; font-weight:700; letter-spacing:.06em;
          text-transform:uppercase; color:#6e6e73; padding:6px 8px; border-bottom:2px solid #f0f0f0; }
.tbl td { padding:8px 8px; border-bottom:1px solid #f9fafb; }
.tbl tr:last-child td { border-bottom:none; }
.grid2 { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-top:6px; }
.gcell { background:#f9fafb; border-radius:8px; padding:10px 12px; }
.gcell .gl { font-size:10px; color:#6e6e73; margin-bottom:3px; }
.gcell .gv { font-size:13px; font-weight:600; }
.footer { padding:14px 26px; background:#f9fafb; }
.footer p { font-size:10px; color:#aeaeb2; line-height:1.6; }
.note-bar { padding:10px 26px; background:#eff6ff; border-top:1px solid #bfdbfe; }
.note-bar p { font-size:11px; color:#1e40af; }
"""

def html_wrap(body_content, title='Portfolio Signals'):
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{title}</title>
<style>{BASE_CSS}</style>
</head>
<body><div class="wrap">{body_content}</div></body>
</html>"""

# ── SIGNAL ROW BUILDER ────────────────────────────────────────────────
def sig_row(icon_html, icon_cls, ticker, price_html, tag_html, bullets, action_html=''):
    bullets_li = ''.join(f'<li>{b}</li>' for b in bullets)
    return f"""
<div class="row">
  <div class="icon {icon_cls}">{icon_html}</div>
  <div class="body">
    <div class="row-top">
      <span class="ticker">{ticker}</span>
      {price_html}
      {tag_html}
    </div>
    <ul class="notes">{bullets_li}</ul>
    {action_html}
  </div>
</div>"""

def action(text, style=''):
    return f'<span class="action {style}">{text}</span>'

def price_html(price, chg=None):
    if price is None: return ''
    chg_html = ''
    if chg is not None and chg != 0:
        cls = 'g' if chg > 0 else 'r'
        chg_html = f' <span class="{cls}">{chg:+.2f}%</span>'
    return f'<span class="price">${price:,.2f}{chg_html}</span>'

# ── DAILY EMAIL ───────────────────────────────────────────────────────
def build_daily(payload, insider_data, pead_data):
    market  = payload.get('market', {})
    summary = payload.get('summary', {})
    signals = payload.get('analytics', {}).get('signals', [])
    run_date = payload.get('run_date', str(date.today()))

    vix_z   = market.get('vix_zscore', 0)
    vix_cur = market.get('vix_current', 20)
    vix_lbl = 'Fear' if vix_z > 1 else 'Greed' if vix_z < -1 else 'Neutral'
    vix_cls = 'g' if vix_z > 1 else 'a' if vix_z < -1 else 'n'
    spy_ok  = market.get('sp500_extended', False) is False
    s_cap   = market.get('strong_cap', 3)
    r_cap   = market.get('regular_cap', 3)

    n_strong = summary.get('strong_buy', 0)
    n_watch  = summary.get('watch', 0)
    n_factor = summary.get('factor_zone', 0)
    n_sell   = market.get('sell_cap', 0)
    n_insider = len(insider_data.get('signals', {})) if insider_data else 0
    n_pead    = len(pead_data.get('signals', {})) if pead_data else 0

    headline = (
        f'{"★★ " + str(n_strong) + " new buy" + ("s" if n_strong != 1 else "") + " · " if n_strong else "No new buys · "}'
        f'{n_watch} watch · {n_factor} factor zone · Market {vix_lbl.lower()}'
    )

    # ── Header
    html = f"""
<div class="hdr">
  <div class="hdr-meta">
    <span class="hdr-label">Portfolio Signals · Daily</span>
    <span class="hdr-date">{run_date}</span>
  </div>
  <h1>{headline}</h1>
  <p>Buy cap {s_cap} strong · {r_cap} regular · {n_insider} insider signals · {n_pead} PEAD active</p>
</div>
<div class="mkt">
  <div><div class="lbl">VIX</div><div class="val {vix_cls}">{vix_cur:.1f} z={vix_z:+.2f} ({vix_lbl})</div></div>
  <div><div class="lbl">SPY</div><div class="val {'g' if spy_ok else 'r'}">{'Above 200d MA ✓' if spy_ok else 'Below 200d MA ⚠'}</div></div>
  <div><div class="lbl">Buy Cap</div><div class="val n">{s_cap} strong · {r_cap} regular</div></div>
</div>"""

    # ── Summary pills
    html += '<div class="sec"><div class="sec-title">Today\'s Summary</div><div class="pills">'
    html += f'<span class="pill p-{"navy" if n_strong else "gray"}">★★ {n_strong} Strong Buy{"s" if n_strong != 1 else ""}</span>'
    html += f'<span class="pill p-navy">↑ {n_watch} Watch</span>'
    html += f'<span class="pill p-gray">⊙ {n_factor} Factor Zone</span>'
    if n_sell: html += f'<span class="pill p-amber">⚠ {n_sell} Sell Flags</span>'
    if n_pead: html += f'<span class="pill p-purple">⚡ {n_pead} PEAD</span>'
    html += '</div>'

    # Summary bullets
    no_action = not any(s.get('signal') in ('BUY','SELL') for s in signals if s.get('is_holding'))
    html += '<ul class="notes">'
    if n_strong == 0: html += '<li>No signals cleared the <strong>80-point buy threshold</strong> today</li>'
    else:
        buys = [s for s in signals if s.get('signal') == 'BUY'][:3]
        for b in buys:
            html += f'<li><strong>★★ {b["ticker"]}</strong> — buy score {b["buy_score"]} · FW {b.get("framework_score","?")}</li>'
    top_watch = [s for s in signals if s.get('fdfv3') and not s.get('signal') == 'BUY'][:1]
    if top_watch:
        w = top_watch[0]
        html += f'<li>Highest quality watch: <strong>{w["ticker"]}</strong> score {w["buy_score"]} · FW {w.get("framework_score","?")}</li>'
    if no_action: html += '<li><strong>No action required</strong> on any held position today</li>'
    html += '</ul></div>'

    # ── Watch signals (Factor+DFV V3, score ≥60, not already BUY)
    watches = [s for s in signals if s.get('fdfv3') and s.get('buy_score',0) >= 60
               and s.get('signal') != 'BUY'][:5]
    if watches:
        html += '<div class="sec"><div class="sec-title">★★ Watch — Factor+DFV V3 Active</div>'
        for s in watches:
            dist = s.get('dist_252h', 0)
            dfv  = s.get('dfv_lift', 0)
            fw   = s.get('framework_score', '?')
            gap  = 80 - s.get('buy_score', 0)
            bullets = [
                f'Down <strong>{abs(dist):.1f}%</strong> from 252d high · DFV lift {dfv:.1f} · FW {fw}',
                f'Needs <strong>{gap} more points</strong> to trigger buy signal',
            ]
            if fw and isinstance(fw, int) and fw < 50:
                bullets.append(f'<strong>Caution: FW {fw} — speculative.</strong> Size small if entering')
            html += sig_row('★★', 'ic-w', s['ticker'],
                            price_html(s.get('price'), s.get('change_pct')),
                            f'<span class="tag t-w">WATCH · {s["buy_score"]}</span>',
                            bullets,
                            action('Monitor — close to trigger') if gap <= 10 else action('Monitor', 'hold'))
        html += '</div>'

    # ── Strong buys (if any)
    strong_buys = [s for s in signals if s.get('signal') == 'BUY' and s.get('fdfv3')]
    if strong_buys:
        html += '<div class="sec"><div class="sec-title">★★ New Buy Signals</div>'
        for s in strong_buys:
            dist = s.get('dist_252h', 0)
            fw   = s.get('framework_score', '?')
            html += sig_row('★★', 'ic-w', s['ticker'],
                            price_html(s.get('price'), s.get('change_pct')),
                            f'<span class="tag t-g">BUY · {s["buy_score"]}</span>',
                            [
                                f'Factor+DFV V3 · down <strong>{abs(dist):.1f}%</strong> from 252d high · FW {fw}',
                                s.get('guidance', '')[:120],
                            ],
                            action('★★ Consider entry'))
        html += '</div>'

    # ── Insider signals
    ins_signals = insider_data.get('signals', {}) if insider_data else {}
    if ins_signals:
        html += '<div class="sec"><div class="sec-title">🔑 Active Insider Signals</div>'
        for ticker, ins in list(ins_signals.items())[:4]:
            is_cluster = ins.get('insider_cluster', False)
            buys = ins.get('insider_buys', [])
            n_ins = ins.get('insider_n', 1)
            bullets = []
            if is_cluster:
                bullets.append(f'<strong>Cluster buy</strong> — {n_ins} insiders, same ticker, 60-day window')
            for b in buys[:2]:
                bullets.append(f'{b.get("title","").split(",")[0]} bought ${b.get("value_usd",0):,.0f} on {b.get("date","")}')
            bullets.append(f'Buy score boost: +{ins.get("insider_score",0)}pts · FW {ins.get("framework_score", "?") if "framework_score" in ins else "—"}')
            html += sig_row('🔑🔑' if is_cluster else '🔑', 'ic-i', ticker, '', 
                            f'<span class="tag t-i">{"CLUSTER" if is_cluster else "INSIDER"} · +{ins.get("insider_score",0)}pts</span>',
                            bullets,
                            action('Watch for factor gate opening', 'hold'))
        html += '</div>'

    # ── PEAD signals
    pead_signals = pead_data.get('signals', {}) if pead_data else {}
    held_pead = {t: v for t, v in pead_signals.items()
                 if any(s.get('ticker') == t and s.get('is_holding') for s in signals)}
    all_pead = {**held_pead, **{t: v for t, v in pead_signals.items() if t not in held_pead}}
    if all_pead:
        html += '<div class="sec"><div class="sec-title">⚡ Active PEAD Signals</div>'
        for ticker, ps in list(all_pead.items())[:3]:
            sue     = ps.get('sue', 0)
            expires = ps.get('expires_at', '?')
            is_held = ticker in held_pead
            sig_row_data = next((s for s in signals if s.get('ticker') == ticker), {})
            sell_score = sig_row_data.get('sell_score', 0)
            bullets = [
                f'SUE <strong>{sue:.2f}</strong> — top-quintile earnings surprise · expires {expires}',
                f'PEAD drift expected for up to 60 days post-earnings',
            ]
            if is_held:
                bullets.append(f'<strong>HELD</strong> · sell score {sell_score:.0f} — FW damping {"holding sell below TRIM" if sell_score < 52 else "approaching TRIM"}')
            else:
                bullets.append('Not a held position')
            html += sig_row('⚡', 'ic-p', ticker, price_html(sig_row_data.get('price')),
                            f'<span class="tag t-p">SUE {sue:.2f} · {expires}</span>',
                            bullets,
                            action('Hold — PEAD active' if is_held else 'Watch only', 'hold' if not is_held else ''))
        html += '</div>'

    # ── Held positions with sell flags
    sell_held = [s for s in signals if s.get('is_holding') and s.get('sell_score', 0) >= 30]
    sell_held.sort(key=lambda x: x.get('sell_score', 0), reverse=True)
    if sell_held:
        html += '<div class="sec"><div class="sec-title">📊 Held Positions — Sell Monitor</div>'
        for s in sell_held[:4]:
            sell_score = s.get('sell_score', 0)
            fw   = s.get('framework_score', '?')
            dist = s.get('dist_252h', 0)
            action_str = ('EXIT' if sell_score >= 78 else
                          'REDUCE' if sell_score >= 65 else
                          'TRIM' if sell_score >= 52 else 'HOLD')
            act_style = 'red' if sell_score >= 78 else 'warn' if sell_score >= 52 else 'hold'
            flags = s.get('sell_flags', '—')
            bullets = [
                f'Sell score <strong>{sell_score:.0f}</strong> · Action: <strong>{action_str}</strong> · FW {fw}',
                f'Dist from 252d high: {dist:+.1f}% · Flags: {flags}',
            ]
            if sell_score < 52:
                bullets.append('FW damping keeps score below TRIM threshold — hold')
            html += sig_row('⚠' if sell_score >= 52 else '📊',
                            'ic-s' if sell_score >= 52 else 'ic-h',
                            s['ticker'], price_html(s.get('price'), s.get('change_pct')),
                            f'<span class="tag t-{"s" if sell_score>=52 else "h"}">{action_str} · {sell_score:.0f}</span>',
                            bullets,
                            action(action_str, act_style if action_str != 'HOLD' else 'hold'))
        html += '</div>'

    # ── Footer note + footer
    html += '''<div class="note-bar">
  <p>📋 <strong>Weekly summary</strong> — sent every Monday morning with insider signals, PEAD activations, regime review and watchlist for the week ahead.</p>
</div>'''
    html += f'''<div class="footer">
  <p>Auto-generated · portfolio-signals · {run_date} · 2× daily 06:00 + 18:00 Bangkok · Buy ≥80 · TRIM ≥52 · REDUCE ≥65 · EXIT ≥78</p>
</div>'''

    return html_wrap(html, f'Portfolio Signals · {run_date}')


# ── WEEKLY EMAIL ──────────────────────────────────────────────────────
def build_weekly(payload, insider_data, pead_data):
    market  = payload.get('market', {})
    signals = payload.get('analytics', {}).get('signals', [])
    run_date = payload.get('run_date', str(date.today()))

    vix_z   = market.get('vix_zscore', 0)
    vix_cur = market.get('vix_current', 20)
    vix_lbl = 'Neutral' if abs(vix_z) < 1 else ('Fear' if vix_z > 1 else 'Greed')
    spy_ok  = not market.get('sp500_extended', False)

    ins_signals  = insider_data.get('signals', {}) if insider_data else {}
    pead_signals = pead_data.get('signals', {}) if pead_data else {}
    n_ins  = len(ins_signals)
    n_pead = len(pead_signals)

    held = [s for s in signals if s.get('is_holding')]
    exit_count   = sum(1 for s in held if s.get('sell_score', 0) >= 78)
    reduce_count = sum(1 for s in held if 65 <= s.get('sell_score', 0) < 78)
    top_watch    = sorted([s for s in signals if s.get('fdfv3')],
                           key=lambda x: (-x.get('buy_score',0), -x.get('framework_score',0)))[:2]

    week_label = f'Week ending {run_date}'

    html = f"""
<div class="hdr">
  <div class="hdr-meta">
    <span class="hdr-label">Portfolio Signals · Weekly Summary</span>
    <span class="hdr-date">{week_label}</span>
  </div>
  <h1>{exit_count} exits · {reduce_count} reduces · {n_ins} insider signals · {n_pead} PEAD active</h1>
  <p>Regime: {vix_lbl} all week · SPY {'above' if spy_ok else 'below'} 200d MA</p>
</div>
<div class="mkt">
  <div><div class="lbl">VIX</div><div class="val n">{vix_cur:.1f} z={vix_z:+.2f} · {vix_lbl}</div></div>
  <div><div class="lbl">SPY 200d</div><div class="val {'g' if spy_ok else 'r'}">{'Clear ✓' if spy_ok else 'Broken ⚠'}</div></div>
  <div><div class="lbl">Buy Cap</div><div class="val n">{market.get('strong_cap',3)}/{market.get('regular_cap',3)} available</div></div>
</div>"""

    # Week in numbers grid
    html += '<div class="sec"><div class="sec-title">📊 Week in Numbers</div><div class="grid2">'
    for label, value, color in [
        ('Insider Signals', n_ins, '#1d1d1f'),
        ('PEAD Active', n_pead, '#7e22ce'),
        ('EXIT Signals', exit_count, '#15803d' if exit_count == 0 else '#b91c1c'),
        ('REDUCE Signals', reduce_count, '#15803d' if reduce_count == 0 else '#d97706'),
        ('New BUY Signals', sum(1 for s in signals if s.get('signal')=='BUY'), '#94a3b8'),
        ('Top Watch Score', max((s.get('buy_score',0) for s in signals if s.get('fdfv3')), default=0), '#1d1d1f'),
    ]:
        html += f'<div class="gcell"><div class="gl">{label}</div><div class="gv" style="color:{color}">{value}</div></div>'
    html += '</div></div>'

    # ── Insider signals
    if ins_signals:
        html += '<div class="sec"><div class="sec-title">🔑 Insider Buying This Week</div>'
        for ticker, ins in list(ins_signals.items())[:6]:
            is_cluster = ins.get('insider_cluster', False)
            buys = ins.get('insider_buys', [])
            bullets = []
            if is_cluster:
                bullets.append(f'<strong>Cluster buy</strong> — {ins.get("insider_n",2)}+ insiders, same 60-day window')
            for b in buys[:2]:
                title = (b.get('title') or '').split(',')[0]
                val   = b.get('value_usd', 0)
                dt    = b.get('date', '')
                bullets.append(f'{title} bought <strong>${val:,.0f}</strong> on {dt}')
            html += sig_row('🔑🔑' if is_cluster else '🔑', 'ic-i', ticker, '',
                            f'<span class="tag t-i">{"CLUSTER" if is_cluster else "INSIDER"} · +{ins.get("insider_score",0)}pts</span>',
                            bullets)
        html += '</div>'

    # ── PEAD signals
    if pead_signals:
        html += '<div class="sec"><div class="sec-title">⚡ PEAD Signals Active</div>'
        for ticker, ps in list(pead_signals.items())[:4]:
            sue     = ps.get('sue', 0)
            expires = ps.get('expires_at', '?')
            ann     = ps.get('announce_date', '?')
            is_held = any(s.get('ticker') == ticker and s.get('is_holding') for s in signals)
            html += sig_row('⚡', 'ic-p', ticker, '',
                            f'<span class="tag t-p">SUE {sue:.2f} · Expires {expires}</span>',
                            [
                                f'Announced <strong>{ann}</strong> · top-quintile earnings surprise',
                                f'PEAD drift window: 60 days · {"<strong>HELD</strong>" if is_held else "Not held"}',
                            ])
        html += '</div>'

    # ── Held positions table
    if held:
        html += '<div class="sec"><div class="sec-title">📋 Held Positions — Weekly Review</div>'
        html += '''<table class="tbl"><thead><tr>
          <th>Ticker</th><th>Buy</th><th>Sell</th><th>Action</th><th>Note</th>
        </tr></thead><tbody>'''
        for s in sorted(held, key=lambda x: -x.get('sell_score', 0))[:8]:
            sell  = s.get('sell_score', 0)
            act   = 'EXIT' if sell >= 78 else 'REDUCE' if sell >= 65 else 'TRIM' if sell >= 52 else 'HOLD'
            color = '#b91c1c' if act=='EXIT' else '#d97706' if act in ('REDUCE','TRIM') else '#15803d'
            note  = s.get('guidance', '')[:60]
            html += f'''<tr>
              <td style="font-weight:700">{s["ticker"]}</td>
              <td>{s.get("buy_score","—")}</td>
              <td style="color:{'#d97706' if sell>=30 else '#6e6e73'}">{sell:.0f}</td>
              <td style="color:{color};font-weight:600">{act}</td>
              <td style="color:#6e6e73">{note}</td>
            </tr>'''
        html += '</tbody></table>'
        if exit_count == 0 and reduce_count == 0:
            html += '<p style="font-size:11px;color:#6e6e73;margin-top:10px">No EXIT or REDUCE signals on held positions this week.</p>'
        html += '</div>'

    # ── Watch next week
    if top_watch:
        html += '<div class="sec"><div class="sec-title">👀 Priority Watch — Coming Week</div>'
        for s in top_watch:
            fw   = s.get('framework_score', '?')
            dist = s.get('dist_252h', 0)
            gap  = 80 - s.get('buy_score', 0)
            html += sig_row('★★', 'ic-w', s['ticker'],
                            price_html(s.get('price')),
                            f'<span class="tag t-w">WATCH · {s["buy_score"]}</span>',
                            [
                                f'Down <strong>{abs(dist):.1f}%</strong> from 252d high · FW {fw}',
                                f'<strong>{gap} points</strong> below buy trigger · DFV lift {s.get("dfv_lift","?"):.1f}',
                            ],
                            action('Primary watchlist entry for next week'))
        html += '</div>'

    html += f'''<div class="footer">
  <p>Auto-generated weekly summary · portfolio-signals · {run_date} · Next weekly: Monday · Buy ≥80 · TRIM ≥52 · REDUCE ≥65 · EXIT ≥78</p>
</div>'''

    return html_wrap(html, f'Portfolio Signals · Weekly · {run_date}')


# ── SEND EMAIL ────────────────────────────────────────────────────────
def send_email(subject, html_body):
    if not all([GMAIL_FROM, GMAIL_PASS, GMAIL_TO]):
        print('ERROR: GMAIL_FROM, GMAIL_APP_PASSWORD, GMAIL_TO must all be set')
        sys.exit(1)

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From']    = GMAIL_FROM
    msg['To']      = GMAIL_TO
    msg.attach(MIMEText(html_body, 'html'))

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_FROM, GMAIL_PASS)
            server.sendmail(GMAIL_FROM, GMAIL_TO, msg.as_string())
        print(f'Email sent: {subject}')
    except Exception as e:
        print(f'Email failed: {e}')
        sys.exit(1)


# ── MAIN ──────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['daily','weekly'], required=True)
    args = parser.parse_args()

    payload      = load_payload()
    insider_data = load_insider()
    pead_data    = load_pead()

    if not payload:
        print('ERROR: signals_payload.json not found')
        sys.exit(1)

    run_date = payload.get('run_date', str(date.today()))

    if args.mode == 'daily':
        html    = build_daily(payload, insider_data, pead_data)
        n_buys  = payload.get('summary', {}).get('strong_buy', 0)
        n_sells = payload.get('market', {}).get('sell_cap', 0)
        subject = f'📊 Portfolio Signals · {run_date} · {n_buys} buy{"s" if n_buys!=1 else ""} · {n_sells} sell flags'
        send_email(subject, html)

    elif args.mode == 'weekly':
        html    = build_weekly(payload, insider_data, pead_data)
        subject = f'📋 Weekly Summary · {run_date} · {len(insider_data.get("signals",{}))} insider · {len(pead_data.get("signals",{}))} PEAD'
        send_email(subject, html)


if __name__ == '__main__':
    main()
