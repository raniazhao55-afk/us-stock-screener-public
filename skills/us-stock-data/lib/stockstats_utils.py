"""OHLCV loading/caching + technical-indicator helpers.

Ported from TauricResearch/TradingAgents (tradingagents/dataflows/stockstats_utils.py,
Apache-2.0), imports rewired to this skill's flat `lib/` layout. Logic
unchanged — the rate-limit retry, stale-data guard, and look-ahead-bias
filtering here are non-obvious and worth keeping intact rather than
rewriting from scratch.
"""

import logging
import os
import time
from typing import Annotated

import pandas as pd
import yfinance as yf
from stockstats import wrap
from yfinance.exceptions import YFRateLimitError

from .config import get_config
from .symbol_utils import NoMarketDataError, normalize_symbol
from .utils import safe_ticker_component

logger = logging.getLogger(__name__)

# A vendor's latest OHLCV row this many calendar days before the requested date
# is treated as stale. Generous enough to span long holiday weekends, tight
# enough to catch a year-old frame yfinance occasionally returns.
MAX_OHLCV_STALE_DAYS = 10

# How long a same-day cache that does not yet reach the requested day may be
# reused before it is refetched. Short enough that an intraday run picks up
# today's close soon after it publishes, long enough that a day with no bar
# at all (weekend, holiday) cannot trigger a download on every call.
OHLCV_CACHE_TTL_SECONDS = 900


def yf_retry(func, max_retries=3, base_delay=2.0):
    """Execute a yfinance call with exponential backoff on rate limits.

    yfinance raises YFRateLimitError on HTTP 429 responses but does not
    retry them internally. This wrapper adds retry logic specifically for
    rate limits. Other exceptions propagate immediately.
    """
    for attempt in range(max_retries + 1):
        try:
            return func()
        except YFRateLimitError:
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    f"Yahoo Finance rate limited, retrying in {delay:.0f}s "
                    f"(attempt {attempt + 1}/{max_retries})"
                )
                time.sleep(delay)
            else:
                raise


def _ensure_date_column(data: pd.DataFrame) -> pd.DataFrame:
    """Normalize the date column to ``Date``.

    Some yfinance builds leave the index unnamed (so ``reset_index()`` yields
    ``index``) or use ``Datetime`` for intraday data.
    """
    if "Date" in data.columns:
        return data
    for candidate in ("index", "Datetime", "date"):
        if candidate in data.columns:
            return data.rename(columns={candidate: "Date"})
    return data


def _clean_dataframe(data: pd.DataFrame) -> pd.DataFrame:
    """Normalize a stock DataFrame for stockstats: parse dates, drop invalid rows, fill price gaps."""
    data = _ensure_date_column(data)
    data["Date"] = pd.to_datetime(data["Date"], errors="coerce")
    data = data.dropna(subset=["Date"])

    price_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in data.columns]
    data[price_cols] = data[price_cols].apply(pd.to_numeric, errors="coerce")
    data = data.dropna(subset=["Close"])
    data[price_cols] = data[price_cols].ffill().bfill()

    return data


def _coerce_ohlcv_dates(data: pd.DataFrame) -> pd.Series:
    """Return parsed dates from an OHLCV frame, whether Date is a column or the index."""
    if "Date" in data.columns:
        return pd.to_datetime(data["Date"], errors="coerce").dropna()
    if isinstance(data.index, pd.DatetimeIndex):
        return pd.Series(pd.to_datetime(data.index, errors="coerce")).dropna()
    df = data.reset_index()
    for col in ("Date", "Datetime", "date", "index"):
        if col in df.columns:
            parsed = pd.to_datetime(df[col], errors="coerce").dropna()
            if not parsed.empty:
                return parsed
    return pd.Series(dtype="datetime64[ns]")


def _assert_ohlcv_not_stale(
    data: pd.DataFrame,
    curr_date: str,
    symbol: str,
    canonical: str | None = None,
    *,
    max_stale_days: int = MAX_OHLCV_STALE_DAYS,
) -> None:
    """Reject OHLCV whose latest row is far older than curr_date.

    Guards against a vendor returning a stale (e.g. year-old) frame that
    would otherwise feed wrong prices into a thesis.
    """
    if data is None or data.empty:
        return
    requested = pd.to_datetime(curr_date, errors="coerce")
    if pd.isna(requested):
        return
    requested = requested.normalize()
    dates = _coerce_ohlcv_dates(data)
    if dates.empty:
        return
    latest = dates.max().normalize()
    stale_days = (requested - latest).days
    if stale_days > max_stale_days:
        raise NoMarketDataError(
            symbol,
            canonical,
            f"latest row is {latest.date()}, {stale_days} days before the "
            f"requested {requested.date()} (stale) — refusing to use it",
        )


def _needs_same_day_refresh(data_file, curr_date_dt, today_date) -> bool:
    """Whether a cached frame must be refetched to reflect the requested day."""
    if curr_date_dt.date() < today_date.date():
        return False
    return time.time() - os.path.getmtime(data_file) > OHLCV_CACHE_TTL_SECONDS


def _cache_row_is_nan_for_requested_day(cached: pd.DataFrame, curr_date_dt: pd.Timestamp) -> bool:
    """Whether the cached frame's row for curr_date_dt (if present) has a NaN Close.

    Guards against a transient vendor glitch landing in the same-day cache
    write: if a Yahoo Finance hiccup wrote a hole exactly on the day being
    requested, _needs_same_day_refresh's date check alone can never catch it
    again once curr_date is in the past relative to "today" — the hole would
    otherwise be silently dropped by _clean_dataframe's dropna and read back
    as "no data that day" forever (found 2026-09-09 backtesting past
    curr_date values: 186/186 symbols' cached rows for one specific day were
    all-NaN from one bad fetch, and stayed that way across repeated calls).
    """
    if cached.empty or "Close" not in cached.columns:
        return False
    dates = _coerce_ohlcv_dates(cached)
    if dates.empty:
        return False
    match = dates[dates.dt.normalize() == curr_date_dt.normalize()]
    if match.empty:
        return False
    close_val = pd.to_numeric(cached.loc[match.index[0], "Close"], errors="coerce")
    return pd.isna(close_val)


_SPLITS_CHECK_CACHE: dict[str, "pd.Timestamp | None"] = {}


def _latest_split_date(canonical: str) -> "pd.Timestamp | None":
    """Most recent stock-split date for canonical, as a UTC-naive Timestamp.

    Memoized per process (module-level dict) so a backtest looping over many
    curr_date values for the same symbol pays for this lookup once, not per
    call.
    """
    if canonical in _SPLITS_CHECK_CACHE:
        return _SPLITS_CHECK_CACHE[canonical]
    latest = None
    try:
        splits = yf_retry(lambda: yf.Ticker(canonical).splits)
        if splits is not None and not splits.empty:
            ts = pd.Timestamp(splits.index.max())
            if ts.tzinfo is not None:
                ts = ts.tz_convert("UTC").tz_localize(None)
            latest = ts
    except Exception:
        # A failed split lookup shouldn't block OHLCV loading -- treat as
        # "unknown" and fall back to trusting the other staleness checks.
        latest = None
    _SPLITS_CHECK_CACHE[canonical] = latest
    return latest


def _cache_stale_relative_to_new_splits(data_file, canonical: str) -> bool:
    """Whether a stock split for canonical was registered after data_file was written.

    yfinance's auto_adjust=True retroactively re-scales a symbol's *entire*
    price history against every split known at download time. A cache file
    written before a later split is registered therefore shows a different
    (by today's adjustment, wrong) price for the same historical date than a
    fresh download would -- found 2026-09-09 on FXHO (5 reverse splits
    between 2024-09 and 2026-06): cache files for the same date range built
    on different calendar days each gave a different Close for 2025-09-10,
    depending on how many of the later splits had already happened when that
    particular file was written. Distinct from _needs_same_day_refresh
    (curr_date vs today, unrelated) and _cache_row_is_nan_for_requested_day
    (this data isn't NaN, just adjusted against a stale split count).
    """
    latest_split = _latest_split_date(canonical)
    if latest_split is None:
        return False
    file_mtime = pd.Timestamp(os.path.getmtime(data_file), unit="s")
    return latest_split > file_mtime


def load_ohlcv(symbol: str, curr_date: str) -> pd.DataFrame:
    """Fetch OHLCV data with caching, filtered to prevent look-ahead bias.

    Downloads 5 years of data up to today and caches per symbol. Rows after
    curr_date are filtered out so backtests/thesis-generation never see
    future prices.
    """
    canonical = normalize_symbol(symbol)
    safe_symbol = safe_ticker_component(canonical)

    config = get_config()
    curr_date_dt = pd.to_datetime(curr_date)

    today_date = pd.Timestamp.today()
    start_date = today_date - pd.DateOffset(years=5)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = (today_date + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    os.makedirs(config["data_cache_dir"], exist_ok=True)
    data_file = os.path.join(
        config["data_cache_dir"],
        f"{safe_symbol}-YFin-data-{start_str}-{end_str}.csv",
    )

    data = None
    if os.path.exists(data_file):
        cached = pd.read_csv(data_file, on_bad_lines="skip", encoding="utf-8")
        if (
            not cached.empty
            and "Close" in cached.columns
            and not _needs_same_day_refresh(data_file, curr_date_dt, today_date)
            and not _cache_row_is_nan_for_requested_day(cached, curr_date_dt)
            and not _cache_stale_relative_to_new_splits(data_file, canonical)
        ):
            data = cached

    if data is None:
        downloaded = yf_retry(lambda: yf.download(
            canonical,
            start=start_str,
            end=end_str,
            multi_level_index=False,
            progress=False,
            auto_adjust=True,
        ))
        downloaded = _ensure_date_column(downloaded.reset_index())
        if downloaded.empty or "Close" not in downloaded.columns:
            raise NoMarketDataError(symbol, canonical, "Yahoo Finance returned no rows")
        downloaded.to_csv(data_file, index=False, encoding="utf-8")
        data = downloaded

    data = _clean_dataframe(data)
    data = data[data["Date"] <= curr_date_dt]

    _assert_ohlcv_not_stale(data, curr_date, symbol, canonical)

    return data


def filter_financials_by_date(data: pd.DataFrame, curr_date: str) -> pd.DataFrame:
    """Drop financial-statement columns (fiscal period timestamps) after curr_date."""
    if not curr_date or data.empty:
        return data
    cutoff = pd.Timestamp(curr_date)
    mask = pd.to_datetime(data.columns, errors="coerce") <= cutoff
    return data.loc[:, mask]


class StockstatsUtils:
    @staticmethod
    def get_stock_stats(
        symbol: Annotated[str, "ticker symbol for the company"],
        indicator: Annotated[str, "technical indicator to compute"],
        curr_date: Annotated[str, "YYYY-mm-dd"],
    ):
        data = load_ohlcv(symbol, curr_date)
        df = wrap(data)
        df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
        curr_date_str = pd.to_datetime(curr_date).strftime("%Y-%m-%d")

        df[indicator]  # trigger stockstats to calculate the indicator
        matching_rows = df[df["Date"].str.startswith(curr_date_str)]

        if not matching_rows.empty:
            return matching_rows[indicator].values[0]
        return "N/A: Not a trading day (weekend or holiday)"
