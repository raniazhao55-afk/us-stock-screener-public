"""OHLCV, technical indicators, fundamentals, financial statements, insider transactions.

Ported from TauricResearch/TradingAgents (tradingagents/dataflows/y_finance.py,
Apache-2.0), imports rewired to this skill's flat `lib/` layout. Function
names/signatures kept identical to the upstream project so future upstream
fixes are easy to diff and re-port.
"""

from datetime import datetime
from typing import Annotated

import pandas as pd
import yfinance as yf
from dateutil.relativedelta import relativedelta

from .stockstats_utils import (
    StockstatsUtils,
    _assert_ohlcv_not_stale,
    filter_financials_by_date,
    load_ohlcv,
    yf_retry,
)
from .symbol_utils import NoMarketDataError, normalize_symbol


def get_YFin_data_online(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format"],
):
    datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")

    canonical = normalize_symbol(symbol)
    ticker = yf.Ticker(canonical)

    # yfinance treats `end` as EXCLUSIVE, so it would drop the requested
    # end_date row. Request one day past end_date so the range is inclusive.
    end_inclusive = (end_dt + relativedelta(days=1)).strftime("%Y-%m-%d")
    data = yf_retry(lambda: ticker.history(start=start_date, end=end_inclusive))

    if data.empty:
        raise NoMarketDataError(symbol, canonical, f"no rows between {start_date} and {end_date}")

    if data.index.tz is not None:
        data.index = data.index.tz_localize(None)

    _assert_ohlcv_not_stale(data, end_date, symbol, canonical)

    numeric_columns = ["Open", "High", "Low", "Close", "Adj Close"]
    for col in numeric_columns:
        if col in data.columns:
            data[col] = data[col].round(2)

    csv_string = data.to_csv()

    label = canonical if canonical == symbol.upper() else f"{canonical} (from {symbol})"
    header = f"# Stock data for {label} from {start_date} to {end_date}\n"
    header += f"# Total records: {len(data)}\n"
    header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    return header + csv_string


_INDICATOR_NOTES = {
    "close_50_sma": "50 SMA: medium-term trend indicator, lags price.",
    "close_200_sma": "200 SMA: long-term trend benchmark, golden/death cross reference.",
    "close_10_ema": "10 EMA: responsive short-term average, noisy in choppy markets.",
    "macd": "MACD: momentum via EMA differences; watch crossovers/divergence.",
    "macds": "MACD Signal: EMA smoothing of MACD; crossovers trigger signals.",
    "macdh": "MACD Histogram: gap between MACD and signal; visualizes momentum strength.",
    "rsi": "RSI: overbought/oversold via 70/30 thresholds; can stay extreme in strong trends.",
    "boll": "Bollinger Middle: 20 SMA basis for the bands.",
    "boll_ub": "Bollinger Upper Band: ~2 std dev above middle; overbought/breakout zone.",
    "boll_lb": "Bollinger Lower Band: ~2 std dev below middle; oversold zone.",
    "atr": "ATR: average true range, used for stop-loss/position sizing by volatility.",
    "vwma": "VWMA: volume-weighted moving average, confirms trend with volume.",
    "mfi": "MFI: Money Flow Index, volume+price momentum, >80 overbought / <20 oversold.",
}


def get_stock_stats_indicators_window(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator name"],
    curr_date: Annotated[str, "YYYY-mm-dd"],
    look_back_days: Annotated[int, "how many days to look back"],
) -> str:
    if indicator not in _INDICATOR_NOTES:
        raise ValueError(
            f"Indicator {indicator} is not supported. Choose from: {list(_INDICATOR_NOTES)}"
        )

    end_date = curr_date
    curr_date_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    before = curr_date_dt - relativedelta(days=look_back_days)

    try:
        indicator_data = _get_stock_stats_bulk(symbol, indicator, curr_date)
        current_dt = curr_date_dt
        date_values = []
        while current_dt >= before:
            date_str = current_dt.strftime("%Y-%m-%d")
            value = indicator_data.get(date_str, "N/A: Not a trading day (weekend or holiday)")
            date_values.append((date_str, value))
            current_dt -= relativedelta(days=1)
        ind_string = "".join(f"{d}: {v}\n" for d, v in date_values)
    except NoMarketDataError:
        raise
    except Exception as e:
        print(f"Error getting bulk stockstats data: {e}")
        ind_string = ""
        current_dt = curr_date_dt
        while current_dt >= before:
            value = get_stockstats_indicator(symbol, indicator, current_dt.strftime("%Y-%m-%d"))
            ind_string += f"{current_dt.strftime('%Y-%m-%d')}: {value}\n"
            current_dt -= relativedelta(days=1)

    return (
        f"## {indicator} values from {before.strftime('%Y-%m-%d')} to {end_date}:\n\n"
        + ind_string
        + "\n\n"
        + _INDICATOR_NOTES.get(indicator, "No description available.")
    )


def _get_stock_stats_bulk(symbol: str, indicator: str, curr_date: str) -> dict:
    """Fetch OHLCV once and calculate `indicator` for every available date."""
    from stockstats import wrap

    data = load_ohlcv(symbol, curr_date)
    df = wrap(data)
    df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
    df[indicator]  # trigger stockstats to calculate the indicator

    result = {}
    for _, row in df.iterrows():
        value = row[indicator]
        result[row["Date"]] = "N/A" if pd.isna(value) else str(value)
    return result


def get_stockstats_indicator(
    symbol: Annotated[str, "ticker symbol of the company"],
    indicator: Annotated[str, "technical indicator name"],
    curr_date: Annotated[str, "YYYY-mm-dd"],
) -> str:
    curr_date = datetime.strptime(curr_date, "%Y-%m-%d").strftime("%Y-%m-%d")
    try:
        value = StockstatsUtils.get_stock_stats(symbol, indicator, curr_date)
    except NoMarketDataError:
        raise
    except Exception as e:
        print(f"Error getting stockstats indicator {indicator} on {curr_date}: {e}")
        return ""
    return str(value)


def get_fundamentals(
    ticker: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "current date (not used for yfinance)"] = None,
):
    """Company fundamentals overview (PE, market cap, margins, etc.) from yfinance."""
    canonical = normalize_symbol(ticker)
    try:
        ticker_obj = yf.Ticker(canonical)
        info = yf_retry(lambda: ticker_obj.info)

        if not info:
            raise NoMarketDataError(ticker, canonical, "no fundamentals returned")

        fields = [
            ("Name", info.get("longName")),
            ("Sector", info.get("sector")),
            ("Industry", info.get("industry")),
            ("Market Cap", info.get("marketCap")),
            ("PE Ratio (TTM)", info.get("trailingPE")),
            ("Forward PE", info.get("forwardPE")),
            ("PEG Ratio", info.get("pegRatio")),
            ("Price to Book", info.get("priceToBook")),
            ("EPS (TTM)", info.get("trailingEps")),
            ("Forward EPS", info.get("forwardEps")),
            ("Dividend Yield", info.get("dividendYield")),
            ("Beta", info.get("beta")),
            ("52 Week High", info.get("fiftyTwoWeekHigh")),
            ("52 Week Low", info.get("fiftyTwoWeekLow")),
            ("50 Day Average", info.get("fiftyDayAverage")),
            ("200 Day Average", info.get("twoHundredDayAverage")),
            ("Revenue (TTM)", info.get("totalRevenue")),
            ("Gross Profit", info.get("grossProfits")),
            ("EBITDA", info.get("ebitda")),
            ("Net Income", info.get("netIncomeToCommon")),
            ("Profit Margin", info.get("profitMargins")),
            ("Operating Margin", info.get("operatingMargins")),
            ("Return on Equity", info.get("returnOnEquity")),
            ("Return on Assets", info.get("returnOnAssets")),
            ("Debt to Equity", info.get("debtToEquity")),
            ("Current Ratio", info.get("currentRatio")),
            ("Book Value", info.get("bookValue")),
            ("Free Cash Flow", info.get("freeCashflow")),
            ("Short % of Float", info.get("shortPercentOfFloat")),
            ("Shares Short", info.get("sharesShort")),
        ]

        lines = [f"{label}: {value}" for label, value in fields if value is not None]

        if not lines:
            raise NoMarketDataError(ticker, canonical, "no fundamental fields returned")

        header = f"# Company Fundamentals for {canonical}\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        return header + "\n".join(lines)

    except NoMarketDataError:
        raise
    except Exception as e:
        return f"Error retrieving fundamentals for {ticker}: {str(e)}"


def _statement(ticker: str, freq: str, curr_date: str, attr_annual: str, attr_quarterly: str, label: str):
    canonical = normalize_symbol(ticker)
    try:
        ticker_obj = yf.Ticker(canonical)
        attr = attr_quarterly if freq.lower() == "quarterly" else attr_annual
        data = yf_retry(lambda: getattr(ticker_obj, attr))
        data = filter_financials_by_date(data, curr_date)

        if data.empty:
            raise NoMarketDataError(ticker, canonical, f"no {label.lower()} data")

        csv_string = data.to_csv()
        header = f"# {label} data for {canonical} ({freq})\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        return header + csv_string
    except NoMarketDataError:
        raise
    except Exception as e:
        return f"Error retrieving {label.lower()} for {ticker}: {str(e)}"


def get_balance_sheet(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "YYYY-MM-DD"] = None,
):
    return _statement(ticker, freq, curr_date, "balance_sheet", "quarterly_balance_sheet", "Balance Sheet")


def get_cashflow(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "YYYY-MM-DD"] = None,
):
    return _statement(ticker, freq, curr_date, "cashflow", "quarterly_cashflow", "Cash Flow")


def get_income_statement(
    ticker: Annotated[str, "ticker symbol of the company"],
    freq: Annotated[str, "'annual' or 'quarterly'"] = "quarterly",
    curr_date: Annotated[str, "YYYY-MM-DD"] = None,
):
    return _statement(ticker, freq, curr_date, "income_stmt", "quarterly_income_stmt", "Income Statement")


def get_insider_transactions(ticker: Annotated[str, "ticker symbol of the company"]):
    """Insider (Form 4) transactions from yfinance."""
    canonical = normalize_symbol(ticker)
    try:
        ticker_obj = yf.Ticker(canonical)
        data = yf_retry(lambda: ticker_obj.insider_transactions)

        # Empty is normal (many valid symbols have no recent insider filings).
        if data is None or data.empty:
            return f"No insider transactions reported for symbol '{canonical}'"

        csv_string = data.to_csv()
        header = f"# Insider Transactions data for {canonical}\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        return header + csv_string
    except Exception as e:
        return f"Error retrieving insider transactions for {ticker}: {str(e)}"


def get_institutional_holders(ticker: Annotated[str, "ticker symbol of the company"]):
    """Institutional ownership (13F-derived) from yfinance — a-share版'北向/龙虎榜'没有直接对应，
    这是美股里最接近'谁在买'的免费信号：机构持仓集中度变化。"""
    canonical = normalize_symbol(ticker)
    try:
        ticker_obj = yf.Ticker(canonical)
        data = yf_retry(lambda: ticker_obj.institutional_holders)
        if data is None or data.empty:
            return f"No institutional holders reported for symbol '{canonical}'"
        csv_string = data.to_csv()
        header = f"# Institutional Holders for {canonical}\n"
        header += f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        return header + csv_string
    except Exception as e:
        return f"Error retrieving institutional holders for {ticker}: {str(e)}"


def get_quote(ticker: Annotated[str, "ticker symbol of the company"]) -> dict:
    """Lightweight current-ish quote (price/prev close/day range/volume) — new code,
    not from TradingAgents (its dataflows/ has no plain quote helper). Used for Phase B
    repricing and for a quick "is this even trading" sanity check.

    NOTE (known limitation, 2026-08-23): yfinance has no reliable free
    "is this halted right now" flag. A stale/zero volume value here is a rough
    proxy for not-tradeable-today, not a certainty — treat it as a prompt to
    double-check manually, not as ground truth.
    """
    canonical = normalize_symbol(ticker)
    try:
        fast = yf_retry(lambda: yf.Ticker(canonical).fast_info)
        return {
            "symbol": canonical,
            "last_price": fast.get("lastPrice"),
            "previous_close": fast.get("previousClose"),
            "day_high": fast.get("dayHigh"),
            "day_low": fast.get("dayLow"),
            "volume": fast.get("lastVolume"),
            "market_cap": fast.get("marketCap"),
        }
    except Exception as e:
        return {"symbol": canonical, "error": str(e)}


def get_analyst_recommendations(ticker: Annotated[str, "ticker symbol of the company"]):
    """Analyst rating distribution + recent upgrades/downgrades from yfinance."""
    canonical = normalize_symbol(ticker)
    try:
        ticker_obj = yf.Ticker(canonical)
        rec = yf_retry(lambda: ticker_obj.recommendations)
        changes = yf_retry(lambda: ticker_obj.upgrades_downgrades)

        parts = [f"# Analyst Recommendations for {canonical}\n"]
        if rec is not None and not rec.empty:
            parts.append("## Rating distribution by period\n" + rec.to_csv())
        else:
            parts.append("## Rating distribution: none reported\n")

        if changes is not None and not changes.empty:
            recent = changes.head(10)
            parts.append("## Recent rating changes (up to 10)\n" + recent.to_csv())
        else:
            parts.append("## Recent rating changes: none reported\n")

        return "\n".join(parts)
    except Exception as e:
        return f"Error retrieving analyst recommendations for {ticker}: {str(e)}"
