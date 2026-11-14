import warnings; warnings.filterwarnings('ignore')
import pandas as pd, numpy as np, siglib as sl
d=pd.read_pickle('signals.pkl'); S,E=d['S'],d['E']; pan=pd.read_pickle('panel.pkl'); P=pan['P']
r=P.pct_change(fill_method=None)
def caltime(mask, hold, name):
    """Calendar-time portfolio: enter at next close after the signal, hold `hold` days, equal weight.
    Daily return vs equal-weight eligible universe -> no overlapping-window problem."""
    ent=(mask&E).shift(1,fill_value=False)                       # tradable the day after the signal
    active=ent.rolling(hold,min_periods=1).max().astype(bool)    # in portfolio for `hold` days
    rn=r.shift(-1)                                               # return earned over the next day
    pr=rn.where(active).mean(axis=1); n=active.sum(1)
    ur=rn.where(E).mean(axis=1)
    ex=(pr-ur).where(n>=5).loc['2010-02-01':].dropna()
    ann=ex.mean()*252*100; te=ex.std()*np.sqrt(252)*100; t=ex.mean()/ex.std()*np.sqrt(len(ex))
    _,tnw,_=sl.nw_tstat(ex.values,10)
    h1=ex.loc[:'2013-06-30'].mean()*252*100; h2=ex.loc['2013-07-01':].mean()*252*100
    print(f'{name:<34} hold {hold:>3}d  names {n.loc["2010-02":].mean():7.1f}  excess {ann:+6.2f}%/yr  TE {te:5.1f}%  IR {ann/te:+.2f}  t {t:+.2f} (NW {tnw:+.2f})  2010-13 {h1:+6.2f}  2013-17 {h2:+6.2f}')
print('Calendar-time portfolios vs equal-weight liquid universe (2010-02..2017-03)')
for nm,m in [('factor gate (the +40 anchor)',S['f']),('factor + DFV3',S['fdfv3']),('buy score >= 60',S['buy']>=60),('dist < -20% only',S['dist']<-0.2),
             ('near 52w high (SELL trigger)',S['near_high']),('sell score >= 52',S['sell']>=52),('triple',S['triple']),('PFD',S['pfd'])]:
    for hold in (63,252): caltime(m.fillna(False),hold,nm)
# how reachable are the sell thresholds?
s=S['sell'].where(E).loc['2012-06-01':]; tot=s.notna().sum().sum()
print('\nShare of stock-days by raw sell score (before FW damping, neutral VIX):')
for fw,lab in [(None,'no FW'),(68,'FW 68'),(80,'FW 80'),(85,'FW 85 (BKNG)'),(93,'FW 93 (NVDA)')]:
    damp=1.0 if fw is None else 1-0.5*(fw/100)**1.5
    print(f'  {lab:<14} damp x{damp:.2f}: TRIM {((s*damp)>=52).sum().sum()/tot*100:6.3f}%  REDUCE {((s*damp)>=65).sum().sum()/tot*100:6.3f}%  EXIT {((s*damp)>=78).sum().sum()/tot*100:6.3f}%   max possible {min(100,95*damp):.0f}')
print('  raw score percentiles:', s.stack().quantile([.5,.9,.99,.999,1]).round(1).to_dict())
