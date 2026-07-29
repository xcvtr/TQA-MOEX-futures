"""
BrokerDOM — исполнение сделок через реальный стакан из PG futures.dom.

Заменяет расчёт PnL по close+lvl в manage_positions() на вытеснение
через OrderBookExecutor с проверкой объёмов bid/ask.
"""
import os

PG_HOST = os.getenv('MOEX_PG_HOST', '10.0.0.60')
PG_PORT = int(os.getenv('MOEX_PG_PORT', '5432'))
PG_DB = os.getenv('MOEX_PG_DB', 'moex')
PG_USER = os.getenv('MOEX_PG_USER', 'postgres')

import psycopg2


class BrokerDOM:
    """Брокер-эмулятор на основе стакана из PG. Position-agnostic."""

    def __init__(self, commission=4):
        self.commission = commission

    def _get_book(self, ticker: str):
        """Последний стакан из PG futures.dom. Возвращает (bids, asks)."""
        conn = psycopg2.connect(host=PG_HOST, port=PG_PORT, dbname=PG_DB,
                                user=PG_USER, connect_timeout=3)
        cur = conn.cursor()
        cur.execute("""
            SELECT side, price, volume FROM futures.dom
            WHERE ticker = %s AND ts > now() - INTERVAL '10 seconds'
            ORDER BY ts DESC LIMIT 60
        """, (ticker,))
        rows = cur.fetchall()
        cur.close(); conn.close()
        if not rows:
            return [], []
        bids, asks = [], []
        seen = set()
        for side, price, vol in rows:
            key = (side, round(price, 4))
            if key in seen:
                continue
            seen.add(key)
            if side == 1:  # bid
                bids.append((price, vol))
            else:           # ask
                asks.append((price, vol))
        bids.sort(key=lambda x: -x[0])
        asks.sort(key=lambda x: x[0])
        return bids[:15], asks[:15]

    def exit_long(self, ticker, entry_price, contracts, min_step, step_price, pct=1.0, fee=4):
        """Продажа по bid side. Возвращает (exit_price, pnl_rub, slippage)."""
        bids, _ = self._get_book(ticker)
        if not bids:
            return entry_price, 0.0, 0  # fallback — no slippage

        remaining = contracts
        total = 0.0
        levels = 0
        for price, vol in bids:
            if remaining <= 0:
                break
            fill = min(remaining, int(vol))
            total += fill * price
            remaining -= fill
            levels += 1

        if remaining > 0:
            total += remaining * (bids[-1][0] - min_step)

        avg_price = total / contracts
        ticks = (avg_price - entry_price) / max(min_step, 0.0001)
        pnl = ticks * step_price * contracts * pct - fee * 2 * contracts
        slippage = max(0, levels - 1)
        return avg_price, round(pnl, 2), slippage

    def exit_short(self, ticker, entry_price, contracts, min_step, step_price, pct=1.0, fee=4):
        """Покупка по ask side. Возвращает (exit_price, pnl_rub, slippage)."""
        _, asks = self._get_book(ticker)
        if not asks:
            return entry_price, 0.0, 0

        remaining = contracts
        total = 0.0
        levels = 0
        for price, vol in asks:
            if remaining <= 0:
                break
            fill = min(remaining, int(vol))
            total += fill * price
            remaining -= fill
            levels += 1

        if remaining > 0:
            total += remaining * (asks[-1][0] + min_step)

        avg_price = total / contracts
        ticks = (entry_price - avg_price) / max(min_step, 0.0001)
        pnl = ticks * step_price * contracts * pct - fee * 2 * contracts
        slippage = max(0, levels - 1)
        return avg_price, round(pnl, 2), slippage

    def entry_slippage(self, ticker, direction, contracts, min_step):
        """Реалистичное проскальзывание на вход в тиках."""
        bids, asks = self._get_book(ticker)
        if direction == 'long':
            book = asks
        else:
            book = bids
        if not book:
            return 2  # fallback
        levels = 0
        rem = contracts
        for _, vol in book:
            if rem <= 0:
                break
            rem -= int(vol)
            levels += 1
        return max(1, levels)
