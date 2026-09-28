"""Industry/peer comparison — the piece a-stock-data has (同业股票池对比) that
us-stock-data was missing until now. New code, not ported from anywhere.

Uses yfinance's Sector/Industry domain objects (free, no key): given a ticker,
resolve its industryKey, then pull the peer weight/rating table, the YTD
performance ranking across the WHOLE industry (not just your watchlist), and
an industry-size overview. This is real-time peer standing, not a-stock-
screener's market_calibration.json (which is a slower-moving statistical
base-rate curve built by pooling historical data) — the two are complementary:
this answers "who are my peers and where do I rank against them right now",
market_calibration answers "given this kind of move, what's the historical
odds of a 5%+ swing".
"""

from __future__ import annotations

from datetime import date, timedelta

import yfinance as yf

from .stockstats_utils import yf_retry
from .symbol_utils import normalize_symbol


def get_industry_snapshot(ticker: str, peer_limit: int = 12) -> dict:
    """Sector/industry classification + peer weight/rating table + industry-wide
    YTD performance ranking + industry-size overview for one ticker.

    Returns an error dict (not an exception) if the ticker's industry can't be
    resolved (e.g. some ADRs/foreign issuers don't carry a yfinance industryKey).
    """
    canonical = normalize_symbol(ticker)
    try:
        info = yf_retry(lambda: yf.Ticker(canonical).info)
    except Exception as e:
        return {"ok": False, "error": f"could not fetch ticker info: {e}"}

    sector_key = info.get("sectorKey")
    industry_key = info.get("industryKey")
    if not industry_key:
        return {
            "ok": False,
            "error": f"{canonical} has no industryKey from yfinance (common for some ADRs/foreign issuers)",
        }

    try:
        ind = yf.Industry(industry_key)
        overview = ind.overview or {}
        top_companies = ind.top_companies
        top_performers = ind.top_performing_companies
        top_growth = ind.top_growth_companies
    except Exception as e:
        return {"ok": False, "error": f"could not fetch industry data for {industry_key}: {e}"}

    def _rows(df, cols, limit):
        if df is None or df.empty:
            return []
        out = []
        for sym, row in df.head(limit).iterrows():
            entry = {"symbol": sym}
            for c in cols:
                if c in row:
                    v = row[c]
                    entry[c.replace(" ", "_")] = None if v != v else v  # NaN check
            out.append(entry)
        return out

    def _drop_self(df):
        return df.drop(index=canonical, errors="ignore") if df is not None else df

    peers = _rows(_drop_self(top_companies), ["name", "rating", "market weight"], peer_limit)
    performers = _rows(top_performers, ["name", "ytd return", "last price", "target price"], peer_limit)
    growth = _rows(_drop_self(top_growth), ["name", "ytd return", "growth estimate"], peer_limit)

    ticker_ytd_rank = None
    ticker_ytd_return = None
    if top_performers is not None and canonical in top_performers.index:
        ticker_ytd_rank = int(top_performers.index.get_loc(canonical)) + 1
        ticker_ytd_return = float(top_performers.loc[canonical, "ytd return"])

    return {
        "ok": True,
        "ticker": canonical,
        "sector": info.get("sector"),
        "sector_key": sector_key,
        "industry": info.get("industry"),
        "industry_key": industry_key,
        "industry_overview": {
            "companies_count": overview.get("companies_count"),
            "market_cap": overview.get("market_cap"),
            "market_weight_of_sector": overview.get("market_weight"),
            "description": overview.get("description"),
        },
        "ticker_ytd_rank_in_industry": ticker_ytd_rank,
        "ticker_ytd_return": ticker_ytd_return,
        "top_peers_by_weight": peers,
        "top_performers_ytd": performers,
        "top_growth_companies": growth,
    }


def compare_peer_returns(symbols: list[str], curr_date: str | None = None) -> dict:
    """Side-by-side 1d/5d/20d/60d return table for a short explicit list of
    tickers (e.g. the target + a handful of peers pulled from
    get_industry_snapshot). Uses the same OHLCV loader as the rest of this
    skill, so results share its cache/staleness guards.

    Keep `symbols` short (under ~15) — this does one OHLCV fetch per symbol.
    """
    from .stockstats_utils import load_ohlcv

    curr_date = curr_date or date.today().strftime("%Y-%m-%d")
    windows = {"1d": 1, "5d": 5, "20d": 20, "60d": 60}
    results = []
    for sym in symbols:
        try:
            df = load_ohlcv(sym, curr_date)
            if df.empty:
                results.append({"symbol": sym, "error": "no data"})
                continue
            latest = df["Close"].iloc[-1]
            row = {"symbol": normalize_symbol(sym), "latest_close": round(float(latest), 2)}
            for label, w in windows.items():
                if len(df) > w:
                    past = df["Close"].iloc[-(w + 1)]
                    row[f"return_{label}"] = round(float((latest - past) / past), 4)
                else:
                    row[f"return_{label}"] = None
            results.append(row)
        except Exception as e:
            results.append({"symbol": sym, "error": str(e)})

    return {"date": curr_date, "peers": results}
