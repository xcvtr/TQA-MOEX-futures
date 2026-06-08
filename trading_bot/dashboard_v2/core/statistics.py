"""WR, PF, DD, equity curve, rolling WR."""


def compute_stats(signals):
    n = len(signals)
    if n == 0:
        return {'n': 0, 'wr': 0.0, 'pf': 0.0, 'dd': 0.0, 'avg_ret': 0.0}

    won = [s for s in signals if s.get('return_pct', 0) > 0]
    lost = [s for s in signals if s.get('return_pct', 0) <= 0]
    wr = len(won) / n * 100

    total_gain = sum(s['return_pct'] for s in won) if won else 0
    total_loss = abs(sum(s['return_pct'] for s in lost)) if lost else 0
    pf = total_gain / total_loss if total_loss > 0 else (999.99 if total_gain > 0 else 0)

    eq = equity_curve(signals)
    dd = max_drawdown(eq)
    avg_ret = sum(s['return_pct'] for s in signals) / n

    return {
        'n': n,
        'wr': round(wr, 2),
        'pf': round(pf, 2),
        'dd': round(dd, 2),
        'avg_ret': round(avg_ret, 4),
    }


def equity_curve(signals):
    cum = 0.0
    curve = []
    for s in signals:
        cum += s.get('return_pct', 0)
        curve.append(round(cum, 4))
    return curve


def max_drawdown(equity):
    if not equity:
        return 0.0
    peak = equity[0]
    max_dd = 0.0
    for val in equity:
        if val > peak:
            peak = val
        dd = (peak - val) / peak * 100 if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd


def rolling_wr(signals, window=50):
    wr_curve = []
    for i in range(len(signals)):
        chunk = signals[max(0, i - window + 1):i + 1]
        won = sum(1 for s in chunk if s.get('return_pct', 0) > 0)
        wr_curve.append(round(won / len(chunk) * 100, 2) if chunk else 0)
    return wr_curve
