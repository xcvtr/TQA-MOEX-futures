-- Таблица спецификаций фьючерсов MOEX FORTS
-- Статические поля: lot_volume, min_step, step_price (не меняются от контракта к контракту)
-- Динамические: initial_margin (меняется ежедневно от волатильности)
-- Обновляется ежедневно из IS

CREATE TABLE IF NOT EXISTS moex_ticker_specs (
    asset_code      VARCHAR(20)     NOT NULL,       -- NG, BR, Si, Eu, ED, ...
    trade_date      DATE            NOT NULL,       -- дата снимка
    sec_id          VARCHAR(20),                    -- фронт-контракт (NGH7, BRJ7, ...)
    short_name      VARCHAR(100),                   -- название
    lot_volume      INTEGER         NOT NULL,       -- множитель контракта
    min_step        NUMERIC(20,8)   NOT NULL,       -- шаг цены
    step_price      NUMERIC(20,6)   NOT NULL,       -- стоимость шага цены (RUB)
    decimals        INTEGER,                         -- кол-во знаков после запятой
    initial_margin  NUMERIC(20,2),                   -- ГО (начальное обеспечение)
    updated_at      TIMESTAMP       DEFAULT NOW(),

    PRIMARY KEY (asset_code, trade_date)
);

-- Индекс для быстрого поиска последней записи
CREATE INDEX IF NOT EXISTS idx_ticker_specs_asset
    ON moex_ticker_specs (asset_code, trade_date DESC);
