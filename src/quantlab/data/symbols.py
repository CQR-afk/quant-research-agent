"""Canonical exchange-aware A-share symbols."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class AShareSymbol:
    symbol: str
    code: str
    exchange: str


_EXCHANGE_BY_PREFIX = {
    "600": "SSE",
    "601": "SSE",
    "603": "SSE",
    "605": "SSE",
    "688": "SSE",
    "689": "SSE",
    "000": "SZSE",
    "001": "SZSE",
    "002": "SZSE",
    "003": "SZSE",
    "300": "SZSE",
    "301": "SZSE",
}


def normalize_ashare_symbol(value: str) -> AShareSymbol:
    """Normalize common six-digit SSE/SZSE share forms while keeping leading zeros."""
    if not isinstance(value, str):
        raise ValueError("A-share symbols must be strings so leading zeros are preserved.")
    raw = value.strip().upper()
    match = re.fullmatch(r"(\d{6})\.(SH|SZ)", raw)
    if match:
        code, suffix = match.groups()
        declared_exchange = "SSE" if suffix == "SH" else "SZSE"
    else:
        match = re.fullmatch(r"(?:SH|SZ)\.?([0-9]{6})", raw)
        if match:
            code = match.group(1)
            declared_exchange = "SSE" if raw.startswith("SH") else "SZSE"
        elif re.fullmatch(r"\d{6}", raw):
            code = raw
            declared_exchange = None
        else:
            raise ValueError(f"Invalid A-share code {value!r}; use 000001.SZ or 600519.SH.")

    exchange = _EXCHANGE_BY_PREFIX.get(code[:3])
    if exchange is None:
        raise ValueError(f"Unsupported A-share code prefix in {value!r}.")
    if declared_exchange is not None and declared_exchange != exchange:
        raise ValueError(f"A-share code {code} maps to {exchange}, not {declared_exchange}.")
    suffix = "SH" if exchange == "SSE" else "SZ"
    return AShareSymbol(symbol=f"{code}.{suffix}", code=code, exchange=exchange)

