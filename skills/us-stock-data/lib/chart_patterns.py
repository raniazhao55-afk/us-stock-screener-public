"""General, widely-recognized technical analysis patterns — separate from
`technical_signals.py`'s user-specific RSI/MACD/MA confluence strategy. New
code, not ported from anywhere.

These are the five patterns proposed as a general framework before the user
shared their specific strategy, and were never actually implemented until now
(2026-08-23, flagged by the user as a real omission):

  1. Golden Cross / Death Cross: 50-day SMA crosses above/below 200-day SMA —
     classic long-term trend-regime signal (different timeframe than
     technical_signals.py's MA5/10/20 confluence, which is short-term).
  2. Standalone MACD golden/death cross: reported on its own, independent of
     RSI/MA agreement — useful when you want "did momentum just flip" without
     requiring the full 3-way confluence.
  3. MA alignment ("多头/空头排列"): whether 5/10/20/60-day SMAs are currently
     stacked in strictly ascending (bullish) or descending (bearish) order,
     as a standalone read — not gated behind MACD/RSI agreement the way
     technical_signals.py's entry/exit states are.
  4. Bollinger squeeze + breakout: bandwidth (width relative to the middle
     band) contracts to a local low (volatility compression), then price
     breaks outside a band — the classic "quiet before the move" setup.
  5. Volume-confirmed breakout: price closes at a fresh N-day high AND that
     day's volume exceeds a multiple of its 30-day average — a breakout
     without volume confirmation is weaker evidence than one with it.

Every function returns the values behind the verdict, not just a bare
True/False, matching the rest of this skill's auditability discipline.
"""

from __future__ import annotations

import pandas as pd
from stockstats import wrap

from .stockstats_utils import load_ohlcv

_MA_WINDOWS = (5, 10, 20, 60)


def _prepared_df(symbol: str, curr_date: str) -> pd.DataFrame:
    data = load_ohlcv(symbol, curr_date)
    df = wrap(data)
    df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
    for w in (5, 10, 20, 50, 60, 200):
        df[f"close_{w}_sma"]
    df["macd"]
    df["macds"]
    df["boll"]
    df["boll_ub"]
    df["boll_lb"]
    df["close_50_sma_xu_close_200_sma"]
    df["close_50_sma_xd_close_200_sma"]
    df["macd_xu_macds"]
    df["macd_xd_macds"]
    return df


def _row_at(df: pd.DataFrame, curr_date: str) -> pd.Series:
    idx = df.index[df["Date"] == curr_date]
    if len(idx) == 0:
        raise ValueError(f"{curr_date} not found in OHLCV data (holiday/weekend/no data yet)")
    return df.loc[idx[0]]


def _fired_dates(df: pd.DataFrame, row_idx: int, col: str, lookback: int) -> list[str]:
    start = max(0, row_idx - lookback + 1)
    win = df.iloc[start : row_idx + 1]
    return win.loc[win[col].fillna(False), "Date"].tolist()


# ---------------------------------------------------------------------------
# 1. Golden Cross / Death Cross (50/200)
# ---------------------------------------------------------------------------


def check_golden_death_cross(symbol: str, curr_date: str, lookback: int = 10) -> dict:
    """50日SMA上穿/下穿200日SMA。长周期趋势regime信号，和technical_signals.py的
    MA5/10/20短周期确认体系是不同的时间尺度。"""
    df = _prepared_df(symbol, curr_date)
    row = _row_at(df, curr_date)
    row_idx = row.name

    golden_dates = _fired_dates(df, row_idx, "close_50_sma_xu_close_200_sma", lookback)
    death_dates = _fired_dates(df, row_idx, "close_50_sma_xd_close_200_sma", lookback)

    state = "bullish" if row["close_50_sma"] > row["close_200_sma"] else "bearish"
    return {
        "pattern": "golden_death_cross",
        "current_state": state,
        "golden_cross_dates_in_lookback": golden_dates,
        "death_cross_dates_in_lookback": death_dates,
        "latest_values": {
            "close_50_sma": round(float(row["close_50_sma"]), 2),
            "close_200_sma": round(float(row["close_200_sma"]), 2),
        },
    }


# ---------------------------------------------------------------------------
# 2. Standalone MACD golden/death cross
# ---------------------------------------------------------------------------


def check_macd_cross(symbol: str, curr_date: str, lookback: int = 5) -> dict:
    """MACD单独金叉/死叉，不要求RSI/MA同时确认——用于只想知道'动量刚刚翻了没有'。"""
    df = _prepared_df(symbol, curr_date)
    row = _row_at(df, curr_date)
    row_idx = row.name

    golden_dates = _fired_dates(df, row_idx, "macd_xu_macds", lookback)
    death_dates = _fired_dates(df, row_idx, "macd_xd_macds", lookback)

    return {
        "pattern": "macd_cross",
        "current_state": "bullish" if row["macd"] > row["macds"] else "bearish",
        "golden_cross_dates_in_lookback": golden_dates,
        "death_cross_dates_in_lookback": death_dates,
        "latest_values": {"macd": round(float(row["macd"]), 4), "macds": round(float(row["macds"]), 4)},
    }


# ---------------------------------------------------------------------------
# 3. MA alignment ("多头/空头排列") — standalone, no MACD/RSI gate
# ---------------------------------------------------------------------------


def check_ma_alignment(symbol: str, curr_date: str) -> dict:
    """5/10/20/60日均线是否严格多头排列(依次递减)或空头排列(依次递增)。"""
    df = _prepared_df(symbol, curr_date)
    row = _row_at(df, curr_date)
    values = {w: float(row[f"close_{w}_sma"]) for w in _MA_WINDOWS}
    ordered_desc = list(values.values())
    is_bullish_aligned = ordered_desc == sorted(ordered_desc, reverse=True)
    is_bearish_aligned = ordered_desc == sorted(ordered_desc)

    alignment = "bullish" if is_bullish_aligned else "bearish" if is_bearish_aligned else "mixed"
    return {
        "pattern": "ma_alignment",
        "alignment": alignment,
        "price_above_all_mas": bool(all(row["close"] > v for v in values.values())),
        "price_below_all_mas": bool(all(row["close"] < v for v in values.values())),
        "latest_values": {f"close_{w}_sma": round(v, 2) for w, v in values.items()},
    }


# ---------------------------------------------------------------------------
# 4. Bollinger squeeze + breakout
# ---------------------------------------------------------------------------


def check_bollinger_squeeze_breakout(
    symbol: str,
    curr_date: str,
    squeeze_lookback: int = 120,
    squeeze_percentile: float = 0.2,
    breakout_window: int = 10,
) -> dict:
    """带宽((上轨-下轨)/中轨)收缩到近squeeze_lookback天的低位(squeeze_percentile分位以下)
    后，breakout_window天内价格是否突破上/下轨——波动率收缩后的方向性突破。"""
    df = _prepared_df(symbol, curr_date)
    row = _row_at(df, curr_date)
    row_idx = row.name

    df["bandwidth"] = (df["boll_ub"] - df["boll_lb"]) / df["boll"]
    hist_start = max(0, row_idx - squeeze_lookback)
    hist_bw = df.iloc[hist_start:row_idx]["bandwidth"]
    threshold = hist_bw.quantile(squeeze_percentile) if not hist_bw.empty else None

    win_start = max(0, row_idx - breakout_window + 1)
    win = df.iloc[win_start : row_idx + 1]
    was_squeezed = bool((win["bandwidth"] <= threshold).any()) if threshold is not None else False

    breakout_up = bool((win["close"] > win["boll_ub"]).any())
    breakout_down = bool((win["close"] < win["boll_lb"]).any())

    triggered = was_squeezed and (breakout_up or breakout_down)
    direction = "up" if breakout_up and not breakout_down else "down" if breakout_down and not breakout_up else "both" if (breakout_up and breakout_down) else None

    return {
        "pattern": "bollinger_squeeze_breakout",
        "triggered": triggered,
        "was_squeezed_in_window": was_squeezed,
        "breakout_direction": direction,
        "current_bandwidth": round(float(row["bandwidth"]) if "bandwidth" in row else float(df.loc[row_idx, "bandwidth"]), 4),
        "squeeze_threshold": round(float(threshold), 4) if threshold is not None else None,
    }


# ---------------------------------------------------------------------------
# 5. Volume-confirmed breakout
# ---------------------------------------------------------------------------


def check_volume_confirmed_breakout(
    symbol: str,
    curr_date: str,
    new_high_window: int = 20,
    volume_multiple: float = 1.5,
    avg_window: int = 30,
) -> dict:
    """价格创N日新高 + 当日成交量超过近avg_window日均量的volume_multiple倍——
    无量突破的可信度低于放量突破。"""
    df = _prepared_df(symbol, curr_date)
    row = _row_at(df, curr_date)
    row_idx = row.name

    hi_start = max(0, row_idx - new_high_window)
    prior_high = df.iloc[hi_start:row_idx]["close"].max() if row_idx > hi_start else float("-inf")
    is_fresh_high = bool(row["close"] > prior_high)

    vol_start = max(0, row_idx - avg_window)
    avg_vol = df.iloc[vol_start:row_idx]["volume"].mean() if row_idx > vol_start else None
    volume_ratio = float(row["volume"] / avg_vol) if avg_vol else None
    volume_confirmed = bool(volume_ratio is not None and volume_ratio >= volume_multiple)

    return {
        "pattern": "volume_confirmed_breakout",
        "triggered": bool(is_fresh_high and volume_confirmed),
        "is_fresh_high": is_fresh_high,
        f"is_fresh_{new_high_window}d_high": is_fresh_high,
        "volume_confirmed": volume_confirmed,
        "volume_ratio_vs_avg": round(volume_ratio, 2) if volume_ratio is not None else None,
        "latest_values": {
            "close": round(float(row["close"]), 2),
            "prior_high": round(float(prior_high), 2) if prior_high != float("-inf") else None,
            "volume": int(row["volume"]),
            "avg_volume": round(float(avg_vol), 0) if avg_vol else None,
        },
    }


def check_all_patterns(symbol: str, curr_date: str) -> dict:
    """Run all five and return one report."""
    return {
        "symbol": symbol,
        "date": curr_date,
        "golden_death_cross": check_golden_death_cross(symbol, curr_date),
        "macd_cross": check_macd_cross(symbol, curr_date),
        "ma_alignment": check_ma_alignment(symbol, curr_date),
        "bollinger_squeeze_breakout": check_bollinger_squeeze_breakout(symbol, curr_date),
        "volume_confirmed_breakout": check_volume_confirmed_breakout(symbol, curr_date),
    }
