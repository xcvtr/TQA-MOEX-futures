"""Mean reversion engine for MOEX futures — check_signal() only.

Проверено аудитом (checkpoint 233): SV +216% (CAGR 34%), DD 12.7%, PF 1.46,
Calmar 17.1; TATN +54%, DD 15%, Calmar 3.6. ТОЛЬКО SHORT после бычьей H1
(ret > ret_thr). Вход next_open (close-entry в engine), TP/SL симметрично,
timeout hold_h часов.

Live-адаптация:
- Папер ресемплит M1→H1 (tf=60 из params) → bars_list = H1-бары
- Cooldown: по одному H1-бару сигналим ОДИН раз (state-файл по тикеру),
  иначе папер (тик каждые 5 мин) повторял бы сигнал 12 раз за час.

Формат: как dragon (bar_data dict → signal dict | None).
"""
from __future__ import annotations
import os
import json
import tempfile

_COOLDOWN_FILE = os.path.join(tempfile.gettempdir(), 'mr_cooldown.json')


def _load_cooldown():
    try:
        with open(_COOLDOWN_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cooldown(cd):
    try:
        with open(_COOLDOWN_FILE, 'w') as f:
            json.dump(cd, f)
    except Exception:
        pass


def check_signal(bar_data: dict, ticker: str, params: dict = None) -> dict | None:
    """SHORT после бычьей H1-свечи (ret = (close-open)/open > ret_thr).

    bar_data expects:
        - bars_list: list of dicts с opn, hi, lo, prc (хронологически, H1)
        - prc: текущая цена close
    params:
        - ret_thr: порог возврата (0.006)
        - sl_pct: стоп-лосс (0.012)
        - tp_pct: тейк-профит (0.012)
        - max_bars: timeout в H1-барах (3 = 3 часа)
    """
    if params is None:
        params = {'ret_thr': 0.006, 'sl_pct': 0.012, 'tp_pct': 0.012, 'max_bars': 3}

    bars = bar_data.get('bars_list', [])
    if not bars:
        return None

    # Режим детекта: 'h1' (H1-бар, по умолчанию) или 'window' (скользящее окно)
    det_mode = params.get('detect', 'h1')

    if det_mode == 'window':
        # Скользящее окно: bars_list = M1-бары, ret за последние win_min минут
        win_min = int(params.get('win_min', 60))
        n_back = win_min
        if len(bars) < n_back:
            return None
        px0 = float(bars[-n_back].get('prc', bars[-n_back].get('close', 0)))
        prc = float(bars[-1].get('prc', bars[-1].get('close', 0)))
        if px0 <= 0 or prc <= 0:
            return None
        ret = (prc - px0) / px0
        last = bars[-1]
        ts = last.get('ts') or last.get('bt') or last.get('dt')
        ts_key = str(ts) if ts is not None else ''
    else:
        last = bars[-1]
        ts = last.get('ts') or last.get('bt') or last.get('dt')
        ts_key = str(ts) if ts is not None else ''
        opn = float(last.get('opn', last.get('open', 0)))
        prc = float(last.get('prc', last.get('close', 0)))
        if opn <= 0 or prc <= 0:
            return None
        ret = (prc - opn) / opn
    if ret <= params.get('ret_thr', 0.006):
        return None

    # Cooldown: не сигналить повторно по тому же H1-бару
    cd = _load_cooldown()
    last_ts = cd.get(ticker)
    if last_ts == ts_key and ts_key:
        return None
    cd[ticker] = ts_key
    _save_cooldown(cd)

    return {
        'ticker': ticker,
        'direction': 'short',
        'entry_price': prc,
        'strategy': 'mean_reversion',
        'sl_pct': params.get('sl_pct', 0.012),
        'tp_pct': params.get('tp_pct', 0.012),
        'timeout_bars': params.get('max_bars', 3),
        'reason': f'mr_ret_{ret:.4f}',
    }
