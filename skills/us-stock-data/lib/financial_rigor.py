"""Financial Rigor Toolkit — exact-decimal verification, cross-source
reconciliation, and Benford's Law fabrication check.

Ported 2026-09-11 from xbtlin/ai-berkshire (tools/financial_rigor.py, MIT
License, Copyright (c) 2026 xbtlin). Original is a zero-dependency stdlib CLI;
this port keeps the same calculation logic but returns plain dicts (not
print()-only output) so Phase A/B tasks can consume results programmatically
instead of eyeballing printed tables. The CLI entry point is kept for
standalone manual verification.

Why this exists: every number a candidate thesis leans on (market cap check,
PE/PB/FCF-yield derivation, multi-source data reconciliation) should be
computed with exact Decimal arithmetic, not LLM mental math or float
arithmetic that can silently drift. This module is the enforcement layer —
call it, don't recompute by hand.
"""

import math
from decimal import Context, Decimal, ROUND_HALF_EVEN

_CTX = Context(prec=28, rounding=ROUND_HALF_EVEN)


def exact(value) -> Decimal:
    """Convert any numeric to exact Decimal, avoiding float traps."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def fmt_number(d: Decimal) -> str:
    """Format large numbers in human-readable form (B/T/M)."""
    v = float(d)
    abs_v = abs(v)
    if abs_v >= 1e12:
        return f"{v/1e12:.2f}T"
    if abs_v >= 1e9:
        return f"{v/1e9:.2f}B"
    if abs_v >= 1e6:
        return f"{v/1e6:.2f}M"
    return f"{v:,.2f}"


# ---------------------------------------------------------------------------
# 1. Market Cap Verification (price x shares vs. reported market cap)
# ---------------------------------------------------------------------------

def verify_market_cap(price, shares, reported_cap, currency: str = "") -> dict:
    """Cross-check market_cap = price * shares against a reported figure.

    Catches stale share counts (buybacks/dilution not yet reflected in a
    cached `info['marketCap']`) and unit mismatches. Deviation bands: <=1%
    pass, 1-5% acceptable (price/share-count drift), >5% flag for review.
    """
    p, s, r = exact(price), exact(shares), exact(reported_cap)
    calculated = _CTX.multiply(p, s)
    deviation_pct = abs(float(calculated - r) / float(r)) * 100 if r != 0 else 0.0

    if deviation_pct > 5:
        status = "fail"
    elif deviation_pct > 1:
        status = "warn"
    else:
        status = "pass"

    return {
        "price": float(p), "shares": float(s), "currency": currency,
        "calculated_market_cap": float(calculated),
        "reported_market_cap": float(r),
        "deviation_pct": round(deviation_pct, 4),
        "status": status,  # pass | warn | fail
        "note": {
            "fail": "偏差>5%，检查股本是否最新(回购/增发)、单位是否一致、股价是否最新",
            "warn": "偏差在1-5%可接受范围，可能因股价波动/股本变化",
            "pass": "验证通过",
        }[status],
    }


# ---------------------------------------------------------------------------
# 2. Valuation Metrics Verification
# ---------------------------------------------------------------------------

def verify_valuation(price, eps=None, bvps=None, fcf_per_share=None,
                      dividend=None, revenue_per_share=None) -> dict:
    """Derive PE/PB/ROE/P-FCF/FCF-yield/dividend-yield/PS from raw per-share
    inputs with exact Decimal arithmetic. Pass only the ratios you have data
    for; absent inputs are simply omitted from the result.
    """
    p = exact(price)
    results = {"price": float(p)}

    if eps is not None:
        e = exact(eps)
        if e != 0:
            results["PE"] = float(_CTX.divide(p, e))
            results["earnings_yield_pct"] = float(_CTX.divide(e, p) * 100)

    if bvps is not None:
        b = exact(bvps)
        if b != 0:
            results["PB"] = float(_CTX.divide(p, b))
            if eps is not None and float(exact(eps)) != 0:
                results["ROE_pct"] = float(_CTX.divide(exact(eps), b) * 100)

    if fcf_per_share is not None:
        f = exact(fcf_per_share)
        if f != 0:
            results["P_FCF"] = float(_CTX.divide(p, f))
            results["FCF_yield_pct"] = float(_CTX.divide(f, p) * 100)

    if dividend is not None:
        d = exact(dividend)
        if p != 0:
            results["dividend_yield_pct"] = float(_CTX.divide(d, p) * 100)

    if revenue_per_share is not None:
        r = exact(revenue_per_share)
        if r != 0:
            results["PS"] = float(_CTX.divide(p, r))

    return results


# ---------------------------------------------------------------------------
# 3. Cross-Source Data Validation
# ---------------------------------------------------------------------------

def cross_validate(field_name: str, source_values: dict, unit: str = "",
                    tolerance_pct: float = 2.0) -> dict:
    """Compare one data point across multiple sources, flag outliers against
    the median, return a consensus (median) value.

    Use this whenever the same figure is pulled from >1 source (e.g. revenue
    from yfinance vs a company's own 8-K exhibit) instead of picking one
    arbitrarily or eyeballing which "looks right".
    """
    values = {k: exact(v) for k, v in source_values.items()}
    sorted_vals = sorted(float(v) for v in values.values())
    n = len(sorted_vals)
    median = sorted_vals[n // 2] if n % 2 == 1 else (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) / 2

    per_source, all_ok = {}, True
    for src, val in values.items():
        dev = abs(float(val) - median) / median * 100 if median != 0 else 0.0
        ok = dev <= tolerance_pct
        all_ok = all_ok and ok
        per_source[src] = {"value": float(val), "deviation_pct": round(dev, 4), "status": "pass" if ok else "fail"}

    return {
        "field": field_name, "unit": unit, "tolerance_pct": tolerance_pct,
        "sources": per_source, "median": median, "consensus": median,
        "all_consistent": all_ok,
    }


# ---------------------------------------------------------------------------
# 4. Benford's Law Quick Check (financial-statement fabrication screen)
# ---------------------------------------------------------------------------

_BENFORD = {d: math.log10(1 + 1 / d) for d in range(1, 10)}


def benford_check(values: list) -> dict:
    """Leading-digit distribution check against Benford's Law.

    Needs >=50 usable data points to be statistically meaningful — feed it
    every line item across several years/quarters of a statement (revenue
    lines, expense lines, segment breakdowns), not a handful of headline
    numbers. Not a fraud proof either way: non-conformity flags "worth a
    closer look", conformity doesn't clear a company.
    """
    digits = []
    for v in values:
        v = abs(float(v))
        if v > 0:
            sig = 10 ** (math.log10(v) - math.floor(math.log10(v)))
            d = int(sig)
            if 1 <= d <= 9:
                digits.append(d)

    n = len(digits)
    if n < 50:
        return {"n": n, "insufficient_sample": True, "note": f"样本量{n}<50，Benford分析不可靠"}

    counts = {}
    for d in digits:
        counts[d] = counts.get(d, 0) + 1
    observed = {d: counts.get(d, 0) / n for d in range(1, 10)}

    mad = sum(abs(observed.get(d, 0) - _BENFORD[d]) for d in range(1, 10)) / 9
    chi2 = sum((counts.get(d, 0) - _BENFORD[d] * n) ** 2 / (_BENFORD[d] * n) for d in range(1, 10))

    if mad < 0.006:
        conformity = "close"
    elif mad < 0.012:
        conformity = "acceptable"
    elif mad < 0.015:
        conformity = "marginally_acceptable"
    else:
        conformity = "nonconforming"

    return {
        "n": n, "insufficient_sample": False,
        "mad": round(mad, 6), "chi2": round(chi2, 2), "conformity": conformity,
        "is_conforming": mad < 0.015,
        "digit_distribution": {d: {"observed": round(observed.get(d, 0), 4), "expected": round(_BENFORD[d], 4)} for d in range(1, 10)},
        "note": "不符合Benford定律不代表造假，但值得进一步调查具体是哪类科目偏离" if mad >= 0.015 else "首位数字分布符合Benford定律",
    }


# ---------------------------------------------------------------------------
# 5. Exact Calculator
# ---------------------------------------------------------------------------

def exact_calc(expr: str) -> dict:
    """Evaluate a financial arithmetic expression with exact Decimal, no
    float drift. Supports +, -, *, /, (), numbers incl. scientific notation.
    """
    allowed = set("0123456789.+-*/() eE")
    if not all(c in allowed for c in expr.replace(" ", "")):
        return {"error": f"不安全的表达式: {expr}"}
    try:
        result = eval(expr, {"__builtins__": {}}, {})
        d_result = exact(result)
        return {"expr": expr, "result": float(d_result), "exact": str(d_result)}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# 6. Three-Scenario Valuation (bull/base/bear N-year target price)
# ---------------------------------------------------------------------------

def three_scenario_valuation(current_price, current_eps, shares_billion,
                              growth_optimistic, growth_neutral, growth_pessimistic,
                              pe_optimistic, pe_neutral, pe_pessimistic,
                              years: int = 3, currency: str = "") -> dict:
    """Simpler complement to the 3.7E terminal-value model: instead of a
    perpetuity discount, project EPS at a flat annual growth rate for N years
    then apply a scenario exit multiple. Avoids the terminal-value model's
    r<=g "undefined" wall (see PHASE_A_TASK.md 3.7E note), at the cost of
    being a cruder N-year-only projection with no discounting back to present
    value — the two methods are meant to be read side by side, not as
    substitutes for each other.
    """
    p, eps, shares = exact(current_price), exact(current_eps), exact(shares_billion)
    scenarios_in = [
        ("bull", growth_optimistic, pe_optimistic),
        ("base", growth_neutral, pe_neutral),
        ("bear", growth_pessimistic, pe_pessimistic),
    ]

    out = {"current_price": float(p), "current_eps": float(eps), "years": years, "currency": currency, "scenarios": {}}
    for name, growth, pe in scenarios_in:
        g, target_pe = exact(growth), exact(pe)
        future_eps = eps
        for _ in range(years):
            future_eps = _CTX.multiply(future_eps, _CTX.add(Decimal("1"), g))
        target_price = _CTX.multiply(future_eps, target_pe)
        change_pct = float(target_price - p) / float(p) * 100 if p != 0 else None
        out["scenarios"][name] = {
            "annual_growth": float(g), "target_pe": float(target_pe),
            "future_eps": float(future_eps), "target_price": float(target_price),
            "change_pct": round(change_pct, 2) if change_pct is not None else None,
        }
    return out


# ---------------------------------------------------------------------------
# CLI entry point (standalone manual verification; library calls above are
# the primary interface for Phase A/B tasks)
# ---------------------------------------------------------------------------

def _cli():
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Financial Rigor Toolkit")
    sub = parser.add_subparsers(dest="command")

    mc = sub.add_parser("verify-market-cap")
    mc.add_argument("--price", type=float, required=True)
    mc.add_argument("--shares", type=float, required=True)
    mc.add_argument("--reported", type=float, required=True)
    mc.add_argument("--currency", default="")

    val = sub.add_parser("verify-valuation")
    val.add_argument("--price", type=float, required=True)
    val.add_argument("--eps", type=float, default=None)
    val.add_argument("--bvps", type=float, default=None)
    val.add_argument("--fcf-per-share", type=float, default=None)
    val.add_argument("--dividend", type=float, default=None)
    val.add_argument("--revenue-per-share", type=float, default=None)

    cv = sub.add_parser("cross-validate")
    cv.add_argument("--field", required=True)
    cv.add_argument("--values", required=True, help="JSON: {source: value}")
    cv.add_argument("--unit", default="")
    cv.add_argument("--tolerance", type=float, default=2.0)

    bf = sub.add_parser("benford")
    bf.add_argument("--values", required=True, help="JSON array")

    ca = sub.add_parser("calc")
    ca.add_argument("--expr", required=True)

    ts = sub.add_parser("three-scenario")
    ts.add_argument("--price", type=float, required=True)
    ts.add_argument("--eps", type=float, required=True)
    ts.add_argument("--shares", type=float, required=True)
    ts.add_argument("--growth", nargs=3, type=float, required=True)
    ts.add_argument("--pe", nargs=3, type=float, required=True)
    ts.add_argument("--years", type=int, default=3)
    ts.add_argument("--currency", default="")

    args = parser.parse_args()
    if args.command == "verify-market-cap":
        print(json.dumps(verify_market_cap(args.price, args.shares, args.reported, args.currency), indent=2))
    elif args.command == "verify-valuation":
        print(json.dumps(verify_valuation(args.price, args.eps, args.bvps, args.fcf_per_share, args.dividend, args.revenue_per_share), indent=2))
    elif args.command == "cross-validate":
        print(json.dumps(cross_validate(args.field, json.loads(args.values), args.unit, args.tolerance), indent=2))
    elif args.command == "benford":
        print(json.dumps(benford_check(json.loads(args.values)), indent=2))
    elif args.command == "calc":
        print(json.dumps(exact_calc(args.expr), indent=2))
    elif args.command == "three-scenario":
        print(json.dumps(three_scenario_valuation(
            args.price, args.eps, args.shares,
            args.growth[0], args.growth[1], args.growth[2],
            args.pe[0], args.pe[1], args.pe[2],
            args.years, args.currency), indent=2))
    else:
        parser.print_help()


if __name__ == "__main__":
    _cli()
