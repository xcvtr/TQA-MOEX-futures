
#!/usr/bin/env python3
import pandas as pd, numpy as np
from sqlalchemy import create_engine
from datetime import datetime
from itertools import combinations
engine = create_engine("postgresql://postgres@10.0.0.60:5432/moex")
tickers = pd.read_sql("SELECT DISTINCT symbol FROM openinterest_moex ORDER BY symbol", engine)["symbol"].tolist()
print("Tickers:", len(tickers))
print(tickers)
