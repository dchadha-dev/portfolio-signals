import warnings; warnings.filterwarnings('ignore')
import pandas as pd, numpy as np, siglib as sl
d=pd.read_pickle('signals.pkl'); S,E=d['S'],d['E']; F=pd.read_pickle('fwd.pkl')
# volatility quintile per day among eligible names
vol=S['vol126'].where(E); G=(vol.rank(axis=1,pct=True)*5).clip(upper=4.999).fillna(-1).astype(int)
Fadj={h:sl.group_demean(F[h],E,G) for h in F}
START='2010-02-01'; SPLIT='2013-07-01'
def row(name, mask, hs=(21,63,126,252)):
    out=[]
    for h in hs:
        ds=sl.daily_spread(mask,E,F[h]).loc[START:]
        m,t,n=sl.nw_tstat(ds['spread'],h)
        # vol-matched: signal names' mean vol-quintile-demeaned return
        da=sl.daily_spread(mask,E,Fadj[h]).loc[START:]
        ma,ta,_=sl.nw_tstat(da['sig'],h)
        m1,t1,_=sl.nw_tstat(ds['spread'].loc[:SPLIT],h); m2,t2,_=sl.nw_tstat(ds['spread'].loc[SPLIT:],h)
        out.append(dict(signal=name,h=h,names_day=round(ds['n'].mean(),1),spread=m*100,t=t,vol_adj=ma*100,t_va=ta,first_half=m1*100,second_half=m2*100,hit=(ds['spread']>0).mean()*100))
    return out
tests={
 'factor gate (f)':S['f'], 'DFV3 alone':S['dfv3']&~S['f'], 'factor+DFV3':S['fdfv3'], 'factor w/o DFV3':S['f']&~S['dfv3'],
 'PFD':S['pfd'], 'triple (63d>20%)':S['triple'], 'banker_weak (-20)':S['banker_weak'], 'rbear (-10)':S['rbear'],
 'buy score >=60':S['buy']>=60, 'buy score >=80':S['buy']>=80,
 'dist<-20% only':S['dist']<-0.20, 'trend>0 only':S['trend']>0, 'quality>0.2 only':S['quality']>0.2,
 'SELL near_high':S['near_high'], 'SELL cmf<0 near high':S['cmf_neg_near_high'], 'SELL atr>3.5 near high':S['atr_ext_near_high'], 'SELL rv_z>2':S['rv_z2'],
 'SELL score>=52':S['sell']>=52, 'SELL score>=60':S['sell']>=60,
}
rows=[]
for k,m in tests.items(): rows+=row(k,m.fillna(False))
R=pd.DataFrame(rows); R.to_csv('eval1.csv',index=False)
pd.set_option('display.width',250); pd.set_option('display.max_rows',200)
for h in (63,252):
    print(f'\n=== horizon {h}d: mean forward return of signal names minus other eligible names (pp), Newey-West t ===')
    print(R[R.h==h].drop(columns='h').round(2).to_string(index=False))
