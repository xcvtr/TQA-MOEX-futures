# Engine для CVD-стратегии на MOEX фьючерсах
#
# Не зависит от PG, executor-ов, формата хранения.
# Вход: bars (OHLCV) + params (словарь)
# Выход: signal {direction, entry_price, reason, score}
