import pandas as pd, numpy as np, pickle, siglib as sl, time
t=time.time(); pan=pd.read_pickle('panel.pkl'); P,H,L,V,E=pan['P'],pan['H'],pan['L'],pan['V'],pan['elig']
S=sl.compute(P,H,L,V); print('signals',round(time.time()-t),'s')
E = E & S['dist'].notna() & S['quality'].notna()
pd.to_pickle({'S':S,'E':E},'signals.pkl')
F={h:sl.fwd_returns(P,h) for h in (21,63,126,252)}
pd.to_pickle(F,'fwd.pkl')
print('eligible w/ signals per day', E.sum(1).loc['2010-02':].describe().round(0).to_dict())
for k in ['f','dfv3','fdfv3','pfd','triple','banker_weak','rbear','near_high','cmf_neg_near_high','atr_ext_near_high','rv_z2']:
    m=(S[k]&E).loc['2010-02':]; print(f'{k:<20} avg names/day {m.sum(1).mean():7.1f}  share of eligible {m.sum().sum()/E.loc["2010-02":].sum().sum()*100:5.1f}%')
b=S['buy'].where(E).loc['2010-02':]; print('buy>=80 per day', (b>=80).sum(1).mean().round(1), ' 60-79', ((b>=60)&(b<80)).sum(1).mean().round(1))
s=S['sell'].where(E).loc['2010-02':]; print('sell raw >=52', (s>=52).sum(1).mean().round(1), '>=65', (s>=65).sum(1).mean().round(1), '>=78', (s>=78).sum(1).mean().round(1))
