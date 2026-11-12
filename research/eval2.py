import warnings; warnings.filterwarnings('ignore')
import pandas as pd, numpy as np, siglib as sl, sys, re
sys.path.insert(0,'/tmp/claude-0/-home-claude/4641050d-2c5a-5f89-98d1-d4082914d9d2/scratchpad/stubs'); sys.path.insert(1,'/home/claude/work/portfolio-signals')
d=pd.read_pickle('signals.pkl'); S,E=d['S'],d['E']; F=pd.read_pickle('fwd.pkl')
idx,cols=E.index,E.columns
# point-in-time S&P 500 membership
sp=pd.read_csv('/home/claude/data/sp500/S&P 500 Historical Components & Changes (Updated).csv',parse_dates=['date']).sort_values('date')
sp=sp[sp.date<=idx[-1]]
Mv=np.zeros(E.shape,dtype=bool)
pos=np.searchsorted(sp['date'].values, idx.values, side='right')-1
cache={}
for i,p in enumerate(pos):
    if p<0: continue
    if p not in cache: cache[p]=cols.isin([t.strip().replace('.','-') for t in sp['tickers'].iloc[p].split(',')]) | cols.isin([t.strip() for t in sp['tickers'].iloc[p].split(',')])
    Mv[i]=cache[p]
M=pd.DataFrame(Mv,index=idx,columns=cols)
print('PIT S&P500 members matched per day:', M.sum(1).describe().round(0)[['min','50%','max']].to_dict())
# his own 2026 universe (hindsight)
import os; os.chdir('/home/claude/work/portfolio-signals'); import signal_scanner as sc; os.chdir('/home/claude/research')
mine=[t for t in dict.fromkeys(sc.DIRECT_HOLDINGS+sc.CANDIDATES) if t in cols]
fw70=[t for t in mine if (sc.FRAMEWORK_SCORES.get(t) or 0)>=70]
print('his 2026 universe present in 2009-17 data:',len(mine),'| of which FW>=70:',len(fw70))
U={'ALL liquid US stocks':E, 'Point-in-time S&P 500':E&M, 'His 2026 universe (hindsight)':E&pd.DataFrame(np.broadcast_to(cols.isin(mine),E.shape),index=idx,columns=cols),
   'His FW>=70 names (hindsight)':E&pd.DataFrame(np.broadcast_to(cols.isin(fw70),E.shape),index=idx,columns=cols)}
# ex-ante "compounder" proxy: top third of trailing 3y return AND above 200d MA 12 months ago is unknowable; use trailing 3y return only
P=pd.read_pickle('panel.pkl')['P']; r3=P.pct_change(756,fill_method=None).where(E); U['Ex-ante winners (top 1/3 trailing 3y return)']=E&(r3.rank(axis=1,pct=True)>2/3)
tests={'factor gate':S['f'],'factor+DFV3':S['fdfv3'],'dist<-20%':S['dist']<-0.20,'triple':S['triple'],'buy>=60':S['buy']>=60,'SELL near_high':S['near_high'],'SELL score>=52':S['sell']>=52}
rows=[]
for un,Em in U.items():
    base={h:sl.daily_spread(Em,Em,F[h]) for h in (63,252)}
    for k,m in tests.items():
        for h in (63,252):
            ds=sl.daily_spread(m.fillna(False),Em,F[h],min_names=3).loc['2010-02-01':]
            mm,t,n=sl.nw_tstat(ds['spread'],h)
            rows.append(dict(universe=un,signal=k,h=h,names_day=round(ds['n'].mean(),1),sig_ret=np.nanmean(ds['sig'])*100,others_ret=np.nanmean(ds['non'])*100,spread=mm*100 if mm==mm else np.nan,t=t,days=n))
R=pd.DataFrame(rows); R.to_csv('eval2.csv',index=False)
pd.set_option('display.width',250); pd.set_option('display.max_rows',300)
for h in (63,252):
    print(f'\n=== {h}d forward return, % ===')
    print(R[R.h==h].drop(columns='h').round(2).to_string(index=False))
# universe-level hindsight premium: mean 252d fwd return of each universe vs all
for un,Em in U.items():
    f=F[252].where(Em).loc['2010-02-01':]; a=F[252].where(E).loc['2010-02-01':]
    print(f'{un:<45} avg 252d fwd return {np.nanmean(f.mean(1))*100:6.1f}%  vs all liquid {np.nanmean(a.mean(1))*100:6.1f}%')
