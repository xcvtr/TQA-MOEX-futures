"""
Configuration for MOEX OI Loader

Open Interest data from Moscow Exchange (MOEX).
Fetches from ISS API: https://iss.moex.com/iss/analyticalproducts/futoi/securities/
"""

import os
from pathlib import Path

# ── PostgreSQL (legacy, данные перенесены в ClickHouse) ─────────────────
DB_HOST = os.getenv("MOEX_DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("MOEX_DB_PORT", "5432"))
DB_NAME = os.getenv("MOEX_DB_NAME", "moex")
DB_USER = os.getenv("MOEX_DB_USER", "postgres")
DB_PASSWORD = os.getenv("MOEX_DB_PASSWORD", "")

DATABASE_URL = os.getenv(
    "MOEX_DATABASE_URL",
    f"postgresql://{DB_USER}:***@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)

# ── ClickHouse (основное хранилище) ─────────────────────────────────────
CH_HOST = os.getenv("MOEX_CH_HOST", "127.0.0.1")
CH_PORT = int(os.getenv("MOEX_CH_PORT", "8123"))
CH_DB = os.getenv("MOEX_CH_DB", "moex")

# ── MOEX Auth (optional for futoi, but included for reliability) ──────────
MOEX_LOGIN = os.getenv("MOEX_LOGIN", "")
MOEX_PASSWORD = os.getenv("MOEX_PASSWORD", "")

# ── Tickers ───────────────────────────────────────────────────────────────
# Short ticker codes used in ISS API URLs
# Auto-discovered from MOEX ISS futoi API on 2026-05-16
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

# Long → Short ticker mapping (from MT5 symbols to ISS short codes)
# Auto-generated — maps all MOEX ISS tickers + common MT5 aliases
TICKER_MAP = {
    # Currencies
    "Si": "Si", "SiH4": "Si", "ALLFUTSi": "Si",
    "USDRUB": "Si", "USDRUBF": "USDRUBF",
    "Eu": "Eu", "EURRUB": "Eu", "EURRUBF": "EURRUBF",
    "ALLFUTEu": "Eu",
    "CNYRUBF": "CNYRUBF", "CNYRUB_TOM": "CNYRUBF",
    "ED": "ED",
    # Indices
    "RI": "RI", "MX": "MX", "MM": "MM",
    "IMOEXF": "IMOEXF",
    "MXI": "MM", "MIX": "MX",
    # Oil & Energy
    "BR": "BR", "CR": "CR", "NG": "NG",
    # Metals
    "GD": "GD", "GOLD": "GD", "XAUUSD": "GD", "GLDRUBF": "GLDRUBF",
    "SV": "SV", "SILV": "SV", "XAGUSD": "SV",
    "PT": "PT", "PLT": "PLT", "PD": "PD", "PLD": "PLD",
    # Bonds (OFZ)
    "O2": "O2", "O4": "O4", "O6": "O6", "OX": "OX", "OV": "OV",
    "OFZ2": "O2", "OFZ4": "O4", "OFZ6": "O6", "OF10": "OX", "OF15": "OV",
    # Stocks
    "SR": "SR", "SBRF": "SR", "SBERF": "SBERF",
    "GZ": "GZ", "GAZR": "GZ", "GAZPF": "GAZPF",
    "LK": "LK", "ALLFUTLKOH": "LK", "LKOH": "LK",
    "VB": "VB", "VTBR": "VB",
    "RN": "RN", "MN": "MN", "AF": "AF", "AL": "AL",
    "SN": "SN", "TT": "TT", "SP": "SP",
    "NM": "NM", "HY": "HY", "ME": "ME",
    "GK": "GK", "MG": "MG",
    "VI": "VI", "RVI": "VI",
    # Futures
    "CL": "CL", "FV": "FV", "ML": "ML", "YN": "YN",
    # New tickers from 2026 discovery
    "AU": "AU", "AUDU": "AU",
    "SF": "SF",
    "BM": "BM", "CC": "CC", "CE": "CE", "CH": "CH",
    "DX": "DX", "FF": "FF",
    "GL": "GL",
    "HS": "HS", "IB": "IB",
    "KC": "KC", "MC": "MC", "MY": "MY", "NA": "NA",
    "NR": "NR", "OJ": "OJ",
    "RB": "RB", "RL": "RL", "RM": "RM",
    "SE": "SE", "SS": "SS",
    "TN": "TN", "UC": "UC",
    "W4": "W4", "X5": "X5", "YD": "YD",
}

# ── Loading ───────────────────────────────────────────────────────────────
START_DATE = "2024-01-01"       # default start for full backfill
DAYS_BACKFILL = 30               # how many days back to check (MOEX hides last 14 days for free)
REQUEST_TIMEOUT = 30            # HTTP timeout in seconds
RETRY_ATTEMPTS = 3
RETRY_DELAY = 2                 # seconds between retries

# ── Paths ─────────────────────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = Path.home() / ".hermes" / "scripts"
