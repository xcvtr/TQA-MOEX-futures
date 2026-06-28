#!/usr/bin/env python3
"""
Rebuild supercandles_fo with correct OI.
Strategy: compute daily OI from tradestats_fo, then INSERT all bars at once.
"""
import subprocess
import sys

CH = ["clickhouse-client", "-h", "10.0.0.60"]

def run(sql):
    r = subprocess.run(CH + ["--query", sql], capture_output=True, text=True, timeout=120)
    if r.returncode:
        print(f"ERROR: {r.stderr[:300]}", file=sys.stderr)
        return None
    return r.stdout

print("Dropping and recreating table...")

# Удаляем старую таблицу и создаём новую
run("DROP TABLE IF EXISTS moex.supercandles_fo_v2")

run("""
CREATE TABLE moex.supercandles_fo_v2 (
    tradedate      Date,
    tradetime      DateTime,
    secid          LowCardinality(String),
    ticker         LowCardinality(String),
    pr_open        Float64,
    pr_high        Float64,
    pr_low         Float64,
    pr_close       Float64,
    vol_sum        Int64,
    val_sum        Float64,
    trades_sum     Int32,
    vol_b_sum      Int64,
    vol_s_sum      Int64,
    trades_b_sum   Int32,
    trades_s_sum   Int32,
    val_b_sum      Float64,
    val_s_sum      Float64,
    disb_mean      Float64,
    disb_std       Float64,
    disb_last      Float64,
    net_vol        Int64,
    net_vol_pct    Float64,
    vwap           Float64,
    vwap_b         Float64,
    vwap_s         Float64,
    oi_open        Int64,
    oi_high        Int64,
    oi_low         Int64,
    oi_close       Int64,
    oi_change      Int64,
    vol_b_ratio    Float64,
    trades_b_ratio Float64,
    val_b_ratio    Float64,
    pr_change_pct  Float64,
    pr_range_pct   Float64,
    pr_std         Float64,
    im             Float64
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(tradedate)
ORDER BY (ticker, tradedate, tradetime)
SETTINGS index_granularity = 8192
""")

print("Table created. Now computing daily OI...")

# Шаг 1: Получаем все (ticker, date, oi_daily) из tradestats_fo
# Создаём временную таблицу с дневными OI за 2025
print("Computing daily OI from tradestats_fo...")
run("""
CREATE TABLE moex._daily_oi ENGINE = Memory AS
WITH tickers AS (
    SELECT DISTINCT ticker FROM moex.supercandles_fo
)
SELECT 
    t.ticker,
    ts.tradedate,
    argMin(ts.oi_close, ts.tradetime) as oi_first,
    argMax(ts.oi_close, ts.tradetime) as oi_last,
    max(ts.oi_high) as oi_high,
    min(ts.oi_low) as oi_low,
    argMin(ts.oi_open, ts.tradetime) as oi_open
FROM moex.tradestats_fo ts
JOIN tickers t ON substring(ts.secid, 1, length(t.ticker)) = t.ticker
WHERE ts.tradedate >= '2025-01-01'
  AND substring(ts.tradetime, 1, 2) >= '10'
GROUP BY t.ticker, ts.tradedate
""")

daily_count = run("SELECT count() FROM moex._daily_oi")
print(f"Daily OI computed: {daily_count.strip()} rows")

# Шаг 2: INSERT bars WITH JOIN to daily OI
print("Inserting supercandles with correct OI...")

run("""
INSERT INTO moex.supercandles_fo_v2
WITH 
  bars AS (
    SELECT
        tradedate,
        toStartOfFiveMinutes(toDateTime(concat(toString(tradedate), ' ', tradetime))) as bar_time,
        secid,
        multiIf(
            secid = 'EURRUBF', 'Eu',
            secid = 'USDRUBF', 'Si',
            secid = 'CNYRUBF', 'CNY',
            secid = 'GLDRUBF', 'GLD',
            substring(secid, 1, 2) = 'Si', 'Si',
            substring(secid, 1, 2) = 'Eu', 'Eu',
            substring(secid, 1, 2) = 'Cr', 'CR',
            substring(secid, 1, 2) = 'Co', 'CO',
            substring(secid, 1, 2) = 'Su', 'SU',
            substring(secid, 1, 2) = 'Nl', 'NL',
            substring(secid, 1, 2) = 'Zn', 'ZN',
            substring(secid, 1, 3) IN ('CCH','CCM','CCU','CCZ'), 'CC',
            substring(secid, 1, 2) = 'CC', 'CC',
            secid IN ('SBER','VTBR','GAZP','LKOH','ROSN','GMKN','NVTK','NLMK','MAGN','MOEX','PLZL','HYDR','IRAO','CHMF','ALRS','TATN','SNGS','SNGSP','SBERP','RTKM','MTSS','MFON'), secid,
            substring(secid, 1, 5) = 'IMOEX', 'IMOEX',
            substring(secid, 1, 4) = 'RGBI', 'RGBI',
            substring(secid, 1, 6) = 'EURRUB', 'Eu',
            substring(secid, 1, 6) = 'USDRUB', 'Si',
            substring(secid, 1, 6) = 'CNYRUB', 'CNY',
            substring(secid, 1, 6) = 'GLDRUB', 'GLD',
            substring(secid, 1, 2)
        ) AS raw_ticker,
        sum(vol) as vol_sum,
        sum(val) as val_sum,
        sum(trades) as trades_sum,
        sum(vol_b) as vol_b_sum,
        sum(vol_s) as vol_s_sum,
        sum(trades_b) as trades_b_sum,
        sum(trades_s) as trades_s_sum,
        sum(val_b) as val_b_sum,
        sum(val_s) as val_s_sum,
        avg(disb) as disb_mean,
        stddevSamp(disb) as disb_std,
        argMax(disb, tradetime) as disb_last,
        argMax(im, tradetime) as im
    FROM moex.tradestats_fo
    WHERE tradedate >= '2025-01-01'
      AND substring(tradetime, 1, 2) >= '10'
    GROUP BY tradedate, bar_time, secid, raw_ticker
  )
SELECT
    b.tradedate,
    b.bar_time as tradetime,
    b.secid,
    b.raw_ticker as ticker,
    -- OHLC
    argMax(ts.pr_open, ts.tradetime) as pr_open,
    max(ts.pr_high) as pr_high,
    min(ts.pr_low) as pr_low,
    argMax(ts.pr_close, ts.tradetime) as pr_close,
    -- Volumes
    b.vol_sum,
    b.val_sum,
    b.trades_sum,
    b.vol_b_sum,
    b.vol_s_sum,
    b.trades_b_sum,
    b.trades_s_sum,
    b.val_b_sum,
    b.val_s_sum,
    b.disb_mean,
    b.disb_std,
    b.disb_last,
    b.vol_b_sum - b.vol_s_sum as net_vol,
    if(b.vol_sum > 0, (b.vol_b_sum - b.vol_s_sum) / b.vol_sum, 0) as net_vol_pct,
    if(b.vol_sum > 0, b.val_sum / b.vol_sum, 0) as vwap,
    if(b.vol_b_sum > 0, b.val_b_sum / b.vol_b_sum, 0) as vwap_b,
    if(b.vol_s_sum > 0, b.val_s_sum / b.vol_s_sum, 0) as vwap_s,
    -- Daily OI from precomputed table
    d.oi_open,
    d.oi_high,
    d.oi_low,
    d.oi_last as oi_close,
    d.oi_last - d.oi_open as oi_change,
    -- Ratios
    if(b.vol_sum > 0, b.vol_b_sum / b.vol_sum, 0) as vol_b_ratio,
    if(b.trades_sum > 0, b.trades_b_sum / b.trades_sum, 0) as trades_b_ratio,
    if(b.val_sum > 0, b.val_b_sum / b.val_sum, 0) as val_b_ratio,
    if(argMax(ts.pr_open, ts.tradetime) > 0, 
        (argMax(ts.pr_close, ts.tradetime) - argMax(ts.pr_open, ts.tradetime)) / argMax(ts.pr_open, ts.tradetime) * 100, 0) as pr_change_pct,
    if(argMax(ts.pr_open, ts.tradetime) > 0,
        (max(ts.pr_high) - min(ts.pr_low)) / argMax(ts.pr_open, ts.tradetime) * 100, 0) as pr_range_pct,
    0 as pr_std,
    b.im
FROM bars b
LEFT JOIN moex._daily_oi d ON b.raw_ticker = d.ticker AND b.tradedate = d.tradedate
LEFT JOIN moex.tradestats_fo ts ON ts.secid = b.secid AND ts.tradedate = b.tradedate AND ts.tradetime = formatDateTime(b.bar_time, '%H:%M:%S')
WHERE b.raw_ticker IN (SELECT DISTINCT ticker FROM moex.futoi)
GROUP BY b.tradedate, b.bar_time, b.secid, b.raw_ticker,
         b.vol_sum, b.val_sum, b.trades_sum, b.vol_b_sum, b.vol_s_sum,
         b.trades_b_sum, b.trades_s_sum, b.val_b_sum, b.val_s_sum,
         b.disb_mean, b.disb_std, b.disb_last, b.im,
         d.oi_open, d.oi_high, d.oi_low, d.oi_last
ORDER BY b.raw_ticker, b.tradedate, b.bar_time
""")

print("INSERT done. Verifying...")

# Проверка
for t in ['Si', 'GL', 'BR', 'NG', 'CR', 'GD', 'SR', 'AF', 'RI', 'PD', 'PT']:
    r = run(f"""
        SELECT toString(count()), toString(min(oi_change)), toString(max(oi_change)), toString(avg(oi_change))
        FROM moex.supercandles_fo_v2 WHERE ticker = '{t}' AND oi_change != 0
    """)
    if r:
        parts = r.strip().split('\t')
        print(f"  {t}: non-zero={parts[0]}, min={parts[1]}, max={parts[2]}, avg={parts[3]}")

# Переименовываем
print("\nReplacing old table...")
run("DROP TABLE moex.supercandles_fo")
run("RENAME TABLE moex.supercandles_fo_v2 TO moex.supercandles_fo")
run("DROP TABLE IF EXISTS moex._daily_oi")

print("Done!")
