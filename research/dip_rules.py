import warnings; warnings.filterwarnings('ignore')
import sys, os, pandas as pd, numpy as np, siglib as sl
sys.path.insert(0,'/tmp/claude-0/-home-claude/4641050d-2c5a-5f89-98d1-d4082914d9d2/scratchpad/stubs'); sys.path.insert(1,'/home/claude/work/portfolio-signals')
cwd=os.getcwd(); os.chdir('/home/claude/work/portfolio-signals'); import signal_scanner as sc; os.chdir(cwd)
pan=pd.read_pickle('sp5_panel.pkl'); P0,M0=pan['P'],pan['M']; S0=pd.read_pickle('sp5_sig.pkl')
mine=[t for t in dict.fromkeys(sc.DIRECT_HOLDINGS+sc.CANDIDATES) if t in P0.columns and P0[t].loc['2026-10':].notna().sum()>5]
def lists(A,B):
    full=[t for t in P0.columns if M0[t].loc[A:B].any() and P0[t].loc[A:A[:4]+'-03'].notna().any() and P0[t].loc[B[:4]+'-10':B].notna().any()]
    tot=(P0[full].loc[A:B].ffill().iloc[-1]/P0[full].loc[A:B].bfill().iloc[0]).sort_values(ascending=False)
    return list(tot.index[:150]), list(pd.Index(full).to_series().sample(150,random_state=7))
rows=[]
for A,B in [('2011-01-01','2018-12-31'),('2019-01-01','2026-11-10'),('1997-01-01','2010-12-31')]:
    top,rnd=lists(A,B); A0=str(pd.Timestamp(A)-pd.Timedelta(days=30))[:10]
    U={'yours':mine,'hindsight150':top,'random150':rnd,'PIT S&P':None}
    if A.startswith('1997'): U.pop('yours')
    for un,cols in U.items():
        if cols is None: cols=list(M0.columns[M0.loc[A:B].any()])
        sl_=lambda X: X[cols].loc[A0:B]
        P=sl_(P0); r=P.pct_change(fill_method=None).clip(-0.95,3); rn=r.shift(-1)
        dist=sl_(S0['dist']); vol=sl_(S0['vol126']); up=sl_(S0['trend'])>0; q_=sl_(S0['quality']); f=sl_(S0['f']).fillna(False).astype(bool)
        rsi14=sl_(sl.rsi(P0[cols].loc[str(pd.Timestamp(A0)-pd.Timedelta(days=200))[:10]:B],14)); dds=dist/vol
        E=dist.notna()&q_.notna()
        if un=='PIT S&P': E=E&sl_(M0).astype(bool)
        C={'G0 v2 gate (dist<-20,>200d,quality)':f,'G1 dist<-20 & >200d':(dist<-0.2)&up,'G2 dist<-20 any trend':dist<-0.2,'G3 dist<-20 & <200d':(dist<-0.2)&~up,
           'G4 dist<-30':dist<-0.3,'G4b dist<-30 & >200d':(dist<-0.3)&up,'G5 dd<=-0.6vol & >200d':(dds<=-0.6)&up,'G6 dd<=-0.6vol':dds<=-0.6,'G7 dd<=-1.0vol':dds<=-1.0,
           'G8 dist<-20 & RSI14<40':(dist<-0.2)&(rsi14<40),'G9 dist<-20 & >200d & RSI14<45':(dist<-0.2)&up&(rsi14<45),'G10 dist<-20 & quality':(dist<-0.2)&(q_>0.2)}
        qq=(vol.where(E).rank(axis=1,pct=True)*3).clip(upper=2.999).fillna(-1).astype(int); adj=pd.DataFrame(np.nan,index=P.index,columns=P.columns)
        for g in range(3):
            m=E&(qq==g); adj=adj.where(~m, rn.sub(rn.where(m).mean(axis=1),axis=0))
        raw=rn.sub(rn.where(E).mean(axis=1),axis=0)
        for k,m in C.items():
            ent=(m.fillna(False)&E).shift(1,fill_value=False)
            for h in (63,252):
                act=ent.rolling(h,min_periods=1).max().astype(bool); ok=act.sum(1)>=3; o={}
                for nm,X in (('raw',raw),('vm',adj)):
                    ex=X.where(act).mean(axis=1).where(ok).loc[A:B].dropna()
                    o[nm]=(ex.mean()*252*100, ex.mean()/ex.std()*np.sqrt(len(ex))) if len(ex)>250 else (np.nan,np.nan)
                rows.append(dict(period=A[:4]+'-'+B[:4],universe=un,rule=k,hold=h,names=round(act.sum(1).loc[A:B].mean(),1),excess=o['raw'][0],t=o['raw'][1],vol_matched=o['vm'][0],t_vm=o['vm'][1]))
        print(un,A,'done',flush=True)
R=pd.DataFrame(rows); R.to_csv('dipfast.csv',index=False)
pd.set_option('display.width',250)
for h in (252,63):
    for v in ('excess','t','vol_matched','t_vm','names'):
        print(f'\n{v}, hold {h}d'); print(R[R.hold==h].pivot_table(index='rule',columns=['universe','period'],values=v,sort=False).round(1).to_string())
