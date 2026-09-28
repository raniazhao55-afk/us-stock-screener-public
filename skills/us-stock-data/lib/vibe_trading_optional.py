"""Layer 4 (OPTIONAL): three genuinely new-capability tools borrowed from
HKUDS/Vibe-Trading (https://github.com/HKUDS/Vibe-Trading, MIT license) —
options chain, prediction-market data, and academic factor research search.

Why these three and not the rest of that project: Vibe-Trading is a huge
multi-broker, multi-LLM trading platform whose own agent runtime calls
metered LLM APIs — that conflicts with this project's "Claude subscription
only, no external per-token billing" rule, so we never run its agent/swarm
layer, only import its plain data-fetching functions directly (no MCP
transport, no LLM key needed for these three).

Why NOT its other overlapping tools (get_sec_filings, get_financial_statements,
screen_market, technical_indicators, get_institutional_holdings): overlaps
with Layer 1/2/3 above, which are already verified working and simpler to
reason about. Real bugs found during evaluation (2026-08-23), not guessed:
  - `technical_indicators(symbol="AAPL")` (no market suffix) misroutes
    internally toward a tushare/A-share path and fails with "No data
    returned" — only works as `"AAPL.US"`.
  - `screen_market(market="us", ...)` times out hitting push2.eastmoney.com
    (a Chinese endpoint that a "us" market scan should never touch).
  - `get_institutional_holdings(mode="ticker_holders")` resolves ticker->CUSIP
    via SEC's "fails-to-deliver" file, which only covers securities that had
    a settlement failure — fails for most ordinary tickers. `mode="top_managers"`
    works fine though.
Given those, adding it to the core pipeline would mean re-deriving the same
kind of workaround/fallback discipline we already built by hand in Layer 1-3,
for tools that duplicate what we have. Not worth the dependency weight there.
Installing it DID downgrade this venv's pandas 3.0.5 -> 2.3.3 (it pins <3.0);
Layer 1-3 re-verified working after that downgrade.

Requires: `pip install vibe-trading-ai` in this skill's venv (already done as
of 2026-08-23; if missing, every function below raises ImportError with a
clear message instead of a confusing traceback).
"""

from __future__ import annotations

_registry = None


def _get_registry():
    global _registry
    if _registry is not None:
        return _registry
    try:
        from src.tools import build_registry
    except ImportError as e:
        raise ImportError(
            "Layer 4 (vibe_trading_optional) needs the vibe-trading-ai package: "
            "run `~/.claude/skills/us-stock-data/.venv/bin/pip install vibe-trading-ai`"
        ) from e
    _registry = build_registry(include_shell_tools=False)
    return _registry


def get_options_chain(ticker: str, expiration: int | None = None) -> str:
    """US-listed options chain (calls+puts: strike/bid/ask/last/volume/OI/IV/ITM) via Yahoo.

    `expiration` is a Unix epoch seconds value from a prior call's
    `expirations` list; omit for the nearest expiration.
    """
    params = {"ticker": ticker}
    if expiration is not None:
        params["expiration"] = expiration
    return _get_registry().execute("get_options_chain", params)


def prediction_market_search(query: str) -> str:
    """Search Polymarket event-contract markets by keyword (event-driven macro/political signal).

    Each outcome's quote IS the market-implied probability (0-1) — a 0.63
    quote means a 63% chance, not $0.63 of exposure.
    """
    return _get_registry().execute("prediction_market", {"mode": "search", "query": query})


def prediction_market_event(event_ids: list[str]) -> str:
    """One or more Polymarket events with the markets/outcomes beneath each, by id(s) from a prior search."""
    return _get_registry().execute("prediction_market", {"mode": "event", "ids": event_ids})


def research_papers_search(query: str, limit: int = 10) -> str:
    """Search academic finance/ML papers (arXiv q-fin/cs.LG + OpenAlex) by topic."""
    return _get_registry().execute("research_papers", {"mode": "search", "query": query, "limit": limit})


def research_papers_read(paper_ids: list[str]) -> str:
    """Read specific paper(s) by id (from research_papers_search) and extract a factor brief:
    the proposed signal, required input data, claimed backtest window/market/performance, and
    falsifiable reproduction checks — evidence-only, quoting the source sentence for each claim."""
    return _get_registry().execute("research_papers", {"mode": "read", "paper_ids": paper_ids})
