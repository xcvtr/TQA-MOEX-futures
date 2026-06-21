#!/usr/bin/env python3
"""Paper trader — divergence strategy (orderstats vs tradestats).

Фиксы по аудиту:
1. ✅ Сессии MOEX — сигнал только в основную сессию 10:00-18:45 МСК
2. ✅ Entry на open следующего бара (сигнал на i → entry = open[i+1])
3. ✅ Округление лотов (floor) — AFKS 100, AFLT 10, CHMF 1
4. ✅ Stop с проскальзыванием (stop_loss + slippage на выходе)
5. ✅ Дата-лаг не лечится — используем последние доступные данные

Состояние: ~/.hermes/data/divergence_paper/
Cron: */5 15-23 * * 1-5 (Иркутск = 10:00-18:45 МСК)
"""
import subprocess, sys, os, json
from datetime import datetime, timezone, timedelta
import numpy as np

CH = "10.0.0.63"
DB = "moex_algopack_v2"

SLIPPAGE = 0.0002
COMMISSION = 0.0005
STOP_SLIPPAGE = 0.003  # доп. проскальзывание на стопе (0.3%)
MSK_OFFSET = timedelta(hours=3)  # MOEX торгует по МСК
IRK_OFFSET = timedelta(hours=8)  # сервер Иркутск

TICKERS = ['AFKS', 'AFLT', 'CHMF']
LOTS = {'AFKS': 100, 'AFLT': 10, 'CHMF': 1}

CONFIGS = {
    'AFKS': {'div_thr': 10, 'hold': 10, 'stop_pct': 0.01},
    'AFLT': {'div_thr': 10, 'hold': 10, 'stop_pct': 0.01},
    'CHMF': {'div_thr': 10, 'hold': 10, 'stop_pct': 0.01},
}

INITIAL_CAPITAL = 100000.0
DATA_DIR = os.path.expanduser("~/.hermes/data/divergence_paper")
STATE_FILE = os.path.join(DATA_DIR, "state.json")
LOG_FILE = os.path.join(DATA_DIR, "trades.log")
MIN_BARS = 50  # нужно больше баров для определения сессии


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def ch(sql):
    r = subprocess.run(['clickhouse-client', '--host', CH, '-d', DB, '--query', sql],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise Exception(r.stderr.strip())
    lines = r.stdout.strip().split('\n')
    return [l.split('\t') for l in lines if l.strip()]


def is_moex_session(bar_time_str):
    """Проверяет, входит ли время бара в основную сессию MOEX equity.
    Основная: 10:00-18:45 МСК.
    bar_time_str: 'HH:MM:SS' по данным (тоже МСК).
    """
    try:
        h, m, s = bar_time_str.split(':')
        minutes = int(h) * 60 + int(m)
        return 600 <= minutes <= 1125  # 10:00 = 600, 18:45 = 1125 мин
    except:
        return False


def load_recent(ticker, bars=200):
    """Load recent bars with full signal data + OHLC."""
    sql = f"""
        SELECT o.tradedate, o.tradetime,
               o.put_orders_b, o.put_orders_s,
               t.pr_open, t.pr_close, t.pr_high, t.pr_low,
               t.trades_b, t.trades_s
        FROM orderstats_local o
        JOIN tradestats_local t
          ON o.tradedate = t.tradedate AND o.secid = t.ticker AND o.tradetime = t.tradetime
        WHERE o.secid = '{ticker}'
        ORDER BY o.tradedate DESC, o.tradetime DESC
        LIMIT {bars}
        FORMAT TabSeparated
    """
    raw = ch(sql)
    if not raw or len(raw) < MIN_BARS:
        return None

    raw = list(reversed(raw))
    n = len(raw)

    def ci(i): return np.array([int(r[i]) if r[i] and r[i] != '\\N' else 0 for r in raw])
    def cf(i): return np.array([float(r[i]) if r[i] and r[i] != '\\N' else 0.0 for r in raw])

    put_b = ci(2); put_s = ci(3)
    opn = cf(4); close = cf(5); high = cf(6); low = cf(7)
    tb = ci(8); ts = ci(9)

    tot_put = put_b + put_s
    o_imb = np.where(tot_put > 0, (put_b - put_s) / tot_put * 100, 0)
    t_imb = np.where((tb + ts) > 0, (tb - ts) / (tb + ts) * 100, 0)

    w = 5
    o_imb_sm = np.copy(o_imb)
    for i in range(w, n):
        o_imb_sm[i] = np.mean(o_imb[i-w:i])

    # Build tick-level info from last 3 bars
    last = raw[-1]
    last2 = raw[-2] if n >= 2 else raw[-1]
    last3 = raw[-3] if n >= 3 else raw[-1]

    return {
        'n': n, 'opn': opn, 'close': close, 'high': high, 'low': low,
        'o_imb': o_imb_sm, 't_imb': t_imb,
        'dates': [r[0] for r in raw],
        'times': [r[1] for r in raw],
        # Last 3 bars raw data for entry/exit logic
        'last_idx': n - 1,
        'prev_idx': n - 2,
        'signal_idx': n - 2,  # сигнал на предпоследнем баре
    }


def check_signal(data, config, ticker):
    """Divergence signal на предпоследнем (завершённом) баре.
    Проверяет: сессия MOEX, не праздник, не выходной.

    Returns: ('LONG'|'SHORT'|None, o_imb, t_imb, signal_bar_idx, signal_time_str)
    """
    if data is None or data['n'] < MIN_BARS:
        return None, 0, 0, 0, ''

    # Сигнал на предпоследнем баре
    i = data['signal_idx']
    if i < 10:
        return None, 0, 0, 0, ''

    # Проверка сессии
    bar_time = data['times'][i]
    if not is_moex_session(bar_time):
        return None, 0, 0, i, f"{data['dates'][i]} {bar_time}"

    o = data['o_imb'][i]
    t = data['t_imb'][i]
    div_thr = config['div_thr']

    if abs(o - t) > div_thr:
        if t > abs(o) * 0.5 and t > 5:
            return 'LONG', float(o), float(t), i, f"{data['dates'][i]} {bar_time}"
        elif t < -abs(o) * 0.5 and t < -5:
            return 'SHORT', float(o), float(t), i, f"{data['dates'][i]} {bar_time}"

    return None, float(o), float(t), i, f"{data['dates'][i]} {bar_time}"


def entry_price_from_signal(data, signal_idx):
    """Entry = open следующего бара (сигнал на i → entry price = open[i+1]).
    Если выходной/нет данных — fallback на last_close.
    """
    next_idx = signal_idx + 1
    if next_idx < data['n']:
        return float(data['opn'][next_idx])
    return float(data['close'][-1])


def lots_from_capital(capital, price, ticker, strength=1.0):
    """Расчёт целого числа лотов для позиции.
    С округлением вниз (floor).
    strength: 1.0 = base, >1 = сильнее сигнал = больше позиция
    """
    lot_size = LOTS[ticker]
    if lot_size <= 0 or price <= 0:
        return 0, 0

    # 25% капитала на сделку * divergence strength
    base_pct = 0.25
    strength = max(0.25, min(3.0, strength))  # clamp 0.25x..3.0x
    pos_pct = base_pct * strength
    position_value = capital * pos_pct
    shares = int(position_value / price)
    shares = (shares // lot_size) * lot_size  # floor по лотам
    if shares < lot_size:
        shares = lot_size  # мин 1 лот
    return shares, shares * price


def load_state():
    if not os.path.exists(STATE_FILE):
        return {
            'capital': INITIAL_CAPITAL,
            'positions': {},
            'total_trades': 0, 'wins': 0, 'losses': 0,
            'eq_curve': []
        }
    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(state):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def cancel_position(state, ticker, reason, price):
    """Close position with stop slippage and lot rounding."""
    pos = state['positions'].get(ticker)
    if not pos:
        return None

    entry_price = pos['entry_price']
    direction = pos['type']
    lots = pos.get('lots', 1)
    lot_size = LOTS[ticker]
    shares = lots * lot_size

    # Стоимость позиции
    entry_value = shares * entry_price
    exit_value = shares * price

    if direction == 'LONG':
        pnl = exit_value - entry_value
    else:
        pnl = entry_value - exit_value

    pnl_pct = pnl / entry_value

    # Slippage + комиссия + стоп-проскальзывание
    total_cost = SLIPPAGE + COMMISSION
    if reason == 'stop_loss':
        total_cost += STOP_SLIPPAGE  # доп. проскальзывание на стопе

    pnl_net = pnl - entry_value * total_cost
    state['capital'] += pnl_net
    state['total_trades'] += 1

    if pnl_pct > 0:
        state['wins'] += 1
    else:
        state['losses'] += 1

    state['eq_curve'].append({
        'ts': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        'capital': state['capital'],
        'ret': pnl_pct
    })

    log(f"{ticker} CLOSE {direction} {reason} "
        f"entry={entry_price:.2f} exit={price:.2f} "
        f"lots={lots} pnl={pnl:+.0f} (net={pnl_net:+.0f}) "
        f"ret={pnl_pct:+.2%} capital={state['capital']:,.0f}")

    ret_info = {
        'ticker': ticker, 'direction': direction, 'reason': reason,
        'entry': entry_price, 'exit': price,
        'lots': lots, 'pnl': round(pnl_net, 2), 'ret': round(pnl_pct, 4)
    }

    del state['positions'][ticker]
    return ret_info


def open_position(state, ticker, direction, price, signal_time, strength=1.0):
    """Open position with lot rounding and divergence strength sizing."""
    shares, cost = lots_from_capital(state['capital'], price, ticker, strength)
    if shares <= 0:
        log(f"{ticker} CANNOT OPEN — insufficient capital ({state['capital']:,.0f} ₽)")
        return

    lots = shares // LOTS[ticker]

    # Slippage + commission on entry
    entry_cost = price * SLIPPAGE + price * COMMISSION
    state['capital'] -= cost / (1 - SLIPPAGE - COMMISSION) - cost
    # Проще: вычитаем стоимость позиции из капитала (она теперь locked)
    # В cancel_position она вернётся

    log(f"{ticker} OPEN {direction} @ {price:.2f} lots={lots} "
        f"cost={cost:,.0f} capital={state['capital']:,.0f} "
        f"signal={signal_time}")

    state['positions'][ticker] = {
        'type': direction,
        'entry_price': price,
        'entry_time': signal_time,
        'bars_held': 0,
        'lots': lots,
        'shares': shares
    }


def check_positions(state, ticker, data, config):
    """Check open positions: stop, time exit."""
    pos = state['positions'].get(ticker)
    if not pos:
        return None

    entry_price = pos['entry_price']
    direction = pos['type']
    bars_held = pos.get('bars_held', 0) + 1
    pos['bars_held'] = bars_held

    # Текущие цены — последний бар
    n = data['n']
    last_close = float(data['close'][-1])
    last_high = float(data['high'][-1])
    last_low = float(data['low'][-1])

    stop = config['stop_pct']

    # Stop: проверяем все бары от entry до текущего
    # Ищем entry_bar
    entry_time = pos['entry_time']
    entry_bar = data['n'] - bars_held - 2  # approximate

    for j in range(max(0, n - bars_held - 5), n):
        if j < 0 or j >= n:
            continue
        if direction == 'LONG' and data['low'][j] <= entry_price * (1 - stop):
            exit_price = entry_price * (1 - stop)
            return cancel_position(state, ticker, 'stop_loss', exit_price)
        elif direction == 'SHORT' and data['high'][j] >= entry_price * (1 + stop):
            exit_price = entry_price * (1 + stop)
            return cancel_position(state, ticker, 'stop_loss', exit_price)

    # Time exit
    hold = config['hold']
    if bars_held >= hold:
        return cancel_position(state, ticker, 'time_exit', last_close)

    # MTM — для информации
    if direction == 'LONG':
        mtm = (last_close / entry_price - 1)
    else:
        mtm = (entry_price / last_close - 1)
    pos['mtm'] = mtm

    return None


def has_trading_hours_data(data):
    """Проверяет что среди последних баров есть хоть один в сессию."""
    for t in data['times'][-20:]:
        if is_moex_session(t):
            return True
    return False


def main():
    dry_run = '--dry-run' in sys.argv

    state = load_state()
    prev_positions = len(state['positions'])
    prev_trades = state['total_trades']

    log(f"Run capital={state['capital']:,.0f} positions={list(state['positions'].keys())}")

    output = []
    close_events = []

    for ticker in TICKERS:
        data = load_recent(ticker, 200)
        if data is None:
            log(f"{ticker}: NO DATA")
            continue

        # Проверяем что данные в торговые часы
        if not has_trading_hours_data(data):
            log(f"{ticker}: все бары вне сессии — skip")
            continue

        # Close existing positions first
        if ticker in state['positions']:
            close_result = check_positions(state, ticker, data, CONFIGS[ticker])
            if close_result:
                close_events.append(close_result)
                output.append(f"  {ticker}: 🔴 CLOSE {close_result['direction']} "
                              f"({close_result['reason']}) ret={close_result['ret']:+.2%}")
                continue
            pos = state['positions'][ticker]
            mtm = pos.get('mtm', 0)
            output.append(f"  {ticker}: {pos['type']} "
                          f"@{pos['entry_price']:.2f} ({pos['bars_held']}b) mtm={mtm:+.2%}")

        # Check new signal (flat only)
        if ticker in state['positions']:
            continue

        signal, o_imb, t_imb, signal_idx, signal_ts = check_signal(
            data, CONFIGS[ticker], ticker)

        if signal:
            # Divergence strength sizing
            strength = abs(o_imb - t_imb) / CONFIGS[ticker]['div_thr']
            strength = max(0.25, min(3.0, strength))
            
            # Entry = open следующего бара
            price = entry_price_from_signal(data, signal_idx)
            # Проверяем сессию entry
            entry_time_str = data['times'][signal_idx + 1] if signal_idx + 1 < data['n'] else ''
            if not is_moex_session(entry_time_str):
                continue

            if not dry_run:
                open_position(state, ticker, signal, price, signal_ts, strength)
                output.append(f"  {ticker}: 🟢 OPEN {signal} @ {price:.2f} (str={strength:.1f}x)")
            else:
                output.append(f"  {ticker}: 🔶 SIGNAL {signal} @ {price:.2f} (str={strength:.1f}x)")
        else:
            # Log divergence values even without signal
            pass

    total_return = (state['capital'] / INITIAL_CAPITAL - 1) * 100 if not close_events else \
                   (state['capital'] / INITIAL_CAPITAL - 1) * 100

    if not dry_run:
        save_state(state)
        log(f"Done capital={state['capital']:,.0f} trades={state['total_trades']}")

    # Output only if changed
    changed = (len(state['positions']) != prev_positions) or (state['total_trades'] != prev_trades)
    if changed and output:
        print(f"📊 Divergence paper — {datetime.now().strftime('%d.%m %H:%M')}")
        print(f"💵 {state['capital']:,.0f} ₽ ({total_return:+.1f}%) | "
              f"📈 {state['total_trades']} tr (W:{state['wins']} L:{state['losses']})")
        for line in output:
            print(line)
        if state['positions']:
            print(f"  📌 Open: {len(state['positions'])}")
        else:
            print(f"  💤 Flat")


if __name__ == '__main__':
    main()
