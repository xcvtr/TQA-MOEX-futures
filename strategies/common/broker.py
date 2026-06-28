"""Broker — Position + BrokerSim + BrokerLive (заглушка)."""

DEFAULT_ACTIVATION = 0.5   # %
DEFAULT_TRAIL = 0.3        # %
DEFAULT_TIMEOUT = 12       # bars
DEFAULT_COMMISSION = 4     # RUB


class Position:
    def __init__(self, ticker, direction, entry_price, entry_bar, shares, strategy,
                 go=0, step_price=1.0, min_step=0.01, trailing_params=None):
        self.ticker = ticker
        self.direction = direction
        self.entry_price = entry_price
        self.entry_bar = entry_bar
        self.shares = shares
        self.strategy = strategy
        self.go = go
        self.step_price = step_price
        self.min_step = min_step
        self.trailing_params = trailing_params or {}
        self.best_price = 0.0
        self.trail_activated = False
        self.pnl = 0.0
        self.exit_reason = 'open'
        self.closed = False
        self.exit_price: float | None = None

    @property
    def activation_pct(self):
        return float(self.trailing_params.get('activation', DEFAULT_ACTIVATION))

    @property
    def trail_pct(self):
        return float(self.trailing_params.get('trail', DEFAULT_TRAIL))

    @property
    def timeout_bars(self):
        return int(self.trailing_params.get('timeout', DEFAULT_TIMEOUT))

    def __repr__(self):
        return (f"<{self.strategy} {self.direction} {self.ticker} "
                f"entry={self.entry_price} shares={self.shares}>")


class BrokerSim:
    """Simulator — trailing из Position.trailing_params, нет внешних зависимостей."""

    def __init__(self, commission=DEFAULT_COMMISSION):
        self.commission = commission

    def update(self, pos: Position, bar_idx: int, hi: float, lo: float, prc: float) -> float:
        """Проверить позицию на баре. Вернуть PnL если закрыта, иначе 0."""
        if pos.closed:
            return 0.0

        entry = pos.entry_price
        direction = pos.direction

        # Максимальное движение в нашу сторону (%)
        if direction == 'long':
            fav = (hi - entry) / entry * 100
            cur_dn = (entry - lo) / entry * 100
        else:
            fav = (entry - lo) / entry * 100
            cur_dn = (hi - entry) / entry * 100

        if fav > pos.best_price:
            pos.best_price = fav

        # Активация трейлинга
        if not pos.trail_activated and fav >= pos.activation_pct:
            pos.trail_activated = True

        # Трейлинг
        if pos.trail_activated:
            trail_stop = pos.best_price - pos.trail_pct
            if cur_dn >= trail_stop:
                exit_px = entry * (1 + trail_stop / 100) if direction == 'long' else entry * (1 - trail_stop / 100)
                return self._close(pos, exit_px, 'trailing_tp')

        # Таймаут
        if bar_idx - pos.entry_bar >= pos.timeout_bars:
            return self._close(pos, prc, 'timeout')

        return 0.0

    def _close(self, pos: Position, exit_px: float, reason: str) -> float:
        ticks = (exit_px - pos.entry_price) / max(pos.min_step, 0.0001)
        if pos.direction == 'short':
            ticks = -ticks
        pnl = ticks * pos.step_price * pos.shares - self.commission * pos.shares
        pos.pnl = round(pnl, 2)
        pos.exit_reason = reason
        pos.exit_price = exit_px
        pos.closed = True
        return pos.pnl


class BrokerLive:
    """Заглушка для Alor API. Пока кидает NotImplementedError."""

    def __init__(self, token=None, endpoint=None):
        self.token = token
        self.endpoint = endpoint

    def update(self, pos, bar_idx, hi, lo, prc):
        raise NotImplementedError("BrokerLive — заглушка, реализовать позже")
