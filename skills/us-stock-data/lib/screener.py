"""Supplementary-scan candidates: Yahoo Finance predefined screens via yfinance.

This is the US equivalent of a-share's 强势股/涨停池/龙虎榜/北向/概念热点 补充扫描——
there's no equivalent official disclosure-driven pool in the US, so this uses
Yahoo's own curated screens instead (day gainers, most active, etc).

KNOWN INSTABILITY (record this like a-stock-data records eastmoney outages):
yfinance's screen() endpoint has broken before without a library version bump
(ranaroussi/yfinance#2419 — Yahoo changed something server-side and GET
requests that used to work started failing). Treat failures here as expected
sometimes, not as a sign something is wrong with this code — degrade to "no
supplementary candidates this run" rather than blocking Phase A on it.
"""

from __future__ import annotations

import yfinance as yf

# A subset of yfinance.PREDEFINED_SCREENER_QUERIES relevant to a momentum/
# catalyst screener. Check `yf.PREDEFINED_SCREENER_QUERIES.keys()` in the
# installed version for the full current list if one of these stops working.
SUPPLEMENTARY_SCAN_TYPES = {
    "day_gainers": "day_gainers",
    "most_actives": "most_actives",
    "day_losers": "day_losers",
    "most_shorted": "most_shorted_stocks",
    "undervalued_growth": "undervalued_growth_stocks",
    "small_cap_gainers": "small_cap_gainers",
    "aggressive_small_caps": "aggressive_small_caps",
}


def run_supplementary_scan(scan_type: str, limit: int = 25) -> list[dict]:
    """Run one predefined Yahoo screen, return a list of {symbol, name, price, pct_change, volume}.

    Returns an empty list (not an exception) on any failure — this endpoint is
    known to be flaky; callers should treat an empty result as "no
    supplementary candidates this run", not a hard error.
    """
    query_name = SUPPLEMENTARY_SCAN_TYPES.get(scan_type)
    if query_name is None:
        raise ValueError(f"Unknown scan_type {scan_type!r}. Choose from: {list(SUPPLEMENTARY_SCAN_TYPES)}")

    try:
        result = yf.screen(query_name, size=limit)
    except Exception as e:
        print(f"[screener] {scan_type} ({query_name}) failed, returning no candidates: {e}")
        return []

    quotes = result.get("quotes", []) if isinstance(result, dict) else []
    candidates = []
    for q in quotes:
        candidates.append({
            "symbol": q.get("symbol"),
            "name": q.get("shortName") or q.get("longName"),
            "price": q.get("regularMarketPrice"),
            "pct_change": q.get("regularMarketChangePercent"),
            "volume": q.get("regularMarketVolume"),
        })
    return candidates
