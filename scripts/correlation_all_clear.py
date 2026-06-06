#!/home/user/venvs/tqa/main/bin/python
"""
Correlation All-Clear Checker (v4 — с COT анализатором).
Проверяет, безопасно ли сейчас входить в сделки по корреляционной стратегии.
Анализирует: экономический календарь + последнюю корреляцию + DOM + COT.

COT анализ:
- z-score позиций EUR, GBP, JPY, AUD (trailing 52 weeks)
- Если |z| >= 1.5 — предупреждение (экстремум)
- Если |z| >= 2.0 — блокировка (исторический экстремум)
- Если EUR и GBP z-score противоположных знаков — блокировка (COT divergence)

Использование:
  /home/user/venvs/tqa/main/bin/python scripts/correlation_all_clear.py
  exit code: 0=ALL CLEAR, 1=calendar block, 2=correlation weak, 3=DOM extreme, 4=warning only
"""
import psycopg2
import numpy as np
import pandas as pd
import warnings
from datetime import datetime, timedelta, timezone

warnings.filterwarnings('ignore')

DB_HOST = '10.0.0.60'
DB_NAME = 'forex'
DB_USER = 'postgres'
DB_PASS = 'postgres'

MIN_BARS = 30
HIGH_CORR = 0.5
LOW_CORR = 0.3
EVENT_LOOKAHEAD = 4
CENTRAL_BANK_LOOKAHEAD = 12
COT_Z_WARN = 1.5
COT_Z_BLOCK = 2.0

CRITICAL_KW = [
    'nonfarm', 'payrolls', 'unemployment',
    'cpi', 'consumer price', 'inflation', 'ppi',
    'fed', 'fomc', 'interest rate', 'monetary policy',
    'gdp', 'gross domestic',
    'boj', 'boe', 'ecb', 'rba', 'rbnz', 'boc',
    'ism manufacturing', 'ism non-manufacturing',
    'retail sales', 'jobless claims',
]


def check_calendar(conn):
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(hours=EVENT_LOOKAHEAD)
    cb_horizon = now + timedelta(hours=CENTRAL_BANK_LOOKAHEAD)

    df = pd.read_sql("""
        SELECT event_time, country_code, name, importance
        FROM economic_calendar
        WHERE event_time >= %s AND event_time <= %s AND importance >= 2
        ORDER BY event_time
    """, conn, params=(now, horizon))

    cb_df = pd.read_sql("""
        SELECT event_time, country_code, name, importance
        FROM economic_calendar
        WHERE event_time >= %s AND event_time <= %s AND importance >= 2
        AND name ILIKE '%%rate%%'
        ORDER BY event_time
    """, conn, params=(now, cb_horizon))

    critical, moderate = [], []
    for _, r in df.iterrows():
        nl = str(r['name'] or '').lower()
        entry = {'country': r['country_code'], 'name': str(r['name'] or ''),
                 'time': r['event_time'], 'importance': r['importance']}
        if any(kw in nl for kw in CRITICAL_KW) or r['importance'] == 3:
            critical.append(entry)
        else:
            moderate.append(entry)

    return critical, moderate, cb_df.to_dict('records') if len(cb_df) > 0 else []


def check_correlation(conn):
    now = datetime.now(timezone.utc)
    lookback = now - timedelta(hours=72)  # 72h для выходных

    pairs = [
        ('eurusd', 'gbpusd', 'EURUSD—GBPUSD'),
        ('eurjpy', 'gbpjpy', 'EURJPY—GBPJPY'),
        ('audjpy', 'gbpjpy', 'AUDJPY—GBPJPY'),
    ]

    results = []
    for a, b, label in pairs:
        try:
            df_a = pd.read_sql(f"SELECT time, price FROM {a}_data WHERE time >= %s ORDER BY time",
                               conn, params=(lookback,))
            df_b = pd.read_sql(f"SELECT time, price FROM {b}_data WHERE time >= %s ORDER BY time",
                               conn, params=(lookback,))
        except Exception as e:
            results.append({'label': label, 'status': 'ERROR', 'corr': None, 'detail': str(e)})
            continue

        if len(df_a) < MIN_BARS or len(df_b) < MIN_BARS:
            results.append({'label': label, 'status': 'INSUFFICIENT', 'corr': None,
                            'n_bars': min(len(df_a), len(df_b))})
            continue

        df_a = df_a.set_index('time').drop_duplicates(keep='first')
        df_b = df_b.set_index('time').drop_duplicates(keep='first')
        merged = pd.DataFrame({'a': df_a['price'].reindex(df_a.index.union(df_b.index)).ffill(),
                               'b': df_b['price'].reindex(df_a.index.union(df_b.index)).ffill()}).dropna()

        if len(merged) < MIN_BARS:
            results.append({'label': label, 'status': 'INSUFFICIENT', 'corr': None, 'n_bars': len(merged)})
            continue

        window = min(120, len(merged) - 2)
        rets = np.log(merged / merged.shift(1)).dropna()
        if len(rets) < window:
            window = len(rets) - 1
        if window < 5:
            results.append({'label': label, 'status': 'INSUFFICIENT', 'corr': None, 'n_bars': len(rets)})
            continue

        corr = rets['a'].rolling(window).corr(rets['b'])
        current = float(corr.iloc[-1]) if not corr.empty else 0
        min_corr = float(corr.min()) if not corr.empty else 0

        if abs(current) >= HIGH_CORR and min_corr > 0:
            status = 'OK'
        elif abs(current) >= LOW_CORR:
            status = 'WARNING'
        else:
            status = 'BLOCKED'

        results.append({'label': label, 'status': status, 'corr': current,
                        'min_corr': min_corr, 'window': window, 'n_bars': len(merged)})

    return results


def check_cot(conn):
    """Проверяет COT non-commercial net positions на экстремумы."""
    now = datetime.now(timezone.utc)
    lookback = now - timedelta(days=400)  # 52+ недель
    
    instruments = ['eur', 'gbp', 'jpy', 'aud']
    results = []
    
    for inst in instruments:
        df = pd.read_sql("""
            SELECT event_time, actual_value
            FROM economic_calendar
            WHERE event_code = %s
              AND actual_value IS NOT NULL
              AND event_time >= %s
            ORDER BY event_time
        """, conn, params=(
            f'cftc-{inst}-non-commercial-net-positions',
            lookback
        ))
        
        if len(df) < 10:
            results.append({'instrument': inst.upper(), 'status': 'SKIP', 
                            'z_score': None, 'value': None})
            continue
        
        values = df['actual_value'].astype(float).values
        mean = values.mean()
        std = values.std()
        last_val = values[-1]
        z_score = (last_val - mean) / std if std > 0 else 0
        
        if abs(z_score) >= COT_Z_BLOCK:
            status = 'BLOCKED'
        elif abs(z_score) >= COT_Z_WARN:
            status = 'WARNING'
        else:
            status = 'OK'
        
        # Weekly change
        change_pct = ((last_val - values[-2]) / abs(values[-2]) * 100) if len(values) > 1 else 0
        
        results.append({
            'instrument': inst.upper(),
            'status': status,
            'z_score': z_score,
            'value': int(last_val),
            'change_pct': round(change_pct, 1)
        })
    
    # COT divergence: EUR vs GBP opposite signs
    divergence = False
    eur_z = next((r['z_score'] for r in results if r['instrument'] == 'EUR'), None)
    gbp_z = next((r['z_score'] for r in results if r['instrument'] == 'GBP'), None)
    if eur_z is not None and gbp_z is not None:
        divergence = (eur_z > COT_Z_WARN * 0.3 and gbp_z < -COT_Z_WARN * 0.3) or \
                     (eur_z < -COT_Z_WARN * 0.3 and gbp_z > COT_Z_WARN * 0.3)
    
    return results, divergence


def check_dom(conn, symbol='eurusd'):
    now = datetime.now(timezone.utc)
    lookback = now - timedelta(hours=1)

    df = pd.read_sql(f"""
        SELECT time, price, orders, positions
        FROM {symbol}_dom WHERE time >= %s ORDER BY time
    """, conn, params=(lookback,))

    if len(df) < 10:
        return {'status': 'SKIP', 'detail': 'мало данных за последний час'}

    pos = df['positions'].values
    pos_c = pos[~np.isnan(pos)]
    if len(pos_c) < 5:
        return {'status': 'SKIP', 'detail': 'мало позиций'}

    longs = float(np.sum(pos_c[pos_c > 0])) if np.any(pos_c > 0) else 0
    shorts = float(abs(np.sum(pos_c[pos_c < 0]))) if np.any(pos_c < 0) else 0
    total = longs + shorts
    ratio = longs / total if total > 0 else 0.5

    is_extreme = ratio > 0.8 or ratio < 0.2
    return {
        'status': 'EXTREME' if is_extreme else 'OK',
        'long_pct': f"{ratio:.0%}",
        'detail': f"Длинных {longs:.1f} / Коротких {shorts:.1f} ({ratio:.0%})"
    }


def main():
    conn = psycopg2.connect(host=DB_HOST, dbname=DB_NAME, user=DB_USER, password=DB_PASS, connect_timeout=5)
    now = datetime.now(timezone.utc)

    # Handle cb_results with correct column names
    critical, moderate, cb = check_calendar(conn)
    corr_results = check_correlation(conn)
    dom = check_dom(conn)
    cot_results, cot_divergence = check_cot(conn)
    conn.close()

    cal_block = len(critical) > 0 or len(cb) > 0
    cal_warn = len(moderate) > 0
    corr_block = any(r.get('status') == 'BLOCKED' for r in corr_results)
    corr_warn = any(r.get('status') == 'WARNING' for r in corr_results)
    dom_block = dom.get('status') == 'EXTREME'
    cot_block = any(r.get('status') == 'BLOCKED' for r in cot_results) or cot_divergence
    cot_warn = any(r.get('status') == 'WARNING' for r in cot_results)
    cot_skip = all(r.get('status') == 'SKIP' for r in cot_results)

    # ── OUTPUT ──
    print(f"🕐 {now.strftime('%Y-%m-%d %H:%M UTC')} | Correlation All-Clear")
    print("=" * 55)

    if cal_block:
        print(f"\n🔴 КРИТИЧЕСКИЕ события в ближайшие часы:")
        for ev in critical + cb:
            et = ev.get('event_time') or ev.get('time')
            if hasattr(et, 'strftime'):
                et = et.strftime('%H:%M')
            cc = ev.get('country_code') or ev.get('country', '?')
            print(f"   {et} [{cc}] {str(ev.get('name','?'))[:55]}")
    elif cal_warn:
        print(f"\n🟡 Некритичные события в ближайшие часы:")
        for ev in moderate[:5]:
            print(f"   {ev['time'].strftime('%H:%M')} [{ev['country']}] {str(ev['name'])[:50]}")
    else:
        print(f"\n🟢 Календарь: чисто (след. важное событие >4ч)")

    print(f"\n📊 Корреляция:")
    for r in corr_results:
        if r.get('corr') is not None:
            ico = {'OK': '🟢', 'WARNING': '🟡', 'BLOCKED': '🔴'}.get(r['status'], '⚪')
            print(f"   {ico} {r['label']}: r={r['corr']:.3f} (min={r['min_corr']:.3f})")
        else:
            print(f"   ⚪ {r['label']}: {r['status']} ({r.get('n_bars','?')} баров)")

    print(f"\n📊 DOM (EURUSD, последний час): {dom['detail']}")
    print(f"   {'🔴' if dom_block else '🟢'} Перекос: {dom.get('long_pct','?')}")

    # COT
    if not cot_skip:
        print(f"\n📊 COT non-commercial positions (z-score, 52w trailing):")
        for r in cot_results:
            ico = {'OK': '🟢', 'WARNING': '🟡', 'BLOCKED': '🔴'}.get(r['status'], '⚪')
            print(f"   {ico} {r['instrument']}: z={r['z_score']:+.2f}  δ={r['change_pct']:+.1f}%")
        if cot_divergence:
            print(f"   🔴 COT DIVERGENCE: EUR и GBP смотрят в разные стороны!")
    else:
        print(f"\n📊 COT: недостаточно данных")

    # Verdict
    print("\n" + "=" * 55)
    if cal_block or corr_block or dom_block or cot_block:
        reasons = []
        if cal_block: reasons.append("📅 события")
        if corr_block: reasons.append("📉 корреляция")
        if dom_block: reasons.append("📊 DOM перекос")
        if cot_block: reasons.append("📈 COT экстремум")
        print(f"⛔ ТОРГОВЛЯ ПО КОРРЕЛЯЦИИ ЗАБЛОКИРОВАНА")
        print(f"   Причины: {', '.join(reasons)}")
    else:
        print(f"✅ ALL CLEAR — корреляционную стратегию можно использовать")
        if cal_warn or corr_warn or cot_warn:
            print("   🟡 (с оговорками — см. выше)")

    if cal_block: return 1
    if corr_block: return 2
    if dom_block: return 3
    if cot_block: return 10
    if cal_warn or corr_warn: return 4
    return 0


if __name__ == '__main__':
    exit(main())
