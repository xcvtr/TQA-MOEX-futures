"""OI DOM engine for MOEX futures — check_signal() only.

OI-сигнал (day_net физиков) ПОДТВЕРЖДАЕТСЯ стаканом (DOM imbalance):
- contrarian long (физ продают) → подтверждение: ask-heavy (покупки манипуляторов)
- contrarian short (физ покупают) → подтверждение: bid-heavy (продажи)

Стакан отсекает ложные OI-сигналы (нет согласованного потока в стакане).

bar_data expects:
    - day_net: float — накопление нетто-позиции физлиц за день в % от OI
    - dom_imb: float — imbalance стакана (ask-bid)/(ask+bid) за последние N минут
    - prc: текущая цена (для entry)
"""

DEFAULT_PARAMS = {
    'thr': 3.0,       # порог |day_net| (%)
    'imb_thr': 0.1,   # порог подтверждения стакана
    'direction': 'contrarian',  # 'contrarian' (сырьё) или 'momentum' (валюта)
}


def check_signal(bar_data: dict, ticker: str, params: dict = None) -> dict:
    """Detect OI contrarian signal подтверждённый стаканом."""
    if params is None:
        params = DEFAULT_PARAMS

    thr = abs(float(params.get('thr', DEFAULT_PARAMS['thr'])))
    imb_thr = float(params.get('imb_thr', DEFAULT_PARAMS['imb_thr']))
    direction = params.get('direction', DEFAULT_PARAMS['direction'])

    day_net = bar_data.get('day_net')
    dom_imb = bar_data.get('dom_imb')
    if day_net is None or dom_imb is None:
        return None

    if direction == 'momentum':
        # валюта: физ покупают → рост (подтверждение: ask-heavy)
        if day_net >= thr and dom_imb >= imb_thr:
            direction_out = 'long'
            reason = f'oi_dom_mom_buy_{day_net:.1f}%_imb{dom_imb:+.2f}'
            score = round(min(abs(day_net - thr) / 10.0, 1.0), 3) + 0.1
        elif day_net <= -thr and dom_imb <= -imb_thr:
            direction_out = 'short'
            reason = f'oi_dom_mom_sell_{day_net:.1f}%_imb{dom_imb:+.2f}'
            score = round(min(abs(day_net + thr) / 10.0, 1.0), 3) + 0.1
        else:
            return None
    else:
        # contrarian: физ продают → long (подтверждение: ask-heavy = покупки)
        if day_net <= -thr and dom_imb >= imb_thr:
            direction_out = 'long'
            reason = f'oi_dom_sell_{day_net:.1f}%_imb{dom_imb:+.2f}'
            score = round(min(abs(day_net + thr) / 10.0, 1.0), 3) + 0.1
        elif day_net >= thr and dom_imb <= -imb_thr:
            direction_out = 'short'
            reason = f'oi_dom_buy_{day_net:.1f}%_imb{dom_imb:+.2f}'
            score = round(min(abs(day_net - thr) / 10.0, 1.0), 3) + 0.1
        else:
            return None

    if score < 0.15:
        score = 0.15

    return {
        'ticker': ticker,
        'direction': direction_out,
        'entry_price': float(bar_data.get('prc', 0)),
        'reason': reason,
        'score': score,
        'strategy': 'oi_dom',
    }
