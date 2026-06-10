"""
Portfolio Optimizer v2 — Priority + Correlation + Score-Aware.

Improvements over v1:
1. Score-based position sizing (more capital on confident signals)
2. Score-based eviction (keep high-quality, not just high-priority)
3. ATR-adaptive stop-loss (per-ticker volatility respect)
4. Score decay filter (reduce hot-ticker over-trading after win streaks)
5. Look-ahead bias FIXED in all eviction paths
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
    max_hold_bars: int = 40,
    # ── v2 improvements ──
    use_score_sizing: bool = True,
    use_score_eviction: bool = True,
    atr_stop_mult: float = 0.0,
    use_score_decay: bool = True,
    use_mtm: bool = True,
) -> Dict:
    """
    Portfolio-aware simulation with v2 improvements.

    v2 features (all opt-in):
    - use_score_sizing: position size scales with signal quality score
    - use_score_eviction: evict by score × priority, not pure priority
    - atr_stop_mult: if > 0, use ATR-based stop instead of fixed percentage
    - use_score_decay: boost effective threshold after consecutive wins
    """
    capital = float(initial_capital)
    equity = [capital]
    margin_ratio_history = [0.0]
    peak = capital
    compression_history = [1.0]
    active: Dict[str, Dict] = {}
    trades: List[Dict] = []

    # ── Score decay tracker ──
    consecutive_wins: Dict[str, int] = {}

    def _total_equity():
        base = capital + sum(p['locked_go'] for p in active.values())
        if use_mtm:
            mtm_pnl = 0.0
            for tk_pos, p in active.items():
                current_est = p.get('last_price', p['entry_price'])
                mtm_pnl += _calc_pnl(p['direction'], p['entry_price'], current_est, p['contracts'], tk_pos)
            return base + mtm_pnl
        return base

    def _record_margin_usage():
        te = _total_equity()
        if te > 0:
            locked = sum(p['locked_go'] for p in active.values())
            margin_ratio_history.append(locked / te)
        else:
            margin_ratio_history.append(0.0)

    def _group_count(group_name: str) -> int:
        return sum(1 for p in active.values() if p.get('group') == group_name)

    def _get_current_price(sig) -> float:
        """Get the current bar's price from the signal."""
        return sig.get('entry', 0)

    def _close_position(tk: str, close_price: float):
        """Close a position and return PnL."""
        pos = active.pop(tk)
        pnl = _calc_pnl(pos['direction'], pos['entry_price'], close_price, pos['contracts'], tk)
        return pnl, pos

    def _compute_effective_threshold(tk: str, base_th: float) -> float:
        """Apply score decay: after consecutive wins, raise threshold."""
        if not use_score_decay:
            return base_th
        cw = consecutive_wins.get(tk, 0)
        if cw <= 1:
            return base_th
        # decay factor: 1 win = 1.0, 2 wins = 1.15, 3 = 1.30, 4+ = 1.45
        decay = min(1.0 + (cw - 1) * 0.15, 1.45)
        return base_th * decay

    for sig in signals:
        tk = sig.get('ticker', '')
        if not tk or tk not in TICKER_CONFIGS:
            continue

        # ── Score decay gate ──
        sig_score = sig.get('score', 0.3)
        eff_th = _compute_effective_threshold(tk, score_threshold)
        if sig_score < eff_th:
            continue

        current_price = _get_current_price(sig)
        if use_mtm and tk in active:
            active[tk]['last_price'] = current_price

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
                close_price = current_price if t == tk else pos.get('last_price', pos['entry_price'])
                pnl = _calc_pnl(pos['direction'], pos['entry_price'], close_price, pos['contracts'], t)
                capital += pos['locked_go'] + pnl
            equity.append(_total_equity())
            _record_margin_usage()
            break

        # ── Dynamic Time-stop (v3: score-adaptive + ADX modulator) ──
        if max_hold_bars > 0:
            for t in list(active.keys()):
                active[t]['bars_held'] = active[t].get('bars_held', 0) + 1
                if t == tk:
                    active[t]['last_price'] = current_price
                pos_score = active[t].get('score', 0.3)
                # Score-каскад: hold_limit = max_hold * (0.5 + score), clamp [10, 80]
                hold_limit = int(max_hold_bars * (0.5 + pos_score))
                hold_limit = max(10, min(hold_limit, 80))
                # ADX модулятор
                adx_val = active[t].get('adx_value', 0)
                if adx_val > 25:
                    hold_limit = int(hold_limit * 1.5)
                elif adx_val > 0 and adx_val < 15:
                    hold_limit = int(hold_limit * 0.7)
                hold_limit = max(hold_limit, 1)
                if active[t]['bars_held'] >= hold_limit:
                    pos = active.pop(t)
                    # Close at exit_price (set at entry time, no look-ahead)
                    pnl = _calc_pnl(pos['direction'], pos['entry_price'], pos['exit_price'], pos['contracts'], t)
                    capital += pos['locked_go'] + pnl
                    if use_score_decay:
                        if pnl > 0:
                            consecutive_wins[t] = consecutive_wins.get(t, 0) + 1
                        else:
                            consecutive_wins[t] = 0
                    peak = max(peak, _total_equity())
                    equity.append(_total_equity())
                    _record_margin_usage()
                    trades.append({
                        'ticker': t, 'pnl': pnl,
                        'entry_time': pos.get('entry_time', ''),
                        'exit_time': sig.get('time', ''),
                        'direction': pos['direction'],
                        'contracts': pos['contracts'],
                    })
        # ── Same-ticker rollover: close old at current price ──
        if tk in active:
            close_price = current_price
            pnl, pos = _close_position(tk, close_price)
            capital += pos['locked_go'] + pnl
            # Update score decay
            if use_score_decay:
                if pnl > 0:
                    consecutive_wins[tk] = consecutive_wins.get(tk, 0) + 1
                else:
                    consecutive_wins[tk] = 0
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
            # Score-based eviction: rank by score × priority, evict worst
            if use_score_eviction:
                def _position_value(t):
                    p = active[t]
                    s = p.get('score', 0.0)
                    prio = TICKER_PRIORITY.get(t, 99)
                    return s / (1 + prio / 10)  # score dominant, priority tiebreaker

                worst_tk = min(active, key=_position_value)
                worst_val = _position_value(worst_tk)
                new_val = sig_score / (1 + priority / 10)
                if new_val <= worst_val:
                    continue
                pos = active.pop(worst_tk)
                close_price = current_price
                pnl = _calc_pnl(pos['direction'], pos['entry_price'], close_price, pos['contracts'], worst_tk)
                # Update score decay for evicted position
                if use_score_decay:
                    if pnl > 0:
                        consecutive_wins[worst_tk] = consecutive_wins.get(worst_tk, 0) + 1
                    else:
                        consecutive_wins[worst_tk] = 0
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
            else:
                # Original priority-only eviction (but with fixed look-ahead)
                worst_tk = min(active, key=lambda t: TICKER_PRIORITY.get(t, 99))
                worst_priority = TICKER_PRIORITY.get(worst_tk, 99)
                if priority >= worst_priority:
                    continue
                pos = active.pop(worst_tk)
                close_price = current_price
                pnl = _calc_pnl(pos['direction'], pos['entry_price'], close_price, pos['contracts'], worst_tk)
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

        # ── Score-based position sizing ──
        if use_score_sizing:
            score_mult = 0.5 + sig_score  # score 0.3 → ×0.8, 1.0 → ×1.5
            score_mult = min(score_mult, 1.5)
        else:
            score_mult = 1.0

        max_risk = total_cap * adaptive_margin * (weight / MAX_WEIGHT) * score_mult
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

        # ── ATR-adaptive stop-loss ──
        if stop_loss_pct > 0:
            if atr_stop_mult > 0 and sig.get('atr_pct', 0) > 0:
                # Use ATR-based stop (per-ticker, per-bar volatility)
                atr_pct = sig['atr_pct']
                dynamic_sl = min(max(atr_pct * atr_stop_mult, 0.003), stop_loss_pct)
            else:
                dynamic_sl = stop_loss_pct

            if direction == 'LONG':
                stop_price = entry_price * (1 - dynamic_sl)
                if exit_price < stop_price:
                    exit_price = stop_price
            else:
                stop_price = entry_price * (1 + dynamic_sl)
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
            'score': sig_score,
            'last_price': entry_price,
            'bars_held': 0,
        }
        _record_margin_usage()

    # Remaining positions at end of data: close at planned exit
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
