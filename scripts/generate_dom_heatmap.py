#!/usr/bin/env python3
"""
Скрипт генерации DOM тепловой карты напрямую из БД — без MCP, без LLM.

Использует data_provider для чтения DOM и баров из PostgreSQL,
строит Plotly тепловую карту и сохраняет PNG + HTML.

Пример:
  # Весь январь
  python scripts/generate_dom_heatmap.py --symbol AUDJPY --start 2025-01-01 --end 2025-01-31

  # Конкретный день с кастомным выводом
  python scripts/generate_dom_heatmap.py --symbol AUDJPY --start 2025-01-15 --end 2025-01-15 --output ~/chart.png
"""
import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# --- Пути проекта ---
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORE_PATH = PROJECT_ROOT / "services" / "CORE"
SCOPE_PATH = PROJECT_ROOT / "services" / "SCOPE" / "backend"
sys.path.insert(0, str(CORE_PATH))
sys.path.insert(0, str(SCOPE_PATH))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import data_provider
import numpy as np
from heatmap_renderer import build_heatmap_figure, compute_max_abs


# --- PostgreSQL ---
DB_PASSWORD = os.environ.get("DB_PASSWORD", "postgres")
DATABASE_URL = f"postgresql://postgres:***@localhost:5432/forex"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


def build_heatmap(symbol: str, start: datetime, end: datetime,
                  limit: int = None, min_volume: float = 0.0,
                  display_range: float = 0.04,
                  max_times: int = 500, max_prices: int = 200):
    """Построить DOM тепловую карту. Возвращает (fig, stats) или None."""
    session = SessionLocal()
    try:
        # Читаем DOM из БД (raw — без лимита)
        dom_levels = data_provider.read_dom(
            session, symbol, start, end, min_positions=0.1
        )
        if not dom_levels:
            print("Нет DOM данных за указанный период")
            return None

        print(f"Прочитано {len(dom_levels)} DOM уровней из БД")

        # Группируем по времени
        dom_by_time = {}
        for level in dom_levels:
            if level.time not in dom_by_time:
                dom_by_time[level.time] = []
            dom_by_time[level.time].append(level)

        # Бары для цен
        bars = data_provider.read_bars(session, symbol, start, end)
        for bar in bars:
            if bar.time.tzinfo is None:
                bar.time = bar.time.replace(tzinfo=timezone.utc)
        bars_dict = {bar.time: bar for bar in bars}

        # Собираем снапшоты (как MCP сервер)
        sorted_times = sorted(dom_by_time.keys())
        if limit:
            sorted_times = sorted_times[:limit]

        csv_lines = []
        for t in sorted_times:
            levels = dom_by_time[t]
            bar = bars_dict.get(t)
            if not bar:
                closest = min(bars, key=lambda b: abs((b.time - t).total_seconds()),
                              default=None)
                if closest and abs((closest.time - t).total_seconds()) <= 1200:
                    bar = closest
            if not bar:
                continue

            current_price = bar.close
            price_range = current_price * 0.05

            filtered = []
            for level in levels:
                if abs(level.price - current_price) > price_range:
                    continue
                if abs(level.positions) < 0.1:
                    continue
                filtered.append({
                    "price": round(level.price, 2),
                    "positions": round(level.positions, 2),
                })

            if not filtered:
                continue

            levels_str = ";".join(
                f"{l['price']},{l['positions']}" for l in filtered
            )
            csv_lines.append(
                f"{t.isoformat()}|{current_price}|{levels_str}"
            )

        if not csv_lines:
            print("Нет снапшотов после фильтрации")
            return None

        print(f"Собрано {len(csv_lines)} снапшотов")

        # Парсим CSV (как plot_dom_heatmap.py)
        dom_rows = _parse_dom_csv("\n".join(csv_lines))
        print(f"Парсинг: {len(dom_rows)} DOM позиций")

        # Собираем instrument_prices
        instrument_prices = {}
        for row in dom_rows:
            t = row["time"]
            if t not in instrument_prices:
                instrument_prices[t] = row["instrument_price"]

        # Тепловая карта
        heat_data = _create_heatmap_data(
            dom_rows, symbol,
            instrument_prices=instrument_prices,
            display_range_percent=display_range,
            min_volume_abs=min_volume,
        )
        if not heat_data:
            return None

        max_abs = compute_max_abs(heat_data["z"])

        # Plotly через общий рендерер
        price_times = sorted(instrument_prices.keys())
        price_values = [instrument_prices[t] for t in price_times]

        fig = build_heatmap_figure(
            times=heat_data["x"],
            prices=heat_data["y"],
            z=heat_data["z"],
            max_abs=max_abs,
            symbol=symbol,
            title=f"DOM Heatmap — {symbol} ({start.date()} to {end.date()})",
            price_times=price_times,
            price_values=price_values,
        )

        stats = {
            "snapshots": len(csv_lines),
            "dom_levels": len(dom_levels),
            "buy_max": heat_data["max_pos"],
            "sell_max": heat_data["max_neg"],
        }

        return fig, stats

    finally:
        session.close()


def _parse_dom_csv(csv_data: str) -> list:
    """Парсит CSV: время|цена|цена1,объём1;цена2,объём2"""
    rows = []
    for line in csv_data.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) < 3:
            continue
        time_str, inst_price = parts[0], float(parts[1])
        for pos in parts[2].split(";"):
            pos = pos.strip()
            if not pos:
                continue
            try:
                p, v = pos.split(",")
                rows.append({"time": time_str, "price": float(p),
                             "volume": float(v), "instrument_price": inst_price})
            except (ValueError, IndexError):
                continue
    return rows


def _create_heatmap_data(dom_rows, symbol, instrument_prices=None,
                          display_range_percent=0.04,
                          max_times=500, max_prices=200, min_volume_abs=0.0):
    """SCOPE-точный алгоритм построения матрицы тепловой карты."""
    if not dom_rows:
        return None

    jpy_pairs = ["USDJPY", "EURJPY", "GBPJPY", "AUDJPY", "CADJPY", "CHFJPY"]
    pip_size = 0.01 if symbol.upper() in jpy_pairs else 0.0001

    if min_volume_abs > 0:
        dom_rows = [r for r in dom_rows if abs(r["volume"]) >= min_volume_abs]
    if not dom_rows:
        return None

    if instrument_prices and len(instrument_prices) > 0:
        prices = list(instrument_prices.values())
        p_min, p_max = min(prices), max(prices)
        pad = (p_max - p_min) * 0.3
        d_min, d_max = p_min - pad, p_max + pad
    else:
        all_p = [r["price"] for r in dom_rows]
        med = np.median(all_p)
        d_min = med * (1 - display_range_percent)
        d_max = med * (1 + display_range_percent)

    filtered = [r for r in dom_rows if d_min <= r["price"] <= d_max]
    if not filtered:
        filtered = dom_rows

    dom_data = []
    for r in filtered:
        dom_data.append({
            "time": r["time"],
            "price": round(r["price"] / pip_size) * pip_size,
            "volume": r["volume"],
        })

    times = sorted(set(r["time"] for r in dom_data))
    prices = sorted(set(r["price"] for r in dom_data))

    if len(times) > max_times:
        step = int(np.ceil(len(times) / max_times))
        times = [times[i] for i in range(0, len(times), step)][:max_times]
    if len(prices) > max_prices:
        step = int(np.ceil(len(prices) / max_prices))
        prices = [prices[i] for i in range(0, len(prices), step)][:max_prices]

    ts, ps = set(times), set(prices)
    matrix = {}
    for r in dom_data:
        if r["time"] in ts and r["price"] in ps:
            key = f"{r['price']}_{r['time']}"
            matrix[key] = matrix.get(key, 0) + r["volume"]

    z = []
    for p in prices:
        row = [matrix.get(f"{p}_{t}", 0) for t in times]
        z.append(row)

    max_neg = max((abs(v) for row in z for v in row if v < 0), default=1)
    max_pos = max((v for row in z for v in row if v > 0), default=1)
    if max_neg == 0: max_neg = 1
    if max_pos == 0: max_pos = 1
    max_abs = max(max_neg, max_pos)

    return {"x": times, "y": prices, "z": z,
            "max_neg": max_neg, "max_pos": max_pos, "max_abs": max_abs}


def main():
    parser = argparse.ArgumentParser(
        description="Генерация DOM тепловой карты напрямую из БД"
    )
    parser.add_argument("--symbol", default="AUDJPY", help="Торговый символ")
    parser.add_argument("--start", required=True,
                        help="Начало (ISO: 2025-01-01 или 2025-01-01T00:00:00)")
    parser.add_argument("--end", required=True,
                        help="Конец (ISO: 2025-01-31 или 2025-01-31T23:59:59)")
    parser.add_argument("--output", default=None,
                        help="Путь для PNG (по умолчанию ~/.hermes/cache/screenshots/)")
    parser.add_argument("--range", type=float, default=0.04,
                        help="±% отображения (default: 0.04)")
    parser.add_argument("--min-volume", type=float, default=0.0,
                        help="Мин. |volume|")
    parser.add_argument("--limit", type=int, default=None,
                        help="Макс. снепшотов")
    args = parser.parse_args()

    # Нормализация дат
    start_str = args.start
    end_str = args.end
    if "T" not in start_str:
        start_str += "T00:00:00"
    if "T" not in end_str:
        end_str += "T23:59:59"

    start = datetime.fromisoformat(start_str)
    end = datetime.fromisoformat(end_str)
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    print(f"Генерация DOM тепловой карты: {args.symbol} "
          f"{start.date()} → {end.date()}")

    result = build_heatmap(
        args.symbol, start, end,
        limit=args.limit,
        min_volume=args.min_volume,
        display_range=args.range,
    )

    if not result:
        print("Ошибка: не удалось построить тепловую карту")
        return 1

    fig, stats = result

    # Путь вывода
    if args.output:
        output_path = Path(args.output)
    else:
        cache_dir = Path.home() / ".hermes" / "cache" / "screenshots"
        cache_dir.mkdir(parents=True, exist_ok=True)
        date_str = f"{start.strftime('%Y%m%d')}_{end.strftime('%Y%m%d')}"
        output_path = cache_dir / f"dom_heatmap_{args.symbol.lower()}_{date_str}.png"

    os.makedirs(output_path.parent, exist_ok=True)

    # HTML
    html_path = output_path.with_suffix(".html")
    fig.write_html(str(html_path), include_plotlyjs="cdn")
    print(f"HTML: {html_path}")

    # PNG
    try:
        import plotly.io as pio
        pio.write_image(fig, str(output_path), format="png",
                        width=1200, height=800, scale=2)
        print(f"PNG:  {output_path}")
    except Exception as e:
        print(f"PNG export failed: {e}")

    print(f"\nСтатистика:")
    print(f"  Снепшотов:  {stats['snapshots']}")
    print(f"  DOM уровней: {stats['dom_levels']}")
    print(f"  Buy max:     {stats['buy_max']:.2f} lot")
    print(f"  Sell max:    {stats['sell_max']:.2f} lot")

    return 0


if __name__ == "__main__":
    sys.exit(main())
