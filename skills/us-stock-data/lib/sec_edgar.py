"""SEC EDGAR filings — the US analog of 巨潮公告 (official, legally-mandated disclosure).

New code (not ported from TradingAgents — its dataflows/ has no EDGAR module).
Two free, no-key endpoints:

  - data.sec.gov/submissions/CIK##########.json
        Full filing history for one company (form, date, accession#, items).
        Used here to list a company's recent 8-K filings — the closest US
        equivalent of cninfo_announcements() for "什么公司自己公开确认的事件".
  - efts.sec.gov/LATEST/search-index
        Full-text keyword search across ALL filers since 2001. Used for
        cross-company keyword search (e.g. scanning for a specific event type).

SEC's fair-access policy requires every request to carry a descriptive
User-Agent with a real contact (name/email) — a generic or missing UA gets
403'd. Set it yourself via the SEC_EDGAR_CONTACT env var, e.g.:

    export SEC_EDGAR_CONTACT="Your Name your.email@example.com"

Rate limit discipline (learned the hard way with a-stock-data's eastmoney
throttling — see [[eastmoney-selfinflicted-rateban]] pattern): SEC asks for
≤10 req/sec; this module throttles to ~4/sec with jitter and reuses one
session, so a batch run across a watchlist can't accidentally trip it.
"""

from __future__ import annotations

import os
import random
import time

import requests

from .errors import VendorNotConfiguredError
from .symbol_utils import normalize_symbol

_SESSION = requests.Session()
_MIN_INTERVAL = 0.25  # ~4 req/sec, well under SEC's stated 10/sec ceiling
_last_request_ts = 0.0

_TICKER_MAP_CACHE_FILE = os.path.join(
    os.path.expanduser("~"), ".claude", "skills", "us-stock-data", ".cache", "sec_company_tickers.json"
)
_ticker_to_cik: dict[str, str] | None = None


def _user_agent() -> str:
    ua = os.getenv("SEC_EDGAR_CONTACT")
    if not ua:
        raise VendorNotConfiguredError(
            "SEC_EDGAR_CONTACT env var is not set. SEC requires a descriptive "
            "User-Agent with a real contact, e.g.:\n"
            '  export SEC_EDGAR_CONTACT="Your Name your.email@example.com"\n'
            "A missing/generic UA gets 403'd — see SEC's fair-access policy."
        )
    return ua


def sec_get(url: str, params: dict | None = None) -> requests.Response:
    """Throttled GET with the required SEC User-Agent. Use for every sec.gov/data.sec.gov call."""
    global _last_request_ts
    elapsed = time.monotonic() - _last_request_ts
    wait = _MIN_INTERVAL - elapsed + random.uniform(0, 0.15)
    if wait > 0:
        time.sleep(wait)
    resp = _SESSION.get(url, params=params, headers={"User-Agent": _user_agent()}, timeout=15)
    _last_request_ts = time.monotonic()
    resp.raise_for_status()
    return resp


def _load_ticker_map() -> dict[str, str]:
    """ticker (upper) -> zero-padded 10-digit CIK, from SEC's company_tickers.json (cached on disk)."""
    global _ticker_to_cik
    if _ticker_to_cik is not None:
        return _ticker_to_cik

    if os.path.exists(_TICKER_MAP_CACHE_FILE):
        age = time.time() - os.path.getmtime(_TICKER_MAP_CACHE_FILE)
        if age < 7 * 86400:  # company_tickers.json changes rarely; 7-day cache is plenty
            import json
            with open(_TICKER_MAP_CACHE_FILE, encoding="utf-8") as f:
                raw = json.load(f)
            _ticker_to_cik = {row["ticker"].upper(): str(row["cik_str"]).zfill(10) for row in raw.values()}
            return _ticker_to_cik

    resp = sec_get("https://www.sec.gov/files/company_tickers.json")
    raw = resp.json()
    os.makedirs(os.path.dirname(_TICKER_MAP_CACHE_FILE), exist_ok=True)
    with open(_TICKER_MAP_CACHE_FILE, "w", encoding="utf-8") as f:
        f.write(resp.text)
    _ticker_to_cik = {row["ticker"].upper(): str(row["cik_str"]).zfill(10) for row in raw.values()}
    return _ticker_to_cik


def get_cik(ticker: str) -> str:
    """Resolve a ticker to its zero-padded 10-digit CIK. Raises KeyError if unknown to SEC
    (e.g. OTC/pink-sheet symbols not registered with the SEC, or a bad ticker)."""
    canonical = normalize_symbol(ticker).upper()
    mapping = _load_ticker_map()
    if canonical not in mapping:
        raise KeyError(f"No SEC CIK found for ticker {ticker!r} (resolved {canonical!r})")
    return mapping[canonical]


def get_recent_filings(ticker: str, forms: list[str] | None = None, limit: int = 20) -> list[dict]:
    """Recent filings for one company from its EDGAR submissions history.

    Returns a list of dicts: form, filingDate, reportDate, items, accessionNumber,
    primaryDocument, and a ready-to-open `url` to the filing index page.
    `forms` filters to specific form types (e.g. ["8-K"]); None returns all forms.
    """
    cik = get_cik(ticker)
    resp = sec_get(f"https://data.sec.gov/submissions/CIK{cik}.json")
    data = resp.json()
    recent = data.get("filings", {}).get("recent", {})

    n = len(recent.get("form", []))
    results = []
    for i in range(n):
        form = recent["form"][i]
        if forms and form not in forms:
            continue
        accession = recent["accessionNumber"][i].replace("-", "")
        results.append({
            "form": form,
            "filingDate": recent["filingDate"][i],
            "reportDate": recent.get("reportDate", [None] * n)[i],
            "items": recent.get("items", [None] * n)[i],
            "primaryDocDescription": recent.get("primaryDocDescription", [None] * n)[i],
            "accessionNumber": recent["accessionNumber"][i],
            "url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/{recent['primaryDocument'][i]}",
        })
        if len(results) >= limit:
            break
    return results


def get_recent_8k_filings(ticker: str, limit: int = 10) -> str:
    """Recent 8-K (material event) filings for a ticker, formatted as a text report.

    This is the closest US equivalent of cninfo_announcements() — 8-K is the
    SEC's mandatory "something material just happened" disclosure (earnings,
    executive changes, M&A, bankruptcy, etc). `items` in the output are the
    8-K Item numbers (e.g. "5.02" = officer/director changes, "2.02" = results
    of operations) — cross-reference against SEC's Form 8-K item list if the
    number itself isn't self-explanatory.

    Each row is flagged `[READ REQUIRED]` when its items include one from
    `ITEMS_REQUIRING_FULL_READ` — see that set's docstring for why. Finding
    that flag in the output means: don't stop at the item code, call
    `get_filing_text(url)` on that row before writing a thesis around it.
    """
    try:
        filings = get_recent_filings(ticker, forms=["8-K"], limit=limit)
    except KeyError as e:
        return str(e)
    except Exception as e:
        return f"Error retrieving 8-K filings for {ticker}: {str(e)}"

    if not filings:
        return f"No 8-K filings found for {ticker}"

    lines = [f"# Recent 8-K filings for {ticker.upper()} (most recent first)\n"]
    for f in filings:
        flag = " [READ REQUIRED]" if _needs_full_read(f["items"]) else ""
        lines.append(
            f"- {f['filingDate']} | items: {f['items'] or 'n/a'}{flag} | "
            f"{f['primaryDocDescription'] or ''} | {f['url']}"
        )
    return "\n".join(lines)


# Item codes whose number alone tells you nothing about WHAT happened — the
# code just says "some agreement/event exists", not its content, counterparty,
# or size. Found the hard way (2026-08-23): NVDA's 8/17 8-K carried items
# "1.01,2.03,7.01" and was treated as "a catalyst exists, item confirmed" without
# reading it — the actual filing disclosed a $105B residual-value guaranty
# NVIDIA gave backstopping OpenAI's lease obligations at a data center site, a
# real contingent-liability finding that a name-only read would never surface.
# Contrast with e.g. "2.02" (results of operations — you know it's an earnings
# release without opening it) or "5.02" (officer/director change) or "5.07"
# (shareholder vote results) — those are genuinely self-explanatory from the
# code, reading every one of those is not a good use of a research budget.
ITEMS_REQUIRING_FULL_READ = {
    "1.01",  # Entry into a Material Definitive Agreement — could be anything
    "1.02",  # Termination of a Material Definitive Agreement
    "2.01",  # Completion of Acquisition or Disposition of Assets
    "2.03",  # Creation of a Direct Financial Obligation (or off-balance-sheet arrangement)
    "2.05",  # Costs Associated with Exit or Disposal Activities
    "2.06",  # Material Impairments
    "3.02",  # Unregistered Sales of Equity Securities
    "3.03",  # Material Modification to Rights of Security Holders
    "4.01",  # Changes in Registrant's Certifying Accountant
    "4.02",  # Non-Reliance on Previously Issued Financial Statements
    "5.01",  # Changes in Control of Registrant
    "8.01",  # Other Events — a catch-all with zero information in the code itself
}


def _needs_full_read(items: str | None) -> bool:
    if not items:
        return False
    codes = {c.strip() for c in items.split(",")}
    return bool(codes & ITEMS_REQUIRING_FULL_READ)


def get_filing_text(url: str, max_chars: int = 6000) -> str:
    """Fetch an SEC filing's primary document and return its text content
    (HTML tags stripped), truncated to max_chars. Use this whenever
    get_recent_8k_filings() flags a row `[READ REQUIRED]` — the item code
    alone does not tell you what the filing says.
    """
    try:
        resp = sec_get(url)
    except Exception as e:
        return f"Error fetching filing text from {url}: {e}"

    import re

    text = re.sub(r"<[^>]+>", " ", resp.text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&#\d+;", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars] + f"... [truncated, {len(text)} chars total, re-fetch with a higher max_chars if needed]"
    return text


def search_filings_fulltext(
    query: str,
    forms: list[str] | None = None,
    ciks: list[str] | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 10,
) -> str:
    """Cross-company full-text keyword search over SEC filings since 2001.

    Use for scanning a theme/event type across many companies (e.g. a
    supplementary-scan candidate source), not for a single company's own
    filing history — use get_recent_8k_filings() for that instead.
    """
    params: dict = {"q": query, "forms": ",".join(forms) if forms else None}
    if ciks:
        params["ciks"] = ",".join(ciks)
    if date_from and date_to:
        params.update({"dateRange": "custom", "startdt": date_from, "enddt": date_to})
    params = {k: v for k, v in params.items() if v is not None}

    try:
        resp = sec_get("https://efts.sec.gov/LATEST/search-index", params=params)
        data = resp.json()
    except Exception as e:
        return f"Error running SEC full-text search for {query!r}: {str(e)}"

    hits = data.get("hits", {}).get("hits", [])[:limit]
    if not hits:
        return f"No SEC filings found for query {query!r}"

    lines = [f"# SEC full-text search results for {query!r}\n"]
    for h in hits:
        src = h.get("_source", {})
        # _id is "{accession-with-dashes}:{filename}"; build the real filing
        # URL from the first CIK + accession (no dashes) + filename, since the
        # raw _id is not itself a usable path.
        accession, _, filename = h.get("_id", "").partition(":")
        ciks = src.get("ciks") or [""]
        url = (
            f"https://www.sec.gov/Archives/edgar/data/{int(ciks[0])}/"
            f"{accession.replace('-', '')}/{filename}"
            if ciks[0] and accession and filename
            else "(malformed hit, no url)"
        )
        lines.append(
            f"- {src.get('file_date')} | {src.get('display_names', ['?'])[0]} | "
            f"{src.get('root_form', src.get('form'))} | {url}"
        )
    return "\n".join(lines)
