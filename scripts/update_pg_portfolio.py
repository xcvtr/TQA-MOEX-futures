#!/usr/bin/env python3
"""Update PG portfolio with final config."""
import psycopg2, json
pg = psycopg2.connect(host='10.0.0.60', port=5432, dbname='moex', user='postgres')
cur = pg.cursor()

cur.execute("UPDATE futures.portfolio SET enabled=False WHERE enabled=True")

entries = [
    ('Si', 'impulse_return', {'trend': True}, 0.005, 0.003, 12),
    ('GD', 'dragon', {'impulse_pct': 0.3, 'retrace_max_pct': 70, 'hump_extension': 0.1, 'lookback': 100, 'trend': True}, 0.015, 0.005, 60),
    ('MM', 'dragon', {'impulse_pct': 0.3, 'retrace_max_pct': 70, 'hump_extension': 0.1, 'lookback': 100, 'trend': True}, 0.015, 0.005, 60),
    ('RN', 'stop_hunt', {'lookback': 60, 'retrace': 0.05}, 0.005, 0.003, 12),
    ('NG', 'dragon', {'impulse_pct': 0.3, 'retrace_max_pct': 70, 'hump_extension': 0.1, 'lookback': 100, 'trend': True}, 0.015, 0.005, 60),
]

for ticker, strategy, params, ta, tt, to in entries:
    params_json = json.dumps(params)
    cur.execute("""
        INSERT INTO futures.portfolio (ticker, strategy, enabled, contracts, weight, params, trailing_activation, trailing_trail, timeout_bars)
        VALUES (%s, %s, True, NULL, 1.0, %s::jsonb, %s, %s, %s)
        ON CONFLICT (ticker, strategy) DO UPDATE SET enabled=True, contracts=NULL, weight=1.0, params=%s::jsonb, trailing_activation=%s, trailing_trail=%s, timeout_bars=%s
    """, (ticker, strategy, params_json, ta, tt, to, params_json, ta, tt, to))

pg.commit()
cur.execute("SELECT ticker, strategy, enabled FROM futures.portfolio WHERE enabled=True ORDER BY ticker")
print('✅ Enabled portfolio:')
for r in cur.fetchall():
    print(f'  {r[0]:4s} {r[1]:20s}')
cur.close(); pg.close()
