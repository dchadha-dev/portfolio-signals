"""Clean wide panels from pystock-data: total-return price index, adjusted OHLC, volume, eligibility."""
import pandas as pd, numpy as np
d=pd.read_parquet('pystock_long.parquet')
g=d.groupby('symbol',observed=True)['date'].count(); keep=g[g>=400].index
d=d[d['symbol'].isin(keep)]
W={c:d.pivot(index='date',columns='symbol',values=c) for c in ['close','adj_close','high','low','volume']}
del d
C=W['close']; A=W['adj_close']; V=W['volume']
# drop non-trading days (holidays carried with zero volume / no rows)
nz=(V>0).sum(axis=1); live=nz>0.5*nz.max()
print('rows',len(C),'trading days kept',int(live.sum()))
for k in W: W[k]=W[k][live]
C=W['close']; A=W['adj_close']; V=W['volume']; H=W['high']; L=W['low']
seam=pd.Timestamp('2015-03-20')
r_init=A/A.shift(1)-1                      # consistent snapshot through the seam
r_daily=C/A.shift(1)-1                     # after: yesterday's adj is already in today's terms
m=np.broadcast_to((C.index<=seam)[:,None], C.shape)
# Daily period: Yahoo's adj_close sometimes reflects a split 1-2 days before it is
# effective in raw closes. When the three candidate returns disagree by a split-sized
# amount, take the one with the smallest absolute log move (the basis-consistent one).
cand=np.stack([np.log((A/A.shift(1)).values), np.log((C/A.shift(1)).values), np.log((C/C.shift(1)).values)])
spread=np.nanmax(cand,axis=0)-np.nanmin(cand,axis=0)
best=np.take_along_axis(cand, np.nanargmin(np.abs(np.where(np.isnan(cand),np.inf,cand)),axis=0)[None],axis=0)[0]
r_fix=pd.DataFrame(np.where(spread>0.15, np.expm1(best), r_daily.values), index=C.index, columns=C.columns)
print('split-basis fixes in daily period:', int(((spread>0.15)&~m).sum()))
r=r_init.where(m, r_fix)
# bad ticks: a >+100% day that is >60% reversed the next day (or the mirror) -> treat both as flat
nxt=r.shift(-1); spike=((r>1.0)&(nxt<-0.4))|((r<-0.5)&(nxt>0.8))
r=r.mask(spike,0.0).mask(spike.shift(1,fill_value=False),0.0)
r=r.clip(lower=-0.95,upper=3.0)
print('bad-tick pairs removed',int(spike.sum().sum()))
P=(1+r.fillna(0)).cumprod().where(C.notna())  # total-return index, NaN outside listing
adj=P/C
dv=(C*V).rolling(60,min_periods=40).median()
elig=(C>=5)&(dv>=2e6)&(P.notna())
out=dict(P=P.astype('float64'),H=(H*adj).astype('float64'),L=(L*adj).astype('float64'),V=V.astype('float64'),C=C.astype('float32'),elig=elig)
pd.to_pickle(out,'panel.pkl')
print('symbols',P.shape[1],'days',P.shape[0],P.index[0].date(),P.index[-1].date())
print('eligible names per day:',elig.sum(axis=1).describe().round(0).to_dict())
for s in ['AAPL','NFLX','QQQ','ACI','GOOGL','BAC']:
    if s in P: x=P[s].dropna(); print(s,'total return x',round(x.iloc[-1]/x.iloc[0],2),'max 1d',round(r[s].max(),2),'min 1d',round(r[s].min(),2))
re=r.where(elig.shift(1,fill_value=False)); ew=re.mean(axis=1)
print('EW eligible universe ann return %.1f%%, vol %.1f%%'%(ew.mean()*252*100, ew.std()*np.sqrt(252)*100))
print('|r|>50% among eligible:',int((re.abs()>0.5).sum().sum()))
