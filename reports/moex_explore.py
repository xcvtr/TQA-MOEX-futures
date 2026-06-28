#!/usr/bin/env python3
"""Исследование базы MOEX: схема таблиц, диапазон данных, символы"""
import psycopg2
import os

DB_CONFIG = {
    'host': '10.0.0.60',
    'port': 5432,
    'dbname': 'moex',
    'user': os.environ.get('PGUSER', 'postgres'),
    'password': os.environ.get('PGPASSWORD', 'postgres')
}

def query(sql, params=None):
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()
    cur.execute(sql, params or ())
    rows = cur.fetchall()
    cols = [desc[0] for desc in cur.description]
    cur.close()
    conn.close()
    return cols, rows

# 1. Проверим таблицы
for table in ['openinterest_moex', 'moex_prices_5m', 'moex_prices_5m_oi']:
    print(f"\n=== {table} ===")
    cols, rows = query(f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name = '{table}'")
    print(f"  Columns: {[r[0] for r in rows]}")
    print(f"  Types: {[r[1] for r in rows]}")
    
    # количество строк
    cols2, rows2 = query(f"SELECT COUNT(*) FROM {table}")
    print(f"  Rows: {rows2[0][0]}")

# 2. Диапазон дат
for table in ['moex_prices_5m', 'moex_prices_5m_oi', 'openinterest_moex']:
    cols, _ = query(f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table}'")
    col_names = [r[0] for r in _]
    print(f"\n=== {table} columns: {col_names} ===")
    for col in col_names:
        if any(x in col.lower() for x in ['time', 'date', 'ts', 'timestamp']):
            try:
                _, rows = query(f"SELECT MIN({col}), MAX({col}) FROM {table} WHERE {col} IS NOT NULL")
                print(f"  {col}: {rows[0][0]} -> {rows[0][1]}")
            except Exception as e:
                print(f"  {col}: error - {e}")

# 3. Какие символы доступны
for table in ['moex_prices_5m', 'moex_prices_5m_oi', 'openinterest_moex']:
    cols, _ = query(f"SELECT column_name FROM information_schema.columns WHERE table_name = '{table}'")
    col_names = [r[0] for r in _]
    
    for col in col_names:
        if any(x in col.lower() for x in ['symbol', 'ticker', 'secid', 'code', 'sec_id']):
            try:
                _, rows = query(f"SELECT DISTINCT {col} FROM {table} ORDER BY {col}")
                vals = [r[0] for r in rows[:50]]
                print(f"\n=== {table}.{col}: {len(rows)} unique, first {min(50,len(rows))} ===")
                print(f"  {vals}")
            except Exception as e:
                print(f"  Error: {e}")
            break

# 4. Первые строки openinterest_moex
print("\n\n=== openinterest_moex sample (5 rows) ===")
try:
    cols, rows = query("SELECT * FROM openinterest_moex LIMIT 5")
    print(f"  {cols}")
    for r in rows:
        print(f"  {r}")
except Exception as e:
    print(f"  {e}")

# 5. Si данные
print("\n\n=== Si in moex_prices_5m ===")
try:
    cols, rows = query("SELECT * FROM moex_prices_5m LIMIT 3")
    print(f"  cols: {cols}")
    for r in rows:
        print(f"  {r}")
except Exception as e:
    print(f"  {e}")

print("\n\nDONE")
