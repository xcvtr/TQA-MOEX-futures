"""
Configuration for MOEX OI Loader

Open Interest data from Moscow Exchange (MOEX).
Fetches from ISS API: https://iss.moex.com/iss/analyticalproducts/futoi/securities/
"""

import os
from pathlib import Path

# ── PostgreSQL ────────────────────────────────────────────────────────────
DB_HOST = os.getenv("MOEX_DB_HOST", "10.0.0.64")
DB_PORT = int(os.getenv("MOEX_DB_PORT", "5432"))
DB_NAME = os.getenv("MOEX_DB_NAME", "moex")
DB_USER = os.getenv("MOEX_DB_USER", "postgres")
DB_PASSWORD = os.getenv("MOEX_DB_PASSWORD", "")

DATABASE_URL = os.getenv(
    "MOEX_DATABASE_URL",
    f"postgresql://{DB_USER}:***@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

# ── ClickHouse (основное хранилище) ───────────────────────────────────────
CH_HOST = os.getenv("MOEX_CH_HOST", "10.0.0.64")
CH_PORT = int(os.getenv("MOEX_CH_PORT", "8123"))
CH_DB = os.getenv("MOEX_CH_DB", "moex")

# ── MOEX ISS API Auth ────────────────────────────────────────────────────
MOEX_LOGIN = os.getenv("MOEX_LOGIN", "")
MOEX_PASSWORD = os.getenv("MOEX_PASSWORD", "")

# ── Tickers (from MOEX ISS futoi API) ─────────────────────────────────────
MOEX_OI_TICKERS = [
    "AF", "AL", "AU", "BM", "BR", "CC", "CE", "CH",
    "CNYRUBF", "CR", "DX", "ED", "EURRUBF", "Eu", "FF",
    "GAZPF", "GD", "GK", "GL", "GLDRUBF", "GZ", "HS",
    "HY", "IB", "IMOEXF", "KC", "LK", "MC", "ME", "MG",
    "MM", "MN", "MX", "MY", "NA", "NG", "NM", "NR",
    "OJ", "PD", "PT", "RB", "RI", "RL", "RM", "RN",
    "SBERF", "SE", "SF", "Si", "SN", "SP", "SR", "SS",
    "SV", "TN", "TT", "UC", "USDRUBF", "VB", "VI", "W4",
    "X5", "YD",
]

# ── Loading ───────────────────────────────────────────────────────────────
START_DATE = "2024-01-01"       # default start for full backfill
DAYS_BACKFILL = 30               # how many days back to check (MOEX hides last 14 days for free)
REQUEST_TIMEOUT = 30            # HTTP timeout in seconds
RETRY_ATTEMPTS = 3
RETRY_DELAY = 2                 # seconds between retries
