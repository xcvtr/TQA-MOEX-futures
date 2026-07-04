#!/usr/bin/env python3
"""Audit all stuck and moex tables on .60 and .63"""
import clickhouse_connect

targets = ['moex.tradestats_fo','moex.obstats_fo','moex.openinterest','moex.tradestats_h1',
           'moex.tradestats_d1','moex.prices_5m_oi','moex.options_board','moex.options_board_local',
           'moex.options_board_raw','moex.options_code','moex.options_list','moex.options_result',
           'moex.options_secid_map','moex.prices','moex.securities','moex.orderstats_fo',
           'moex.alerts_fo','moex.hi2_fo','moex.eq_tradestats','moex.eq_alerts','moex.eq_hi2',
           'moex.eq_obstats','moex.eq_orderstats']

for host in ['10.0.0.60', '10.0.0.63']:
    ch = clickhouse_connect.get_client(host=host, port=8123, database='moex', user='default')
    print(f'\n=== {host} ===')
    for full_name in targets:
        db, tbl = full_name.split('.')
        try:
            r = ch.query(f"SELECT engine FROM system.tables WHERE database='{db}' AND name='{tbl}'")
            if not r.result_rows:
                continue
            engine = r.result_rows[0][0]
            r = ch.query(f"SHOW CREATE TABLE {full_name}")
            ddl = r.result_rows[0][0]
            r = ch.query(f"SELECT count() FROM {full_name}")
            cnt = r.result_rows[0][0]
            # Get size
            r = ch.query(f"SELECT formatReadableSize(sum(data_compressed_bytes)) FROM system.parts WHERE database='{db}' AND table='{tbl}' AND active=1")
            size = r.result_rows[0][0] if r.result_rows[0][0] else '0'
            print(f'  {tbl:30s} │ {cnt:>10,} rows │ {engine:18s} │ {size}')
        except Exception as e:
            pass
    ch.close()
