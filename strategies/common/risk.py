"""RiskManager — ограничения на открытие позиций."""


class RiskManager:
    """Контролирует риск: просадка, концентрация, лимиты."""

    def __init__(self, max_dd_pct=20, max_concurrent=5, max_per_ticker=1):
        self.max_dd_pct = max_dd_pct           # стоп-просадка (%)
        self.max_concurrent = max_concurrent     # макс открытых позиций
        self.max_per_ticker = max_per_ticker     # макс на тикер
        self.peak_equity = 0.0
        self.current_equity = 0.0

    def update(self, equity: float):
        """Обновить текущее состояние."""
        self.current_equity = equity
        if equity > self.peak_equity:
            self.peak_equity = equity

    @property
    def dd_pct(self) -> float:
        """Текущая просадка от пика."""
        if self.peak_equity <= 0:
            return 0.0
        return (self.peak_equity - self.current_equity) / self.peak_equity * 100

    def can_open(self, ticker: str, open_positions: list) -> (bool, str):
        """Проверить, можно ли открыть позицию. Вернуть (ok, reason)."""
        # Просадка
        if self.dd_pct >= self.max_dd_pct:
            return False, f'dd_stop ({self.dd_pct:.1f}% >= {self.max_dd_pct}%)'

        # Концентрация по тикеру
        ticker_count = sum(1 for p in open_positions if not p.closed and p.ticker == ticker)
        if ticker_count >= self.max_per_ticker:
            return False, f'max_per_ticker ({ticker_count})'

        # Общее кол-во открытых
        active = sum(1 for p in open_positions if not p.closed)
        if active >= self.max_concurrent:
            return False, f'max_concurrent ({active})'

        return True, 'ok'
