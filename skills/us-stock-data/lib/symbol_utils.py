"""Symbol normalization and market-data error types for vendor calls.

Ported from TauricResearch/TradingAgents (tradingagents/dataflows/symbol_utils.py,
Apache-2.0), trimmed to the US-equity-relevant paths — kept the forex/crypto/
index-CFD alias table as-is since it's harmless dead weight if unused, and it's
one less thing to re-derive if this skill later grows into futures/crypto.

user types        Yahoo wants       why
---------------   ---------------   -----------------------------------
XAUUSD, XAUUSD+   GC=F              gold has no forex pair on Yahoo
EURUSD            EURUSD=X          spot forex pairs take a ``=X`` suffix
BTCUSD            BTC-USD           crypto pairs use a ``-`` separator
SPX500, US500     ^GSPC             index CFDs map to Yahoo index symbols

KNOWN GAP (not handled here): Yahoo wants dash-separated share classes
(``BRK-B``, ``BF-B``) while some data sources/users write them with a dot
(``BRK.B``). If a ticker with a dot in it returns no data, try the dashed
form before assuming the symbol is wrong.
"""

from __future__ import annotations

import logging
import re

from .errors import NoMarketDataError as NoMarketDataError

logger = logging.getLogger(__name__)

_FOREX_CURRENCIES = frozenset(
    {
        "USD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD",
        "CNY", "CNH", "HKD", "SGD", "SEK", "NOK", "DKK", "PLN",
        "MXN", "ZAR", "TRY", "INR", "KRW", "BRL", "RUB", "THB",
    }
)

_CRYPTO_BASES = frozenset(
    {"BTC", "ETH", "SOL", "XRP", "ADA", "DOGE", "LTC", "BCH", "DOT", "AVAX", "LINK"}
)

_ALIASES = {
    "XAUUSD": "GC=F", "XAU": "GC=F", "GOLD": "GC=F",
    "XAGUSD": "SI=F", "XAG": "SI=F", "SILVER": "SI=F",
    "XPTUSD": "PL=F", "XPDUSD": "PA=F",
    "WTICOUSD": "CL=F", "USOIL": "CL=F", "WTI": "CL=F",
    "BCOUSD": "BZ=F", "UKOIL": "BZ=F", "BRENT": "BZ=F",
    "NATGAS": "NG=F", "XNGUSD": "NG=F",
    "COPPER": "HG=F", "XCUUSD": "HG=F",
    "SPX500": "^GSPC", "US500": "^GSPC", "SPX": "^GSPC",
    "NAS100": "^NDX", "US100": "^NDX", "USTEC": "^NDX",
    "US30": "^DJI", "DJI30": "^DJI", "WS30": "^DJI",
}

_YAHOO_SAFE = re.compile(r"^[A-Za-z0-9._\-\^=]+$")

_CRYPTO_QUOTES = ("USDT", "USDC", "USD")


def crypto_base(raw: str) -> str | None:
    if not isinstance(raw, str):
        return None
    compact = raw.strip().upper().rstrip("+").replace("-", "")
    for quote in _CRYPTO_QUOTES:
        if compact.endswith(quote):
            base = compact[: -len(quote)]
            return base if base in _CRYPTO_BASES else None
    return None


def _normalize_crypto(s: str) -> str | None:
    base = crypto_base(s)
    return f"{base}-USD" if base else None


def normalize_symbol(raw: str) -> str:
    """Map a user symbol to its canonical Yahoo Finance symbol.

    Resolution order (first match wins):
      1. Explicit alias table (metals, energy, index CFDs).
      2. Crypto rule: a known crypto base quoted in USD/USDT/USDC -> ``BASE-USD``.
      3. Forex rule: six letters that are two ISO currency codes -> ``PAIR=X``.
      4. Otherwise the upper-cased symbol is returned unchanged (plain equities,
         ETFs, Yahoo-native symbols like ``GC=F`` or ``^GSPC``).
    """
    if not isinstance(raw, str) or not raw.strip():
        return raw

    s = raw.strip().upper()
    s = s.rstrip("+")

    crypto = _normalize_crypto(s)
    if s in _ALIASES:
        canonical = _ALIASES[s]
    elif crypto is not None:
        canonical = crypto
    elif len(s) == 6 and s[:3] in _FOREX_CURRENCIES and s[3:] in _FOREX_CURRENCIES:
        canonical = f"{s}=X"
    else:
        canonical = s

    if canonical != raw.strip().upper():
        logger.info("Resolved symbol %r to Yahoo symbol %r", raw, canonical)
    return canonical


def is_yahoo_safe(symbol: str) -> bool:
    return bool(symbol) and _YAHOO_SAFE.fullmatch(symbol) is not None
