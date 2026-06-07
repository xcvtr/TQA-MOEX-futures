#!/home/user/venvs/tqa/main/bin/python
"""Debug: check data loading for correlation dashboard."""
import psycopg2
import numpy as np
import pandas as pd
from datetime import datetime
import warnings
warnings.filterwarnings('ignore')

DB_HOST = '10.0.0.64'
DB_NAME = 'forex'
DB_USER = 'postgres'
DB_PASS = 'postgres'

conn = psycopg2.connect(host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=DB_PASS)

START, END = '2024-06-01', '2025-09-30'

for sym in ['eurusd','gbpusd']:
    df = pd.read_sql(f"SELECT time, price FROM {sym}_data WHERE time >= '{START}' AND time <= '{END}' ORDER BY time", conn)
    print(f"\n{sym}: raw {len(df)} rows")
    print(f"  time dtype: {df['time'].dtype}")
    print(f"  sample time: {df['time'].iloc[0]}")
    print(f"  price sample: {df['price'].iloc[:3].values}")
    
    # Check if tz-aware
    t = df['time'].iloc[0]
    print(f"  tzinfo: {t.tzinfo if hasattr(t, 'tzinfo') else 'no tz'}")
    
    # Test conversion
    if hasattr(t, 'tzinfo') and t.tzinfo is not None:
        df['time'] = pd.to_datetime(df['time'])
        print(f"  after pd.to_datetime: tz={df['time'].dt.tz}")
        df['time'] = df['time'].dt.tz_localize(None)
        print(f"  after tz_localize(None): {df['time'].iloc[0]}")
    
    df = df.set_index('time').drop_duplicates(keep='first')
    print(f"  after set_index + dedup: {len(df)}")

conn.close()
