#!/usr/bin/env python3
"""
ClickHouse connection helper for TQA-MOEX.
Data is on 10.0.0.60:8123, database=moex.
"""
import clickhouse_connect
from config import CH_HOST, CH_PORT, CH_DB

_ch_client = None


def get_ch():
    """Get or create a ClickHouse client connection."""
    global _ch_client
    if _ch_client is None:
        _ch_client = clickhouse_connect.get_client(
            host=CH_HOST,
            port=CH_PORT,
            database=CH_DB,
        )
    return _ch_client
