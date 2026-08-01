#!/usr/bin/env python3 -u
"""Portfolio: IR Si 1m (4%) + Dragon MM 5m (5%) + Dragon GZ 3m (7%). Common pool."""
import sys, os, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '/home/user/projects/TQA-MOEX-futures')
import numpy as np, clickhouse_connect as cc
from statistics import median
from strategies.impulse_return.prod.engine import check_signal as ir_check, reset_state
from strategies.dragon.prod.engine import check_signal as dragon_check
from strategies.stop_hunt.prod.engine import check_signal as sh_check

COMMISSION = 4  # fallback
CAPITAL = 200000

# Per-ticker fees from PG futures.ticker_specs (fee_entry = BUYSELLFEE taker)
# Round-trip = fee_entry * 2. Maker (limit) SCALPERFEE ≈ fee_entry * 0.5 → roundtrip = fee_entry
FEES = {
    'Si': {'tc': 4, 'maker': 2},    # entry=3.81, scalper≈2, roundtrip_maker≈4
    'GD': {'tc': 44, 'maker': 22},  # entry=44.28, scalper≈22, roundtrip_maker≈44  
    'MM': {'tc': 2, 'maker': 1},    # entry=1.51
    'RN': {'tc': 7, 'maker': 4},    # entry=7.22
    'NG': {'tc': 4, 'maker': 2},    # entry=4.0
    'SNGP': {'tc': 4, 'maker': 2},   # entry=4.0
    'GAZR': {'tc': 2, 'maker': 1},   # entry=1.96
    'BR': {'tc': 4, 'maker': 2},   # entry=4.0
    'LKOH': {'tc': 4, 'maker': 2},   # entry=4.0
    'TATN': {'tc': 4, 'maker': 2},   # entry=4.0
    'ROSN': {'tc': 7, 'maker': 4},   # entry=7.22
    'TATN': {'tc': 4, 'maker': 2},   # entry=4.0
    'MTSI': {'tc': 4, 'maker': 2},   # entry=4.0
    'HANG': {'tc': 4, 'maker': 2},   # entry=4.0
    'RTKM': {'tc': 4, 'maker': 2},   # entry=4.0
    'SNGR': {'tc': 4, 'maker': 2},   # entry=4.0
    'HYDR': {'tc': 4, 'maker': 2},   # entry=4.0
    'HANG': {'tc': 4, 'maker': 2},   # entry=4.0
    'RTKM': {'tc': 4, 'maker': 2},   # entry=4.0
    'SNGR': {'tc': 4, 'maker': 2},   # entry=4.0
    'HYDR': {'tc': 4, 'maker': 2},   # entry=4.0
    'MTSI': {'tc': 4, 'maker': 2},   # entry=4.0
    'SNGR': {'tc': 4, 'maker': 2},   # entry=4.0
    'SBPR': {'tc': 4, 'maker': 2},   # entry=4.0
    'VTBR': {'tc': 4, 'maker': 2},   # entry=4.0
}
ENTRY_MODE = 'limit'  # 'market' or 'limit'

# Портфель: Retest (BR/GD/Si) + Dragon (GD/NG/BR) + IR (9 тикеров)
CONFIGS = [
    # ── RETEST (возврат к проторговке) ──
    {'ticker': 'BR', 'strat': 'retest', 'tf': 60, 'risk': 0.35,
     'ta': 0.0, 'tt': 0.0, 'sl': 0.0, 'to': 48,
     'ms': 0.01, 'sp': 7.70611, 'go': 8620,
     'params': {'win_size': 24, 'touches': 2, 'min_n_pct': 0.5,
                'width_min': 0.5, 'width_max': 8.0, 'stop_bars': 6, 'limit_frac': 0.15},
     'filters': {}},
    {'ticker': 'GD', 'strat': 'retest', 'tf': 60, 'risk': 0.35,
     'ta': 0.0, 'tt': 0.0, 'sl': 0.0, 'to': 48,
     'ms': 0.1, 'sp': 7.84756, 'go': 54380,
     'params': {'win_size': 24, 'touches': 2, 'min_n_pct': 0.5,
                'width_min': 0.5, 'width_max': 8.0, 'stop_bars': 6, 'limit_frac': 0.15},
     'filters': {}},
    {'ticker': 'Si', 'strat': 'retest', 'tf': 60, 'risk': 0.35,
     'ta': 0.0, 'tt': 0.0, 'sl': 0.0, 'to': 48,
     'ms': 1.0, 'sp': 1.0, 'go': 12654,
     'params': {'win_size': 24, 'touches': 2, 'min_n_pct': 0.5,
                'width_min': 0.5, 'width_max': 8.0, 'stop_bars': 6, 'limit_frac': 0.15},
     'filters': {}},

    # ── DRAGON ──
    {'ticker': 'GD', 'strat': 'dragon', 'tf': 10, 'risk': 0.40,
     'ta': 0.015, 'tt': 0.005, 'sl': 0.01, 'to': 60,
     'ms': 0.1, 'sp': 7.84756, 'go': 54380,
     'params': {'impulse_pct': 0.3, 'retrace_max_pct': 70, 'hump_extension': 0.1, 'lookback': 100},
     'filters': {'trend': True, 'min_vol_ratio': 0.8}},
    {'ticker': 'NG', 'strat': 'dragon', 'tf': 3, 'risk': 0.40,
     'ta': 0.015, 'tt': 0.005, 'sl': 0.01, 'to': 60,
     'ms': 0.001, 'sp': 7.70611, 'go': 11974,
     'params': {'impulse_pct': 0.5, 'retrace_max_pct': 70, 'hump_extension': 0.2, 'lookback': 100},
     'filters': {'trend': True}},
    {'ticker': 'BR', 'strat': 'dragon', 'tf': 10, 'risk': 0.40,
     'ta': 0.015, 'tt': 0.005, 'sl': 0.01, 'to': 60,
     'ms': 0.01, 'sp': 7.70611, 'go': 8620,
     'params': {'impulse_pct': 0.7, 'retrace_max_pct': 70, 'hump_extension': 0.2, 'lookback': 100},
     'filters': {'trend': True}},

    # ── IR (проверенные с cooldown) ──
    {'ticker': 'ROSN', 'strat': 'ir', 'tf': 15, 'risk': 0.25,
     'ta': 0.005, 'tt': 0.003, 'sl': 0.007, 'to': 12,
     'ms': 1.0, 'sp': 1.0, 'go': 13673,
     'params': {'impulse_bars': 6, 'impulse_pct': 1.5, 'retrace': 0.5, 'cooldown': 6, 'min_vol_pct': 0},
     'filters': {'trend': True}},
    {'ticker': 'SNGR', 'strat': 'ir', 'tf': 5, 'risk': 0.25,
     'ta': 0.005, 'tt': 0.003, 'sl': 0.007, 'to': 12,
     'ms': 1.0, 'sp': 1.0, 'go': 4952,
     'params': {'impulse_bars': 12, 'impulse_pct': 2.0, 'retrace': 0.5, 'cooldown': 6, 'min_vol_pct': 0},
     'filters': {'trend': True}},
    {'ticker': 'RTKM', 'strat': 'ir', 'tf': 5, 'risk': 0.25,
     'ta': 0.005, 'tt': 0.003, 'sl': 0.007, 'to': 12,
     'ms': 1.0, 'sp': 1.0, 'go': 2000,
     'params': {'impulse_bars': 6, 'impulse_pct': 2.0, 'retrace': 0.7, 'cooldown': 6, 'min_vol_pct': 0},
     'filters': {'trend': True}},
    {'ticker': 'SNGP', 'strat': 'ir', 'tf': 5, 'risk': 0.25,
     'ta': 0.005, 'tt': 0.003, 'sl': 0.007, 'to': 12,
     'ms': 1.0, 'sp': 1.0, 'go': 6000,
     'params': {'impulse_bars': 3, 'impulse_pct': 2.0, 'retrace': 0.7, 'cooldown': 6, 'min_vol_pct': 0},
     'filters': {'trend': True}},
    {'ticker': 'LKOH', 'strat': 'ir', 'tf': 5, 'risk': 0.25,
     'ta': 0.005, 'tt': 0.003, 'sl': 0.007, 'to': 12,
     'ms': 1.0, 'sp': 1.0, 'go': 22913,
     'params': {'impulse_bars': 6, 'impulse_pct': 2.0, 'retrace': 0.7, 'cooldown': 6, 'min_vol_pct': 0},
     'filters': {'trend': True}},
    {'ticker': 'TATN', 'strat': 'ir', 'tf': 5, 'risk': 0.25,
     'ta': 0.005, 'tt': 0.003, 'sl': 0.007, 'to': 12,
     'ms': 1.0, 'sp': 1.0, 'go': 5000,
     'params': {'impulse_bars': 6, 'impulse_pct': 2.0, 'retrace': 0.7, 'cooldown': 6, 'min_vol_pct': 0},
     'filters': {'trend': True}},
    {'ticker': 'MTSI', 'strat': 'ir', 'tf': 5, 'risk': 0.25,
     'ta': 0.005, 'tt': 0.003, 'sl': 0.007, 'to': 12,
     'ms': 1.0, 'sp': 1.0, 'go': 2000,
     'params': {'impulse_bars': 6, 'impulse_pct': 2.0, 'retrace': 0.7, 'cooldown': 6, 'min_vol_pct': 0},
     'filters': {'trend': True}},
    {'ticker': 'HANG', 'strat': 'ir', 'tf': 5, 'risk': 0.25,
     'ta': 0.005, 'tt': 0.003, 'sl': 0.007, 'to': 12,
     'ms': 1.0, 'sp': 1.0, 'go': 2000,
     'params': {'impulse_bars': 3, 'impulse_pct': 2.0, 'retrace': 0.7, 'cooldown': 6, 'min_vol_pct': 0},
     'filters': {'trend': True}},
    {'ticker': 'HYDR', 'strat': 'ir', 'tf': 5, 'risk': 0.25,
     'ta': 0.005, 'tt': 0.003, 'sl': 0.007, 'to': 12,
     'ms': 1.0, 'sp': 1.0, 'go': 932,
     'params': {'impulse_bars': 6, 'impulse_pct': 2.0, 'retrace': 0.7, 'cooldown': 6, 'min_vol_pct': 0},
     'filters': {'trend': True}},
]

ch = cc.get_client(host='10.0.0.60', port=8123, database='moex')
all_m1 = {}
for cfg in CONFIGS:
    t = cfg['ticker']
    rows = ch.query("SELECT bt,opn,hi,lo,prc,vol FROM moex.mt5_continuous WHERE ticker='" + t + "' AND bt>='2024-01-01' ORDER BY bt").result_rows
    bars = []
    for r in rows:
        ts = r[0]; h, m = ts.hour, ts.minute
        if ts.weekday() >= 5: continue
        if h < 15 or h > 23 or (h == 23 and m > 45): continue
        bars.append({'ts': ts, 'opn': float(r[1]), 'hi': float(r[2]), 'lo': float(r[3]), 'prc': float(r[4]), 'vol': float(r[5])})
    all_m1[t] = bars
    print(f'{t}: {len(bars)} M1 bars', flush=True)
ch.close()

def resample_n(m1, n):
    g = {}
    for b in m1:
        tm = b['ts'].hour * 60 + b['ts'].minute
        km = (tm // n) * n
        k = b['ts'].replace(minute=km % 60, hour=km // 60, second=0)
        if k not in g:
            g[k] = {'ts': k, 'opn': b['opn'], 'hi': b['hi'], 'lo': b['lo'], 'prc': b['prc']}
        else:
            gg = g[k]
            gg['hi'] = max(gg['hi'], b['hi'])
            gg['lo'] = min(gg['lo'], b['lo'])
            gg['prc'] = b['prc']
    return sorted(g.values(), key=lambda x: x['ts'])


def find_retest_setup(dbars, idx, win_size=24, touches=2, min_n_pct=0.5,
                      width_min=0.5, width_max=8.0, stop_bars=6, limit_frac=0.15):
    """Найти сетап «возврат к проторговке», затормаживание завершилось ровно на idx.

    Проторговка: окно [idx-win_size, idx]. Импульс: бар idx-1 (после проторговки).
    Затормаживание: 6 баров после пика, пик должен быть за stop_bars баров до idx.
    Возвращает setup только если ready_idx == idx (свежий, без look-ahead).
    """
    n = len(dbars)
    if idx < win_size + stop_bars + 2 or idx >= n:
        return None
    imp_idx = idx - stop_bars - 1  # бар импульса (сразу после проторговки)
    if imp_idx - win_size < 0 or imp_idx >= n:
        return None
    win = dbars[imp_idx-win_size:imp_idx]  # полные win_size баров ДО импульса
    if len(win) < win_size:
        return None
    H = max(b['hi'] for b in win); L = min(b['lo'] for b in win)
    mid = (H + L) / 2
    width = (H - L) / mid * 100 if mid > 0 else 0
    if not (width_min <= width <= width_max):
        return None
    th = sum(1 for b in win if b['hi'] >= H * 0.999)
    tl = sum(1 for b in win if b['lo'] <= L * 1.001)
    if th < touches or tl < touches:
        return None
    closes = [b['prc'] for b in win]
    slope = np.polyfit(np.arange(len(closes)), closes, 1)[0]
    if abs(slope) / mid * 100 > 0.05:
        return None
    b0 = dbars[imp_idx]
    # Период импульса: [imp_idx, imp_idx + stop_bars] (пик + затормаживание)
    imp_zone = dbars[imp_idx:imp_idx+stop_bars+1]
    if b0['prc'] > H:
        peak = max(b['hi'] for b in imp_zone)
        peak_pos = max(range(len(imp_zone)), key=lambda k: imp_zone[k]['hi'])
        # Пик должен быть в начале зоны (не в последнем баре — иначе stall не завершён)
        if peak_pos >= stop_bars:
            return None
        N = peak - H
        if N / H * 100 < min_n_pct:
            return None
        return {'dir': 'short', 'limit': peak - limit_frac * N,
                'tp': H, 'sl': peak * 1.003}
    elif b0['prc'] < L:
        trough = min(b['lo'] for b in imp_zone)
        trough_pos = min(range(len(imp_zone)), key=lambda k: imp_zone[k]['lo'])
        if trough_pos >= stop_bars:
            return None
        N = L - trough
        if N / L * 100 < min_n_pct:
            return None
        return {'dir': 'long', 'limit': trough + limit_frac * N,
                'tp': L, 'sl': trough * 0.997}
    return None


# Prepare detect bars for all configs
for cfg in CONFIGS:
    m1 = all_m1[cfg['ticker']]
    cfg['dbars'] = resample_n(m1, cfg['tf'])
    # detect_bar → m1 idx map
    d2m = {}
    di = 0
    for mi in range(len(m1)):
        if di < len(cfg['dbars']) and m1[mi]['ts'] >= cfg['dbars'][di]['ts']:
            d2m[di] = mi
            di += 1
    cfg['d2m'] = d2m
    cfg['detect_fired'] = set()
    cfg['db_idx'] = 0
    cfg['pos'] = None
    cfg['trades'] = []
    print(f'{cfg["ticker"]} {cfg["strat"]} tf={cfg["tf"]}мин: {len(cfg["dbars"])} detect bars', flush=True)

reset_state()
_equity_curve = []
_strat_curves = {'total': []}
for cfg in CONFIGS:
    if cfg.get('strat'):
        s = cfg['strat'] + '_' + cfg['ticker']
        _strat_curves[s] = []
        cfg['_strat_contrib'] = 0  # for equity curve export
equity = CAPITAL * 0.8  # reserve 20% for margin
peak = equity
mtm_peak = equity
cash_mdd = mtm_mdd = 0
all_trades = []

# Find max M1 length
max_len = max(len(v) for v in all_m1.values())

for mi in range(60, max_len):
    for cfg in CONFIGS:
        t = cfg['ticker']
        m1 = all_m1[t]
        if mi >= len(m1):
            continue
        b = m1[mi]
        db_idx = cfg['db_idx']
        dbars = cfg['dbars']
        d2m = cfg['d2m']
        df = cfg['detect_fired']
        
        # Tick FIRST — manages positions from PREVIOUS bars only
        # Retest: проверяем исполнение лимитки (если сетап найден)
        if cfg.get('strat') == 'retest' and not cfg['pos'] and cfg.get('pending'):
            p = cfg['pending']
            # Исполнить если цена коснулась лимитки (с учётом допуска)
            if p['dir'] == 'short' and b['lo'] <= p['limit'] * 1.0005:
                shares_r = max(1, int(equity * cfg['risk'] / cfg['go']))
                cfg['pos'] = {'dir': 'short', 'ep': p['limit'], 'bi': mi,
                              'shares': shares_r, 'tr': False, 'tp': p['tp'], 'sl': p['sl'],
                              'retest': True}
                cfg['pending'] = None
            elif p['dir'] == 'long' and b['hi'] >= p['limit'] * 0.9995:
                shares_r = max(1, int(equity * cfg['risk'] / cfg['go']))
                cfg['pos'] = {'dir': 'long', 'ep': p['limit'], 'bi': mi,
                              'shares': shares_r, 'tr': False, 'tp': p['tp'], 'sl': p['sl'],
                              'retest': True}
                cfg['pending'] = None
            # TTL лимитки: 24 H1 баров = 24*60 M1
            elif mi - cfg.get('pending_ts', mi) > 24 * 60:
                cfg['pending'] = None
        if cfg['pos']:
            pos = cfg['pos']
            ex = None
            slev = pos['ep'] * (1 - cfg['sl']) if pos['dir'] == 'long' else pos['ep'] * (1 + cfg['sl'])
            if (pos['dir'] == 'long' and b['lo'] <= slev) or (pos['dir'] == 'short' and b['hi'] >= slev):
                ex = slev
            # Retest: TP (проторговка) — приоритетнее trailing
            if not ex and pos.get('retest'):
                tp = pos.get('tp')
                if tp:
                    if (pos['dir'] == 'short' and b['lo'] <= tp) or (pos['dir'] == 'long' and b['hi'] >= tp):
                        ex = tp
            if not ex:
                if not pos.get('tr'):
                    act = pos['ep'] * (1 + cfg['ta']) if pos['dir'] == 'long' else pos['ep'] * (1 - cfg['ta'])
                    if (pos['dir'] == 'long' and b['hi'] >= act) or (pos['dir'] == 'short' and b['lo'] <= act):
                        pos['tr'] = True
                        pos['tl'] = b['hi'] * (1 - cfg['tt']) if pos['dir'] == 'long' else b['lo'] * (1 + cfg['tt'])
                if pos.get('tr'):
                    if (pos['dir'] == 'long' and b['lo'] <= pos['tl']) or (pos['dir'] == 'short' and b['hi'] >= pos['tl']):
                        ex = pos['tl']
            if not ex and mi - pos['bi'] >= cfg['to']:
                ex = b['prc']
            if ex:
                tc = FEES.get(t, {}).get('tc', COMMISSION)
                pnl = (ex - pos['ep']) / cfg['ms'] * cfg['sp'] * (-1 if pos['dir'] == 'short' else 1) * pos['shares'] - tc * pos['shares']
                equity += pnl
                cfg['trades'].append(pnl)
                cfg['_strat_contrib'] = cfg.get('_strat_contrib', 0) + pnl
                all_trades.append({'ticker': t, 'strat': cfg['strat'], 'pnl': pnl, 'ts': str(b['ts'])})
                peak = max(peak, equity)
                cash_mdd = max(cash_mdd, (peak - equity) / peak * 100)
                cfg['pos'] = None
        
        # Detect — only if no position (creates pos on this bar, ticked from NEXT bar)
        # Cooldown: пропускаем обработку, но db_idx всегда растёт
        if not cfg['pos'] and db_idx < len(dbars) and db_idx not in df and mi >= d2m.get(db_idx, 999999999):
            if db_idx < cfg.get('cd_until', 0):
                cfg['db_idx'] += 1
                continue
            df.add(db_idx)
            db = dbars[db_idx]
            dh = dbars[:db_idx]
            
            sig = None
            if cfg['strat'] == 'retest' and len(dh) >= 24:
                setup = find_retest_setup(dbars, db_idx)
                if setup:
                    cfg['pending'] = setup
                    cfg['pending_ts'] = mi
            elif cfg['strat'] == 'ir' and len(dh) >= 20:
                bd = {'prc': db['prc'], 'hi': db['hi'], 'lo': db['lo'], 'vol': 100,
                      'bars_list': dh[-60:], 'lo_hist': [x['lo'] for x in dh[-50:]],
                      'hi_hist': [x['hi'] for x in dh[-50:]], 'close_hist': [x['prc'] for x in dh[-50:]],
                      'vol_hist': [100] * min(len(dh), 50)}
                sig = ir_check(bd, t, cfg['params'])
                if sig:
                    flt = cfg.get('filters', {})
                    # Trend filter (SMA50 on detect bars)
                    if sig and flt.get('trend') and len(dh) >= 50:
                        sma50 = sum(x['prc'] for x in dh[-50:]) / 50
                        if sig['direction'] == 'long' and db['prc'] < sma50: sig = None
                        elif sig['direction'] == 'short' and db['prc'] > sma50: sig = None
                    # Volume filter
                    if sig and flt.get('min_vol_ratio', 0) > 0:
                        m1_vol = all_m1[t][mi]['vol'] if mi < len(all_m1[t]) else 0
                        if m1_vol > 0:
                            vol_hist = [all_m1[t][j]['vol'] for j in range(max(0, mi-20), mi)]
                            if vol_hist and median(vol_hist) > 0 and m1_vol / median(vol_hist) < flt['min_vol_ratio']:
                                sig = None
            elif cfg['strat'] == 'dragon' and len(dh) >= 30:
                sig = dragon_check({'bars_list': dh + [db], 'prc': db['prc']}, t, cfg['params'])
                if sig:
                    flt = cfg.get('filters', {})
                    if flt.get('trend') and len(dh) >= 50:
                        sma50 = sum(x['prc'] for x in dh[-50:]) / 50
                        if sig['direction'] == 'long' and db['prc'] < sma50: sig = None
                        elif sig['direction'] == 'short' and db['prc'] > sma50: sig = None
            elif cfg['strat'] == 'sh' and len(dh) >= 20:
                lb = cfg['params'].get('lookback', 60)
                bd = {'prc': db['prc'], 'hi': db['hi'], 'lo': db['lo'],
                      'lo_hist': [x['lo'] for x in dh[-lb:]],
                      'hi_hist': [x['hi'] for x in dh[-lb:]]}
                sig = sh_check(bd, t, cfg['params'])
                if sig:
                    flt = cfg.get('filters', {})
                    if flt.get('trend') and len(dh) >= 50:
                        sma50 = sum(x['prc'] for x in dh[-50:]) / 50
                        if sig['direction'] == 'long' and db['prc'] < sma50: sig = None
                        elif sig['direction'] == 'short' and db['prc'] > sma50: sig = None
            
            if sig:
                shares = max(1, int(equity * cfg['risk'] / cfg['go']))
                b_vol = all_m1[t][mi]['vol'] if mi < len(all_m1[t]) else 999999
                if b_vol > 0:
                    vc = 0.5 if cfg['strat'] == 'ir' else 0.2  # Si 50%, rest 20%
                    shares = min(shares, max(1, int(b_vol * vc)))
                shares = min(shares, 20)  # max 20 contracts
                if cfg['go'] * shares > equity:
                    cfg['db_idx'] += 1
                    continue
                slip = cfg['ms']
                # Realistic slippage: 2-5 tick based on position size vs liquidity
                base_slip = 2 + min(shares // 3, 3)  # 2..5 tick
                slip_total = cfg['ms'] * base_slip
                ep = sig['entry_price'] + (slip_total if sig['direction'] == 'long' else -slip_total)
                cfg['pos'] = {'dir': sig['direction'], 'ep': ep, 'bi': mi, 'shares': shares, 'tr': False}
                # Cooldown: не входить повторно в один паттерн (24 detect бара)
                cfg['cd_until'] = db_idx + 24
            cfg['db_idx'] += 1

    # MTM MDD
    floating = 0
    for cfg in CONFIGS:
        if cfg['pos']:
            t = cfg['ticker']
            if mi < len(all_m1[t]):
                b = all_m1[t][mi]
                fp = (b['prc'] - cfg['pos']['ep']) / cfg['ms'] * cfg['sp'] * (-1 if cfg['pos']['dir'] == 'short' else 1) * cfg['pos']['shares']
                floating += fp
    mtm_val = equity + floating
    mtm_peak = max(mtm_peak, mtm_val)
    if mtm_peak > 0:
        mtm_mdd = max(mtm_mdd, (mtm_peak - mtm_val) / mtm_peak * 100)
    
    # Save equity curve (every 60th bar ~1 hour)
    if mi % 60 == 0:
        # Use whichever ticker has this bar index
        for ticker_test in ['NG', 'Si', 'GD', 'MM', 'RN']:
            if mi < len(all_m1.get(ticker_test, [])):
                ts = str(all_m1[ticker_test][mi]['ts'])
                _equity_curve.append((ts, mtm_val))
                # Per-strategy equity (MTM contribution)
                _strat_curves['total'].append((ts, mtm_val))
                strat_total = 0
                for cfg in CONFIGS:
                    if cfg.get('strat'):
                        s = cfg['strat'] + '_' + cfg['ticker']
                        strat_total += cfg.get('_strat_contrib', 0)
                        _strat_curves[s].append((ts, cfg.get('_strat_contrib', 0)))
                break
    # After loop, save curve
import json
with open('/tmp/equity_curve.json', 'w') as f:
    json.dump(_equity_curve, f)
with open('/tmp/strat_curves.json', 'w') as f:
    json.dump(_strat_curves, f)
print(f'Equity curve saved: {len(_equity_curve)} points', flush=True)

print(f'\n=== ПОРТФЕЛЬ (common pool) ===')
print(f'Capital: {CAPITAL:,} → {equity:,.0f} ({(equity-CAPITAL)/CAPITAL*100:+.1f}%)')
print(f'Trades: {len(all_trades)} | Cash MDD: {cash_mdd:.2f}% | MTM MDD: {mtm_mdd:.2f}%')
print()

for cfg in CONFIGS:
    tr = cfg['trades']
    n = len(tr)
    if n:
        w = [p for p in tr if p > 0]
        l = [p for p in tr if p <= 0]
        wr = len(w)/n*100
        pf = sum(w)/sum(abs(p) for p in l) if l else 0
        total = sum(tr)
        print(f'{cfg["strat"]:10s} {cfg["ticker"]:3s} risk={cfg["risk"]*100:.0f}%: n={n:4d} WR={wr:5.1f}% PF={pf:.2f} PnL={total:+13.0f}')
