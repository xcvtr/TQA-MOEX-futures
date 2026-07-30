#!/usr/bin/env python3
"""
Paper Trader v6 — исполнение по стакану (DOM) через BrokerDOM.

Отличается от paper_trader.py:
  • manage_positions() закрывает через BrokerDOM (реальный стакан из PG)
  • entry_slippage из стакана вместо константы 2-5 tick
  • state-key по умолчанию portfolio_v6

Всё остальное идентично: loaded portfolio, сигналы, трейлинг, timeout.
"""
import os, sys, json, logging
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))

# Импортируем всё из оригинального paper_trader
from strategies.common.paper_trader import (
    STATE_KEY, PG_HOST, PG_PORT, PG_DB, PG_USER, PG_PASS,
    CH_HOST, CH_PORT, CH_DB, TRADE_COST, MAX_CONTRACTS,
    load_portfolio, load_specs, load_state, save_state,
    get_latest_bars, get_volume_data, calc_dcvd_z, calc_mtm_equity,
    _load_strategies, STRATEGY_MAP, log
)

from strategies.common.broker_dom import BrokerDOM

# Default state-key для v6
STATE_KEY = os.getenv('STATE_KEY', 'portfolio_v6')
DOM_BROKER = BrokerDOM(commission=4)


def manage_positions_v6(positions, bar_data, specs, bar_idx):
    """Управление позициями с закрытием через BrokerDOM."""
    closed = []
    for p in list(positions):
        if p.get('closed', False):
            continue
        ticker = p['ticker']
        bd = bar_data.get(ticker)
        if not bd:
            continue
        s = specs.get(ticker, {})
        sp, ms = s.get('sp', 1), s.get('ms', 0.01)
        lot = s.get('lot', 1)
        hi, lo = bd['hi'], bd['lo']
        if p['entry_bar'] >= bar_idx:
            continue

        contracts = p.get('contracts', 1)
        entry = p['entry_price']
        pct = p.get('pct', 1.0)
        fee = specs.get(ticker, {}).get('fee', TRADE_COST)

        # Timeout
        if bar_idx - p['entry_bar'] >= p.get('timeout_bars', 12):
            exit_price, pnl, slip = DOM_BROKER.exit_long(
                ticker, entry, contracts, ms, sp, pct, fee) \
                if p['direction'] == 'long' else \
                DOM_BROKER.exit_short(ticker, entry, contracts, ms, sp, pct, fee)
            pnl += p.get('part_pnl', 0)
            p['pnl'] = pnl
            p['exit_price'] = exit_price
            p['exit_reason'] = 'timeout'
            p['closed'] = True
            p['exit_bar'] = bar_idx
            p['slippage'] = slip
            closed.append(p)
            continue

        # Trailing TP (logic unchanged — same trigger conditions)
        exit_triggered = False
        exit_price = None
        reason = ''

        if p['direction'] == 'long':
            if not p.get('trailing_activated'):
                if hi >= entry * (1 + p.get('activation', 0.005)):
                    p['trailing_activated'] = True
                    p['trailing_level'] = hi * (1 - p.get('trail', 0.003))
            elif p['trailing_level'] and hi >= p['trailing_level'] / (1 - p.get('trail', 0.003)):
                p['trailing_level'] = hi * (1 - p.get('trail', 0.003))

            if p.get('trailing_activated') and lo <= p.get('trailing_level', 0):
                exit_price = p['trailing_level']
                reason = 'trailing_tp'
            elif lo <= entry * (1 - p.get('stop_loss', 0.007)):
                exit_price = lo
                reason = 'stop_loss'

        else:  # short
            if not p.get('trailing_activated'):
                if lo <= entry * (1 - p.get('activation', 0.005)):
                    p['trailing_activated'] = True
                    p['trailing_level'] = lo * (1 + p.get('trail', 0.003))
            elif p['trailing_level'] and lo <= p['trailing_level'] / (1 + p.get('trail', 0.003)):
                p['trailing_level'] = lo * (1 + p.get('trail', 0.003))

            if p.get('trailing_activated') and hi >= p.get('trailing_level', 0):
                exit_price = p['trailing_level']
                reason = 'trailing_tp'
            elif hi >= entry * (1 + p.get('stop_loss', 0.007)):
                exit_price = hi
                reason = 'stop_loss'

        if exit_price is not None:
            # Execute exit via DOM broker
            if p['direction'] == 'long':
                exit_px, pnl, slip = DOM_BROKER.exit_long(
                    ticker, entry, contracts, ms, sp, pct, fee)
            else:
                exit_px, pnl, slip = DOM_BROKER.exit_short(
                    ticker, entry, contracts, ms, sp, pct, fee)
            pnl += p.get('part_pnl', 0)
            p['pnl'] = pnl
            p['exit_price'] = exit_px
            p['exit_reason'] = reason
            p['closed'] = True
            p['exit_bar'] = bar_idx
            p['slippage'] = slip
            closed.append(p)

    return closed


def run_tick_v6(strategy_filter=None, mode=None):
    """Аналог run_tick из paper_trader, но с DOM-исполнением."""
    from strategies.common.paper_trader import (
        calc_dcvd_z, get_volume_data, get_latest_bars, calc_mtm_equity,
    )

    # Константы торговой сессии (дубликат из paper_trader для независимости)
    MARKET_OPEN_IRK = 15   # MOEX 10:00 MSK = 15:00 IRK
    MARKET_CLOSE_IRK = 0   # 23:45 MSK = следующие сутки 0:00 IRK

    _load_strategies()

    state = load_state()
    positions = state.get('positions', [])
    equity = state.get('equity', 200000.0)
    capital = state.get('capital', 200000.0)
    peak = state.get('peak', 200000.0)
    trades = state.get('trades', [])
    next_id = state.get('next_id', 1)

    portfolio = load_portfolio()
    if not portfolio:
        log.warning("Empty portfolio")
        return

    if strategy_filter:
        portfolio = {t: [s for s in strats if s.get('strategy') == strategy_filter]
                     for t, strats in portfolio.items()}
        portfolio = {t: s for t, s in portfolio.items() if s}
        if not portfolio:
            log.warning(f"No portfolio entries for strategy '{strategy_filter}'")
            return

    tickers = list(portfolio.keys())
    specs = load_specs(tickers)
    if not specs:
        log.warning("No specs loaded")
        return

    now = datetime.now(timezone.utc)
    irk_hour = now.hour + 8
    if irk_hour >= 24:
        irk_hour -= 24
    market_open = (irk_hour >= MARKET_OPEN_IRK or irk_hour < MARKET_CLOSE_IRK)

    bar_data = {}
    max_bar_idx = 0
    for ticker in tickers:
        s = specs.get(ticker)
        if not s:
            continue
        df = get_latest_bars(ticker, s['asset'])
        if df is None or df.empty:
            continue
        bar_idx = len(df)
        last = df.iloc[-1]
        second_last = df.iloc[-2] if len(df) >= 2 else last

        close_hist = [float(v) for v in df['prc'].iloc[:-1].values]
        vol, vol_b, vol_s = get_volume_data(s.get('asset', ticker))

        vol_age_hours = 0
        if vol:
            try:
                import clickhouse_connect as cc
                ch_tmp = cc.get_client(host=CH_HOST, port=CH_PORT, database=CH_DB)
                r = ch_tmp.query(
                    f"SELECT max(SYSTIME) FROM moex.tradestats_fo WHERE asset_code='{s.get('asset', ticker)}'"
                ).result_rows
                ch_tmp.close()
                if r and r[0][0]:
                    vol_age_hours = (
                        datetime.now(timezone.utc) - r[0][0].replace(tzinfo=timezone.utc)
                    ).total_seconds() / 3600
            except Exception:
                pass

        dcvd_z = 0.0 if vol_age_hours > 30 else (
            calc_dcvd_z(vol_b, vol_s) if vol_b else 0.0
        )

        vol_hist = vol[:-1] if len(vol) > 1 else []
        current_vol = vol[-1] if vol else 100

        bar_data[ticker] = {
            'bt': last['bt'],
            'opn': float(last['opn']),
            'hi': float(last['hi']),
            'lo': float(last['lo']),
            'prc': float(last['prc']),
            'prc_prev': float(second_last['prc']),
            'vol': current_vol,
            'dcvd_z': dcvd_z,
            'close_hist': close_hist,
            'vol_hist': vol_hist,
            'bars_list': [
                {'opn': float(r['opn']), 'hi': float(r['hi']),
                 'lo': float(r['lo']), 'prc': float(r['prc'])}
                for _, r in df.iterrows()
            ],
        }
        hi_hist = [float(v) for v in df['hi'].iloc[-21:-1].values]
        lo_hist = [float(v) for v in df['lo'].iloc[-21:-1].values]
        bar_data[ticker]['hi_hist'] = hi_hist
        bar_data[ticker]['lo_hist'] = lo_hist
        max_bar_idx = max(max_bar_idx, bar_idx)

    state['bar_idx'] = max_bar_idx

    # Manage positions via DOM
    if mode != 'detect':
        closed = manage_positions_v6(positions, bar_data, specs, max_bar_idx)
        for c in closed:
            c['exit_time'] = datetime.now(timezone.utc)
            c['saved'] = False
            c['id'] = next_id
            next_id += 1
            equity += c['pnl']
            trades.append(c)
            slip_info = f" (slippage={c.get('slippage', '?')}t)" if c.get('slippage') else ''
            log.info("Closed %s %s PnL=%.0f (%s)%s",
                     c['ticker'], c['direction'], c['pnl'],
                     c.get('exit_reason', ''), slip_info)

    # Check for new signals
    if mode != 'tick' and market_open:
        for ticker in tickers:
            bd = bar_data.get(ticker)
            if not bd:
                continue
            if any(not p.get('closed', False) and p.get('ticker') == ticker for p in positions):
                continue
            s = specs.get(ticker, {})
            ms = s.get('ms', 0.01)
            sp = s.get('sp', 1)
            lot = s.get('lot', 1)
            fee = s.get('fee', TRADE_COST)

            for entry in portfolio[ticker]:
                strategy_name = entry['strategy']
                fn = STRATEGY_MAP.get(strategy_name)
                if not fn:
                    continue
                try:
                    signal = fn(bd, ticker)
                except Exception as e:
                    log.warning("Signal error %s/%s: %s", ticker, strategy_name, e)
                    continue
                if not signal:
                    continue

                # Trend filter (SMA50)
                params = entry.get('params', {})
                if params.get('trend') and 'close_hist' in bd and len(bd.get('close_hist', [])) >= 50:
                    closes = bd['close_hist'][-50:]
                    sma50 = sum(closes) / 50
                    if signal['direction'] == 'long' and float(bd['prc']) < sma50:
                        continue
                    if signal['direction'] == 'short' and float(bd['prc']) > sma50:
                        continue

                contracts = entry.get('contracts') or 1

                # Volume cap
                b_vol = bd.get('vol', 0)
                if b_vol and b_vol > 0:
                    vc = 0.5 if strategy_name == 'impulse_return' else 0.2
                    contracts = min(contracts, max(1, int(b_vol * vc)))
                contracts = min(contracts, MAX_CONTRACTS)
                if s.get('go', 0) * contracts > equity:
                    contracts = max(1, int(equity * 0.1 / s.get('go', 1)))

                # DOM-based entry slippage (вместо константы)
                dom_slip = DOM_BROKER.entry_slippage(ticker, signal['direction'], contracts, ms)
                slip_total = ms * dom_slip
                entry_price = float(bd['prc']) + (
                    slip_total if signal['direction'] == 'long' else -slip_total
                )
                entry_price = round(entry_price / ms) * ms

                pos = {
                    'id': next_id,
                    'ticker': ticker,
                    'strategy': strategy_name,
                    'direction': signal['direction'],
                    'entry_price': entry_price,
                    'entry_time': datetime.now(timezone.utc),
                    'entry_bar': max_bar_idx,
                    'contracts': contracts,
                    'pnl': 0,
                    'closed': False,
                    'stop_loss': entry.get('stop_loss', 0.007),
                    'activation': entry.get('trailing_activation', 0.005),
                    'trail': entry.get('trailing_trail', 0.003),
                    'timeout_bars': entry.get('timeout_bars', 12),
                    'pct': s.get('pct', 1.0),
                    'rem': s.get('pct', 1.0),
                    'entry_slippage': dom_slip,
                }
                next_id += 1
                positions.append(pos)
                log.info("Opened %s %s %s (%s ct, slip=%dt)",
                         ticker, strategy_name, signal['direction'],
                         contracts, dom_slip)

    # MTM equity
    mtm_eq = calc_mtm_equity(capital, positions, bar_data, specs)
    peak = max(peak, equity)
    mtm_peak = max(peak, mtm_eq)

    state.update({
        'capital': capital,
        'equity': equity,
        'peak': peak,
        'mtm_equity': mtm_eq,
        'mtm_peak': mtm_peak,
        'positions': positions,
        'trades': trades,
        'bar_idx': max_bar_idx,
        'next_id': next_id,
    })
    save_state(state)


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--strategy', type=str, default=None)
    parser.add_argument('--state-key', type=str, default='portfolio_v6')
    parser.add_argument('--mode', type=str, default=None,
                        choices=[None, 'tick', 'detect'])
    args = parser.parse_args()

    # Assign state key
    import strategies.common.paper_trader as _pt
    _pt.STATE_KEY = args.state_key or 'portfolio_v6'
    STATE_KEY = args.state_key or 'portfolio_v6'
    run_tick_v6(args.strategy, args.mode)
