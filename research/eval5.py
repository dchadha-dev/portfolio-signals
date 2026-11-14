import warnings; warnings.filterwarnings('ignore')
import pandas as pd, numpy as np, siglib as sl, sys, os, glob
sys.path.insert(0,'/tmp/claude-0/-home-claude/4641050d-2c5a-5f89-98d1-d4082914d9d2/scratchpad/stubs'); sys.path.insert(1,'/home/claude/work/portfolio-signals')
cwd=os.getcwd(); os.chdir('/home/claude/work/portfolio-signals'); import signal_scanner as sc; os.chdir(cwd)
d=pd.read_pickle('signals.pkl'); S,E=d['S'],d['E']; P=pd.read_pickle('panel.pkl')['P']; M=pd.read_pickle('sp500_mask.pkl')
r=P.pct_change(fill_method=None)
def caltime(mask,Eu,hold,start='2010-02-01',rr=r,minn=3):
    ent=(mask&Eu).shift(1,fill_value=False); active=ent.rolling(hold,min_periods=1).max().astype(bool)
    rn=rr.shift(-1); pr=rn.where(active).mean(axis=1); n=active.sum(1); ur=rn.where(Eu).mean(axis=1)
    ex=(pr-ur).where(n>=minn).loc[start:].dropna()
    return ex.mean()*252*100, ex.mean()/ex.std()*np.sqrt(len(ex)), n.loc[start:].mean(), ur.loc[start:].mean()*252*100
mine=[t for t in dict.fromkeys(sc.DIRECT_HOLDINGS+sc.CANDIDATES) if t in E.columns]
Emine=E&pd.DataFrame(np.broadcast_to(E.columns.isin(mine),E.shape),index=E.index,columns=E.columns)
print('2010-2017, same rule, three universes (calendar-time, hold 252d, excess vs that universe\'s own equal-weight average):')
for un,Eu in [('All liquid US stocks (neutral)',E),('Point-in-time S&P 500',E&M),('Your 2026 universe (hindsight)',Emine)]:
    for nm,m in [('factor gate',S['f']),('dist<-20%',S['dist']<-0.2),('near 52w high',S['near_high'])]:
        a,t,n,u=caltime(m.fillna(False),Eu,252)
        print(f'  {un:<32} {nm:<14} names {n:7.1f}  excess {a:+6.2f}%/yr  t {t:+.2f}   (universe itself {u:5.1f}%/yr)')
# Recent regime, megacap winners only (Pubmarks, 1980-2026): hindsight names, shown for contrast
fr=[]
for f in glob.glob('/home/claude/data/datasets/data/stocks/*/[12]*/ohlcv.csv'):
    x=pd.read_csv(f,parse_dates=['date']); x['symbol']=f.split('/')[-3]; fr.append(x)
if fr:
    L=pd.concat(fr); W={c:L.pivot(index='date',columns='symbol',values=c) for c in ['close','high','low','volume']}
    Pm=W['close']; Sm=sl.compute(Pm,W['high'],W['low'],W['volume']); Em=Pm.notna()&Sm['dist'].notna()&Sm['quality'].notna(); rm=Pm.pct_change(fill_method=None)
    print('\n16 mega-cap winners (hindsight), by period — factor gate, hold 252d, excess vs the 16-name average:')
    for a0,a1 in [('1995-01-01','2009-12-31'),('2010-01-01','2017-12-31'),('2018-01-01','2026-12-31')]:
        ent=(Sm['f']&Em).shift(1,fill_value=False); act=ent.rolling(252,min_periods=1).max().astype(bool); rn=rm.shift(-1)
        ex=(rn.where(act).mean(axis=1)-rn.where(Em).mean(axis=1)).where(act.sum(1)>=1).loc[a0:a1].dropna()
        print(f'  {a0[:4]}-{a1[:4]}  days in signal {len(ex):5d}  excess {ex.mean()*252*100:+6.2f}%/yr  t {ex.mean()/ex.std()*np.sqrt(len(ex)):+.2f}   avg of 16 names {rn.where(Em).mean(axis=1).loc[a0:a1].mean()*252*100:5.1f}%/yr')
