"""Pre-specified candidates from the literature (no parameter search):
   12-1 momentum, nearness to 52w high, above-200d trend, 12m Sharpe ('quality'), low volatility.
   Quintile sorts, top-minus-bottom and top-minus-rest, both halves, two universes."""
import warnings; warnings.filterwarnings('ignore')
import pandas as pd, numpy as np, siglib as sl
d=pd.read_pickle('signals.pkl'); S,E=d['S'],d['E']; F=pd.read_pickle('fwd.pkl'); P=pd.read_pickle('panel.pkl')['P']
M=pd.read_pickle('sp500_mask.pkl') if __import__('os').path.exists('sp500_mask.pkl') else None
if M is None:
    sp=pd.read_csv('/home/claude/data/sp500/S&P 500 Historical Components & Changes (Updated).csv',parse_dates=['date']).sort_values('date'); sp=sp[sp.date<=E.index[-1]]
    Mv=np.zeros(E.shape,bool); pos=np.searchsorted(sp['date'].values,E.index.values,side='right')-1; cache={}
    for i,p in enumerate(pos):
        if p<0: continue
        if p not in cache:
            tk=[t.strip() for t in sp['tickers'].iloc[p].split(',')]; cache[p]=E.columns.isin(tk)|E.columns.isin([t.replace('.','-') for t in tk])
        Mv[i]=cache[p]
    M=pd.DataFrame(Mv,index=E.index,columns=E.columns); M.to_pickle('sp500_mask.pkl')
mom=P.shift(21)/P.shift(252)-1                      # 12-1 momentum
char={'mom 12-1':mom,'near 52w high':S['dist'],'12m Sharpe':S['r252']/(S['vol126'].replace(0,np.nan)),'low vol':-S['vol126'],'trend vs 200d':S['trend'],'dip (far from high)':-S['dist']}
def comp_rank(E_):
    r=sum((char[k].where(E_).rank(axis=1,pct=True)) for k in ['mom 12-1','near 52w high','12m Sharpe'])/3
    return r
SPLIT='2013-07-01'; rows=[]
for un,Em in {'ALL liquid':E,'PIT S&P 500':E&M}.items():
    C=dict(char); C['COMPOSITE trend-quality (equal-weight ranks)']=comp_rank(Em)
    for k,x in C.items():
        q=x.where(Em).rank(axis=1,pct=True)
        top=(q>0.8).fillna(False); bot=(q<=0.2).fillna(False)
        for h in (63,126,252):
            a=sl.daily_spread(top,Em,F[h]).loc['2010-02-01':]; b=sl.daily_spread(bot,Em,F[h]).loc['2010-02-01':]
            tr=a['spread']; tb=a['sig']-b['sig']
            m,t,n=sl.nw_tstat(tr,h); m1,t1,_=sl.nw_tstat(tr.loc[:SPLIT],h); m2,t2,_=sl.nw_tstat(tr.loc[SPLIT:],h); mb,tbb,_=sl.nw_tstat(tb,h)
            rows.append(dict(universe=un,characteristic=k,h=h,top_minus_rest=m*100,t=t,first=m1*100,t1=t1,second=m2*100,t2=t2,top_minus_bottom=mb*100,t_tb=tbb,hit=(tr>0).mean()*100))
R=pd.DataFrame(rows); R.to_csv('eval3.csv',index=False)
pd.set_option('display.width',260); pd.set_option('display.max_rows',300)
for h in (63,252):
    print(f'\n=== top quintile, {h}d forward return spread (pp) ==='); print(R[R.h==h].drop(columns='h').round(2).to_string(index=False))
