import warnings; warnings.filterwarnings('ignore')
import sys, os, pandas as pd, numpy as np, siglib as sl
sys.path.insert(0,'/tmp/claude-0/-home-claude/4641050d-2c5a-5f89-98d1-d4082914d9d2/scratchpad/stubs'); sys.path.insert(1,'/home/claude/work/portfolio-signals')
cwd=os.getcwd(); os.chdir('/home/claude/work/portfolio-signals'); import signal_scanner as sc; os.chdir(cwd)
pan=pd.read_pickle('sp5_panel.pkl'); P0,M0,V0=pan['P'],pan['M'],pan['V']; S0=pd.read_pickle('sp5_sig.pkl')
mine=[t for t in dict.fromkeys(sc.DIRECT_HOLDINGS+sc.CANDIDATES) if t in P0.columns and P0[t].loc['2026-10':].notna().sum()>5]
r0=P0.pct_change(fill_method=None).clip(-0.95,3)
mk=(1+r0.where(M0.astype(bool)).mean(axis=1).fillna(0)).cumprod(); mdist=mk/mk.rolling(252).max()-1   # equal-weight S&P drawdown
print('share of days market 10pct off high:', round(100*float((mdist.loc['2011':]<-0.10).mean())))
B='2026-11-10'
full=[t for t in P0.columns if M0[t].loc['2011':B].any() and P0[t].loc['2011-01':'2011-03'].notna().any() and P0[t].loc['2026-10':B].notna().any()]
rnd=list(pd.Index(full).to_series().sample(150,random_state=11))
rows=[]
for un,cols,A in [('yours 2011-26',mine,'2011-01-01'),('random survivors 2011-26',rnd,'2011-01-01'),('PIT S&P 1997-2026',list(M0.columns[M0.any()]),'1997-01-01')]:
    A0=str(pd.Timestamp(A)-pd.Timedelta(days=40))[:10]; s=lambda X: X[cols].loc[A0:B]
    P=s(P0); r=s(r0); rn=r.shift(-1); dist,vol,q,tr=s(S0['dist']),s(S0['vol126']),s(S0['quality']),s(S0['trend']); f=s(S0['f']).fillna(False).astype(bool)
    E=dist.notna()&q.notna()
    if un.startswith('PIT'): E=E&s(M0).astype(bool)
    md=mdist.loc[A0:B]; mkt=pd.DataFrame(np.broadcast_to((md<-0.10).values[:,None],P.shape),index=P.index,columns=P.columns)
    Pf=P0[cols]; hi_age=s(Pf.rolling(252).apply(lambda x: 251-np.argmax(x),raw=True).shift(1)) if len(cols)<=160 else None
    V=V0[cols]; volr=s(V.rolling(21).mean()/V.rolling(252).mean())
    gap=s(r0[cols].rolling(21).min())
    m121=s(Pf.shift(21)/Pf.shift(252)-1)
    C={'gate (all)':f,'  in a market sell-off (mkt 10%+ off high)':f&mkt,'  market near highs (stock-specific dip)':f&~mkt,
       '  heavy volume (21d/252d > 1.3)':f&(volr>1.3),'  normal volume':f&(volr<=1.3),
       '  had a 1-day drop of 10%+ in last 21d':f&(gap<-0.10),'  no 1-day shock':f&(gap>=-0.10),
       '  well above 200d (>10%)':f&(tr>0.10),'  just above 200d (0-10%)':f&(tr<=0.10),
       '  12-1 momentum > 50%':f&(m121>0.5),'  12-1 momentum <= 50%':f&(m121<=0.5)}
    if hi_age is not None: C.update({'  fast dip (high set < 63d ago)':f&(hi_age<63),'  slow dip (high 63d+ ago)':f&(hi_age>=63)})
    qq=(vol.where(E).rank(axis=1,pct=True)*3).clip(upper=2.999).fillna(-1).astype(int); adj=pd.DataFrame(np.nan,index=P.index,columns=P.columns)
    for k in range(3):
        m=E&(qq==k); adj=adj.where(~m, rn.sub(rn.where(m).mean(axis=1),axis=0))
    for k,m in C.items():
        ent=(m.fillna(False)&E).shift(1,fill_value=False); o={'universe':un,'rule':k,'share':round(100*(m&E).loc[A:B].sum().sum()/max(1,(f&E).loc[A:B].sum().sum()))}
        for h in (63,252):
            act=ent.rolling(h,min_periods=1).max().astype(bool); ex=adj.where(act).mean(axis=1).where(act.sum(1)>=3).loc[A:B].dropna()
            ok=len(ex)>250; o[f'vm{h}']=round(ex.mean()*252*100,1) if ok else np.nan; o[f't{h}']=round(ex.mean()/ex.std()*np.sqrt(len(ex)),1) if ok else np.nan
        rows.append(o)
    print(un,'done',flush=True)
R=pd.DataFrame(rows); R.to_csv('gatefeat.csv',index=False); pd.set_option('display.width',250)
for un in R.universe.unique(): print('\n',un); print(R[R.universe==un].drop(columns='universe').to_string(index=False))
