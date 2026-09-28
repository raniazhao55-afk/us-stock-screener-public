"""Small shared helpers, trimmed from TradingAgents' dataflows/utils.py (Apache-2.0)."""

import re
from datetime import date

# Tickers can contain letters, digits, dot, dash, underscore, caret (index
# symbols like ^GSPC), equals (futures like GC=F). None of these enable
# directory traversal, so the value never escapes a containing directory when
# interpolated into a path. Anything else is rejected.
_TICKER_PATH_RE = re.compile(r"^[A-Za-z0-9._\-\^=]+$")


def safe_ticker_component(value: str, *, max_len: int = 32) -> str:
    """Validate ``value`` is safe to interpolate into a filesystem path.

    Tickers can come from LLM tool calls influenced by attacker-controlled
    content (e.g. prompt injection embedded in fetched news). Without
    validation, a value like ``"../../../etc/foo"`` could escape the cache dir.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(f"ticker must be a non-empty string, got {value!r}")
    if len(value) > max_len:
        raise ValueError(f"ticker exceeds {max_len} chars: {value!r}")
    if not _TICKER_PATH_RE.fullmatch(value):
        raise ValueError(
            f"ticker contains characters not allowed in a filesystem path: {value!r}"
        )
    if set(value) == {"."}:
        raise ValueError(f"ticker cannot consist solely of dots: {value!r}")
    return value


def get_current_date():
    return date.today().strftime("%Y-%m-%d")
