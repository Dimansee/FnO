import sys,os;sys.path.insert(0,'research');sys.path.insert(0,'.')
import warnings;warnings.filterwarnings("ignore")
import numpy as np,pandas as pd
import round73_regime_tilts as R73, outcomes as O
from fno import portfolio_bt as PB
B0=dict(O.B0);k,ak=PB._trade_key(B0),PB._alt_key(B0)
base=pd.concat([O.trades(k,s) for s in O.SYMS],ignore_index=True)
alt=pd.concat([O.trades(ak,s) for s in O.SYMS],ignore_index=True) if ak else None
d=O.candles('NIFTY');days=sorted(set(d.index.strftime('%Y-%m-%d')))
D=pd.Series(days);m=D.str[:7]
first=D.groupby(m).cumcount();last=D.groupby(m).cumcount(ascending=False)
tom=set(D[(first<=2)|(last==0)])
base['tom']=base.date.isin(tom)
base['rs']=(base.prem_out-base.prem_in)*base.lot
base['y']=base.date.str[:4]
print(base.groupby(['y','tom','side']).rs.agg(['count','mean']).round(0).unstack(['tom']).to_string())
print(base.groupby(['y','tom']).rs.mean().round(0).unstack())
