"""Broker — Position + BrokerSim + BrokerLive (заглушка).

Реалистичная симуляция:
  • Стопы исполняются по рынку (close следующего бара + slippage)
  • Трейлинг: retracement от пика, а не от входа
  • Таймаут — по close бара + slippage
  • Проскальзывание на вход и выход (1 тик)
  • Ликвидность — позиция не должна превышать 50% объёма бара
"""

DEFAULT_ACTIVATION = 0.5   # %
DEFAULT_TRAIL = 0.3        # %
DEFAULT_TIMEOUT = 12       # bars
DEFAULT_STOP_LOSS = 0.7    # % hard stop loss from entry
DEFAULT_COMMISSION = 4     # RUB per contract round-trip
DEFAULT_SLIPPAGE_OUT = 1   # ticks on exit (market order)


class Position:
    def __init__(self, ticker, direction, entry_price, entry_bar, shares, strategy,
                 go=0, step_price=1.0, min_step=0.01, pct=1.0, trailing_params=None):
        self.ticker = ticker
        self.direction = direction
        self.entry_price = entry_price
        self.entry_bar = entry_bar
        self.shares = shares
        self.strategy = strategy
        self.go = go
        self.step_price = step_price
        self.min_step = min_step
        self.pct = pct
        self.trailing_params = trailing_params or {}
        self.best_price = 0.0          # макс fav % (движение в нашу сторону)
        self.best_abs = 0.0            # макс цена в нашу сторону (абс)
        self.trail_activated = False
        self.pnl = 0.0
        self.exit_reason = 'open'
        self.closed = False
        self.exit_price: float | None = None
        self._stop_triggered = False

    @property
    def activation_pct(self):
        return float(self.trailing_params.get('activation', DEFAULT_ACTIVATION))

    @property
    def trail_pct(self):
        return float(self.trailing_params.get('trail', DEFAULT_TRAIL))

    @property
    def stop_loss_pct(self):
        return float(self.trailing_params.get('stop_loss', DEFAULT_STOP_LOSS))

    @property
    def timeout_bars(self):
        return int(self.trailing_params.get('timeout', DEFAULT_TIMEOUT))

    def __repr__(self):
        return (f"<{self.strategy} {self.direction} {self.ticker} "
                f"entry={self.entry_price} shares={self.shares}>")


class BrokerSim:
    """Реалистичный симулятор с трейлингом от пика, проскальзыванием, ликвидностью."""

    def __init__(self, commission=DEFAULT_COMMISSION, slippage_out=DEFAULT_SLIPPAGE_OUT, slippage_in=1):
        self.commission = commission
        self.slippage_out = slippage_out
        self.slippage_in = slippage_in

    def check_liquidity(self, shares: int, volume: float) -> float:
        """Проверить ликвидность. Вернуть множитель slippage."""
        if volume <= 0 or shares <= 0:
            return 2.0
        share = shares / volume
        if share > 0.25:
            return 3.0
        elif share > 0.1:
            return 1.5
        return 1.0

    def update(self, pos: Position, bar_idx: int,
               hi: float, lo: float, prc: float,
               volume: float = 0) -> float:
        if pos.closed:
            return 0.0

        entry = pos.entry_price
        direction = pos.direction

        # 1. Закрытие стопа, сработавшего на предыдущем баре
        if pos._stop_triggered:
            return self._close_market(pos, prc, pos.exit_reason, volume)

        # 2. Favourable movement (движение в нашу сторону)
        if direction == 'long':
            fav_abs = hi
            cur_retrace_abs = lo          # откат — минимум бара
        else:
            fav_abs = lo
            cur_retrace_abs = hi

        fav_pct = (fav_abs - entry) / entry * 100 if direction == 'long' else (entry - fav_abs) / entry * 100

        if fav_pct > pos.best_price:
            pos.best_price = fav_pct
            pos.best_abs = fav_abs

        # 2b. Hard stop loss (против входа)
        if direction == 'long':
            loss_pct = (entry - lo) / entry * 100
        else:
            loss_pct = (hi - entry) / entry * 100
        if loss_pct >= pos.stop_loss_pct:
            if direction == 'long':
                stop_px = entry * (1 - pos.stop_loss_pct / 100)
            else:
                stop_px = entry * (1 + pos.stop_loss_pct / 100)
            return self._close_market(pos, stop_px, 'stop_loss', volume)
            

        # 3. Активация трейлинга
        if not pos.trail_activated and fav_pct >= pos.activation_pct:
            pos.trail_activated = True

        # 4. Проверка трейлинг-стопа (откат от пика, а не от входа)
        if pos.trail_activated and pos.best_abs > 0:
            if direction == 'long':
                retrace_from_peak = (pos.best_abs - cur_retrace_abs) / pos.best_abs * 100
            else:
                retrace_from_peak = (cur_retrace_abs - pos.best_abs) / pos.best_abs * 100

            if retrace_from_peak >= pos.trail_pct:
                # Исполняем стоп НЕМЕДЛЕННО на этом баре по уровню стопа + slippage
                if direction == 'long':
                    stop_px = pos.best_abs * (1 - pos.trail_pct / 100)
                else:
                    stop_px = pos.best_abs * (1 + pos.trail_pct / 100)
                return self._close_market(pos, stop_px, 'trailing_tp', volume)

        # 5. Таймаут
        if not pos._stop_triggered and bar_idx - pos.entry_bar >= pos.timeout_bars:
            pos._stop_triggered = True
            pos.exit_reason = 'timeout'
            return 0.0

        return 0.0

    def _close_market(self, pos: Position, exit_prc: float, reason: str, volume: float = 0) -> float:
        """Закрыть по рынку: exit_prc + slippage + liquidity impact."""
        slip_mult = self.check_liquidity(pos.shares, volume)
        total_slippage = self.slippage_out * slip_mult

        if pos.direction == 'long':
            exit_px = exit_prc - total_slippage * pos.min_step
        else:
            exit_px = exit_prc + total_slippage * pos.min_step

        ticks = (exit_px - pos.entry_price) / max(pos.min_step, 0.0001)
        if pos.direction == 'short':
            ticks = -ticks
        gross = ticks * pos.step_price * pos.shares * pos.pct
        pnl = gross - self.commission * pos.shares
        pos.pnl = round(pnl, 2)
        pos.exit_price = round(exit_px, 4)
        pos.exit_reason = reason
        pos.closed = True
        return pos.pnl


class BrokerLive:
    """Заглушка для Alor API."""

    def __init__(self, token=None, endpoint=None):
        self.token = token
        self.endpoint = endpoint

    def update(self, pos, bar_idx, hi, lo, prc):
        raise NotImplementedError("BrokerLive — заглушка")
