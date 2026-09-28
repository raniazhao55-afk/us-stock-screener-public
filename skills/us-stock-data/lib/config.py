"""Minimal standalone config, trimmed from TradingAgents' default_config.py.

The original ties this into a full LLM-agent config dict; we only need the
handful of keys the ported data-fetching functions read (cache dir, news
limits/queries). No LLM/vendor-routing settings — this skill never calls a
metered LLM API, only free market-data endpoints.
"""

import os

_HOME = os.path.join(os.path.expanduser("~"), ".claude", "skills", "us-stock-data")

_config = {
    "data_cache_dir": os.getenv("US_STOCK_DATA_CACHE_DIR", os.path.join(_HOME, ".cache")),
    "news_article_limit": 20,
    "global_news_article_limit": 10,
    "global_news_lookback_days": 7,
    "global_news_queries": [
        "Federal Reserve interest rates inflation",
        "S&P 500 earnings GDP economic outlook",
        "geopolitical risk trade war sanctions",
        "oil commodities supply chain energy",
    ],
}


def get_config() -> dict:
    return dict(_config)
