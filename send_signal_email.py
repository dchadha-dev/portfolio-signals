"""
send_signal_email.py — Portfolio signal digest via Gmail SMTP
Daily:  python send_signal_email.py --mode daily
Weekly: python send_signal_email.py --mode weekly
Secrets: GMAIL_FROM, GMAIL_APP_PASSWORD, GMAIL_TO
"""
import json, os, sys, smtplib, argparse, re, base64
from datetime import datetime, date
from email.utils import formatdate, make_msgid

GMAIL_FROM  = os.environ.get('GMAIL_FROM', '')
GMAIL_PASS  = os.environ.get('GMAIL_APP_PASSWORD', '')
_gmail_to   = os.environ.get('GMAIL_TO', '')
GMAIL_TO    = _gmail_to  # kept for single-recipient display in headers
GMAIL_TO_LIST = [e.strip() for e in _gmail_to.split(',') if e.strip()]

# ── INLINE STYLE CONSTANTS ───────────────────────────────────────────
BG      = '#080c14'
BG2     = '#0d1520'
BG3     = '#0b1120'
BORDER  = '1px solid #151f2e'
BORDER2 = '1px solid #111827'
TXT     = '#c8d3e0'
TXT2    = '#5a6878'
TXT3    = '#3a4a5c'
BLUE    = '#60a5fa'
GREEN   = '#34d399'
AMBER   = '#fbbf24'
PURPLE  = '#a78bfa'
RED     = '#f87171'
NAVY    = '#1e3a5f'
FONT    = "font-family:Arial,'Helvetica Neue',Helvetica,sans-serif;"
MONO    = "font-family:'Courier New',Courier,monospace;"

def s(**kw):
    """Build inline style string from kwargs."""
    return ';'.join(f"{k.replace('_','-')}:{v}" for k,v in kw.items())

# ── LOAD DATA ────────────────────────────────────────────────────────
def load_json(path):
    try:
        with open(path) as f: return json.load(f)
    except: return {}

def load_payload():    return load_json('signals_payload.json')
def load_weekly_log(): return load_json('weekly_signals_log.json')
def load_insider():    return load_json('insider_signals.json')
def load_pead():       return load_json('pead_signals.json')

# ── HTML PRIMITIVES ──────────────────────────────────────────────────
def wrap(content, title='Portfolio Signals'):
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{title}</title>
</head>
<body style="{FONT}margin:0;padding:20px 0;background:{BG};color:{TXT};">
<div style="max-width:560px;margin:0 auto;background:{BG};">
{content}
</div>
</body>
</html>"""

def header(eyebrow, headline, subline):
    return f"""
<div style="background:linear-gradient(135deg,#1e3a5f,#1e40af);padding:28px 28px 24px;">
  <div style="{MONO}font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:rgba(255,255,255,.5);margin-bottom:10px;">{eyebrow}</div>
  <div style="{FONT}font-size:22px;font-weight:700;color:#fff;line-height:1.2;margin-bottom:6px;">{headline}</div>
  <div style="{FONT}font-size:12px;color:rgba(255,255,255,.55);">{subline}</div>
</div>"""

def mkt_strip(cells):
    """cells = list of (label, value, color)"""
    items = ''
    for label, value, color in cells:
        items += f"""<td style="padding:0 20px 0 0;border-right:1px solid #1a2232;vertical-align:top;">
  <div style="{MONO}font-size:9px;text-transform:uppercase;letter-spacing:.1em;color:#2d3f52;margin-bottom:3px;">{label}</div>
  <div style="{MONO}font-size:12px;font-weight:500;color:{color};">{value}</div>
</td>"""
    return f"""
<table width="100%" style="background:{BG3};border-collapse:collapse;" cellpadding="0" cellspacing="0">
<tr><td style="padding:14px 28px;">
  <table cellpadding="0" cellspacing="0"><tr>{items}</tr></table>
</td></tr></table>"""

def section(label, content):
    return f"""
<div style="padding:24px 28px;border-bottom:{BORDER2};">
  <div style="{MONO}font-size:9px;font-weight:600;letter-spacing:.16em;text-transform:uppercase;color:{BLUE};margin-bottom:18px;">{label}</div>
  {content}
</div>"""

def card(ticker, subtitle, badge_txt, badge_color, badge_bg, bullets, action_txt, action_color):
    bullets_html = ''.join(
        f'<tr><td style="padding:2px 0 2px 14px;{FONT}font-size:12px;color:{TXT2};line-height:1.55;vertical-align:top;">'
        f'<span style="margin-left:-14px;color:#1d3a5c;margin-right:6px;">—</span>{b}</td></tr>'
        for b in bullets
    )
    return f"""
<table width="100%" cellpadding="0" cellspacing="0" style="background:{BG2};border:{BORDER};border-radius:10px;margin-bottom:10px;">
<tr><td style="padding:18px 20px;">
  <!-- card head -->
  <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:12px;">
  <tr>
    <td style="{FONT}font-size:15px;font-weight:700;color:#f0f4f8;vertical-align:middle;">
      {ticker}
      <span style="{MONO}font-size:11px;font-weight:400;color:{TXT3};margin-left:8px;">{subtitle}</span>
    </td>
    <td align="right" style="vertical-align:middle;">
      <span style="{MONO}font-size:9px;font-weight:500;letter-spacing:.1em;text-transform:uppercase;padding:3px 9px;border-radius:4px;background:{badge_bg};color:{badge_color};border:1px solid {badge_color}33;">{badge_txt}</span>
    </td>
  </tr>
  </table>
  <!-- bullets -->
  <table cellpadding="0" cellspacing="0" width="100%">{bullets_html}</table>
  <!-- action -->
  <div style="margin-top:12px;padding-top:12px;border-top:{BORDER2};{MONO}font-size:10px;letter-spacing:.06em;color:{action_color};">
    <span style="display:inline-block;width:5px;height:5px;border-radius:50%;background:{action_color};vertical-align:middle;margin-right:6px;"></span>
    {action_txt}
  </div>
</td></tr>
</table>"""

def stat_grid(stats):
    """stats = list of (value, label, color)"""
    cells = ''
    for val, lbl, color in stats:
        cells += f"""<td style="width:33%;padding:0 4px 0 0;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:{BG2};border:{BORDER};border-radius:8px;">
  <tr><td style="padding:14px;text-align:center;">
    <div style="{MONO}font-size:22px;font-weight:500;color:{color};line-height:1;margin-bottom:4px;">{val}</div>
    <div style="{FONT}font-size:10px;color:{TXT3};text-transform:uppercase;letter-spacing:.08em;">{lbl}</div>
  </td></tr>
  </table>
</td>"""
    return f'<table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:16px;"><tr>{cells}</tr></table>'

def bullet_list(items):
    rows = ''.join(
        f'<li style="{FONT}font-size:12px;color:{TXT2};line-height:1.6;margin-bottom:3px;">{i}</li>'
        for i in items
    )
    return f'<ul style="margin:0;padding:0 0 0 18px;">{rows}</ul>'

def note_bar(text):
    return f"""
<div style="padding:12px 28px;background:{NAVY};border-top:{BORDER2};">
  <span style="{MONO}font-size:10px;color:{BLUE};letter-spacing:.04em;">{text}</span>
</div>"""

def footer(left, right):
    return f"""
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0a0f1e;padding:18px 28px;">
<tr>
  <td style="{MONO}font-size:9px;color:#1d2d3d;line-height:1.8;">{left}</td>
  <td align="right" style="{MONO}font-size:9px;color:#1d2d3d;line-height:1.8;">{right}</td>
</tr>
</table>"""

def b(text): return f'<strong style="color:{TXT};">{text}</strong>'
def hi(text, color=BLUE): return f'<span style="color:{color};">{text}</span>'

# ── DAILY EMAIL ──────────────────────────────────────────────────────
def build_daily(payload, insider_data, pead_data):
    market   = payload.get('market', {})
    summary  = payload.get('summary', {})
    signals  = payload.get('analytics', {}).get('signals', [])
    run_date = payload.get('run_date', str(date.today()))

    vix_z   = market.get('vix_zscore', 0)
    vix_cur = market.get('vix_current', 20)
    vix_lbl = 'Fear' if vix_z > 1 else 'Greed' if vix_z < -1 else 'Neutral'
    vix_col = GREEN if vix_z > 1 else AMBER if vix_z < -1 else BLUE
    spy_ok  = not market.get('sp500_extended', False)
    s_cap   = market.get('strong_cap', 3)
    r_cap   = market.get('regular_cap', 3)
    n_strong= summary.get('strong_buy', 0)
    n_watch = summary.get('watch', 0)
    n_factor= summary.get('factor_zone', 0)
    n_sell  = market.get('sell_cap', 0)
    n_pead  = len(pead_data.get('signals', {})) if pead_data else 0
    n_ins   = len(insider_data.get('signals', {})) if insider_data else 0

    headline = (f'{n_strong} new buy{"s" if n_strong!=1 else ""} · {n_watch} watch · Market {vix_lbl.lower()}'
                if n_strong else f'No new buys · {n_watch} watch · {n_factor} factor zone · Market {vix_lbl.lower()}')
    subline  = f'Buy cap {s_cap} strong · {r_cap} regular · {n_ins} insider · {n_pead} PEAD active'

    html  = header(f'Daily Digest · {run_date}', headline, subline)
    html += mkt_strip([
        ('VIX', f'{vix_cur:.1f} z={vix_z:+.2f} ({vix_lbl})', vix_col),
        ('SPY 200d', 'Above ✓' if spy_ok else 'Below ⚠', GREEN if spy_ok else RED),
        ('Buy Cap', f'{s_cap} · {r_cap}', TXT),
        ('Regime', vix_lbl, vix_col),
    ])

    # Summary
    top_watch = sorted([s for s in signals if s.get('fdfv3') and s.get('buy_score',0) < 80],
                       key=lambda x: (-x.get('buy_score',0), -(x.get('framework_score') or 0)))
    sum_bullets = []
    if n_strong == 0:
        sum_bullets.append(f'No signals cleared the {b("80-point buy threshold")} today')
    if top_watch:
        tw = top_watch[0]
        sum_bullets.append(f'Highest quality watch: {b(tw["ticker"])} score {tw["buy_score"]} · FW {tw.get("framework_score","?")}')
    no_action = not any(s.get('sell_score',0) >= 52 and s.get('is_holding') for s in signals)
    if no_action:
        sum_bullets.append(f'{hi("No action required", GREEN)} on any held position today')

    html += section('Summary',
        stat_grid([
            (n_strong or '0', 'Strong Buys', GREEN if n_strong else TXT3),
            (n_watch,  'Watch Zone', BLUE),
            (n_sell,   'Sell Flags', AMBER if n_sell else TXT3),
        ]) + bullet_list(sum_bullets)
    )

    # Watch list
    watches = top_watch[:4]
    if watches:
        cards = ''
        for s in watches:
            dist = s.get('dist_252h', 0)
            fw   = s.get('framework_score', '?')
            gap  = 80 - s.get('buy_score', 0)
            dfv  = s.get('dfv_lift', 0) or 0
            bs   = [
                f'Down {b(f"{abs(dist):.1f}%")} from 252d high · DFV lift {hi(f"{dfv:.1f}")} · FW {fw}',
                f'{hi(f"{gap} more points", AMBER)} needed to trigger buy signal',
            ]
            if isinstance(fw, int) and fw < 50:
                bs.append(f'{hi("Caution: FW " + str(fw) + " — speculative", AMBER)} · size small if entering')
            cards += card(s['ticker'], '', f'WATCH · {s["buy_score"]}', BLUE, '#0c1a3a', bs,
                         'MONITOR — CLOSE TO TRIGGER' if gap <= 8 else 'MONITOR', BLUE)
        html += section('★★ Watch — Factor+DFV V3 Active', cards)

    # Strong buys
    strong_buys = [s for s in signals if s.get('signal') == 'BUY' and s.get('fdfv3')]
    if strong_buys:
        cards = ''
        for s in strong_buys:
            dist = s.get('dist_252h', 0)
            fw   = s.get('framework_score', '?')
            cards += card(s['ticker'], f'${s.get("price",0):,.2f}', f'BUY · {s["buy_score"]}', GREEN, '#041a0f',
                         [f'Factor+DFV V3 · down {b(f"{abs(dist):.1f}%")} from 252d high · FW {fw}',
                          s.get('guidance','')[:120]],
                         '★★ CONSIDER ENTRY', GREEN)
        html += section('★★ New Buy Signals', cards)

    # Insider signals
    ins_sigs = insider_data.get('signals', {}) if insider_data else {}
    if ins_sigs:
        cards = ''
        for ticker, ins in list(ins_sigs.items())[:4]:
            is_cluster = ins.get('insider_cluster', False)
            buys = ins.get('insider_buys', [])
            bs = []
            if is_cluster:
                bs.append(f'{b("Cluster buy")} — {ins.get("insider_n",2)}+ insiders, same 60-day window')
            for bv in buys[:2]:
                title = (bv.get('title') or '').split(',')[0]
                val   = bv.get('value_usd', 0)
                dt    = bv.get('date', '')
                bs.append(f'{title} bought {hi(f"${val:,.0f}", AMBER)} on {dt}')
            ins_sc = ins.get('insider_score', 0)
            bs.append(f'Buy score boost: {hi(f"+{ins_sc}pts", AMBER)} · Watch for factor gate opening')
            ins_lbl = 'CLUSTER' if is_cluster else 'INSIDER'
            cards += card(ticker, 'INSIDER', f'{ins_lbl} · +{ins_sc}pts',
                         AMBER, '#1a1200', bs, 'WATCH FOR FACTOR GATE OPENING', AMBER)
        html += section('🔑 Active Insider Signals', cards)

    # PEAD signals
    pead_sigs = pead_data.get('signals', {}) if pead_data else {}
    if pead_sigs:
        cards = ''
        for ticker, ps in list(pead_sigs.items())[:3]:
            sue     = ps.get('sue', 0)
            expires = ps.get('expires_at', '?')
            is_held = any(s.get('ticker') == ticker and s.get('is_holding') for s in signals)
            sig_    = next((s for s in signals if s.get('ticker') == ticker), {})
            sell_sc = sig_.get('sell_score', 0)
            bs = [
                f'SUE {b(f"{sue:.2f}")} — top-quintile earnings surprise · expires {expires}',
                'PEAD drift expected for up to 60 days post-earnings',
            ]
            if is_held:
                bs.append(f'{hi("HELD", GREEN)} · sell score {sell_sc:.0f} — FW damping holding sell below TRIM')
            else:
                bs.append('Not a held position')
            cards += card(ticker, f'SUE {sue:.2f}', f'PEAD · EXP {expires}', PURPLE, '#12082a',
                         bs, 'HOLD — PEAD ACTIVE' if is_held else 'WATCH ONLY',
                         BLUE if is_held else TXT3)
        html += section('⚡ Active PEAD Signals', cards)

    # Held sell monitor
    sell_held = sorted([s for s in signals if s.get('is_holding') and s.get('sell_score',0) >= 30],
                       key=lambda x: -x.get('sell_score', 0))
    if sell_held:
        cards = ''
        for s in sell_held[:4]:
            sc   = s.get('sell_score', 0)
            fw   = s.get('framework_score', '?')
            dist = s.get('dist_252h', 0)
            act  = 'EXIT' if sc>=78 else 'REDUCE' if sc>=65 else 'TRIM' if sc>=52 else 'HOLD'
            acol = RED if act=='EXIT' else AMBER if act in ('REDUCE','TRIM') else TXT3
            bs   = [
                f'Sell score {b(f"{sc:.0f}")} · Action: {hi(act, acol)} · FW {fw}',
                f'Dist from 252d high: {dist:+.1f}%',
            ]
            if sc < 52:
                bs.append(f'{hi("FW damping keeps score below TRIM — hold", GREEN)}')
            cards += card(s['ticker'], f'${s.get("price",0):,.2f}',
                         f'{act} · {sc:.0f}', acol, '#1a0a0a' if sc>=78 else '#1a1200' if sc>=52 else BG2,
                         bs, act, acol)
        html += section('📊 Held Positions — Sell Monitor', cards)

    html += note_bar('📋 Weekly summary sent every Monday — insider signals, PEAD, regime review, watchlist')
    html += footer(f'portfolio-signals · {run_date}<br>2× daily · 06:00 + 18:00 BKK',
                   'Buy ≥80 · TRIM ≥52<br>REDUCE ≥65 · EXIT ≥78')
    return wrap(html, f'Portfolio Signals · {run_date}')


# ── WEEKLY EMAIL ─────────────────────────────────────────────────────
def build_weekly(payload, insider_data, pead_data):
    market   = payload.get('market', {})
    signals  = payload.get('analytics', {}).get('signals', [])
    run_date = payload.get('run_date', str(date.today()))
    log      = load_weekly_log()
    entries  = log.get('entries', [])

    vix_z   = market.get('vix_zscore', 0)
    vix_cur = market.get('vix_current', 20)
    vix_lbl = 'Neutral' if abs(vix_z) < 1 else ('Fear' if vix_z > 1 else 'Greed')
    spy_ok  = not market.get('sp500_extended', False)
    ins_sigs = insider_data.get('signals', {}) if insider_data else {}
    pead_sigs= pead_data.get('signals', {}) if pead_data else {}
    held     = [s for s in signals if s.get('is_holding')]
    exits    = sum(1 for s in held if s.get('sell_score',0) >= 78)
    reduces  = sum(1 for s in held if 65 <= s.get('sell_score',0) < 78)
    new_buys = sum(1 for s in signals if s.get('signal') == 'BUY')
    top_score= max((s.get('buy_score',0) for s in signals if s.get('fdfv3')), default=0)

    headline = f'{exits} exits · {reduces} reduces · {len(ins_sigs)} insider signals · {len(pead_sigs)} PEAD active'
    subline  = f'Regime: {vix_lbl} all week · SPY {"above" if spy_ok else "below"} 200d MA'

    html  = header(f'Weekly Summary · Week ending {run_date}', headline, subline)
    html += mkt_strip([
        ('VIX Range', f'{vix_cur:.1f} z={vix_z:+.2f} · {vix_lbl}', BLUE),
        ('SPY 200d',  'Above all week ✓' if spy_ok else 'Below ⚠', GREEN if spy_ok else RED),
        ('Buy Caps',  f'{market.get("strong_cap",3)}/{market.get("regular_cap",3)} available', TXT),
    ])

    # Stats grid
    html += section('Week in Numbers',
        stat_grid([
            (len(ins_sigs),  'Insider Signals', AMBER),
            (len(pead_sigs), 'PEAD Active',     PURPLE),
            (exits or '0',   'EXIT / REDUCE',   GREEN if exits==0 else RED),
            (new_buys or '0','New Buys',         GREEN if new_buys else TXT3),
            (top_score or '—','Top Watch Score', BLUE),
            ('HOLD' if exits==0 and reduces==0 else 'ACT', 'Held Status', GREEN if exits==0 and reduces==0 else RED),
        ])
    )

    # Signals fired this week
    if entries:
        EXPIRY_LABELS = {
            'price_recovered': ('DO NOT CHASE', TXT3, 'Price recovered — gate closed'),
            'dfv3_faded':      ('WAIT FOR CONFIRMATION', AMBER, 'Factor gate open but DFV momentum faded'),
            'penalty_active':  ('AVOID', RED, 'Signal overridden by penalty'),
            'sell_cleared':    ('SELL SIGNAL CLEARED', BLUE, 'No longer flagged — reassess hold'),
        }
        cards = ''
        seen = set()
        for e in entries:
            tk = e.get('ticker','')
            if tk in seen: continue
            seen.add(tk)
            sig_type   = e.get('signal_type', 'WATCH')
            fired_date = e.get('timestamp', '')[:10]
            score_fire = e.get('score_at_fire', 0)
            expired    = e.get('expired', False)
            reason     = e.get('expire_reason', '')
            price_chg  = e.get('price_change_pct', 0) or 0
            is_held    = e.get('is_holding', False)
            fw         = e.get('framework_score', '?')

            if expired:
                lbl, acol, desc = EXPIRY_LABELS.get(reason, ('EXPIRED', TXT3, reason))
                badge_txt = f'EXPIRED · {fired_date}'
                badge_col = acol
                badge_bg  = '#1a1200' if acol==AMBER else '#1a0a0a' if acol==RED else BG2
            else:
                lbl, acol, desc = 'ACTIVE', BLUE, 'Still in signal zone'
                badge_txt = f'ACTIVE · {sig_type}'
                badge_col = BLUE
                badge_bg  = '#0c1a3a'

            pchg_col  = GREEN if price_chg > 0 else RED if price_chg < 0 else TXT3
            bs = [
                f'Signal: {b(sig_type)} · Score at fire: {b(str(score_fire))} · FW {fw}{"  · HELD" if is_held else ""}',
                f'Price since signal: {hi(f"{price_chg:+.1f}%", pchg_col)}',
                hi(desc, acol),
            ]
            cards += card(tk, f'Fired {fired_date}', badge_txt, badge_col, badge_bg, bs, lbl, acol)
        html += section('📅 Signals That Fired This Week', cards)

    # Insider signals
    if ins_sigs:
        cards = ''
        for ticker, ins in list(ins_sigs.items())[:5]:
            is_cluster = ins.get('insider_cluster', False)
            buys = ins.get('insider_buys', [])
            bs = []
            if is_cluster:
                bs.append(f'{b("Cluster buy")} — {ins.get("insider_n",2)}+ insiders, same 60-day window')
            for bv in buys[:2]:
                title = (bv.get('title') or '').split(',')[0]
                val   = bv.get('value_usd', 0)
                dt    = bv.get('date', '')
                bs.append(f'{title} bought {hi(f"${val:,.0f}", AMBER)} on {dt}')
            ins_sc  = ins.get('insider_score', 0)
            ins_lbl = 'CLUSTER' if is_cluster else 'INSIDER'
            cards += card(ticker, '', f'{ins_lbl} · +{ins_sc}pts',
                         AMBER, '#1a1200', bs, 'WATCH FOR FACTOR GATE OPENING', AMBER)
        html += section('🔑 Insider Buying This Week', cards)

    # PEAD signals
    if pead_sigs:
        cards = ''
        for ticker, ps in list(pead_sigs.items())[:4]:
            sue     = ps.get('sue', 0)
            expires = ps.get('expires_at', '?')
            ann     = ps.get('announce_date', '?')
            is_held = any(s.get('ticker')==ticker and s.get('is_holding') for s in signals)
            cards += card(ticker, f'SUE {sue:.2f}', f'EXP {expires}', PURPLE, '#12082a',
                         [f'Announced {b(ann)} · top-quintile surprise · drift window 60 days',
                          hi('HELD', GREEN) + ' · +10pts on buy score' if is_held else 'Not held'],
                         'HOLD — PEAD ACTIVE' if is_held else 'WATCH ONLY',
                         BLUE if is_held else TXT3)
        html += section('⚡ PEAD Signals Active', cards)

    # Held positions table
    if held:
        rows = ''
        for s in sorted(held, key=lambda x: -x.get('sell_score',0))[:8]:
            sc   = s.get('sell_score', 0)
            act  = 'EXIT' if sc>=78 else 'REDUCE' if sc>=65 else 'TRIM' if sc>=52 else 'HOLD'
            acol = RED if act=='EXIT' else AMBER if act in ('REDUCE','TRIM') else GREEN
            note = s.get('guidance','')[:55]
            rows += f"""<tr>
<td style="{MONO}font-size:12px;font-weight:700;color:{TXT};padding:9px 10px;border-bottom:1px solid #0f1720;">{s['ticker']}</td>
<td style="{MONO}font-size:12px;color:{TXT2};padding:9px 10px;border-bottom:1px solid #0f1720;">{s.get('buy_score','—')}</td>
<td style="{MONO}font-size:12px;color:{AMBER if sc>=30 else TXT3};padding:9px 10px;border-bottom:1px solid #0f1720;">{sc:.0f}</td>
<td style="{MONO}font-size:12px;font-weight:600;color:{acol};padding:9px 10px;border-bottom:1px solid #0f1720;">{act}</td>
<td style="{FONT}font-size:11px;color:{TXT3};padding:9px 10px;border-bottom:1px solid #0f1720;">{note}</td>
</tr>"""
        tbl = f"""<table width="100%" cellpadding="0" cellspacing="0" style="background:{BG2};border:{BORDER};border-radius:10px;margin-bottom:10px;">
<tr>
<th style="{MONO}font-size:9px;text-transform:uppercase;letter-spacing:.1em;color:{TXT3};padding:9px 10px;border-bottom:1px solid #151f2e;text-align:left;font-weight:500;">Ticker</th>
<th style="{MONO}font-size:9px;text-transform:uppercase;letter-spacing:.1em;color:{TXT3};padding:9px 10px;border-bottom:1px solid #151f2e;text-align:left;font-weight:500;">Buy</th>
<th style="{MONO}font-size:9px;text-transform:uppercase;letter-spacing:.1em;color:{TXT3};padding:9px 10px;border-bottom:1px solid #151f2e;text-align:left;font-weight:500;">Sell</th>
<th style="{MONO}font-size:9px;text-transform:uppercase;letter-spacing:.1em;color:{TXT3};padding:9px 10px;border-bottom:1px solid #151f2e;text-align:left;font-weight:500;">Action</th>
<th style="{MONO}font-size:9px;text-transform:uppercase;letter-spacing:.1em;color:{TXT3};padding:9px 10px;border-bottom:1px solid #151f2e;text-align:left;font-weight:500;">Note</th>
</tr>{rows}</table>"""
        note = '' if exits or reduces else bullet_list([hi('No EXIT or REDUCE signals on held positions this week', GREEN)])
        html += section('📋 Held Positions — Weekly Review', tbl + note)

    # Priority watch
    top_watch = sorted([s for s in signals if s.get('fdfv3')],
                       key=lambda x: (-x.get('buy_score',0), -(x.get('framework_score') or 0)))[:2]
    if top_watch:
        cards = ''
        for s in top_watch:
            fw   = s.get('framework_score', '?')
            dist = s.get('dist_252h', 0)
            gap  = 80 - s.get('buy_score', 0)
            dfv  = s.get('dfv_lift', 0) or 0
            cards += card(s['ticker'], f'FW {fw} · Score {s["buy_score"]}',
                         f'{gap} PTS FROM TRIGGER', BLUE, '#0c1a3a',
                         [f'Down {b(f"{abs(dist):.1f}%")} from 252d high · DFV lift {hi(f"{dfv:.1f}")}',
                          f'{hi(str(gap) + " points", AMBER)} below buy trigger'],
                         'PRIMARY WATCHLIST ENTRY FOR COMING WEEK', BLUE)
        html += section('👀 Priority Watch — Coming Week', cards)

    html += note_bar(f'📊 Daily digest sent 18:00 Bangkok · Next weekly: Mon {run_date}')
    html += footer(f'portfolio-signals · Weekly · {run_date}<br>Monday 07:00 BKK',
                   'Buy ≥80 · TRIM ≥52<br>REDUCE ≥65 · EXIT ≥78')
    return wrap(html, f'Portfolio Signals · Weekly · {run_date}')


# ── SEND EMAIL ────────────────────────────────────────────────────────
def send_email(subject, html_body):
    if not all([GMAIL_FROM, GMAIL_PASS, GMAIL_TO_LIST]):
        print('ERROR: GMAIL_FROM, GMAIL_APP_PASSWORD, GMAIL_TO must all be set')
        sys.exit(1)

    html_b64 = base64.b64encode(html_body.encode('utf-8')).decode('ascii')
    html_b64_lines = '\r\n'.join(html_b64[i:i+76] for i in range(0, len(html_b64), 76))
    boundary = 'sig_' + base64.b64encode(os.urandom(9)).decode('ascii').replace('=','').replace('+','x').replace('/','y')

    # To header shows all recipients
    to_header = ', '.join(GMAIL_TO_LIST)

    raw = '\r\n'.join([
        f'From: Portfolio Signals <{GMAIL_FROM}>',
        f'To: {to_header}',
        f'Subject: {subject}',
        f'Date: {formatdate(localtime=True)}',
        f'Message-ID: {make_msgid()}',
        'MIME-Version: 1.0',
        f'Content-Type: multipart/alternative; boundary="{boundary}"',
        '',
        f'--{boundary}',
        'Content-Type: text/plain; charset=utf-8',
        'Content-Transfer-Encoding: quoted-printable',
        '',
        'Portfolio Signals digest. Please view in an HTML-capable email client.',
        '',
        f'--{boundary}',
        'Content-Type: text/html; charset=utf-8',
        'Content-Transfer-Encoding: base64',
        '',
        html_b64_lines,
        '',
        f'--{boundary}--',
    ])

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(GMAIL_FROM, GMAIL_PASS)
            server.sendmail(GMAIL_FROM, GMAIL_TO_LIST, raw.encode('utf-8'))
        print(f'Email sent to {len(GMAIL_TO_LIST)} recipient(s): {subject}')
    except Exception as e:
        print(f'Email failed: {e}')
        sys.exit(1)


# ── MAIN ─────────────────────────────────────────────────────────────
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
    else:
        html    = build_weekly(payload, insider_data, pead_data)
        n_ins   = len(load_insider().get('signals', {}))
        n_pead  = len(load_pead().get('signals', {}))
        subject = f'📋 Weekly Summary · {run_date} · {n_ins} insider · {n_pead} PEAD'

    send_email(subject, html)

if __name__ == '__main__':
    main()
