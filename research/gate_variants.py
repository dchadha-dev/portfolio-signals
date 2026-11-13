import warnings; warnings.filterwarnings('ignore')
import sys, os, pandas as pd, numpy as np, siglib as sl
sys.path.insert(0,'/tmp/claude-0/-home-claude/4641050d-2c5a-5f89-98d1-d4082914d9d2/scratchpad/stubs'); sys.path.insert(1,'/home/claude/work/portfolio-signals')
cwd=os.getcwd(); os.chdir('/home/claude/work/portfolio-signals'); import signal_scanner as sc; os.chdir(cwd)
pan=pd.read_pickle('sp5_panel.pkl'); P0,M0=pan['P'],pan['M']; S0=pd.read_pickle('sp5_sig.pkl')
mine=[t for t in dict.fromkeys(sc.DIRECT_HOLDINGS+sc.CANDIDATES) if t in P0.columns and P0[t].loc['2026-10':].notna().sum()>5]
A,B='2011-01-01','2026-11-10'
full=[t for t in P0.columns if M0[t].loc[A:B].any() and P0[t].loc['2011-01':'2011-03'].notna().any() and P0[t].loc['2026-10':B].notna().any()]
rnd=list(pd.Index(full).to_series().sample(150,random_state=11))
rows=[]
for un,cols in {'yours':mine,'random survivors':rnd,'PIT S&P':list(M0.columns[M0.loc[A:B].any()])}.items():
    s=lambda X: X[cols].loc['2010-11':B]
    P=s(P0); rn=P.pct_change(fill_method=None).clip(-0.95,3).shift(-1)
    dist,vol,q=s(S0['dist']),s(S0['vol126']),s(S0['quality']); up=s(S0['trend'])>0; f=s(S0['f']).fillna(False).astype(bool); d3=s(S0['dfv3']).fillna(False).astype(bool)
    rsi=s(sl.rsi(P0[cols].loc['2010-01':B],14)); bw=s(S0['banker_weak']).fillna(False).astype(bool); rb=s(S0['rbear']).fillna(False).astype(bool)
    E=dist.notna()&q.notna()
    if un=='PIT S&P': E=E&s(M0).astype(bool)
    g=lambda d,qt: (dist<d)&up&(q>qt)
    C={'gate (as is)':f,'gate & DFV3':f&d3,'gate, no DFV3':f&~d3,'gate, first 21d only':f&~f.shift(21,fill_value=False),'gate & RSI14<45':f&(rsi<45),'gate & RSI14>=45':f&(rsi>=45),
       'gate & banker weak':f&bw,'gate & rbear':f&rb,'dist<-15':g(-.15,.2),'dist<-25':g(-.25,.2),'dist<-30':g(-.30,.2),'quality>0':g(-.2,0),'quality>0.33':g(-.2,.33),'quality>0.5':g(-.2,.5),
       'dip+quality, below 200d':(dist<-.2)&~up&(q>.2),'dip+trend, low quality':(dist<-.2)&up&(q<=.2),'quality+trend, no dip':(dist>-.1)&up&(q>.2)}
    qq=(vol.where(E).rank(axis=1,pct=True)*3).clip(upper=2.999).fillna(-1).astype(int); adj=pd.DataFrame(np.nan,index=P.index,columns=P.columns)
    for k in range(3):
        m=E&(qq==k); adj=adj.where(~m, rn.sub(rn.where(m).mean(axis=1),axis=0))
    for k,m in C.items():
        ent=(m.fillna(False)&E).shift(1,fill_value=False); o={'universe':un,'rule':k,'state%':round(100*(m&E).loc[A:B].sum().sum()/E.loc[A:B].sum().sum(),1)}
        for h in (63,252):
            act=ent.rolling(h,min_periods=1).max().astype(bool); ex=adj.where(act).mean(axis=1).where(act.sum(1)>=3).loc[A:B].dropna()
            o[f'vm{h}']=round(ex.mean()*252*100,1) if len(ex)>250 else np.nan; o[f't{h}']=round(ex.mean()/ex.std()*np.sqrt(len(ex)),1) if len(ex)>250 else np.nan
        rows.append(o)
R=pd.DataFrame(rows); pd.set_option('display.width',250)
print(R.pivot_table(index='rule',columns='universe',values=['state%','vm63','t63','vm252','t252'],sort=False).swaplevel(axis=1).sort_index(axis=1,level=0,sort_remaining=False).to_string())
