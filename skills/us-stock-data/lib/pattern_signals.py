"""K线形态识别库 — 用户提供的小红书截图里列出的经典形态，第一批实现 (2026-09-03)。

截图声称"100多种"，但传统TA形态的算法定义本身就有争议(不同人画的头肩底/双底不完全
一样，"杯柄形态"更是几乎每本书给的量化标准都不同)——硬凑100种容易变成过拟合的伪
精确。这里只把截图明确点名的10个形态各自写清楚精确算法定义，跑通真实数据验证，
不会为了凑数量而加没有清晰定义的形态。跟 technical_signals.py 的既有风格一致：
每个函数返回 triggered + 具体证据(用到的数值/日期)，不是一个不可审计的布尔值。

源清单（用户提供截图，原文逐字）：
  平台突破、杯柄形态、双重底、头肩底、红三兵、上升三角形、下降楔形、口袋支点、
  看涨旗形、底部直升机、月线反转

QUANTIFICATION CHOICES（逐个形态的精确定义，全部可调参数化，默认值不是唯一答案）：

  platform_breakout(平台突破): 过去`platform_days`(默认15)个交易日的(最高价-最低价)/
    最低价 <= `max_range_pct`(默认10%)（横盘够窄），今日收盘价突破该窗口最高价，且
    今日成交量 >= 窗口内均量 × `volume_multiple`(默认1.5)。

  cup_and_handle(杯柄形态): 在`cup_lookback`(默认130个交易日≈6个月)窗口内找左侧高点
    (rim)、随后的最低点(trough)、以及trough后价格回升到rim附近后的浅回调(handle)。
    杯深(rim到trough跌幅)须落在`min_cup_depth`~`max_cup_depth`(默认12%~50%，取自
    William O'Neil经典区间的宽松版)之间；trough不能太靠窗口两端(左右各至少
    `min_cup_side_days`默认15个交易日)——用来排除V形反转，只认"圆弧底"；trough后
    价格须回升到rim的`handle_zone_pct`(默认15%)以内才算杯完成；随后`handle_min_days`
    ~`handle_max_days`(默认5~15个交易日)的浅回调(回调深度<=杯深的`handle_max_depth_ratio`
    默认50%)构成handle；今日收盘突破rim和handle区间高点中较高者，视为突破确认。

  double_bottom(双重底/"W"形): 复用technical_signals.py的_local_minima在**价格**(不是
    RSI)上找局部低点，最近两个低点相差不超过`tolerance_pct`(默认4%)，两低点之间的
    局部高点作为颈线，今日收盘突破颈线确认。跟check_top_reversal_signal的RSI双头逻辑
    是镜像算法，只是找底不找顶、用价格不用RSI。

  head_and_shoulders_bottom(头肩底): 找价格局部低点里最近的三个(左肩/头/右肩)，头部
    须是三者中最低，左右肩高度相差不超过`shoulder_tolerance_pct`(默认6%)且都比头部
    高出至少`min_head_depth_pct`(默认5%)；颈线取左肩-头之间、头-右肩之间两个局部高点
    中较高者；今日收盘突破颈线确认。

  three_white_soldiers(红三兵): 最近3根日K线全部阳线(收盘>开盘)，每根开盘价落在前一
    根实体内(prior_open < today_open < prior_close)，每根收盘价创新高(高于前一根收盘)，
    且上影线不过长(上影线长度 <= 实体长度 × `max_upper_wick_ratio`默认0.3，避免"冲高
    回落"式的假阳线)。

  ascending_triangle(上升三角形): `triangle_lookback`(默认40个交易日)窗口内，对局部
    高点做线性回归，斜率须接近0(|斜率|/价格均值 <= `flat_slope_threshold`默认0.001，
    平头阻力)；对局部低点做线性回归，斜率须明显为正(斜率/价格均值 >=
    `rising_slope_threshold`默认0.001，抬高的支撑)；今日收盘突破阻力位(局部高点回归
    线在今日的投影值)确认。

  falling_wedge(下降楔形): 同样窗口内对局部高点、局部低点分别做线性回归，两者斜率都
    须为负(下降)，且高点斜率比低点斜率更陡(更负)——即通道在收窄(收敛)；今日收盘突破
    上轨(高点回归线投影值)确认。

  pocket_pivot(口袋支点): 引用Gil Morales/Chris Kacher的原始定义(该形态在众多传统TA
    形态里定义最精确、争议最小)——今日为阳线(收盘>前收)，且今日成交量超过最近
    `lookback`(默认10)个交易日里任何一根阴线(收盘<前收)的成交量。距50日均线的偏离度
    作为辅助证据记录，不作为硬性触发条件(避免用自己加的约束稀释这个形态本来就精确
    的定义)。

  bullish_flag(看涨旗形): 先找`flagpole_days`(默认10个交易日)内的最大涨幅(旗杆)，须
    >= `flagpole_min_gain`(默认15%)且伴随放量；随后`flag_days`(默认5~15个交易日)进入
    窄幅盘整(旗面)，区间收窄、成交量较旗杆阶段萎缩(`flag_volume_ratio`默认<0.7)，且
    旗面本身走势平缓或略微下倾(回归斜率不能是大幅向上，否则不是整理而是继续拉升)；
    今日收盘突破旗面区间高点确认。

  base_accumulation(底部直升机——截图原文对这个形态的描述是"底部横盘后，连续小阳线
    堆量，成交量温和放大"，不是传统TA书里的标准名词，按截图原文字面定义实现):
    `decline_lookback`(默认60个交易日)内价格从阶段高点跌幅须 >= `min_prior_decline`
    (默认15%)；随后`base_days`(默认10~20个交易日)进入窄幅横盘(区间宽度同
    platform_breakout的判定)；该窗口内阳线占比 >= `min_bullish_ratio`(默认60%)；成交量
    "温和放大"量化为：窗口后半段均量 > 前半段均量，但没有任何单日成交量超过窗口均量
    的`max_single_day_spike_ratio`(默认3倍)——用来把"温和堆量"和"单日放量抢跑"区分开。

  monthly_reversal(月线反转): 用当前(可能未走完的)月份 vs 上一个**已完整走完**的月份
    比较——本月最低价 < 上月最低价(创出新低)，但本月最新收盘价 > 上月收盘价(价格已经
    收复回上月收盘之上)，是月线级别的"创新低但收复"的反转信号，呼应截图里"直升机
    形态"一节描述的"跌了很久后不再创新低"的精神，但落在月线这个更大级别上。

Every function returns triggered + the具体evidence(用到的数值/日期)，not just a bare
布尔值——跟technical_signals.py的既有约定一致，方便审计。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .stockstats_utils import load_ohlcv
from .technical_signals import _local_maxima, _local_minima, _prepared_df

_DEFAULT_ORDER = 3


def _slope(y: pd.Series, x: pd.Series | None = None) -> float:
    """OLS slope of y (per trading day). `x` defaults to y's own index position 0..n-1
    (fine when y is already a *dense*, consecutive series like a flag's daily closes).
    When y is a *sparse* set of extrema picked out of a wider window (ascending_triangle/
    falling_wedge), the caller MUST pass the extrema's actual row-position as `x` — fitting
    against 0..n-1 instead silently measures "change per extremum" and not "change per day",
    which blows up the slope whenever few extrema are spread across many days (found via
    2026-09-03 real-data testing: GOOGL's falling_wedge extrapolated a trendline to $243
    against an actual price of $339 because a 2-point fit used index positions 0/1 instead
    of the ~60 real trading days between them)."""
    if len(y) < 2:
        return 0.0
    x_vals = np.arange(len(y)) if x is None else np.asarray(x)
    return float(np.polyfit(x_vals, y.values, 1)[0])


# ---------------------------------------------------------------------------
# 平台突破 platform_breakout
# ---------------------------------------------------------------------------


def check_platform_breakout(
    symbol: str,
    curr_date: str,
    platform_days: int = 15,
    max_range_pct: float = 0.10,
    volume_multiple: float = 1.5,
) -> dict:
    """平台突破：过去platform_days窄幅整理(区间<=max_range_pct) + 今日放量突破区间高点。"""
    df = _prepared_df(symbol, curr_date)
    row = df.iloc[-1]
    window = df.iloc[-1 - platform_days : -1]
    if len(window) < platform_days:
        return {"signal": "platform_breakout", "triggered": False, "evidence": {"reason": "insufficient history"}}

    plat_high = float(window["high"].max())
    plat_low = float(window["low"].min())
    range_pct = (plat_high - plat_low) / plat_low if plat_low else float("inf")
    tight = range_pct <= max_range_pct

    avg_vol = float(window["volume"].mean())
    vol_ok = bool(row["volume"] >= avg_vol * volume_multiple) if avg_vol else False
    breakout = bool(row["close"] > plat_high)

    triggered = bool(tight and breakout and vol_ok)
    return {
        "signal": "platform_breakout",
        "triggered": triggered,
        "evidence": {
            "platform_range_pct": round(range_pct, 4),
            "platform_high": round(plat_high, 2),
            "platform_low": round(plat_low, 2),
            "today_close": round(float(row["close"]), 2),
            "today_volume": float(row["volume"]),
            "avg_platform_volume": round(avg_vol, 0),
            "tight_range": tight,
            "breakout": breakout,
            "volume_confirmed": vol_ok,
        },
    }


# ---------------------------------------------------------------------------
# 杯柄形态 cup_and_handle
# ---------------------------------------------------------------------------


def check_cup_and_handle(
    symbol: str,
    curr_date: str,
    cup_lookback: int = 130,
    min_cup_depth: float = 0.12,
    max_cup_depth: float = 0.50,
    min_cup_side_days: int = 15,
    handle_zone_pct: float = 0.15,
    handle_min_days: int = 5,
    handle_max_days: int = 15,
    handle_max_depth_ratio: float = 0.50,
) -> dict:
    """杯柄形态：左侧高点(rim) -> 圆弧底(trough，不能太靠窗口两端，排除V形) -> 回升近rim
    -> 浅回调(handle) -> 突破rim/handle区间高点。"""
    df = _prepared_df(symbol, curr_date)
    if len(df) < cup_lookback + 1:
        return {"signal": "cup_and_handle", "triggered": False, "evidence": {"reason": "insufficient history"}}

    window = df.iloc[-1 - cup_lookback : -1].reset_index(drop=True)
    row = df.iloc[-1]

    rim_idx = int(window["close"].idxmax())
    rim_val = float(window.loc[rim_idx, "close"])
    rim_date = window.loc[rim_idx, "Date"]

    post_rim = window.iloc[rim_idx:]
    if len(post_rim) < min_cup_side_days * 2:
        return {"signal": "cup_and_handle", "triggered": False, "evidence": {"reason": "rim too close to window end"}}

    trough_pos_in_post = int(post_rim["close"].idxmin() - post_rim.index[0])
    trough_idx = rim_idx + trough_pos_in_post
    trough_val = float(window.loc[trough_idx, "close"])
    trough_date = window.loc[trough_idx, "Date"]

    left_side_days = trough_idx - rim_idx
    right_side_days = (len(window) - 1) - trough_idx
    not_v_shaped = left_side_days >= min_cup_side_days and right_side_days >= min_cup_side_days

    cup_depth = (rim_val - trough_val) / rim_val if rim_val else 0.0
    depth_ok = min_cup_depth <= cup_depth <= max_cup_depth

    post_trough = window.iloc[trough_idx:]
    recovered = bool((post_trough["close"] >= rim_val * (1 - handle_zone_pct)).any())

    handle_ok = False
    handle_high = None
    if recovered and not post_trough.empty:
        recover_pos = post_trough.index[post_trough["close"] >= rim_val * (1 - handle_zone_pct)][0]
        handle_region = window.loc[recover_pos:]
        if handle_min_days <= len(handle_region) <= handle_max_days + 30:  # generous upper bound; classify below
            handle_high = float(handle_region["close"].max())
            handle_low = float(handle_region["close"].min())
            handle_depth = (handle_high - handle_low) / handle_high if handle_high else 1.0
            handle_ok = handle_depth <= cup_depth * handle_max_depth_ratio and len(handle_region) >= handle_min_days

    breakout_level = max(rim_val, handle_high) if handle_high else rim_val
    breakout = bool(row["close"] > breakout_level)

    triggered = bool(not_v_shaped and depth_ok and recovered and handle_ok and breakout)
    return {
        "signal": "cup_and_handle",
        "triggered": triggered,
        "evidence": {
            "rim_date": rim_date, "rim_value": round(rim_val, 2),
            "trough_date": trough_date, "trough_value": round(trough_val, 2),
            "cup_depth_pct": round(cup_depth, 4),
            "left_side_days": left_side_days, "right_side_days": right_side_days,
            "not_v_shaped": not_v_shaped, "depth_in_range": depth_ok, "recovered_near_rim": recovered,
            "handle_formed": handle_ok, "handle_high": round(handle_high, 2) if handle_high else None,
            "breakout_level": round(breakout_level, 2), "today_close": round(float(row["close"]), 2),
            "breakout": breakout,
        },
    }


# ---------------------------------------------------------------------------
# 双重底 double_bottom
# ---------------------------------------------------------------------------


def check_double_bottom(
    symbol: str,
    curr_date: str,
    peak_lookback: int = 90,
    order: int = 5,
    tolerance_pct: float = 0.04,
    min_separation_days: int = 10,
) -> dict:
    """双重底："W"形——最近两个价格局部低点相差不超过tolerance_pct、且相距至少
    min_separation_days个交易日(排除order参数下常见的"两个相邻几天的小波动被误判成
    双底"这种噪音——2026-09-03用MU真实数据验证时发现了这个问题：order=3时找到的
    "两个低点"只隔4个交易日，本质是同一次小回调里的噪音，不是真正的W形结构，因此把
    默认order从3提高到5、并新增这条最小间隔约束)，中间局部高点为颈线，今日突破颈线
    确认。算法上是check_top_reversal_signal里RSI双头逻辑的镜像(找底不找顶、用价格不用
    RSI)。"""
    df = _prepared_df(symbol, curr_date)
    row = df.iloc[-1]
    row_idx = df.index[-1]

    hist_start = max(0, row_idx - peak_lookback)
    hist = df.iloc[hist_start : row_idx + 1].reset_index(drop=True)

    minima_mask = _local_minima(hist["close"], order=order)
    raw_troughs = hist.index[minima_mask.fillna(False)].tolist()
    trough_positions: list[int] = []
    for p in raw_troughs:
        if not trough_positions or p - trough_positions[-1] >= min_separation_days:
            trough_positions.append(p)

    if len(trough_positions) < 2:
        return {"signal": "double_bottom", "triggered": False, "evidence": {"reason": "not enough sufficiently-separated local troughs"}}

    p1, p2 = trough_positions[-2], trough_positions[-1]
    v1, v2 = hist.loc[p1, "close"], hist.loc[p2, "close"]
    within_tolerance = bool(abs(v2 - v1) / v1 <= tolerance_pct) if v1 else False

    between = hist.loc[p1:p2, "close"]
    neckline = float(between.max()) if not between.empty else None
    breakout = bool(neckline is not None and hist["close"].iloc[-1] > neckline)

    triggered = bool(within_tolerance and breakout)
    return {
        "signal": "double_bottom",
        "triggered": triggered,
        "evidence": {
            "trough1": {"date": hist.loc[p1, "Date"], "close": round(float(v1), 2)},
            "trough2": {"date": hist.loc[p2, "Date"], "close": round(float(v2), 2)},
            "within_tolerance": within_tolerance,
            "neckline": round(neckline, 2) if neckline else None,
            "breakout": breakout,
        },
    }


# ---------------------------------------------------------------------------
# 头肩底 head_and_shoulders_bottom
# ---------------------------------------------------------------------------


def check_head_and_shoulders_bottom(
    symbol: str,
    curr_date: str,
    peak_lookback: int = 120,
    order: int = 5,
    shoulder_tolerance_pct: float = 0.06,
    min_head_depth_pct: float = 0.05,
    min_separation_days: int = 8,
) -> dict:
    """头肩底：左肩/头/右肩三个局部低点(彼此至少间隔min_separation_days个交易日，避免
    像double_bottom发现的那样把同一次小回调里的噪音误判成三个独立的谷——同样的修正，
    见check_double_bottom的说明)，头部最低，左右肩高度相差不超过shoulder_tolerance_pct
    且都比头部高出至少min_head_depth_pct；颈线取两个局部高点(左肩-头之间、头-右肩之间)
    中较高者，今日突破颈线确认。"""
    df = _prepared_df(symbol, curr_date)
    row_idx = df.index[-1]
    hist_start = max(0, row_idx - peak_lookback)
    hist = df.iloc[hist_start : row_idx + 1].reset_index(drop=True)

    minima_mask = _local_minima(hist["close"], order=order)
    maxima_mask = _local_maxima(hist["close"], order=order)
    raw_troughs = hist.index[minima_mask.fillna(False)].tolist()
    peaks = hist.index[maxima_mask.fillna(False)].tolist()
    troughs: list[int] = []
    for p in raw_troughs:
        if not troughs or p - troughs[-1] >= min_separation_days:
            troughs.append(p)

    if len(troughs) < 3:
        return {"signal": "head_and_shoulders_bottom", "triggered": False, "evidence": {"reason": "not enough sufficiently-separated local troughs"}}

    ls, head, rs = troughs[-3], troughs[-2], troughs[-1]
    v_ls, v_head, v_rs = hist.loc[ls, "close"], hist.loc[head, "close"], hist.loc[rs, "close"]

    head_is_lowest = bool(v_head < v_ls and v_head < v_rs)
    shoulder_diff = abs(v_ls - v_rs) / min(v_ls, v_rs) if min(v_ls, v_rs) else 1.0
    shoulders_similar = bool(shoulder_diff <= shoulder_tolerance_pct)
    head_depth_ls = (v_ls - v_head) / v_ls if v_ls else 0.0
    head_depth_rs = (v_rs - v_head) / v_rs if v_rs else 0.0
    head_deep_enough = bool(head_depth_ls >= min_head_depth_pct and head_depth_rs >= min_head_depth_pct)

    neck_candidates = [p for p in peaks if ls < p < rs]
    neckline = float(hist.loc[neck_candidates, "close"].max()) if neck_candidates else None
    breakout = bool(neckline is not None and hist["close"].iloc[-1] > neckline)

    triggered = bool(head_is_lowest and shoulders_similar and head_deep_enough and breakout)
    return {
        "signal": "head_and_shoulders_bottom",
        "triggered": triggered,
        "evidence": {
            "left_shoulder": {"date": hist.loc[ls, "Date"], "close": round(float(v_ls), 2)},
            "head": {"date": hist.loc[head, "Date"], "close": round(float(v_head), 2)},
            "right_shoulder": {"date": hist.loc[rs, "Date"], "close": round(float(v_rs), 2)},
            "head_is_lowest": head_is_lowest, "shoulders_similar": shoulders_similar,
            "head_deep_enough": head_deep_enough,
            "neckline": round(neckline, 2) if neckline else None, "breakout": breakout,
        },
    }


# ---------------------------------------------------------------------------
# 红三兵 three_white_soldiers
# ---------------------------------------------------------------------------


def check_three_white_soldiers(symbol: str, curr_date: str, max_upper_wick_ratio: float = 0.30) -> dict:
    """红三兵：最近3根日K全阳线，每根开盘价落在前一根实体内，每根收盘创新高，上影线
    不超过实体长度的max_upper_wick_ratio(避免冲高回落的假阳线)。"""
    df = _prepared_df(symbol, curr_date)
    if len(df) < 4:
        return {"signal": "three_white_soldiers", "triggered": False, "evidence": {"reason": "insufficient history"}}

    last3 = df.iloc[-3:].reset_index(drop=True)
    checks = []
    all_bullish = True
    for i in range(3):
        o, c, h = float(last3.loc[i, "open"]), float(last3.loc[i, "close"]), float(last3.loc[i, "high"])
        bullish = c > o
        body = c - o if bullish else 0.0
        upper_wick = h - c
        wick_ok = bool(upper_wick <= body * max_upper_wick_ratio) if body > 0 else False
        opens_within_prior = True
        higher_close = True
        if i > 0:
            prior_o, prior_c = float(last3.loc[i - 1, "open"]), float(last3.loc[i - 1, "close"])
            opens_within_prior = bool(min(prior_o, prior_c) < o < max(prior_o, prior_c))
            higher_close = bool(c > prior_c)
        ok = bullish and wick_ok and opens_within_prior and higher_close
        all_bullish = all_bullish and ok
        checks.append({
            "date": last3.loc[i, "Date"], "open": round(o, 2), "close": round(c, 2),
            "bullish": bullish, "wick_ok": wick_ok, "opens_within_prior_body": opens_within_prior,
            "higher_close": higher_close,
        })

    return {"signal": "three_white_soldiers", "triggered": bool(all_bullish), "evidence": {"candles": checks}}


# ---------------------------------------------------------------------------
# 上升三角形 ascending_triangle / 下降楔形 falling_wedge (share the regression helper)
# ---------------------------------------------------------------------------


_MIN_TRENDLINE_EXTREMA = 3  # a 2-point fit is degenerate (any two points define a "line");
# require >=3 so the slope reflects a real trend, not just the gap between two arbitrary points


def _trendline_slopes(df: pd.DataFrame, lookback: int, order: int) -> tuple[float, float, float, pd.DataFrame]:
    """Return (high_slope, low_slope, price_scale, window, maxima, minima) for the trailing
    `lookback` window. Slopes are fit against each extremum's actual row-position (day offset
    within the window), not its sequential rank among extrema — see _slope()'s docstring for
    why that distinction matters. Requires >=_MIN_TRENDLINE_EXTREMA points per side; fewer than
    that returns slope=0 (treated as "no trend established") rather than a degenerate 2-point
    line."""
    row_idx = df.index[-1]
    hist_start = max(0, row_idx - lookback)
    window = df.iloc[hist_start:row_idx].reset_index(drop=True)  # exclude today (today tests the breakout)
    maxima = window[_local_maxima(window["high"], order=order).fillna(False)]
    minima = window[_local_minima(window["low"], order=order).fillna(False)]
    high_slope = _slope(maxima["high"], x=maxima.index) if len(maxima) >= _MIN_TRENDLINE_EXTREMA else 0.0
    low_slope = _slope(minima["low"], x=minima.index) if len(minima) >= _MIN_TRENDLINE_EXTREMA else 0.0
    price_scale = float(window["close"].mean()) if not window.empty else 1.0
    return high_slope, low_slope, price_scale, window, maxima, minima


def check_ascending_triangle(
    symbol: str,
    curr_date: str,
    triangle_lookback: int = 40,
    order: int = _DEFAULT_ORDER,
    flat_slope_threshold: float = 0.001,
    rising_slope_threshold: float = 0.001,
) -> dict:
    """上升三角形：局部高点回归斜率接近0(平头阻力)，局部低点回归斜率明显为正(抬高的
    支撑)，今日突破阻力位确认。"""
    df = _prepared_df(symbol, curr_date)
    row = df.iloc[-1]
    high_slope, low_slope, scale, window, maxima, minima = _trendline_slopes(df, triangle_lookback, order)
    if len(maxima) < _MIN_TRENDLINE_EXTREMA or len(minima) < _MIN_TRENDLINE_EXTREMA or scale == 0:
        return {"signal": "ascending_triangle", "triggered": False, "evidence": {"reason": "not enough local extrema"}}

    norm_high_slope = high_slope / scale
    norm_low_slope = low_slope / scale
    flat_resistance = bool(abs(norm_high_slope) <= flat_slope_threshold)
    rising_support = bool(norm_low_slope >= rising_slope_threshold)
    resistance_level = float(maxima["high"].mean())
    breakout = bool(row["close"] > resistance_level)

    triggered = bool(flat_resistance and rising_support and breakout)
    return {
        "signal": "ascending_triangle",
        "triggered": triggered,
        "evidence": {
            "norm_high_slope": round(norm_high_slope, 6), "norm_low_slope": round(norm_low_slope, 6),
            "flat_resistance": flat_resistance, "rising_support": rising_support,
            "resistance_level": round(resistance_level, 2), "today_close": round(float(row["close"]), 2),
            "breakout": breakout,
        },
    }


def check_falling_wedge(
    symbol: str,
    curr_date: str,
    wedge_lookback: int = 40,
    order: int = _DEFAULT_ORDER,
) -> dict:
    """下降楔形：局部高点、局部低点回归斜率都为负，且高点斜率更陡(通道收敛)，今日突破
    上轨确认。"""
    df = _prepared_df(symbol, curr_date)
    row = df.iloc[-1]
    row_idx = df.index[-1]
    high_slope, low_slope, scale, window, maxima, minima = _trendline_slopes(df, wedge_lookback, order)
    if len(maxima) < _MIN_TRENDLINE_EXTREMA or len(minima) < _MIN_TRENDLINE_EXTREMA or scale == 0:
        return {"signal": "falling_wedge", "triggered": False, "evidence": {"reason": "not enough local extrema"}}

    both_declining = bool(high_slope < 0 and low_slope < 0)
    converging = bool(high_slope < low_slope)  # high_slope more negative == steeper decline
    upper_trendline_today = float(maxima["high"].iloc[-1]) + high_slope * (len(window) - int(maxima.index[-1]))
    breakout = bool(row["close"] > upper_trendline_today)

    triggered = bool(both_declining and converging and breakout)
    return {
        "signal": "falling_wedge",
        "triggered": triggered,
        "evidence": {
            "high_slope": round(high_slope, 4), "low_slope": round(low_slope, 4),
            "both_declining": both_declining, "converging": converging,
            "upper_trendline_projection": round(upper_trendline_today, 2),
            "today_close": round(float(row["close"]), 2), "breakout": breakout,
        },
    }


# ---------------------------------------------------------------------------
# 口袋支点 pocket_pivot
# ---------------------------------------------------------------------------


def check_pocket_pivot(symbol: str, curr_date: str, lookback: int = 10) -> dict:
    """口袋支点：今日阳线(收盘>前收)，且今日成交量超过最近lookback个交易日里任何一根
    阴线的成交量。原始定义(Gil Morales/Chris Kacher)本身就精确，不额外加自定义约束——
    距50日均线的偏离度只作为辅助证据记录。"""
    df = _prepared_df(symbol, curr_date)
    if len(df) < lookback + 2:
        return {"signal": "pocket_pivot", "triggered": False, "evidence": {"reason": "insufficient history"}}

    row = df.iloc[-1]
    prior_close = float(df.iloc[-2]["close"])
    up_day = bool(row["close"] > prior_close)

    window = df.iloc[-1 - lookback : -1].copy()
    window["prior_close"] = window["close"].shift(1)
    down_days = window[window["close"] < window["prior_close"]]
    max_down_volume = float(down_days["volume"].max()) if not down_days.empty else 0.0

    volume_confirmed = bool(row["volume"] > max_down_volume)
    triggered = bool(up_day and volume_confirmed)

    ma50 = df["close_60_sma"].iloc[-1] if "close_60_sma" in df.columns else None  # 60d proxy for 50d
    pct_above_ma = round((float(row["close"]) - float(ma50)) / float(ma50), 4) if ma50 else None

    return {
        "signal": "pocket_pivot",
        "triggered": triggered,
        "evidence": {
            "up_day": up_day, "today_volume": float(row["volume"]),
            "max_down_day_volume_in_window": max_down_volume, "volume_confirmed": volume_confirmed,
            "pct_above_60d_ma_supplementary": pct_above_ma,
        },
    }


# ---------------------------------------------------------------------------
# 看涨旗形 bullish_flag
# ---------------------------------------------------------------------------


def check_bullish_flag(
    symbol: str,
    curr_date: str,
    flagpole_days: int = 10,
    flagpole_min_gain: float = 0.15,
    flag_days_min: int = 5,
    flag_days_max: int = 15,
    flag_volume_ratio: float = 0.70,
) -> dict:
    """看涨旗形：flagpole_days内涨幅>=flagpole_min_gain(旗杆，需放量)，随后flag_days_min~
    flag_days_max个交易日窄幅盘整(旗面，缩量、走势平缓或略微下倾)，今日突破旗面区间高点
    确认。"""
    df = _prepared_df(symbol, curr_date)
    row_idx = df.index[-1]
    row = df.iloc[-1]

    best = None
    for flag_days in range(flag_days_min, flag_days_max + 1):
        flag_start = row_idx - flag_days
        pole_start = flag_start - flagpole_days
        if pole_start < 0:
            continue
        pole = df.iloc[pole_start:flag_start]
        flag = df.iloc[flag_start:row_idx]
        if pole.empty or flag.empty:
            continue

        pole_gain = (float(pole["close"].iloc[-1]) - float(pole["close"].iloc[0])) / float(pole["close"].iloc[0])
        pole_avg_vol = float(pole["volume"].mean())
        flag_avg_vol = float(flag["volume"].mean())
        flag_high = float(flag["high"].max())
        flag_low = float(flag["low"].min())
        flag_slope = _slope(flag["close"]) / float(flag["close"].mean()) if float(flag["close"].mean()) else 0.0

        pole_ok = pole_gain >= flagpole_min_gain
        flag_volume_ok = bool(flag_avg_vol <= pole_avg_vol * flag_volume_ratio) if pole_avg_vol else False
        flag_flat_or_down = bool(flag_slope <= 0.002)  # allow near-flat, disqualify a still-steep uptrend
        breakout = bool(row["close"] > flag_high)

        candidate = {
            "flag_days": flag_days, "pole_gain": round(pole_gain, 4), "pole_ok": pole_ok,
            "flag_volume_ratio": round(flag_avg_vol / pole_avg_vol, 4) if pole_avg_vol else None,
            "flag_volume_ok": flag_volume_ok, "flag_flat_or_down": flag_flat_or_down,
            "flag_high": round(flag_high, 2), "flag_low": round(flag_low, 2), "breakout": breakout,
            "triggered": bool(pole_ok and flag_volume_ok and flag_flat_or_down and breakout),
        }
        if best is None or candidate["triggered"]:
            best = candidate
            if candidate["triggered"]:
                break

    if best is None:
        return {"signal": "bullish_flag", "triggered": False, "evidence": {"reason": "insufficient history"}}
    return {"signal": "bullish_flag", "triggered": best["triggered"], "evidence": best}


# ---------------------------------------------------------------------------
# 底部直升机 base_accumulation (截图原文命名，非标准TA术语，按截图字面定义实现)
# ---------------------------------------------------------------------------


def check_base_accumulation(
    symbol: str,
    curr_date: str,
    decline_lookback: int = 60,
    min_prior_decline: float = 0.15,
    base_days: int = 15,
    min_bullish_ratio: float = 0.60,
    max_single_day_spike_ratio: float = 3.0,
) -> dict:
    """底部直升机(截图原文描述"底部横盘后，连续小阳线堆量，成交量温和放大")：
    decline_lookback内跌幅>=min_prior_decline，随后base_days窄幅横盘、阳线占比
    >=min_bullish_ratio、后半段均量>前半段均量但没有单日暴量(<=均量的
    max_single_day_spike_ratio倍)——跟pocket_pivot/platform_breakout的"单日放量"逻辑
    刻意区分开，专门抓"温和堆量"这种更缓慢的资金介入方式。"""
    df = _prepared_df(symbol, curr_date)
    row_idx = df.index[-1]
    if row_idx < decline_lookback + base_days:
        return {"signal": "base_accumulation", "triggered": False, "evidence": {"reason": "insufficient history"}}

    decline_window = df.iloc[row_idx - decline_lookback - base_days : row_idx - base_days]
    base_window = df.iloc[row_idx - base_days : row_idx]

    decline_high = float(decline_window["close"].max())
    decline_low = float(base_window["close"].min())
    decline_pct = (decline_high - decline_low) / decline_high if decline_high else 0.0
    decline_ok = decline_pct >= min_prior_decline

    base_high = float(base_window["high"].max())
    base_low = float(base_window["low"].min())
    base_range_pct = (base_high - base_low) / base_low if base_low else float("inf")

    bullish_days = int((base_window["close"] > base_window["open"]).sum())
    bullish_ratio = bullish_days / len(base_window)
    bullish_ok = bullish_ratio >= min_bullish_ratio

    half = len(base_window) // 2
    first_half_vol = float(base_window.iloc[:half]["volume"].mean()) if half else 0.0
    second_half_vol = float(base_window.iloc[half:]["volume"].mean())
    volume_expanding = bool(second_half_vol > first_half_vol)
    window_avg_vol = float(base_window["volume"].mean())
    no_spike = bool((base_window["volume"] <= window_avg_vol * max_single_day_spike_ratio).all()) if window_avg_vol else False

    triggered = bool(decline_ok and bullish_ok and volume_expanding and no_spike)
    return {
        "signal": "base_accumulation",
        "triggered": triggered,
        "evidence": {
            "prior_decline_pct": round(decline_pct, 4), "decline_ok": decline_ok,
            "base_range_pct": round(base_range_pct, 4),
            "bullish_day_ratio": round(bullish_ratio, 4), "bullish_ok": bullish_ok,
            "first_half_avg_volume": round(first_half_vol, 0), "second_half_avg_volume": round(second_half_vol, 0),
            "volume_gently_expanding": volume_expanding, "no_single_day_spike": no_spike,
        },
    }


# ---------------------------------------------------------------------------
# 月线反转 monthly_reversal
# ---------------------------------------------------------------------------


def check_monthly_reversal(symbol: str, curr_date: str) -> dict:
    """月线反转：当前(可能未走完的)月份最低价 < 上一个已完整走完月份的最低价(创新低)，
    但当前最新收盘价 > 上月收盘价(已收复回上月收盘之上)——月线级别的"创新低但收复"信号。"""
    daily = load_ohlcv(symbol, curr_date)[["Date", "Close", "High", "Low"]].dropna()
    daily = daily.rename(columns={"Close": "close", "High": "high", "Low": "low"}).sort_values("Date")
    daily["month"] = daily["Date"].dt.to_period("M")

    months = daily["month"].unique()
    if len(months) < 2:
        return {"signal": "monthly_reversal", "triggered": False, "evidence": {"reason": "insufficient history"}}

    curr_month = months[-1]
    prior_month = months[-2]
    curr_rows = daily[daily["month"] == curr_month]
    prior_rows = daily[daily["month"] == prior_month]

    curr_low = float(curr_rows["low"].min())
    prior_low = float(prior_rows["low"].min())
    curr_close = float(curr_rows["close"].iloc[-1])
    prior_close = float(prior_rows["close"].iloc[-1])

    new_low = bool(curr_low < prior_low)
    reclaimed = bool(curr_close > prior_close)

    triggered = bool(new_low and reclaimed)
    return {
        "signal": "monthly_reversal",
        "triggered": triggered,
        "evidence": {
            "current_month": str(curr_month), "current_month_low": round(curr_low, 2),
            "current_month_latest_close": round(curr_close, 2),
            "prior_month": str(prior_month), "prior_month_low": round(prior_low, 2),
            "prior_month_close": round(prior_close, 2),
            "made_new_low": new_low, "reclaimed_prior_close": reclaimed,
        },
    }


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------


def check_all_patterns(symbol: str, curr_date: str) -> dict:
    """Run all 11 pattern signals and return one report — mirrors technical_signals.check_all_signals()."""
    return {
        "symbol": symbol,
        "date": curr_date,
        "platform_breakout": check_platform_breakout(symbol, curr_date),
        "cup_and_handle": check_cup_and_handle(symbol, curr_date),
        "double_bottom": check_double_bottom(symbol, curr_date),
        "head_and_shoulders_bottom": check_head_and_shoulders_bottom(symbol, curr_date),
        "three_white_soldiers": check_three_white_soldiers(symbol, curr_date),
        "ascending_triangle": check_ascending_triangle(symbol, curr_date),
        "falling_wedge": check_falling_wedge(symbol, curr_date),
        "pocket_pivot": check_pocket_pivot(symbol, curr_date),
        "bullish_flag": check_bullish_flag(symbol, curr_date),
        "base_accumulation": check_base_accumulation(symbol, curr_date),
        "monthly_reversal": check_monthly_reversal(symbol, curr_date),
    }
