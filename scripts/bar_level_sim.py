#!/usr/bin/env python3
"""
Bar-level portfolio simulation with mark-to-market.
Processes signals chronologically grouped by bar time, uses OHLCV bars
for current pricing, trailing stops, time-stops, and drawdown protection.
"""

import json
import pickle
from itertools import product

import numpy as np
import pandas as pd

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

TICKER_TO_GROUP = {}
for group, members in CORRELATION_GROUPS.items():
    for sym in members:
        TICKER_TO_GROUP[sym] = group

PRIORITY_WEIGHTS = {
    (1, 5): 3.0,
    (6, 15): 1.5,
    (16, 999): 1.0,
}
MAX_WEIGHT = 3.0

TICKER_PRIORITY_WEIGHT = {}
for tk, prio in TICKER_PRIORITY.items():
    for (lo, hi), w in PRIORITY_WEIGHTS.items():
        if lo <= prio <= hi:
            TICKER_PRIORITY_WEIGHT[tk] = w
            break
    else:
        TICKER_PRIORITY_WEIGHT[tk] = 1.0


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


class BarLevelPortfolio:
    _signals_cache = None
    _ohlcv_np = None
    _grouped_cache = None

    @classmethod
    def _load_shared_data(cls):
        if cls._signals_cache is not None:
            return
        try:
            with open('.signals_cache.json') as f:
                cls._signals_cache = json.load(f)
            for s in cls._signals_cache:
                s['_time_dt'] = pd.Timestamp(s['time'])
            cls._signals_cache.sort(key=lambda x: x['_time_dt'])
        except (FileNotFoundError, json.JSONDecodeError):
            cls._signals_cache = []

        with open('.ohlcv_cache.pkl', 'rb') as f:
            raw = pickle.load(f)
        cls._ohlcv_np = {}
        for tk, df in raw.items():
            idx = df.index.values
            vals = df['close'].values.astype(np.float64)
            cls._ohlcv_np[tk] = (idx, vals)

    @classmethod
    def _pre_group_fold(cls, fold_signals):
        groups = {}
        for s in fold_signals:
            t = s['_time_dt']
            if t not in groups:
                groups[t] = []
            groups[t].append(s)
        sorted_times = sorted(groups.keys())
        return sorted_times, groups

    def __init__(self, initial_capital=100000, max_dd=0.20,
                 margin_usage=0.10, max_concurrent=5, total_margin_limit=0.15,
                 stop_loss_pct=0.01, use_score_sizing=True, use_score_eviction=True,
                 atr_stop_mult=2.0, use_score_decay=True, max_hold_bars=40,
                 use_mtm=True, use_trailing=True, trailing_mult=3.0,
                 allow_rollover=True):
        self.initial_capital = initial_capital
        self.max_dd = max_dd
        self.margin_usage = margin_usage
        self.max_concurrent = max_concurrent
        self.total_margin_limit = total_margin_limit
        self.stop_loss_pct = stop_loss_pct
        self.use_score_sizing = use_score_sizing
        self.use_score_eviction = use_score_eviction
        self.atr_stop_mult = atr_stop_mult
        self.use_score_decay = use_score_decay
        self.max_hold_bars = max_hold_bars
        self.use_mtm = use_mtm
        self.use_trailing = use_trailing
        self.trailing_mult = trailing_mult
        self.allow_rollover = allow_rollover
        self._load_shared_data()

    def get_all_signals(self):
        return self._signals_cache

    def _lookup_price(self, tk, ts):
        item = self._ohlcv_np.get(tk)
        if item is None:
            return None
        idx, vals = item
        ts64 = np.datetime64(ts.to_datetime64())
        pos = np.searchsorted(idx, ts64, side='right') - 1
        if pos >= 0:
            return float(vals[pos])
        return None

    def run(self, signals=None):
        if signals is None:
            signals = self._signals_cache

        if not signals:
            return {
                'final_capital': self.initial_capital,
                'equity_curve': [self.initial_capital],
                'trades': [],
                'total_return_pct': 0.0,
                'max_dd_pct': 0.0,
                'calmar': 0.0,
            }

        sorted_times, time_groups = self._pre_group_fold(signals)

        capital = float(self.initial_capital)
        active = {}
        trades = []
        equity_curve = []
        peak = self.initial_capital
        consecutive_wins = {}

        use_trail = self.use_trailing
        use_mtm = self.use_mtm
        use_decay = self.use_score_decay
        use_sizing = self.use_score_sizing
        use_evict = self.use_score_eviction
        max_dd = self.max_dd
        max_hold = self.max_hold_bars
        sl_pct = self.stop_loss_pct
        atr_mult = self.atr_stop_mult
        trail_mult = self.trailing_mult
        margin_usage = self.margin_usage
        max_conc = self.max_concurrent
        total_margin_limit = self.total_margin_limit

        lookup = self._lookup_price

        for current_time in sorted_times:
            sigs_at_time = time_groups[current_time]

            for tk in list(active.keys()):
                pos = active[tk]
                cp = lookup(tk, current_time)
                if cp is None:
                    cp = pos.get('current_price', pos['entry_price'])
                pos['current_price'] = cp

                pos['bars_held'] = pos.get('bars_held', 0) + 1

                if use_trail:
                    if pos['direction'].upper() == 'LONG':
                        pos['highest'] = max(pos.get('highest', pos['entry_price']), cp)
                    else:
                        pos['lowest'] = min(pos.get('lowest', pos['entry_price']), cp)

                should_exit = False
                exit_reason = None

                # No trailing stop — exit_price is the real target (OHLCV close at horizon)
                if not should_exit and sl_pct > 0:
                    if pos['direction'].upper() == 'LONG':
                        if cp <= pos['entry_price'] * (1 - sl_pct):
                            should_exit = True
                            exit_reason = 'stop_loss'
                    else:
                        if cp >= pos['entry_price'] * (1 + sl_pct):
                            should_exit = True
                            exit_reason = 'stop_loss'

                if not should_exit and atr_mult > 0 and pos.get('atr_pct', 0) > 0:
                    atr_pct = pos['atr_pct']
                    dynamic_sl = min(max(atr_pct * atr_mult, 0.003), sl_pct if sl_pct > 0 else 0.02)
                    if pos['direction'].upper() == 'LONG':
                        if cp <= pos['entry_price'] * (1 - dynamic_sl):
                            should_exit = True
                            exit_reason = 'atr_stop'
                    else:
                        if cp >= pos['entry_price'] * (1 + dynamic_sl):
                            should_exit = True
                            exit_reason = 'atr_stop'

                if not should_exit and max_hold > 0:
                    pos_score = pos.get('score', 0.3)
                    hold_limit = int(max_hold * (0.5 + pos_score))
                    hold_limit = max(10, min(hold_limit, 80))
                    adx_val = pos.get('adx_value', 0)
                    if adx_val > 25:
                        hold_limit = int(hold_limit * 1.5)
                    elif adx_val > 0 and adx_val < 15:
                        hold_limit = int(hold_limit * 0.7)
                    hold_limit = max(hold_limit, 1)
                    if pos['bars_held'] >= hold_limit:
                        should_exit = True
                        exit_reason = 'time_stop'

                if should_exit:
                    pos_data = active.pop(tk)
                    pnl = _calc_pnl(pos_data['direction'], pos_data['entry_price'], cp, pos_data['contracts'], tk)
                    capital += pos_data['locked_go'] + pnl
                    if use_decay:
                        if pnl > 0:
                            consecutive_wins[tk] = consecutive_wins.get(tk, 0) + 1
                        else:
                            consecutive_wins[tk] = 0
                    trades.append({
                        'ticker': tk, 'pnl': pnl,
                        'entry_time': str(pos_data.get('entry_time', '')),
                        'exit_time': str(current_time),
                        'direction': pos_data['direction'],
                        'contracts': pos_data['contracts'],
                        'exit_reason': exit_reason,
                    })

            current_equity = self._calc_equity(capital, active, use_mtm)
            if current_equity > peak:
                peak = current_equity
            dd = (peak - current_equity) / peak if peak > 0 else 0
            if dd > max_dd:
                for tk in list(active.keys()):
                    pos_data = active.pop(tk)
                    cp = pos_data.get('current_price', pos_data['entry_price'])
                    pnl = _calc_pnl(pos_data['direction'], pos_data['entry_price'], cp, pos_data['contracts'], tk)
                    capital += pos_data['locked_go'] + pnl
                    trades.append({
                        'ticker': tk, 'pnl': pnl,
                        'entry_time': str(pos_data.get('entry_time', '')),
                        'exit_time': str(current_time),
                        'direction': pos_data['direction'],
                        'contracts': pos_data['contracts'],
                        'exit_reason': 'max_dd',
                    })
                equity_curve.append(self._calc_equity(capital, active, use_mtm))
                break

            for sig in sigs_at_time:
                tk = sig.get('ticker', '')
                if not tk or tk not in TICKER_CONFIGS:
                    continue

                sig_score = sig.get('score', 0.3)

                current_price = lookup(tk, current_time)
                if current_price is None:
                    current_price = sig.get('entry', 0)

                if tk in active:
                    if not self.allow_rollover:
                        continue  # skip signal, keep existing position open
                    pos_data = active.pop(tk)
                    # Rollover at signal entry price (limit fill), not OHLCV close
                    roll_price = sig.get('entry', current_price)
                    pnl = _calc_pnl(pos_data['direction'], pos_data['entry_price'], roll_price, pos_data['contracts'], tk)
                    capital += pos_data['locked_go'] + pnl
                    if use_decay:
                        if pnl > 0:
                            consecutive_wins[tk] = consecutive_wins.get(tk, 0) + 1
                        else:
                            consecutive_wins[tk] = 0
                    trades.append({
                        'ticker': tk, 'pnl': pnl,
                        'entry_time': str(pos_data.get('entry_time', '')),
                        'exit_time': str(current_time),
                        'direction': pos_data['direction'],
                        'contracts': pos_data['contracts'],
                        'exit_reason': 'rollover',
                    })

                group = TICKER_TO_GROUP.get(tk, 'misc')
                priority = TICKER_PRIORITY.get(tk, 99)
                cap_limit = SECTOR_CAP.get(group, 1)
                if sum(1 for p in active.values() if p.get('group') == group) >= cap_limit:
                    continue

                if len(active) >= max_conc:
                    if use_evict:
                        worst_tk = min(active, key=lambda t: active[t].get('score', 0.0) / (1 + TICKER_PRIORITY.get(t, 99) / 10))
                        worst_val = active[worst_tk].get('score', 0.0) / (1 + TICKER_PRIORITY.get(worst_tk, 99) / 10)
                        new_val = sig_score / (1 + priority / 10)
                        if new_val <= worst_val:
                            continue
                        pos_data = active.pop(worst_tk)
                        cp_worst = pos_data.get('current_price', pos_data['entry_price'])
                        pnl = _calc_pnl(pos_data['direction'], pos_data['entry_price'], cp_worst, pos_data['contracts'], worst_tk)
                        capital += pos_data['locked_go'] + pnl
                        if use_decay:
                            if pnl > 0:
                                consecutive_wins[worst_tk] = consecutive_wins.get(worst_tk, 0) + 1
                            else:
                                consecutive_wins[worst_tk] = 0
                        trades.append({
                            'ticker': worst_tk, 'pnl': pnl,
                            'entry_time': str(pos_data.get('entry_time', '')),
                            'exit_time': str(current_time),
                            'direction': pos_data['direction'],
                            'contracts': pos_data['contracts'],
                            'exit_reason': 'eviction',
                        })
                    else:
                        worst_tk = min(active, key=lambda t: TICKER_PRIORITY.get(t, 99))
                        worst_priority = TICKER_PRIORITY.get(worst_tk, 99)
                        if priority >= worst_priority:
                            continue
                        pos_data = active.pop(worst_tk)
                        cp_worst = pos_data.get('current_price', pos_data['entry_price'])
                        pnl = _calc_pnl(pos_data['direction'], pos_data['entry_price'], cp_worst, pos_data['contracts'], worst_tk)
                        capital += pos_data['locked_go'] + pnl
                        trades.append({
                            'ticker': worst_tk, 'pnl': pnl,
                            'entry_time': str(pos_data.get('entry_time', '')),
                            'exit_time': str(current_time),
                            'direction': pos_data['direction'],
                            'contracts': pos_data['contracts'],
                            'exit_reason': 'eviction',
                        })

                cfg = TICKER_CONFIGS.get(tk)
                if not cfg:
                    continue
                go = cfg.get('go', 0)
                if go <= 0:
                    continue

                weight = TICKER_PRIORITY_WEIGHT.get(tk, 1.0)
                total_cap = self._calc_equity(capital, active, use_mtm)

                if use_sizing:
                    score_mult = min(0.5 + sig_score, 1.5)
                else:
                    score_mult = 1.0

                max_risk = total_cap * margin_usage * (weight / MAX_WEIGHT) * score_mult
                contracts = int(max_risk // go) if max_risk >= go else 0
                if contracts < 1:
                    continue
                locked_go = contracts * go

                if sum(p['locked_go'] for p in active.values()) + locked_go > total_cap * total_margin_limit:
                    continue

                if locked_go > capital:
                    continue

                entry_price = sig.get('entry', current_price)
                direction = sig.get('direction', 'LONG')

                capital -= locked_go

                active[tk] = {
                    'entry_price': entry_price,
                    'direction': direction,
                    'contracts': contracts,
                    'entry_time': sig.get('time', ''),
                    'locked_go': locked_go,
                    'group': group,
                    'score': sig_score,
                    'current_price': entry_price,
                    'bars_held': 0,
                    'highest': entry_price,
                    'lowest': entry_price,
                    'atr_pct': sig.get('atr_pct', 0),
                    'adx_value': sig.get('adx_value', 0),
                }

            equity_curve.append(self._calc_equity(capital, active, use_mtm))

        for tk in list(active.keys()):
            pos_data = active.pop(tk)
            cp = pos_data.get('current_price', pos_data['entry_price'])
            pnl = _calc_pnl(pos_data['direction'], pos_data['entry_price'], cp, pos_data['contracts'], tk)
            capital += pos_data['locked_go'] + pnl
            trades.append({
                'ticker': tk, 'pnl': pnl,
                'entry_time': str(pos_data.get('entry_time', '')),
                'exit_time': 'end',
                'direction': pos_data['direction'],
                'contracts': pos_data['contracts'],
                'exit_reason': 'end_of_data',
            })
            equity_curve.append(self._calc_equity(capital, active, use_mtm))

        final_capital = self._calc_equity(capital, active, use_mtm)
        total_return_pct = ((final_capital / self.initial_capital) - 1) * 100
        mdd = _max_drawdown(equity_curve)
        calmar = total_return_pct / (mdd * 100) if mdd > 0 else 0.0

        return {
            'final_capital': round(final_capital, 2),
            'equity_curve': equity_curve,
            'trades': trades,
            'total_return_pct': round(total_return_pct, 4),
            'max_dd_pct': round(mdd * 100, 4),
            'calmar': round(calmar, 4),
        }

    def _calc_equity(self, capital, active, use_mtm):
        base = capital + sum(p['locked_go'] for p in active.values())
        if use_mtm:
            mtm = 0.0
            for tk, p in active.items():
                cp = p.get('current_price', p['entry_price'])
                mtm += _calc_pnl(p['direction'], p['entry_price'], cp, p['contracts'], tk)
            return base + mtm
        return base

    def run_stability_check(self):
        signals = self._signals_cache
        n = len(signals)
        n4 = n // 4
        fold_sigs = [signals[:n4], signals[n4:2*n4], signals[2*n4:3*n4], signals[3*n4:]]

        fold_groups = [self._pre_group_fold(fs) for fs in fold_sigs]

        mu_values = [0.10, 0.15, 0.20]
        mc_values = [2, 3, 5, 8]
        tm_values = [0.15, 0.20, 0.30]
        sl_values = [0.01, 0.02]

        all_profitable = []
        not_profitable = []

        for mu, mc, tm, sl in product(mu_values, mc_values, tm_values, sl_values):
            self.max_dd = mu
            self.max_concurrent = mc
            self.total_margin_limit = tm
            self.stop_loss_pct = sl

            fold_returns = []
            for sorted_times, time_groups in fold_groups:
                result = self._run_grouped(sorted_times, time_groups)
                fold_returns.append(result['total_return_pct'])

            profitable_all = all(r > 0 for r in fold_returns)
            entry = {
                'params': {'max_dd': mu, 'max_concurrent': mc,
                           'total_margin_limit': tm, 'stop_loss_pct': sl},
                'fold_returns': [round(r, 4) for r in fold_returns],
                'profitable_all': profitable_all,
            }
            if profitable_all:
                all_profitable.append(entry)
            else:
                not_profitable.append(entry)

        return {
            'profitable_in_all_folds': all_profitable,
            'not_profitable_in_all_folds': not_profitable,
        }

    def _run_grouped(self, sorted_times, time_groups):
        capital = float(self.initial_capital)
        active = {}
        trades = []
        equity_curve = []
        peak = self.initial_capital
        consecutive_wins = {}

        use_trail = self.use_trailing
        use_mtm = self.use_mtm
        use_decay = self.use_score_decay
        use_sizing = self.use_score_sizing
        use_evict = self.use_score_eviction
        max_dd = self.max_dd
        max_hold = self.max_hold_bars
        sl_pct = self.stop_loss_pct
        atr_mult = self.atr_stop_mult
        trail_mult = self.trailing_mult
        margin_usage = self.margin_usage
        max_conc = self.max_concurrent
        total_margin_limit = self.total_margin_limit

        lookup = self._lookup_price

        for current_time in sorted_times:
            sigs_at_time = time_groups[current_time]

            for tk in list(active.keys()):
                pos = active[tk]
                cp = lookup(tk, current_time)
                if cp is None:
                    cp = pos.get('current_price', pos['entry_price'])
                pos['current_price'] = cp
                pos['bars_held'] = pos.get('bars_held', 0) + 1

                if use_trail:
                    if pos['direction'].upper() == 'LONG':
                        pos['highest'] = max(pos.get('highest', pos['entry_price']), cp)
                    else:
                        pos['lowest'] = min(pos.get('lowest', pos['entry_price']), cp)

                should_exit = False
                exit_reason = None

                # No trailing stop — exit_price is the real close target
                if not should_exit and sl_pct > 0:
                    if pos['direction'].upper() == 'LONG':
                        if cp <= pos['entry_price'] * (1 - sl_pct):
                            should_exit = True
                            exit_reason = 'stop_loss'
                    else:
                        if cp >= pos['entry_price'] * (1 + sl_pct):
                            should_exit = True
                            exit_reason = 'stop_loss'

                if not should_exit and atr_mult > 0 and pos.get('atr_pct', 0) > 0:
                    atr_pct = pos['atr_pct']
                    dynamic_sl = min(max(atr_pct * atr_mult, 0.003), sl_pct if sl_pct > 0 else 0.02)
                    if pos['direction'].upper() == 'LONG':
                        if cp <= pos['entry_price'] * (1 - dynamic_sl):
                            should_exit = True
                            exit_reason = 'atr_stop'
                    else:
                        if cp >= pos['entry_price'] * (1 + dynamic_sl):
                            should_exit = True
                            exit_reason = 'atr_stop'

                if not should_exit and max_hold > 0:
                    pos_score = pos.get('score', 0.3)
                    hold_limit = int(max_hold * (0.5 + pos_score))
                    hold_limit = max(10, min(hold_limit, 80))
                    adx_val = pos.get('adx_value', 0)
                    if adx_val > 25:
                        hold_limit = int(hold_limit * 1.5)
                    elif adx_val > 0 and adx_val < 15:
                        hold_limit = int(hold_limit * 0.7)
                    hold_limit = max(hold_limit, 1)
                    if pos['bars_held'] >= hold_limit:
                        should_exit = True
                        exit_reason = 'time_stop'

                if should_exit:
                    pos_data = active.pop(tk)
                    pnl = _calc_pnl(pos_data['direction'], pos_data['entry_price'], cp, pos_data['contracts'], tk)
                    capital += pos_data['locked_go'] + pnl
                    if use_decay:
                        if pnl > 0:
                            consecutive_wins[tk] = consecutive_wins.get(tk, 0) + 1
                        else:
                            consecutive_wins[tk] = 0
                    trades.append({
                        'ticker': tk, 'pnl': pnl,
                        'entry_time': str(pos_data.get('entry_time', '')),
                        'exit_time': str(current_time),
                        'direction': pos_data['direction'],
                        'contracts': pos_data['contracts'],
                        'exit_reason': exit_reason,
                    })

            current_equity = self._calc_equity(capital, active, use_mtm)
            if current_equity > peak:
                peak = current_equity
            dd = (peak - current_equity) / peak if peak > 0 else 0
            if dd > max_dd:
                for tk in list(active.keys()):
                    pos_data = active.pop(tk)
                    cp = pos_data.get('current_price', pos_data['entry_price'])
                    pnl = _calc_pnl(pos_data['direction'], pos_data['entry_price'], cp, pos_data['contracts'], tk)
                    capital += pos_data['locked_go'] + pnl
                    trades.append({
                        'ticker': tk, 'pnl': pnl,
                        'entry_time': str(pos_data.get('entry_time', '')),
                        'exit_time': str(current_time),
                        'direction': pos_data['direction'],
                        'contracts': pos_data['contracts'],
                        'exit_reason': 'max_dd',
                    })
                equity_curve.append(self._calc_equity(capital, active, use_mtm))
                break

            for sig in sigs_at_time:
                tk = sig.get('ticker', '')
                if not tk or tk not in TICKER_CONFIGS:
                    continue

                sig_score = sig.get('score', 0.3)
                current_price = lookup(tk, current_time)
                if current_price is None:
                    current_price = sig.get('entry', 0)

                if tk in active:
                    if not self.allow_rollover:
                        continue  # skip signal, keep existing position open
                    pos_data = active.pop(tk)
                    # Rollover at signal entry price (limit fill), not OHLCV close
                    roll_price = sig.get('entry', current_price)
                    pnl = _calc_pnl(pos_data['direction'], pos_data['entry_price'], roll_price, pos_data['contracts'], tk)
                    capital += pos_data['locked_go'] + pnl
                    if use_decay:
                        if pnl > 0:
                            consecutive_wins[tk] = consecutive_wins.get(tk, 0) + 1
                        else:
                            consecutive_wins[tk] = 0
                    trades.append({
                        'ticker': tk, 'pnl': pnl,
                        'entry_time': str(pos_data.get('entry_time', '')),
                        'exit_time': str(current_time),
                        'direction': pos_data['direction'],
                        'contracts': pos_data['contracts'],
                        'exit_reason': 'rollover',
                    })

                group = TICKER_TO_GROUP.get(tk, 'misc')
                priority = TICKER_PRIORITY.get(tk, 99)
                cap_limit = SECTOR_CAP.get(group, 1)
                if sum(1 for p in active.values() if p.get('group') == group) >= cap_limit:
                    continue

                if len(active) >= max_conc:
                    if use_evict:
                        worst_tk = min(active, key=lambda t: active[t].get('score', 0.0) / (1 + TICKER_PRIORITY.get(t, 99) / 10))
                        worst_val = active[worst_tk].get('score', 0.0) / (1 + TICKER_PRIORITY.get(worst_tk, 99) / 10)
                        new_val = sig_score / (1 + priority / 10)
                        if new_val <= worst_val:
                            continue
                        pos_data = active.pop(worst_tk)
                        cp_worst = pos_data.get('current_price', pos_data['entry_price'])
                        pnl = _calc_pnl(pos_data['direction'], pos_data['entry_price'], cp_worst, pos_data['contracts'], worst_tk)
                        capital += pos_data['locked_go'] + pnl
                        if use_decay:
                            if pnl > 0:
                                consecutive_wins[worst_tk] = consecutive_wins.get(worst_tk, 0) + 1
                            else:
                                consecutive_wins[worst_tk] = 0
                        trades.append({
                            'ticker': worst_tk, 'pnl': pnl,
                            'entry_time': str(pos_data.get('entry_time', '')),
                            'exit_time': str(current_time),
                            'direction': pos_data['direction'],
                            'contracts': pos_data['contracts'],
                            'exit_reason': 'eviction',
                        })
                    else:
                        worst_tk = min(active, key=lambda t: TICKER_PRIORITY.get(t, 99))
                        worst_priority = TICKER_PRIORITY.get(worst_tk, 99)
                        if priority >= worst_priority:
                            continue
                        pos_data = active.pop(worst_tk)
                        cp_worst = pos_data.get('current_price', pos_data['entry_price'])
                        pnl = _calc_pnl(pos_data['direction'], pos_data['entry_price'], cp_worst, pos_data['contracts'], worst_tk)
                        capital += pos_data['locked_go'] + pnl
                        trades.append({
                            'ticker': worst_tk, 'pnl': pnl,
                            'entry_time': str(pos_data.get('entry_time', '')),
                            'exit_time': str(current_time),
                            'direction': pos_data['direction'],
                            'contracts': pos_data['contracts'],
                            'exit_reason': 'eviction',
                        })

                cfg = TICKER_CONFIGS.get(tk)
                if not cfg:
                    continue
                go = cfg.get('go', 0)
                if go <= 0:
                    continue

                weight = TICKER_PRIORITY_WEIGHT.get(tk, 1.0)
                total_cap = self._calc_equity(capital, active, use_mtm)

                score_mult = min(0.5 + sig_score, 1.5) if use_sizing else 1.0
                max_risk = total_cap * margin_usage * (weight / MAX_WEIGHT) * score_mult
                contracts = int(max_risk // go) if max_risk >= go else 0
                if contracts < 1:
                    continue
                locked_go = contracts * go

                if sum(p['locked_go'] for p in active.values()) + locked_go > total_cap * total_margin_limit:
                    continue
                if locked_go > capital:
                    continue

                entry_price = sig.get('entry', current_price)
                direction = sig.get('direction', 'LONG')
                capital -= locked_go

                active[tk] = {
                    'entry_price': entry_price,
                    'direction': direction,
                    'contracts': contracts,
                    'entry_time': sig.get('time', ''),
                    'locked_go': locked_go,
                    'group': group,
                    'score': sig_score,
                    'current_price': entry_price,
                    'bars_held': 0,
                    'highest': entry_price,
                    'lowest': entry_price,
                    'atr_pct': sig.get('atr_pct', 0),
                    'adx_value': sig.get('adx_value', 0),
                }

            equity_curve.append(self._calc_equity(capital, active, use_mtm))

        for tk in list(active.keys()):
            pos_data = active.pop(tk)
            cp = pos_data.get('current_price', pos_data['entry_price'])
            pnl = _calc_pnl(pos_data['direction'], pos_data['entry_price'], cp, pos_data['contracts'], tk)
            capital += pos_data['locked_go'] + pnl
            trades.append({
                'ticker': tk, 'pnl': pnl,
                'entry_time': str(pos_data.get('entry_time', '')),
                'exit_time': 'end',
                'direction': pos_data['direction'],
                'contracts': pos_data['contracts'],
                'exit_reason': 'end_of_data',
            })
            equity_curve.append(self._calc_equity(capital, active, use_mtm))

        final_capital = self._calc_equity(capital, active, use_mtm)
        total_return_pct = ((final_capital / self.initial_capital) - 1) * 100
        mdd = _max_drawdown(equity_curve)
        calmar = total_return_pct / (mdd * 100) if mdd > 0 else 0.0

        return {
            'final_capital': round(final_capital, 2),
            'equity_curve': equity_curve,
            'trades': trades,
            'total_return_pct': round(total_return_pct, 4),
            'max_dd_pct': round(mdd * 100, 4),
            'calmar': round(calmar, 4),
        }


if __name__ == '__main__':
    p = BarLevelPortfolio()
    result = p.run()
    print(f"Final capital: {result['final_capital']:.2f}")
    print(f"Total return: {result['total_return_pct']:.2f}%")
    print(f"Max DD: {result['max_dd_pct']:.2f}%")
    print(f"Calmar: {result['calmar']:.4f}")
    print(f"Trades: {len(result['trades'])}")
    print(f"Equity curve points: {len(result['equity_curve'])}")
