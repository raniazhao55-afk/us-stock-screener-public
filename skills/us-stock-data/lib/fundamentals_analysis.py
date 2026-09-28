"""Mechanical fundamentals checks that don't require LLM judgment calls:
quality-screen gate (7 hard metrics + 3 exemptions), balance-sheet anomaly
detection, and an earnings-surprise-history wrapper.

Methodology for get_quality_screen()/get_balance_sheet_anomalies() adapted
2026-09-11 from xbtlin/ai-berkshire (skills/quality-screen.md and
skills/earnings-review.md sec.4.2, MIT License, Copyright (c) 2026 xbtlin) —
this is a from-scratch reimplementation of their *methodology* against
yfinance data, not a code port (their skill assumes WebSearch-sourced 10-year
figures; this pulls structured numbers from yfinance instead).

**Known data ceiling**: yfinance's free annual financials/balance_sheet/
cashflow endpoints return at most ~5 fiscal years (verified 2026-09-11 on
AAPL: 2021-2025, 5 columns). The original ai-berkshire skill specifies
10-year averages for ROE/net margin. We do NOT have a free 10-year source, so
every function here computes over `min(requested_years, years_available)`
and reports `years_used` explicitly in the output — never silently pretends
to have covered 10 years when it only had 5. Callers (PHASE_A_TASK.md) must
surface `years_used` in the disclosed result, not just the pass/fail verdict.
"""

from typing import Annotated

import yfinance as yf

from .stockstats_utils import yf_retry
from .symbol_utils import NoMarketDataError, normalize_symbol

_TARGET_YEARS = 10  # what ai-berkshire's original skill asks for
_MIN_USABLE_YEARS = 3  # below this, the average is too noisy to gate on


def _row(df, *names):
    """Return the first matching row (as a pandas Series) from a yfinance
    statement DataFrame, trying each candidate label in order. yfinance's
    row labels vary slightly across tickers/periods (e.g. some years carry
    'Net Income' vs 'Net Income Common Stockholders')."""
    if df is None or df.empty:
        return None
    for name in names:
        if name in df.index:
            return df.loc[name]
    return None


def _to_float(v):
    try:
        f = float(v)
        return f if f == f else None  # filter NaN
    except (TypeError, ValueError):
        return None


def get_quality_screen(
    ticker: Annotated[str, "ticker symbol of the company"],
) -> dict:
    """7-metric mechanical quality gate (ai-berkshire quality-screen.md),
    computed from yfinance's annual financials/balance_sheet/cashflow.

    Returns a dict with each metric's value + pass/fail against the
    original thresholds, `years_used` (almost always <=5, see module
    docstring), and `exemption_candidates` — mechanically-checkable
    exemption conditions (A: pre-ROE strategic-investment exemption, B:
    deliberate-low-margin exemption) are evaluated; exemption C
    (membership/platform/high-turnover business model) needs a qualitative
    call this function cannot make and is only flagged as "check manually".

    This is a DISCLOSURE tool, not an auto-veto — PHASE_A_TASK.md decides
    how a fail-without-exemption result affects conviction, this function
    only computes the numbers.
    """
    canonical = normalize_symbol(ticker)
    try:
        t = yf.Ticker(canonical)
        fin = yf_retry(lambda: t.financials)
        bs = yf_retry(lambda: t.balance_sheet)
        cf = yf_retry(lambda: t.cashflow)

        if fin is None or fin.empty:
            raise NoMarketDataError(ticker, canonical, "no annual financials for quality screen")

        years = list(fin.columns)
        years_used = len(years)

        net_income = _row(fin, "Net Income", "Net Income Common Stockholders")
        revenue = _row(fin, "Total Revenue", "Operating Revenue")
        gross_profit = _row(fin, "Gross Profit")
        ebit = _row(fin, "EBIT")
        interest_expense = _row(fin, "Interest Expense", "Interest Expense Non Operating")
        diluted_shares = _row(fin, "Diluted Average Shares")

        equity = _row(bs, "Stockholders Equity", "Common Stock Equity") if bs is not None else None
        ocf = _row(cf, "Operating Cash Flow", "Cash Flow From Continuing Operating Activities") if cf is not None else None
        fcf = _row(cf, "Free Cash Flow") if cf is not None else None
        capex = _row(cf, "Capital Expenditure") if cf is not None else None

        # --- 1. avg ROE ---
        roe_by_year = []
        if net_income is not None and equity is not None:
            for yr in years:
                ni, eq = _to_float(net_income.get(yr)), _to_float(equity.get(yr))
                if ni is not None and eq not in (None, 0):
                    roe_by_year.append(ni / eq)
        avg_roe = sum(roe_by_year) / len(roe_by_year) if roe_by_year else None

        # --- 2. cumulative FCF ---
        fcf_by_year = []
        if fcf is not None:
            fcf_by_year = [_to_float(fcf.get(yr)) for yr in years if _to_float(fcf.get(yr)) is not None]
        elif ocf is not None and capex is not None:
            for yr in years:
                o, c = _to_float(ocf.get(yr)), _to_float(capex.get(yr))
                if o is not None and c is not None:
                    fcf_by_year.append(o + c)  # capex is already negative in yfinance
        cumulative_fcf = sum(fcf_by_year) if fcf_by_year else None

        # --- 3. latest-year interest coverage ---
        interest_coverage = None
        if ebit is not None and interest_expense is not None and years:
            latest = years[0]
            e, i = _to_float(ebit.get(latest)), _to_float(interest_expense.get(latest))
            if e is not None and i not in (None, 0):
                interest_coverage = e / abs(i)

        # --- 4. gross margin (latest year + trend) ---
        gross_margin_by_year = {}
        if gross_profit is not None and revenue is not None:
            for yr in years:
                g, r = _to_float(gross_profit.get(yr)), _to_float(revenue.get(yr))
                if g is not None and r not in (None, 0):
                    gross_margin_by_year[str(yr.date())] = g / r
        latest_gross_margin = next(iter(gross_margin_by_year.values()), None)

        # --- 5. avg OCF/NI ---
        ocf_ni_ratios = []
        if ocf is not None and net_income is not None:
            for yr in years:
                o, ni = _to_float(ocf.get(yr)), _to_float(net_income.get(yr))
                if o is not None and ni not in (None, 0):
                    ocf_ni_ratios.append(o / ni)
        avg_ocf_ni = sum(ocf_ni_ratios) / len(ocf_ni_ratios) if ocf_ni_ratios else None

        # --- 6. avg net margin ---
        net_margin_by_year = []
        if net_income is not None and revenue is not None:
            for yr in years:
                ni, r = _to_float(net_income.get(yr)), _to_float(revenue.get(yr))
                if ni is not None and r not in (None, 0):
                    net_margin_by_year.append(ni / r)
        avg_net_margin = sum(net_margin_by_year) / len(net_margin_by_year) if net_margin_by_year else None

        # --- 7. share dilution over available window ---
        share_dilution_pct = None
        if diluted_shares is not None and len(years) >= 2:
            newest, oldest = _to_float(diluted_shares.get(years[0])), _to_float(diluted_shares.get(years[-1]))
            if newest is not None and oldest not in (None, 0):
                share_dilution_pct = (newest - oldest) / oldest

        def verdict(value, threshold, direction):
            if value is None:
                return "no_data"
            if direction == "min":
                return "pass" if value >= threshold else "fail"
            return "pass" if value <= threshold else "fail"

        metrics = {
            "avg_roe": {"value": avg_roe, "threshold": 0.08, "verdict": verdict(avg_roe, 0.08, "min")},
            "cumulative_fcf": {"value": cumulative_fcf, "threshold": 0, "verdict": verdict(cumulative_fcf, 0, "min")},
            "interest_coverage": {"value": interest_coverage, "threshold": 2.0, "verdict": verdict(interest_coverage, 2.0, "min")},
            "gross_margin": {"value": latest_gross_margin, "threshold": 0.15, "verdict": verdict(latest_gross_margin, 0.15, "min")},
            "avg_ocf_to_ni": {"value": avg_ocf_ni, "threshold": 0.7, "verdict": verdict(avg_ocf_ni, 0.7, "min")},
            "avg_net_margin": {"value": avg_net_margin, "threshold": 0.05, "verdict": verdict(avg_net_margin, 0.05, "min")},
            "share_dilution_pct": {"value": share_dilution_pct, "threshold": 0.20, "verdict": verdict(share_dilution_pct, 0.20, "max")},
        }

        fails = [k for k, v in metrics.items() if v["verdict"] == "fail"]

        exemption_a_eligible = (
            metrics["avg_roe"]["verdict"] == "fail"
            and latest_gross_margin is not None and latest_gross_margin > 0.30
            and len(ocf_ni_ratios) >= 2 and all(r for r in [_to_float(ocf.get(yr)) for yr in years[:2]] if r is not None and r > 0)
        )
        exemption_b_eligible = (
            metrics["avg_net_margin"]["verdict"] == "fail"
            and latest_gross_margin is not None and latest_gross_margin > 0.30
            and net_margin_by_year and net_margin_by_year[0] > 0.05
        )

        overall = "pass" if not fails else (
            "exempt_candidate" if fails and (exemption_a_eligible or exemption_b_eligible)
            else "fail"
        )

        return {
            "symbol": canonical,
            "years_used": years_used,
            "years_used_warning": (
                f"仅取得{years_used}年年度数据，原方法论要求10年，样本不足{_MIN_USABLE_YEARS}年时不应据此下结论"
                if years_used < _TARGET_YEARS else None
            ),
            "metrics": metrics,
            "fails": fails,
            "exemption_a_strategic_investment_period": {
                "mechanically_eligible": exemption_a_eligible,
                "note": "还需人工确认：上市不足10年 这一条本函数未检查，需要结合IPO日期",
            },
            "exemption_b_deliberate_low_margin": {"mechanically_eligible": exemption_b_eligible},
            "exemption_c_high_turnover_model": {
                "mechanically_eligible": None,
                "note": "会员制/平台佣金/高周转薄利这类商业模式判断是定性的，本函数不评估，需人工在3.2生意本质表里确认",
            },
            "overall": overall,  # pass | exempt_candidate | fail
        }
    except NoMarketDataError:
        raise
    except Exception as e:
        return {"symbol": canonical, "error": str(e)}


def get_balance_sheet_anomalies(
    ticker: Annotated[str, "ticker symbol of the company"],
) -> dict:
    """Mechanical red-flag scan (ai-berkshire earnings-review.md sec.4.2):
    AR growth outrunning revenue growth (channel stuffing), inventory growth
    outrunning revenue growth (buildup), OCF/NI degrading, a capex spike, and
    a rising share of non-recurring items. Computed over yfinance's available
    annual window (see module docstring re: 5-year ceiling).

    Each check reports a boolean + magnitude, not a verdict — the caller
    decides how much a single flag matters in context (e.g. AR growth ahead
    of revenue growth pre-IPO or during rapid geographic expansion is often
    benign; the point is to surface it, not auto-penalize).
    """
    canonical = normalize_symbol(ticker)
    try:
        t = yf.Ticker(canonical)
        fin = yf_retry(lambda: t.financials)
        bs = yf_retry(lambda: t.balance_sheet)
        cf = yf_retry(lambda: t.cashflow)

        if fin is None or fin.empty or bs is None or bs.empty:
            raise NoMarketDataError(ticker, canonical, "no annual statements for anomaly scan")

        years = list(fin.columns)
        if len(years) < 2:
            return {"symbol": canonical, "insufficient_years": True}

        latest, prior = years[0], years[1]
        revenue = _row(fin, "Total Revenue", "Operating Revenue")
        net_income = _row(fin, "Net Income", "Net Income Common Stockholders")
        ar = _row(bs, "Accounts Receivable", "Receivables")
        inventory = _row(bs, "Inventory")
        ocf = _row(cf, "Operating Cash Flow") if cf is not None else None
        capex = _row(cf, "Capital Expenditure") if cf is not None else None

        def yoy(row):
            if row is None:
                return None
            a, b = _to_float(row.get(latest)), _to_float(row.get(prior))
            if a is None or b in (None, 0):
                return None
            return (a - b) / abs(b)

        rev_yoy = yoy(revenue)
        ar_yoy = yoy(ar)
        inv_yoy = yoy(inventory)
        capex_yoy = yoy(capex)

        ocf_ni_trend = None
        if ocf is not None and net_income is not None and len(years) >= 2:
            ratios = []
            for yr in years:
                o, ni = _to_float(ocf.get(yr)), _to_float(net_income.get(yr))
                if o is not None and ni not in (None, 0):
                    ratios.append(o / ni)
            if len(ratios) >= 2:
                ocf_ni_trend = {"latest": ratios[0], "prior": ratios[1], "widening_gap": ratios[0] < ratios[1] and ratios[0] < 1.0}

        flags = {
            "ar_outrunning_revenue": {
                "flagged": ar_yoy is not None and rev_yoy is not None and ar_yoy > rev_yoy,
                "ar_yoy": ar_yoy, "revenue_yoy": rev_yoy,
            },
            "inventory_outrunning_revenue": {
                "flagged": inv_yoy is not None and rev_yoy is not None and inv_yoy > rev_yoy,
                "inventory_yoy": inv_yoy, "revenue_yoy": rev_yoy,
            },
            "ocf_ni_gap_widening": ocf_ni_trend or {"flagged": False, "note": "数据不足无法判断"},
            "capex_spike": {
                "flagged": capex_yoy is not None and capex_yoy > 0.5,
                "capex_yoy": capex_yoy,
            },
        }

        return {
            "symbol": canonical,
            "period_compared": f"{str(latest.date())} vs {str(prior.date())}",
            "flags": flags,
            "any_flagged": any(
                (v.get("flagged") if isinstance(v, dict) else False) for v in flags.values()
            ),
        }
    except NoMarketDataError:
        raise
    except Exception as e:
        return {"symbol": canonical, "error": str(e)}


def get_earnings_surprise_history(
    ticker: Annotated[str, "ticker symbol of the company"],
) -> dict:
    """Last ~4 quarters of analyst EPS-estimate-vs-actual surprise, from
    yfinance's `earnings_history`. This is a free, already-available signal
    for "how reliable has analyst consensus been for this specific stock" —
    distinct from PHASE_A_TASK.md 3.7A's company-guidance-vs-actual tracking,
    which compares the COMPANY's own stated guidance (not analyst estimates)
    against results, cached in valuation_cache.json."""
    canonical = normalize_symbol(ticker)
    try:
        t = yf.Ticker(canonical)
        eh = yf_retry(lambda: t.earnings_history)
        if eh is None or eh.empty:
            return {"symbol": canonical, "no_data": True}

        rows = []
        for idx, row in eh.iterrows():
            rows.append({
                "quarter_end": str(idx.date()) if hasattr(idx, "date") else str(idx),
                "eps_actual": _to_float(row.get("epsActual")),
                "eps_estimate": _to_float(row.get("epsEstimate")),
                "surprise_pct": _to_float(row.get("surprisePercent")),
            })
        beats = sum(1 for r in rows if (r["surprise_pct"] or 0) > 0)
        return {
            "symbol": canonical, "quarters": rows,
            "beat_rate": beats / len(rows) if rows else None,
        }
    except Exception as e:
        return {"symbol": canonical, "error": str(e)}
