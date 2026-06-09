"""
Portfolio Optimizer — Priority + Correlation + Sector Filter.

Replaces FIFO with portfolio-level allocation:
1. Per-ticker priority (by WR from oi_screening)
2. Correlation groups (no duplicate risk)
3. Sector caps (1 per group, 2 for rates/agri)
4. Capital weighting by priority tier
"""

from typing import List, Dict, Optional

TICKER_PRIORITY = {
    'FF': 1, 'RB': 2, 'AU': 3, 'SBERF': 4, 'MC': 5,
    'AF': 6, 'GK': 7, 'NM': 8, 'Si': 9, 'SP': 10,
    'MX': 11, 'KC': 12, 'SS': 13, 'NA': 14, 'SE': 15,
    'GL': 16, 'CNYRUBF': 17, 'MG': 18, 'SR': 19, 'GZ': 20,
    'W4': 21, 'CR': 22, 'HS': 23, 'RI': 24, 'RN': 25,
    'DX': 26, 'SN': 27, 'HY': 28, 'RL': 29, 'SF': 30,
    'CE': 31, 'TN': 32, 'PD': 33, 'MN': 34, 'CC': 35,
    'ED': 36, 'BR': 37, 'ME': 38, 'TT': 39, 'IMOEXF': 40,
    'GD': 41, 'EURRUBF': 42, 'SV': 43, 'CH': 44, 'UC': 45,
    'VI': 46, 'GLDRUBF': 47,
}

CORRELATION_GROUPS = {
    'rates':    ['ED', 'FF', 'CR', 'UC'],
    'gold':     ['AU', 'GL', 'GLDRUBF', 'GD'],
    'silver':   ['SV'],
    'aluminum': ['AF'],
    'copper':   ['GK'],
    'nickel':   ['NM'],
    'oil':      ['BR', 'RB'],
    'gas':      ['GZ'],
    'rts':      ['RI'],
    'imoex':    ['IMOEXF'],
    'usd':      ['Si', 'EURRUBF', 'DX'],
    'cny':      ['CNYRUBF'],
    'sber':     ['SBERF'],
    'agri':     ['FF', 'W4', 'CC', 'KC'],
    'metal':    ['MC', 'MX', 'MG', 'MN', 'NA', 'SE', 'SF', 'SN', 'SP', 'PD', 'RL', 'TN', 'SS'],
    'equity':   ['HY', 'SR', 'RN', 'HS', 'CE', 'CH', 'VI', 'TT', 'ME'],
}

SECTOR_CAP = {
    'rates': 2,
    'agri': 2,
}

TICKER_CONFIGS = {
    'AF': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'AU': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'BR': {'go': 3000, 'minstep': 0.01, 'tick_rub': 1.0},
    'CC': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'CE': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'CH': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'CNYRUBF': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'CR': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'DX': {'go': 3000, 'minstep': 1, 'tick_rub': 1.0},
    'ED': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'EURRUBF': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'FF': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'GD': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'GK': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'GL': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'GLDRUBF': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'GZ': {'go': 2065, 'minstep': 0.01, 'tick_rub': 0.01},
    'HS': {'go': 5000, 'minstep': 1, 'tick_rub': 1.0},
    'HY': {'go': 3000, 'minstep': 1, 'tick_rub': 1.0},
    'IMOEXF': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'KC': {'go': 2500, 'minstep': 0.01, 'tick_rub': 80.0},
    'MC': {'go': 3149, 'minstep': 0.01, 'tick_rub': 1.0},
    'ME': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'MG': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'MN': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'MX': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'NA': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'NM': {'go': 1405, 'minstep': 1, 'tick_rub': 1.0},
    'PD': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'RB': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'RI': {'go': 5000, 'minstep': 1, 'tick_rub': 1.0},
    'RL': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'RN': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'SBERF': {'go': 2500, 'minstep': 1, 'tick_rub': 1.0},
    'SE': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'SF': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'SN': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'SP': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'SR': {'go': 5719, 'minstep': 0.01, 'tick_rub': 1.0},
    'SS': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'SV': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'Si': {'go': 1000, 'minstep': 0.01, 'tick_rub': 1.0},
    'TN': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'TT': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'UC': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'VI': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
    'W4': {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0},
}

TICKER_TO_GROUP: Dict[str, str] = {}
for group, members in CORRELATION_GROUPS.items():
    for sym in members:
        TICKER_TO_GROUP[sym] = group

PRIORITY_WEIGHTS = {
    (1, 5): 3.0,
    (6, 15): 1.5,
    (16, 999): 1.0,
}
MAX_WEIGHT = 3.0


def _get_weight(priority: int) -> float:
    for (lo, hi), w in PRIORITY_WEIGHTS.items():
        if lo <= priority <= hi:
            return w
    return 1.0


def _calc_pnl(direction, entry, exit_price, contracts, symbol):
    cfg = TICKER_CONFIGS.get(symbol, {'go': 5000, 'minstep': 0.01, 'tick_rub': 1.0})
    minstep = cfg['minstep']
    tick_rub = cfg['tick_rub']
    moves = (exit_price - entry) / minstep
    if direction.upper() == 'SHORT':
        moves = -moves
    return round(moves * tick_rub * contracts, 2)


def _max_drawdown(equity):
    if not equity:
        return 0.0
    peak = equity[0]
    mdd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0
        if dd > mdd:
            mdd = dd
    return mdd


def simulate_adaptive_portfolio(
    signals: List[Dict],
    initial_capital: float,
    base_margin_usage: float,
    max_concurrent: int,
    base_total_margin_limit: float,
    max_dd_limit: float,
    stop_loss_pct: float = 0.02,
    score_threshold: float = 0.0,
) -> Dict:
    """Portfolio-aware simulation with priority, correlation groups, sector caps."""
    capital = float(initial_capital)
    equity = [capital]
    margin_ratio_history = [0.0]
    peak = capital
    compression_history = [1.0]
    active: Dict[str, Dict] = {}
    trades: List[Dict] = []

    def _total_equity():
        return capital + sum(p['locked_go'] for p in active.values())

    def _record_margin_usage():
        te = _total_equity()
        if te > 0:
            locked = sum(p['locked_go'] for p in active.values())
            margin_ratio_history.append(locked / te)
        else:
            margin_ratio_history.append(0.0)

    def _group_count(group_name: str) -> int:
        return sum(1 for p in active.values() if p.get('group') == group_name)

    for sig in signals:
        tk = sig.get('ticker', '')
        if not tk or tk not in TICKER_CONFIGS:
            continue

        te = _total_equity()
        if te > peak:
            peak = te
        compression = te / peak if peak > 0 else 1.0
        compression = max(min(compression, 1.0), 0.3)
        compression_history.append(compression)

        adaptive_margin = base_margin_usage * compression
        adaptive_tm_limit = base_total_margin_limit * compression

        dd = (peak - te) / peak if peak > 0 else 0
        if dd > max_dd_limit:
            for t in list(active.keys()):
                pos = active.pop(t)
                pnl = _calc_pnl(pos['direction'], pos['entry_price'], pos['exit_price'], pos['contracts'], t)
                capital += pos['locked_go'] + pnl
            equity.append(_total_equity())
            _record_margin_usage()
            break

        if tk in active:
            pos = active.pop(tk)
            pnl = _calc_pnl(pos['direction'], pos['entry_price'], pos['exit_price'], pos['contracts'], tk)
            capital += pos['locked_go'] + pnl
            peak = max(peak, _total_equity())
            equity.append(_total_equity())
            _record_margin_usage()
            trades.append({
                'ticker': tk, 'pnl': pnl,
                'entry_time': pos.get('entry_time', ''),
                'exit_time': sig.get('time', ''),
                'direction': pos['direction'],
                'contracts': pos['contracts'],
            })

        # ── Portfolio logic: correlation group + priority ──
        group = TICKER_TO_GROUP.get(tk, 'misc')
        priority = TICKER_PRIORITY.get(tk, 99)
        cap = SECTOR_CAP.get(group, 1)

        if _group_count(group) >= cap:
            continue

        if len(active) >= max_concurrent:
            worst_tk = min(active, key=lambda t: TICKER_PRIORITY.get(t, 99))
            worst_priority = TICKER_PRIORITY.get(worst_tk, 99)
            if priority >= worst_priority:
                continue
            pos = active.pop(worst_tk)
            pnl = _calc_pnl(pos['direction'], pos['entry_price'], pos['exit_price'], pos['contracts'], worst_tk)
            capital += pos['locked_go'] + pnl
            peak = max(peak, _total_equity())
            equity.append(_total_equity())
            _record_margin_usage()
            trades.append({
                'ticker': worst_tk, 'pnl': pnl,
                'entry_time': pos.get('entry_time', ''),
                'exit_time': sig.get('time', ''),
                'direction': pos['direction'],
                'contracts': pos['contracts'],
            })

        cfg = TICKER_CONFIGS.get(tk)
        if not cfg:
            continue
        go = cfg.get('go', 0)
        if go <= 0:
            continue

        weight = _get_weight(priority)
        total_cap = _total_equity()
        max_risk = total_cap * adaptive_margin * (weight / MAX_WEIGHT)
        contracts = int(max_risk // go) if max_risk >= go else 0
        if contracts < 1:
            continue
        locked_go = contracts * go

        total_locked = sum(p['locked_go'] for p in active.values())
        if total_locked + locked_go > total_cap * adaptive_tm_limit:
            continue

        entry_price = sig.get('entry', 0)
        exit_price = sig.get('exit', 0)
        direction = sig.get('direction', 'LONG')

        if stop_loss_pct > 0:
            if direction == 'LONG':
                stop_price = entry_price * (1 - stop_loss_pct)
                if exit_price < stop_price:
                    exit_price = stop_price
            else:
                stop_price = entry_price * (1 + stop_loss_pct)
                if exit_price > stop_price:
                    exit_price = stop_price

        if locked_go > capital:
            continue
        capital -= locked_go

        active[tk] = {
            'entry_price': entry_price,
            'exit_price': exit_price,
            'direction': direction,
            'contracts': contracts,
            'entry_time': sig.get('time', ''),
            'locked_go': locked_go,
            'group': group,
        }
        _record_margin_usage()

    for tk in list(active.keys()):
        pos = active.pop(tk)
        pnl = _calc_pnl(pos['direction'], pos['entry_price'], pos['exit_price'], pos['contracts'], tk)
        capital += pos['locked_go'] + pnl
        peak = max(peak, _total_equity())
        equity.append(_total_equity())
        _record_margin_usage()

    return {
        'final_capital': round(_total_equity(), 2),
        'equity': equity,
        'trades': trades,
        'margin_ratio': margin_ratio_history,
        'compression': compression_history,
    }
