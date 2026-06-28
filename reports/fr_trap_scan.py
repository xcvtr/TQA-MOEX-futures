#!/usr/bin/env python3
"""
FR Trap Scanner — сканирует funding_rates на предмет FR Traps.
FR Trap = экстремальный funding rate (|z| > 2.5) после которого
цена движется в противоположную сторону.

Толпа в лонге (rate > 0, z > +2.5) → цена падает (разворот вниз)
Толпа в шорте (rate < 0, z < -2.5) → цена растёт (разворот вверх)
"""

import psycopg2
import psycopg2.extras
import json
import sys
from datetime import datetime, timedelta, timezone
from collections import defaultdict

DB_CONN = {
    "host": "10.0.0.60",
    "port": 5432,
    "dbname": "crypto",
    "user": "postgres",
    "password": "postgres",
}

# Периоды для проверки разворота (в шагах по 8h)
FORWARD_PERIODS = [3, 6, 9, 12]

# Порог z-score
Z_THRESHOLD = 2.5

# Размер окна для z-score (в периодах, ~30 дней = 90 периодов * 8h)
ROLLING_WINDOW = 90


def get_symbols(conn):
    """Получить список всех символов"""
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT symbol FROM funding_rates ORDER BY symbol")
        return [row[0] for row in cur.fetchall()]


def get_funding_data(conn, symbol):
    """Получить все funding_rates для символа, отсортированные по времени"""
    query = """
        SELECT timestamp, rate, mark_price
        FROM funding_rates
        WHERE symbol = %s
        ORDER BY timestamp ASC
    """
    with conn.cursor() as cur:
        cur.execute(query, (symbol,))
        rows = cur.fetchall()
    return rows  # list of (timestamp, rate, mark_price)


def calculate_z_scores(rows, window=ROLLING_WINDOW):
    """
    Рассчитать rolling z-score для funding rate.
    Возвращает список словарей с результатами.
    """
    import numpy as np

    results = []
    rates = np.array([r[1] for r in rows], dtype=np.float64)
    prices = np.array([r[2] for r in rows], dtype=np.float64)
    timestamps = [r[0] for r in rows]

    for i in range(len(rows)):
        if i < window:
            results.append({
                "timestamp": timestamps[i],
                "rate": float(rates[i]),
                "mark_price": float(prices[i]),
                "z_score": None,
                "is_extreme": False,
            })
            continue

        window_rates = rates[i - window : i]
        mean = np.mean(window_rates)
        std = np.std(window_rates, ddof=1)

        if std == 0:
            z = 0.0
        else:
            z = float((rates[i] - mean) / std)

        is_extreme = abs(z) > Z_THRESHOLD

        results.append({
            "timestamp": timestamps[i],
            "rate": float(rates[i]),
            "mark_price": float(prices[i]),
            "z_score": round(z, 4),
            "is_extreme": is_extreme,
        })

    return results


def check_reversal(extreme_idx, results, forward_periods):
    """
    Проверить, был ли разворот после экстремума.
    Возвращает словарь с результатами для каждого forward_period.
    """
    extreme = results[extreme_idx]
    entry_price = extreme["mark_price"]
    entry_rate = extreme["rate"]
    is_long_crowd = entry_rate > 0  # толпа в лонге, ждём падения

    reversal_results = {}

    for fp in forward_periods:
        check_idx = extreme_idx + fp
        if check_idx >= len(results):
            reversal_results[fp] = {
                "check_idx": None,
                "exit_price": None,
                "price_change_pct": None,
                "reversed": None,
                "reason": "insufficient_data",
            }
            continue

        exit_price = results[check_idx]["mark_price"]
        if entry_price == 0:
            price_change_pct = 0.0
            reversed_flag = False
        else:
            price_change_pct = ((exit_price - entry_price) / entry_price) * 100

            # Разворот = цена пошла ПРОТИВ направления funding rate
            if is_long_crowd:
                # Толпа в лонге, ждём падения
                reversed_flag = price_change_pct < 0
            else:
                # Толпа в шорте, ждём роста
                reversed_flag = price_change_pct > 0

        reversal_results[fp] = {
            "check_idx": check_idx,
            "exit_price": float(exit_price),
            "price_change_pct": round(float(price_change_pct), 4),
            "reversed": reversed_flag,
            "reason": "ok",
        }

    return reversal_results


def save_results_table(conn, symbol, extremes_analysis):
    """Сохранить результаты в таблицу fr_trap_scan"""
    create_sql = """
        CREATE TABLE IF NOT EXISTS fr_trap_scan (
            id SERIAL PRIMARY KEY,
            symbol TEXT NOT NULL,
            timestamp TIMESTAMPTZ NOT NULL,
            rate REAL,
            mark_price REAL,
            z_score REAL,
            is_long_crowd BOOLEAN,
            reversal_3_pct REAL,
            reversal_3 BOOLEAN,
            reversal_6_pct REAL,
            reversal_6 BOOLEAN,
            reversal_9_pct REAL,
            reversal_9 BOOLEAN,
            reversal_12_pct REAL,
            reversal_12 BOOLEAN,
            scanned_at TIMESTAMPTZ DEFAULT NOW()
        )
    """
    with conn.cursor() as cur:
        cur.execute(create_sql)
        conn.commit()

    # Создаём уникальный индекс для upsert
    index_sql = """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_fr_trap_scan_symbol_ts
        ON fr_trap_scan (symbol, timestamp)
    """
    with conn.cursor() as cur:
        cur.execute(index_sql)
        conn.commit()

    insert_sql = """
        INSERT INTO fr_trap_scan
            (symbol, timestamp, rate, mark_price, z_score, is_long_crowd,
             reversal_3_pct, reversal_3,
             reversal_6_pct, reversal_6,
             reversal_9_pct, reversal_9,
             reversal_12_pct, reversal_12)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (symbol, timestamp) DO NOTHING
    """

    rows_to_insert = []
    for item in extremes_analysis:
        r = item["reversal"]
        rows_to_insert.append((
            symbol,
            item["timestamp"],
            item["rate"],
            item["mark_price"],
            item["z_score"],
            item["is_long_crowd"],
            r.get(3, {}).get("price_change_pct"),
            r.get(3, {}).get("reversed"),
            r.get(6, {}).get("price_change_pct"),
            r.get(6, {}).get("reversed"),
            r.get(9, {}).get("price_change_pct"),
            r.get(9, {}).get("reversed"),
            r.get(12, {}).get("price_change_pct"),
            r.get(12, {}).get("reversed"),
        ))

    if rows_to_insert:
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, insert_sql, rows_to_insert, page_size=500)
            conn.commit()


def save_results_json(all_stats, output_file="fr_trap_results.json"):
    """Сохранить общую статистику в JSON"""
    with open(output_file, "w") as f:
        json.dump(all_stats, f, indent=2, default=str)
    print(f"  Результаты сохранены в {output_file}")


def create_stats_table(conn):
    """Создать таблицу для сводной статистики"""
    sql = """
        CREATE TABLE IF NOT EXISTS fr_trap_summary (
            id SERIAL PRIMARY KEY,
            symbol TEXT NOT NULL UNIQUE,
            total_extremes INT DEFAULT 0,
            long_crowd_extremes INT DEFAULT 0,
            short_crowd_extremes INT DEFAULT 0,
            reversal_3_count INT DEFAULT 0,
            reversal_3_winrate REAL DEFAULT 0,
            reversal_3_avg_move REAL DEFAULT 0,
            reversal_6_count INT DEFAULT 0,
            reversal_6_winrate REAL DEFAULT 0,
            reversal_6_avg_move REAL DEFAULT 0,
            reversal_9_count INT DEFAULT 0,
            reversal_9_winrate REAL DEFAULT 0,
            reversal_9_avg_move REAL DEFAULT 0,
            reversal_12_count INT DEFAULT 0,
            reversal_12_winrate REAL DEFAULT 0,
            reversal_12_avg_move REAL DEFAULT 0,
            scanned_at TIMESTAMPTZ DEFAULT NOW()
        )
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        conn.commit()


def save_symbol_stats(conn, symbol, stats):
    """Сохранить статистику по символу"""
    upsert_sql = """
        INSERT INTO fr_trap_summary
            (symbol, total_extremes, long_crowd_extremes, short_crowd_extremes,
             reversal_3_count, reversal_3_winrate, reversal_3_avg_move,
             reversal_6_count, reversal_6_winrate, reversal_6_avg_move,
             reversal_9_count, reversal_9_winrate, reversal_9_avg_move,
             reversal_12_count, reversal_12_winrate, reversal_12_avg_move)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (symbol) DO UPDATE SET
            total_extremes = EXCLUDED.total_extremes,
            long_crowd_extremes = EXCLUDED.long_crowd_extremes,
            short_crowd_extremes = EXCLUDED.short_crowd_extremes,
            reversal_3_count = EXCLUDED.reversal_3_count,
            reversal_3_winrate = EXCLUDED.reversal_3_winrate,
            reversal_3_avg_move = EXCLUDED.reversal_3_avg_move,
            reversal_6_count = EXCLUDED.reversal_6_count,
            reversal_6_winrate = EXCLUDED.reversal_6_winrate,
            reversal_6_avg_move = EXCLUDED.reversal_6_avg_move,
            reversal_9_count = EXCLUDED.reversal_9_count,
            reversal_9_winrate = EXCLUDED.reversal_9_winrate,
            reversal_9_avg_move = EXCLUDED.reversal_9_avg_move,
            reversal_12_count = EXCLUDED.reversal_12_count,
            reversal_12_winrate = EXCLUDED.reversal_12_winrate,
            reversal_12_avg_move = EXCLUDED.reversal_12_avg_move,
            scanned_at = NOW()
    """
    with conn.cursor() as cur:
        cur.execute(upsert_sql, (
            symbol,
            stats["total_extremes"],
            stats["long_crowd_extremes"],
            stats["short_crowd_extremes"],
            stats["reversal_3"]["count"],
            stats["reversal_3"]["winrate"],
            stats["reversal_3"]["avg_move"],
            stats["reversal_6"]["count"],
            stats["reversal_6"]["winrate"],
            stats["reversal_6"]["avg_move"],
            stats["reversal_9"]["count"],
            stats["reversal_9"]["winrate"],
            stats["reversal_9"]["avg_move"],
            stats["reversal_12"]["count"],
            stats["reversal_12"]["winrate"],
            stats["reversal_12"]["avg_move"],
        ))
        conn.commit()


def collect_stats_for_symbol(extremes_analysis):
    """Собрать статистику по символу"""
    stats = {
        "total_extremes": len(extremes_analysis),
        "long_crowd_extremes": 0,
        "short_crowd_extremes": 0,
    }

    # Инициализируем для каждого forward period
    for fp in FORWARD_PERIODS:
        stats[f"reversal_{fp}"] = {
            "count": 0,
            "reversal_count": 0,
            "winrate": 0.0,
            "total_move": 0.0,
            "avg_move": 0.0,
        }

    for item in extremes_analysis:
        if item["is_long_crowd"]:
            stats["long_crowd_extremes"] += 1
        else:
            stats["short_crowd_extremes"] += 1

        for fp in FORWARD_PERIODS:
            rev = item["reversal"].get(fp, {})
            if rev.get("reason") == "ok":
                stats[f"reversal_{fp}"]["count"] += 1
                if rev["reversed"]:
                    stats[f"reversal_{fp}"]["reversal_count"] += 1
                stats[f"reversal_{fp}"]["total_move"] += abs(rev.get("price_change_pct", 0))

    for fp in FORWARD_PERIODS:
        s = stats[f"reversal_{fp}"]
        if s["count"] > 0:
            s["winrate"] = round(s["reversal_count"] / s["count"] * 100, 2)
            s["avg_move"] = round(s["total_move"] / s["count"], 4)

    return stats


def scan_symbol(conn, symbol):
    """Сканировать один символ на FR Traps"""
    rows = get_funding_data(conn, symbol)
    if len(rows) < ROLLING_WINDOW + max(FORWARD_PERIODS):
        return None, f"Недостаточно данных ({len(rows)} записей, нужно > {ROLLING_WINDOW + max(FORWARD_PERIODS)})"

    # Рассчитать z-scores
    scored = calculate_z_scores(rows)

    # Найти экстремумы
    extremes_analysis = []
    for i, s in enumerate(scored):
        if not s["is_extreme"]:
            continue
        rev = check_reversal(i, scored, FORWARD_PERIODS)
        extremes_analysis.append({
            "idx": i,
            "timestamp": s["timestamp"],
            "rate": s["rate"],
            "mark_price": s["mark_price"],
            "z_score": s["z_score"],
            "is_long_crowd": s["rate"] > 0,
            "reversal": rev,
        })

    return extremes_analysis, None


def main():
    print("=" * 70)
    print("FR TRAP SCANNER — сканирование funding rate traps")
    print("=" * 70)
    print(f"Порог Z-score: |z| > {Z_THRESHOLD}")
    print(f"Окно: {ROLLING_WINDOW} периодов (~30 дней)")
    print(f"Проверка разворота через: {FORWARD_PERIODS} периодов (8h)")
    print()

    conn = psycopg2.connect(**DB_CONN)
    conn.autocommit = False

    print("Получаю список символов...")
    symbols = get_symbols(conn)
    print(f"Всего символов: {len(symbols)}")
    print()

    # Создаём таблицы для результатов
    create_stats_table(conn)

    all_symbol_stats = {}
    total_extremes_all = 0
    symbols_with_extremes = 0

    for idx, symbol in enumerate(symbols):
        print(f"[{idx + 1}/{len(symbols)}] {symbol}...", end=" ", flush=True)

        extremes_analysis, err = scan_symbol(conn, symbol)

        if err:
            print(f"⚠ {err}")
            continue

        if not extremes_analysis:
            print("✓ нет экстремумов")
            continue

        # Сохраняем детальные результаты
        save_results_table(conn, symbol, extremes_analysis)

        # Собираем статистику
        stats = collect_stats_for_symbol(extremes_analysis)
        all_symbol_stats[symbol] = stats
        total_extremes_all += stats["total_extremes"]

        # Сохраняем статистику
        save_symbol_stats(conn, symbol, stats)

        print(f"✓ {stats['total_extremes']} экстремумов", end="")
        for fp in FORWARD_PERIODS:
            s = stats[f"reversal_{fp}"]
            print(f" | {fp}p: {s['winrate']}% wr ({s['reversal_count']}/{s['count']})", end="")
        print()

        symbols_with_extremes += 1

    print()
    print("=" * 70)
    print("ГЛОБАЛЬНАЯ СТАТИСТИКА")
    print("=" * 70)
    print(f"Всего символов с экстремумами: {symbols_with_extremes}")
    print(f"Всего экстремумов: {total_extremes_all}")

    # Агрегируем глобальную статистику
    global_stats = {}
    for fp in FORWARD_PERIODS:
        fp_total_count = 0
        fp_reversal_count = 0
        fp_total_move = 0.0
        for sym, st in all_symbol_stats.items():
            s = st[f"reversal_{fp}"]
            fp_total_count += s["count"]
            fp_reversal_count += s["reversal_count"]
            fp_total_move += s["total_move"]

        global_stats[fp] = {
            "total_tested": fp_total_count,
            "reversals": fp_reversal_count,
            "winrate_pct": round(fp_reversal_count / fp_total_count * 100, 2) if fp_total_count > 0 else 0,
            "avg_move_pct": round(fp_total_move / fp_total_count, 4) if fp_total_count > 0 else 0,
        }
        print(f"\n  Разворот через {fp} периодов ({fp * 8}h):")
        print(f"    Протестировано: {fp_total_count}")
        print(f"    Разворотов: {fp_reversal_count}")
        print(f"    Win rate: {global_stats[fp]['winrate_pct']}%")
        print(f"    Среднее движение: {global_stats[fp]['avg_move_pct']}%")

    # Сохраняем глобальную статистику
    output = {
        "scan_timestamp": datetime.now(timezone.utc).isoformat(),
        "z_threshold": Z_THRESHOLD,
        "rolling_window_periods": ROLLING_WINDOW,
        "forward_periods": FORWARD_PERIODS,
        "symbols_with_extremes": symbols_with_extremes,
        "total_extremes": total_extremes_all,
        "global_stats": global_stats,
        "per_symbol": {},
    }

    for sym, st in all_symbol_stats.items():
        output["per_symbol"][sym] = {
            "total_extremes": st["total_extremes"],
            "long_crowd": st["long_crowd_extremes"],
            "short_crowd": st["short_crowd_extremes"],
        }
        for fp in FORWARD_PERIODS:
            output["per_symbol"][sym][f"reversal_{fp}p"] = {
                "tested": st[f"reversal_{fp}"]["count"],
                "reversals": st[f"reversal_{fp}"]["reversal_count"],
                "winrate_pct": st[f"reversal_{fp}"]["winrate"],
                "avg_move_pct": st[f"reversal_{fp}"]["avg_move"],
            }

    save_results_json(output)
    conn.close()
    print("\n✅ Сканирование завершено!")


if __name__ == "__main__":
    main()
