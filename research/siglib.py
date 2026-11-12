"""Vectorised replica of signal_scanner.compute_signals + sell_side_scorer signals on wide panels,
and overlap-robust evaluation tools."""
import numpy as np, pandas as pd

def rsi(P, period):
    d = P.diff()
    g = d.clip(lower=0).ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    l = (-d).clip(lower=0).ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    return 100 - 100/(1 + g/l.replace(0, np.nan))

def compute(P, H, L, V, DIST_T=-0.20, QUALITY_T=0.20, DFV_LIFT=2.5):
    S = {}
    rsi14, rsi40, rsi47 = rsi(P,14), rsi(P,40), rsi(P,47)
    hm = ((rsi40-30)*0.7).clip(0,20)
    lift = hm - hm.rolling(10).min().shift(1)
    banker = ((rsi47-51)*1.5).clip(0,20)
    high252 = P.rolling(252).max().shift(1)
    dist = (P-high252)/high252
    ma200 = P.rolling(200).mean()
    trend = (P-ma200)/ma200
    ret1 = P.pct_change(fill_method=None)
    r252 = P.pct_change(252, fill_method=None); r126 = P.pct_change(126, fill_method=None); r63 = P.pct_change(63, fill_method=None)
    vol252 = ret1.rolling(252).std()*np.sqrt(252)
    quality = (r252/vol252.replace(0,np.nan)/3).clip(0,1)
    S['dist']=dist; S['trend']=trend; S['quality']=quality; S['lift']=lift; S['r252']=r252; S['r126']=r126; S['r63']=r63
    S['vol126']=ret1.rolling(126).std()*np.sqrt(252)
    f = (dist<DIST_T)&(trend>0)&(quality>QUALITY_T)
    dfv3 = lift>DFV_LIFT
    S['f']=f; S['dfv3']=dfv3; S['fdfv3']=f&dfv3
    S['pfd']=((r252-2*r126)>0.05)&(quality>QUALITY_T*2)
    S['triple']=r63>0.20
    S['banker_weak']=(banker.shift(1)>=20)&(banker<20)
    ra=(rsi14-60).clip(lower=0).ewm(span=3,adjust=False).mean()
    S['rbear']=(P>P.rolling(20).max().shift(1)*1.01)&(ra<ra.rolling(20).max().shift(1)*0.85)&(ra>0)
    buy = 40*f + 25*S['fdfv3'] + 8*S['pfd'] + 10*S['triple'] + 5*(dfv3&~f) - 20*S['banker_weak'] - 10*S['rbear']
    S['buy']=buy.clip(0,100)
    # sell side
    near_high = dist>-0.05
    tr = np.maximum(H-L, np.maximum((H-P.shift(1)).abs(), (L-P.shift(1)).abs()))
    atr14 = tr.rolling(14).mean()
    atr_dist = (P-ma200)/atr14.replace(0,np.nan)
    hl=(H-L).replace(0,np.nan); mfm=((P-L)-(H-P))/hl
    cmf=(mfm*V).rolling(20).sum()/V.rolling(20).sum()
    rv20=np.log(P/P.shift(1)).rolling(20).std()*np.sqrt(252)
    rvz=(rv20-rv20.rolling(756).mean())/rv20.rolling(756).std().replace(0,np.nan)
    dp=dist*100
    near_pts=(35*(1+dp/10)).clip(0,35).where(dp>-10,0)
    cmf_pts=(cmf.abs()*30).clip(upper=30).where(near_high&(cmf<0),0)
    atr_pts=((atr_dist-2)/3*20).clip(0,20).where(near_high&(atr_dist>2),0)
    rv_pts=((rvz-1)/2*10).clip(0,10).where(rvz>1,0).fillna(0)
    S['near_high']=near_high; S['cmf_neg_near_high']=near_high&(cmf<0); S['atr_gt35']=atr_dist>3.5; S['atr_ext_near_high']=near_high&(atr_dist>3.5)
    S['rv_z2']=rvz>2; S['atr_dist']=atr_dist; S['cmf']=cmf
    S['sell']=(near_pts+cmf_pts+atr_pts+rv_pts).clip(0,100).where(dist.notna())
    return S

def fwd_returns(P, h, end_fill=True):
    """Return from close t+1 to close t+1+h (signal at close t, trade next day).
    Delisted names are carried at their last price; windows past the data end are NaN."""
    Pf = P.ffill() if end_fill else P
    last_valid = P.apply(lambda c: c.last_valid_index())
    alive_to_end = last_valid >= P.index[-1]
    F = Pf.shift(-(1+h))/Pf.shift(-1) - 1
    F = F.where(P.notna())          # only when the name is actually trading on the signal day
    return F.clip(upper=5.0)

def nw_tstat(x, lag):
    x = np.asarray(x, float); x = x[~np.isnan(x)]; n=len(x)
    if n < 30: return np.nan, np.nan, n
    m = x.mean(); e = x-m
    lag = int(min(lag, n-2))
    var = (e@e)/n
    for k in range(1, lag+1):
        var += 2*(1-k/(lag+1))*(e[k:]@e[:-k])/n
    se = np.sqrt(max(var,1e-18)/n)
    return m, m/se, n

def daily_spread(Smask, E, F, min_names=5):
    """Per-day mean forward return of signal names minus non-signal eligible names."""
    s = (Smask & E).values; ns = (E & ~Smask).values; f = F.values
    ok = ~np.isnan(f)
    s = s & ok; ns = ns & ok
    cs = s.sum(1); cn = ns.sum(1)
    ms = np.where(cs>=min_names, np.nansum(np.where(s,f,0),1)/np.maximum(cs,1), np.nan)
    mn = np.where(cn>=min_names, np.nansum(np.where(ns,f,0),1)/np.maximum(cn,1), np.nan)
    return pd.DataFrame({'sig':ms,'non':mn,'spread':ms-mn,'n':cs}, index=F.index)

def group_demean(F, E, G, ngroups=5):
    """Demean forward returns within date x group (eligible names only)."""
    out = F.copy(); fv=F.values; ev=E.values & ~np.isnan(fv); gv=G.values
    res = np.full(fv.shape, np.nan)
    for g in range(ngroups):
        m = ev & (gv==g)
        c = m.sum(1)
        mu = np.where(c>0, np.nansum(np.where(m,fv,0),1)/np.maximum(c,1), np.nan)
        res = np.where(m, fv-mu[:,None], res)
    return pd.DataFrame(res, index=F.index, columns=F.columns)
