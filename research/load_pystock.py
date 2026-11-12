"""Assemble pystock-data (all US-listed stocks, 2009-01..2017-03) into one compact table."""
import tarfile, glob, pandas as pd, numpy as np
frames=[]
files=sorted(glob.glob('/home/claude/data/pystock-data/20*/*.tar.gz'))
for i,f in enumerate(files):
    try:
        with tarfile.open(f) as tf:
            for m in tf.getmembers():
                if m.name.endswith('prices.csv'):
                    df=pd.read_csv(tf.extractfile(m), dtype=str)
                    df=df[df['date']!='date']
                    out=pd.DataFrame({'symbol':df['symbol'].astype('category'),
                                      'date':pd.to_datetime(df['date'],errors='coerce')})
                    for c in ['open','high','low','close','adj_close']: out[c]=pd.to_numeric(df[c],errors='coerce').astype('float32')
                    out['volume']=pd.to_numeric(df['volume'],errors='coerce').astype('float64')
                    frames.append(out.dropna(subset=['date','close']))
    except Exception as e:
        print('skip',f,str(e)[:60])
d=pd.concat(frames,ignore_index=True); del frames
d['symbol']=d['symbol'].astype('category')
d=d.drop_duplicates(['symbol','date'],keep='last').sort_values(['symbol','date'])
d.to_parquet('pystock_long.parquet')
print(d['date'].min(), d['date'].max(), d['symbol'].nunique(), len(d))
