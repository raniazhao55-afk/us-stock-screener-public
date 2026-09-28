"""RSI+MACD+MA confluence signals — a specific retail technical strategy the
user provided verbatim, illustrated with 6 chart images (2026-08-23), NOT a
general TA framework. New code, not ported from anywhere.

v2 (2026-08-23): rewritten after v1's strict "all three cross within the same
N-day window" reading proved too literal — real confluence rarely lands on
the same day (tested on MSFT's actual 7/29-30 breakout: RSI crossed 7/27, MA5
double-crossed 7/30, MACD had already crossed earlier — three different days,
zero hits under the v1 rule). The chart images clarify these are STATES that
persist over a range, not instants: chart 3 labels the MACD moment "零轴附近
张嘴" (mouths opening near the zero axis — a widening gap, not a single
cross tick), chart 1/2 show the three indicators running aligned across many
bars, not crossing together on one bar. v2 checks "is the aligned state true
today, and did it NOT hold `lookback` days ago" (state + recently-formed),
which matches what the charts actually show.

Source strategy, per the 6 images + the original text:
  图1/信号1 ENTRY-STATE ("上车"，多头排列持续): MA白(5)穿黄(10)紫(20)且价格在三线
      上方持续运行 + MACD白穿黄持续在上方运行(金叉状态) + RSI白穿黄紫持续在上方运行。
  图2/信号2 EXIT-STATE ("下车"，空头排列持续): 上面的镜像。
  图3/信号3 LAUNCH ("启动"): 价格站上短中期所有均线 + MACD零轴附近张嘴(金叉且DIF-DEA
      的差值在放大) + RSI三线金叉但还没冲太高("待上"，不是已经冲高)。
  图4/信号4 EXPLOSION ("爆发"): 一根阳线至少打穿三条均线并突破前期整理区间 + MACD空中
      金叉(已经运行在零轴上方较高处) + RSI三线金叉。
  图5 TOP REVERSAL WARNING (新增，原文字未提): MA5/MA10拐头向下交叉 + MACD在高位死叉
      且红柱(macdh)在死叉之前就已经连续缩量 + RSI做出双头形态后跌破两个高点之间的颈线。
  图6 BEARISH DIVERGENCE (新增，原文字未提): 价格创新高，但MACD/RSI在对应的高点没有
      跟着创新高(不断走低)——经典顶背离，用局部波峰比较判断，和前面的交叉事件是完全不同
      的算法(要先找波峰波谷，不是查某一天的数值穿越)。

QUANTIFICATION CHOICES (documented defaults — re-tune if they don't match your
own charting platform's exact settings):
  - White/Yellow/Purple RSI = RSI6/RSI12/RSI24 (confirmed with user 2026-08-23).
  - "MACD near the zero axis" vs "in the air" (chart 3 vs chart 4): classified
    by comparing |macd| against the 25th percentile of |macd| over the
    trailing 120 trading days. Below that percentile = near_zero; above it
    (and macd > 0) = in_the_air.
  - "最近才形成" (freshly formed, not stale): the combined bullish/bearish
    state's current run must not be longer than `lookback` trading days
    (default 10) — i.e. it must have actually turned True within the lookback
    window, not merely have been False at the single day exactly `lookback`
    days back (see `_fresh()`'s 2026-09-08 fix note: checking only that one
    point missed the case where an unrelated earlier flicker happened to sit
    at exactly that day). A state that's been running for weeks already isn't
    "just forming" — chasing it is a different risk profile than catching it
    early.
  - "K至少打穿三条均线" (chart 4): at least 3 of {MA5,10,20,60} pierced by one
    candle's real body (open below, close above), AND that close is a fresh
    local high (breaking a prior consolidation range) — approximated here as
    a new N-day high (default 20).
  - 图5/图6 peak-finding: a point is a local max/min if it equals the
    max/min of a centered window of size 2*order+1 (default order=3). "Double
    top" (图5): the two most recent RSI peaks within `peak_lookback` (default
    60 trading days) differ by no more than `double_top_tolerance` (default 3
    RSI points), and current RSI has broken below the trough between them
    ("颈线"/neckline). Divergence (图6): price's most recent swing high >
    the one before it, while MACD's value at the more recent high is LOWER
    than at the earlier high — the classic non-confirmation.

Every function returns the individual dates/values behind the verdict, not
just a bare True/False — a signal you can't audit is a signal you can't trust
when it's wrong.
"""

from __future__ import annotations

import pandas as pd
from stockstats import wrap

from .stockstats_utils import load_ohlcv

_MA_WINDOWS = (5, 10, 20, 60)
_NEAR_ZERO_PERCENTILE = 0.25
_MACD_HISTORY_LOOKBACK = 120


def _prepared_df(symbol: str, curr_date: str) -> pd.DataFrame:
    """OHLCV + every indicator column this module needs, computed once."""
    data = load_ohlcv(symbol, curr_date)
    df = wrap(data)
    df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")

    for w in _MA_WINDOWS:
        df[f"close_{w}_sma"]
    df["macd"]
    df["macds"]
    df["macdh"]
    df["rsi_6"]
    df["rsi_12"]
    df["rsi_24"]
    df["close_5_sma_xd_close_10_sma"]

    df["ma_bullish"] = (
        (df["close_5_sma"] > df["close_10_sma"])
        & (df["close_10_sma"] > df["close_20_sma"])
        & (df["close"] > df["close_5_sma"])
    )
    df["ma_bearish"] = (
        (df["close_5_sma"] < df["close_10_sma"])
        & (df["close_10_sma"] < df["close_20_sma"])
        & (df["close"] < df["close_5_sma"])
    )
    df["macd_bullish"] = df["macd"] > df["macds"]
    df["macd_bearish"] = df["macd"] < df["macds"]
    df["rsi_bullish"] = (df["rsi_6"] > df["rsi_12"]) & (df["rsi_12"] > df["rsi_24"]) & (df["rsi_6"] > 50)
    df["rsi_bearish"] = (df["rsi_6"] < df["rsi_12"]) & (df["rsi_12"] < df["rsi_24"]) & (df["rsi_6"] < 50)

    df["entry_state"] = df["ma_bullish"] & df["macd_bullish"] & df["rsi_bullish"]
    df["exit_state"] = df["ma_bearish"] & df["macd_bearish"] & df["rsi_bearish"]

    return df


def _row_at(df: pd.DataFrame, curr_date: str) -> pd.Series:
    idx = df.index[df["Date"] == curr_date]
    if len(idx) == 0:
        raise ValueError(f"{curr_date} not found in OHLCV data (holiday/weekend/no data yet)")
    return df.loc[idx[0]]


def _macd_cross_zone(df: pd.DataFrame, row_idx: int) -> str:
    """Classify the MACD level at row_idx as 'near_zero' or 'in_the_air'/'below_zero'."""
    hist_start = max(0, row_idx - _MACD_HISTORY_LOOKBACK)
    hist = df.iloc[hist_start:row_idx]["macd"].abs()
    threshold = hist.quantile(_NEAR_ZERO_PERCENTILE) if not hist.empty else 0.0
    macd_val = df.loc[row_idx, "macd"]
    if abs(macd_val) <= threshold:
        return "near_zero"
    return "in_the_air" if macd_val > 0 else "below_zero"


def _fresh(df: pd.DataFrame, row_idx: int, state_col: str, lookback: int) -> bool:
    """Is `state_col`'s current True-run (ending at row_idx) no longer than `lookback`
    trading days — i.e. did it actually start within the lookback window, rather than
    having been running continuously for longer than that?

    True `state` today + this returns True == the state just formed recently.

    2026-09-08 bug fix: the original implementation only checked whether state_col was
    False at the single point exactly `lookback` days before row_idx — not whether the
    run had actually been continuous since then. That breaks whenever state_col happened
    to also be True at that one specific earlier day for an unrelated reason (an earlier,
    already-ended run) and False in between: a genuine 1-day-old rising edge would get
    misclassified as stale purely because day `-lookback` happened to be True too. Found
    via backtest verification (strategy_backtest_pattern_signals_extended.json spot-checks,
    2026-09-08): 3 of 5 sampled dates had check_entry_signal() report triggered=False on a
    day where entry_state was unambiguously a fresh 1-day rising edge
    (entry_state[t]=True, entry_state[t-1]=False), solely because entry_state also
    happened to be True exactly `lookback` trading days earlier. Now walks backward
    counting the actual consecutive-True run length ending at row_idx, which is immune
    to that kind of unrelated earlier flicker.
    """
    if not bool(df.loc[row_idx, state_col]):
        return False  # state isn't even true today; "freshly formed" doesn't apply
    run_length = 0
    idx = row_idx
    while idx >= 0 and bool(df.loc[idx, state_col]):
        run_length += 1
        if run_length > lookback:
            return False  # state has been running longer than the lookback window
        idx -= 1
    return True  # ran out of history, or hit a False, before exceeding lookback


# ---------------------------------------------------------------------------
# 图1/图2: entry-state / exit-state (persistent alignment, recently formed)
# ---------------------------------------------------------------------------


def check_entry_signal(symbol: str, curr_date: str, lookback: int = 10) -> dict:
    """信号1('上车'/图1): MA/MACD/RSI三线同时处于多头对齐状态，且这个状态是lookback天内才形成的。"""
    df = _prepared_df(symbol, curr_date)
    row = _row_at(df, curr_date)
    row_idx = row.name

    triggered = bool(row["entry_state"]) and _fresh(df, row_idx, "entry_state", lookback)
    return {
        "signal": "entry",
        "triggered": triggered,
        "lookback_days": lookback,
        "evidence": {
            "ma_bullish_now": bool(row["ma_bullish"]),
            "macd_bullish_now": bool(row["macd_bullish"]),
            "rsi_bullish_now": bool(row["rsi_bullish"]),
            "state_was_false_lookback_days_ago": _fresh(df, row_idx, "entry_state", lookback),
            "latest_values": {
                "close_5_sma": round(float(row["close_5_sma"]), 2),
                "close_10_sma": round(float(row["close_10_sma"]), 2),
                "close_20_sma": round(float(row["close_20_sma"]), 2),
                "macd": round(float(row["macd"]), 4),
                "macds": round(float(row["macds"]), 4),
                "rsi_6": round(float(row["rsi_6"]), 2),
                "rsi_12": round(float(row["rsi_12"]), 2),
                "rsi_24": round(float(row["rsi_24"]), 2),
            },
        },
    }


def check_exit_signal(symbol: str, curr_date: str, lookback: int = 10) -> dict:
    """信号2('下车'/图2): MA/MACD/RSI三线同时处于空头对齐状态，且这个状态是lookback天内才形成的。"""
    df = _prepared_df(symbol, curr_date)
    row = _row_at(df, curr_date)
    row_idx = row.name

    triggered = bool(row["exit_state"]) and _fresh(df, row_idx, "exit_state", lookback)
    return {
        "signal": "exit",
        "triggered": triggered,
        "lookback_days": lookback,
        "evidence": {
            "ma_bearish_now": bool(row["ma_bearish"]),
            "macd_bearish_now": bool(row["macd_bearish"]),
            "rsi_bearish_now": bool(row["rsi_bearish"]),
            "state_was_false_lookback_days_ago": _fresh(df, row_idx, "exit_state", lookback),
            "latest_values": {
                "macd": round(float(row["macd"]), 4),
                "macds": round(float(row["macds"]), 4),
                "rsi_6": round(float(row["rsi_6"]), 2),
            },
        },
    }


# ---------------------------------------------------------------------------
# 图3: launch — near-zero MACD "mouth opening", price above all MAs, RSI just aligned
# ---------------------------------------------------------------------------


def check_launch_signal(symbol: str, curr_date: str, lookback: int = 10, widen_days: int = 5) -> dict:
    """信号3('启动'/图3): 价格站上所有短中期均线 + MACD零轴附近且DIF-DEA差值正在放大(张嘴)
    + RSI三线刚排好多头顺序但还没到高位("待上")。"""
    df = _prepared_df(symbol, curr_date)
    row = _row_at(df, curr_date)
    row_idx = row.name

    above_all_mas = all(row["close"] > row[f"close_{w}_sma"] for w in _MA_WINDOWS)

    zone = _macd_cross_zone(df, row_idx)
    gap = df["macd"] - df["macds"]
    widen_start = max(0, row_idx - widen_days)
    gap_widening = bool(gap.iloc[row_idx] > gap.iloc[widen_start]) if row_idx > widen_start else False
    macd_ok = zone == "near_zero" and bool(row["macd_bullish"]) and gap_widening

    rsi_ordered = bool(row["rsi_6"] > row["rsi_12"] > row["rsi_24"])
    rsi_not_yet_high = bool(row["rsi_6"] < 70)  # "待上" — just aligned, not already overbought
    rsi_ok = rsi_ordered and rsi_not_yet_high

    state_now = above_all_mas and macd_ok and rsi_ok
    was_fresh = _fresh(df, row_idx, "entry_state", lookback)  # reuse as a coarse staleness proxy
    triggered = bool(state_now)

    return {
        "signal": "launch",
        "triggered": triggered,
        "evidence": {
            "price_above_all_mas": above_all_mas,
            "macd_zone": zone,
            "macd_gap_widening": gap_widening,
            "macd_bullish_now": bool(row["macd_bullish"]),
            "rsi_ordered_bullish": rsi_ordered,
            "rsi_not_yet_overbought": rsi_not_yet_high,
            "note_recently_formed_proxy": was_fresh,
            "latest_values": {
                "close": round(float(row["close"]), 2),
                **{f"close_{w}_sma": round(float(row[f"close_{w}_sma"]), 2) for w in _MA_WINDOWS},
                "macd": round(float(row["macd"]), 4),
                "macds": round(float(row["macds"]), 4),
                "rsi_6": round(float(row["rsi_6"]), 2),
            },
        },
    }


# ---------------------------------------------------------------------------
# 图4: explosion — candle pierces >=3 MAs on a fresh N-day high, MACD already
# elevated (in_the_air), RSI ordered bullish (can already be running high).
# ---------------------------------------------------------------------------


def check_explosion_signal(symbol: str, curr_date: str, new_high_window: int = 20) -> dict:
    """信号4('爆发'/图4): 一根阳线打穿至少3条均线且收盘创N日新高(突破整理区间) + MACD空中金叉
    (已运行在零轴上方较高处) + RSI三线金叉(不要求"待上"，可以已经在冲高)。"""
    df = _prepared_df(symbol, curr_date)
    row = _row_at(df, curr_date)
    row_idx = row.name

    ma_values = {w: row[f"close_{w}_sma"] for w in _MA_WINDOWS}
    pierced = [w for w, v in ma_values.items() if row["open"] < v < row["close"]]
    is_bullish_candle = bool(row["close"] > row["open"])

    hist_start = max(0, row_idx - new_high_window)
    prior_high = df.iloc[hist_start:row_idx]["close"].max() if row_idx > hist_start else float("-inf")
    is_fresh_high = bool(row["close"] > prior_high)

    zone = _macd_cross_zone(df, row_idx)
    macd_ok = zone == "in_the_air" and bool(row["macd_bullish"])
    rsi_ordered = bool(row["rsi_6"] > row["rsi_12"] > row["rsi_24"])

    triggered = bool(is_bullish_candle and len(pierced) >= 3 and is_fresh_high and macd_ok and rsi_ordered)
    return {
        "signal": "explosion",
        "triggered": triggered,
        "evidence": {
            "is_bullish_candle": is_bullish_candle,
            "mas_pierced": pierced,
            "mas_pierced_count": len(pierced),
            f"is_fresh_{new_high_window}d_high": is_fresh_high,
            "macd_zone": zone,
            "macd_bullish_now": bool(row["macd_bullish"]),
            "rsi_ordered_bullish": rsi_ordered,
            "latest_values": {
                "open": round(float(row["open"]), 2),
                "close": round(float(row["close"]), 2),
                "prior_high": round(float(prior_high), 2) if prior_high != float("-inf") else None,
                "macd": round(float(row["macd"]), 4),
            },
        },
    }


# ---------------------------------------------------------------------------
# Peak/trough helpers for 图5/图6 (a genuinely different algorithm family —
# comparing local extrema, not reading a single day's value).
# ---------------------------------------------------------------------------


def _local_maxima(series: pd.Series, order: int = 3) -> pd.Series:
    roll_max = series.rolling(window=2 * order + 1, center=True).max()
    return series == roll_max


def _local_minima(series: pd.Series, order: int = 3) -> pd.Series:
    roll_min = series.rolling(window=2 * order + 1, center=True).min()
    return series == roll_min


def check_top_reversal_signal(
    symbol: str,
    curr_date: str,
    lookback: int = 10,
    peak_lookback: int = 60,
    order: int = 3,
    double_top_tolerance: float = 3.0,
) -> dict:
    """图5('顶部反转警告'，原文字未提): MA5/MA10拐头向下交叉 + MACD在高位死叉且红柱(macdh)
    在死叉前已连续缩量 + RSI做出双头后跌破两高点间的颈线。三个子条件独立判断，都成立才算触发。"""
    df = _prepared_df(symbol, curr_date)
    row = _row_at(df, curr_date)
    row_idx = row.name

    # 1. MA5/MA10 fresh death cross
    win_start = max(0, row_idx - lookback + 1)
    ma_cross_dates = df.loc[win_start:row_idx].loc[
        df.loc[win_start:row_idx, "close_5_sma_xd_close_10_sma"].fillna(False), "Date"
    ].tolist()

    # 2. MACD death cross at a high level, with macdh (histogram) shrinking
    #    for several days going into the cross.
    macdh_shrinking = False
    cross_zone = None
    if ma_cross_dates:
        # anchor the macd check near the same window as the MA cross
        recent = df.loc[win_start:row_idx]
        macdh_diffs = recent["macdh"].diff().dropna()
        macdh_shrinking = bool((macdh_diffs.tail(5) < 0).sum() >= 3)  # shrinking most of the last 5 days
        cross_zone = _macd_cross_zone(df, row_idx)
    macd_ok = bool(row["macd_bearish"]) and cross_zone == "in_the_air" and macdh_shrinking

    # 3. RSI double top + neckline break
    hist_start = max(0, row_idx - peak_lookback)
    hist = df.iloc[hist_start : row_idx + 1].reset_index(drop=True)
    maxima_mask = _local_maxima(hist["rsi_6"], order=order)
    peak_positions = hist.index[maxima_mask.fillna(False)].tolist()
    double_top = False
    neckline_break = False
    peak_info = []
    if len(peak_positions) >= 2:
        p1, p2 = peak_positions[-2], peak_positions[-1]
        v1, v2 = hist.loc[p1, "rsi_6"], hist.loc[p2, "rsi_6"]
        peak_info = [
            {"date": hist.loc[p1, "Date"], "rsi_6": round(float(v1), 2)},
            {"date": hist.loc[p2, "Date"], "rsi_6": round(float(v2), 2)},
        ]
        double_top = bool(abs(v2 - v1) <= double_top_tolerance or v2 <= v1)
        between = hist.loc[p1:p2, "rsi_6"]
        neckline = between.min() if not between.empty else None
        if double_top and neckline is not None:
            neckline_break = bool(hist["rsi_6"].iloc[-1] < neckline)

    triggered = bool(ma_cross_dates and macd_ok and double_top and neckline_break)
    return {
        "signal": "top_reversal",
        "triggered": triggered,
        "evidence": {
            "ma5_death_cross_dates": ma_cross_dates,
            "macd_bearish_now": bool(row["macd_bearish"]),
            "macd_zone_at_signal": cross_zone,
            "macdh_shrinking_into_cross": macdh_shrinking,
            "rsi_double_top": double_top,
            "rsi_peaks": peak_info,
            "rsi_neckline_break": neckline_break,
        },
    }


def check_bearish_divergence_signal(
    symbol: str,
    curr_date: str,
    peak_lookback: int = 90,
    order: int = 3,
) -> dict:
    """图6('顶背离'，原文字未提): 价格创新高，但对应时点的MACD/RSI没有跟着创新高——
    经典顶背离。用最近两个价格波峰做比较，波峰用局部极值算法找，不是逐日查交叉。"""
    df = _prepared_df(symbol, curr_date)
    row = _row_at(df, curr_date)
    row_idx = row.name

    hist_start = max(0, row_idx - peak_lookback)
    hist = df.iloc[hist_start : row_idx + 1].reset_index(drop=True)

    price_max_mask = _local_maxima(hist["close"], order=order)
    peak_positions = hist.index[price_max_mask.fillna(False)].tolist()

    result = {
        "signal": "bearish_divergence",
        "triggered": False,
        "evidence": {"reason": "not enough local price peaks in lookback window to compare"},
    }
    if len(peak_positions) < 2:
        return result

    p1, p2 = peak_positions[-2], peak_positions[-1]
    price1, price2 = hist.loc[p1, "close"], hist.loc[p2, "close"]
    macd1, macd2 = hist.loc[p1, "macd"], hist.loc[p2, "macd"]
    rsi1, rsi2 = hist.loc[p1, "rsi_6"], hist.loc[p2, "rsi_6"]

    price_higher_high = bool(price2 > price1)
    macd_lower_high = bool(macd2 < macd1)
    rsi_lower_high = bool(rsi2 < rsi1)

    triggered = bool(price_higher_high and macd_lower_high)  # MACD non-confirmation is the core signal
    result["triggered"] = triggered
    result["evidence"] = {
        "peak1": {"date": hist.loc[p1, "Date"], "close": round(float(price1), 2), "macd": round(float(macd1), 4), "rsi_6": round(float(rsi1), 2)},
        "peak2": {"date": hist.loc[p2, "Date"], "close": round(float(price2), 2), "macd": round(float(macd2), 4), "rsi_6": round(float(rsi2), 2)},
        "price_higher_high": price_higher_high,
        "macd_lower_high": macd_lower_high,
        "rsi_lower_high_confirmation": rsi_lower_high,
    }
    return result


# ---------------------------------------------------------------------------
# 月线+周线趋势闸门 + 周线关键支撑位价格止损 (新增 2026-08-31，用户提供的小红书
# "MACD六周期共振战法"截图，吸收其中"大周期定方向、小周期找点位"里两块可机械化的部分：
# (1) 月线+周线的MACD零轴多空闸门 (2) 周线关键支撑位价格止损。不是照搬完整的六周期
# (月/周/日/60分/15分/5分)体系——上面五个信号(entry/exit/launch/explosion/
# top_reversal/bearish_divergence)已经覆盖日线级别的多指标共振，这里只补最上面缺的
# 两层(月/周)。60分钟/15分钟/5分钟那三层日内精细化点位不在这个系统范围内：本系统是
# 隔日执行的swing/position trading节奏(Phase A收盘后跑、Phase B次日开盘后跑)，不是
# 日内交易，用不上分钟级别的点位。
# ---------------------------------------------------------------------------

_TREND_GATE_MACD_FAST = 12
_TREND_GATE_MACD_SLOW = 26
_TREND_GATE_MACD_SIGNAL = 9
_MIN_MONTHLY_BARS = 30  # 5年日K resample后正常有~60根月K；不足这个数当次新股处理
_MIN_WEEKLY_BARS = 40   # 5年日K resample后正常有~260根周K；不足这个数当次新股处理


def _resampled_macd(daily: pd.DataFrame, rule: str) -> pd.DataFrame:
    """把日线收盘价resample成`rule`('W-FRI'=周五收盘的周K，'ME'=月末收盘的月K)，
    标准MACD(12,26,9)，EMA用adjust=False(递归公式，和大多数看盘软件一致)。"""
    s = daily.set_index("Date")["close"].sort_index().resample(rule).last().dropna()
    ema_fast = s.ewm(span=_TREND_GATE_MACD_FAST, adjust=False).mean()
    ema_slow = s.ewm(span=_TREND_GATE_MACD_SLOW, adjust=False).mean()
    macd = ema_fast - ema_slow
    signal = macd.ewm(span=_TREND_GATE_MACD_SIGNAL, adjust=False).mean()
    hist = macd - signal
    return pd.DataFrame({"close": s, "macd": macd, "macds": signal, "macdh": hist})


def check_trend_gate(
    symbol: str,
    curr_date: str,
    weekly_support_lookback_weeks: int = 26,
    weekly_support_ma_period_weeks: int = 30,
    price_stop_loss_pct_below_support: float = 0.03,
    price_stop_loss_confirm_days: int = 3,
) -> dict:
    """月线+周线MACD零轴趋势闸门 + 周线关键支撑位价格止损。

    月线：MACD(12,26,9)在零轴(0)上方=多头，下方=空头——这是长期趋势方向，级别越大
    信号越少但越稳，样本量换来的是稳定性而不是灵敏度。
    周线：同样的MACD零轴判断 + 当前是金叉状态(macd>macds)还是死叉状态，是月线趋势的
    "波段强弱"验证层。两者组合成四档regime：
      strong_aligned    : 月多头+周金叉（强势波段，两个大周期共振向上）
      pullback          : 月多头+周死叉（短期回调，轻仓/快进快出）
      weak_rebound      : 月空头+周金叉（弱势反弹，绝不恋战）
      bearish_confirmed : 月空头+周死叉（月周共振向下，属于最强反方证据级别）
    历史不足`_MIN_MONTHLY_BARS`根月K或`_MIN_WEEKLY_BARS`根周K（通常是次新股）时
    regime为`insufficient_history`，不强行分类。

    周线关键支撑位 = max(最近`weekly_support_lookback_weeks`根周线收盘价的最低点,
    周线`weekly_support_ma_period_weeks`周简单均线)——结构性摆动低点和趋势性均线两个
    来源取更高(更保守)的一个。
    价格止损：最近`price_stop_loss_confirm_days`个交易日的**日线**收盘价是否连续低于
    支撑位×(1-`price_stop_loss_pct_below_support`)，连续满足才触发（单日插针不算，
    呼应用户提供截图里"跌破周线关键支撑位3%，且3天收不回"的确认窗口）。
    """
    daily = load_ohlcv(symbol, curr_date)[["Date", "Close"]].dropna().rename(columns={"Close": "close"})

    monthly = _resampled_macd(daily, "ME")
    weekly = _resampled_macd(daily, "W-FRI")

    result = {
        "symbol": symbol,
        "curr_date": curr_date,
        "monthly_bars": len(monthly),
        "weekly_bars": len(weekly),
    }

    if len(monthly) < _MIN_MONTHLY_BARS or len(weekly) < _MIN_WEEKLY_BARS:
        result.update({
            "trend_regime": "insufficient_history",
            "weekly_support_level": None,
            "pct_below_weekly_support": None,
            "price_stop_loss_triggered": False,
        })
        return result

    monthly_bullish = bool(monthly["macd"].iloc[-1] > 0)
    weekly_golden = bool(weekly["macd"].iloc[-1] > weekly["macds"].iloc[-1])
    weekly_zero_axis_bullish = bool(weekly["macd"].iloc[-1] > 0)

    if monthly_bullish and weekly_golden:
        regime = "strong_aligned"
    elif monthly_bullish and not weekly_golden:
        regime = "pullback"
    elif not monthly_bullish and weekly_golden:
        regime = "weak_rebound"
    else:
        regime = "bearish_confirmed"

    weekly_closes = weekly["close"]
    swing_low = float(weekly_closes.tail(weekly_support_lookback_weeks).min())
    if len(weekly_closes) >= weekly_support_ma_period_weeks:
        weekly_ma = float(weekly_closes.rolling(weekly_support_ma_period_weeks).mean().iloc[-1])
    else:
        weekly_ma = swing_low
    support_level = max(swing_low, weekly_ma)

    current_price = float(daily["close"].iloc[-1])
    pct_below_support = (support_level - current_price) / support_level

    stop_threshold = support_level * (1 - price_stop_loss_pct_below_support)
    trailing_closes = daily["close"].tail(price_stop_loss_confirm_days)
    price_stop_loss_triggered = bool(
        len(trailing_closes) == price_stop_loss_confirm_days
        and (trailing_closes < stop_threshold).all()
    )

    result.update({
        "trend_regime": regime,
        "evidence": {
            "monthly_macd": round(float(monthly["macd"].iloc[-1]), 4),
            "monthly_zero_axis": "above" if monthly_bullish else "below",
            "weekly_macd": round(float(weekly["macd"].iloc[-1]), 4),
            "weekly_macds": round(float(weekly["macds"].iloc[-1]), 4),
            "weekly_cross_state": "golden" if weekly_golden else "death",
            "weekly_zero_axis": "above" if weekly_zero_axis_bullish else "below",
        },
        "weekly_support_level": round(support_level, 2),
        "weekly_support_components": {
            "swing_low_lookback_weeks": weekly_support_lookback_weeks,
            "swing_low": round(swing_low, 2),
            "ma_period_weeks": weekly_support_ma_period_weeks,
            "weekly_ma": round(weekly_ma, 2),
        },
        "current_price": round(current_price, 2),
        "pct_below_weekly_support": round(pct_below_support, 4),
        "price_stop_loss_triggered": price_stop_loss_triggered,
        "price_stop_loss_params": {
            "pct_below_support_threshold": price_stop_loss_pct_below_support,
            "confirm_days": price_stop_loss_confirm_days,
            "trailing_closes": [round(float(v), 2) for v in trailing_closes.tolist()],
        },
    })
    return result


def check_all_signals(symbol: str, curr_date: str) -> dict:
    """Run all six and return one report — convenient for a batch scan."""
    return {
        "symbol": symbol,
        "date": curr_date,
        "entry": check_entry_signal(symbol, curr_date),
        "exit": check_exit_signal(symbol, curr_date),
        "launch": check_launch_signal(symbol, curr_date),
        "explosion": check_explosion_signal(symbol, curr_date),
        "top_reversal": check_top_reversal_signal(symbol, curr_date),
        "bearish_divergence": check_bearish_divergence_signal(symbol, curr_date),
    }
