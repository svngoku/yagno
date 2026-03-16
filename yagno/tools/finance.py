"""Finance tools backed by real, free APIs — no API keys required.

APIs used:
  - Yahoo Finance (yfinance >=1.0): stock quotes, company info, news.
  - CoinGecko public API: cryptocurrency prices (30 req/min, no key).
  - SEC EDGAR REST API: regulatory filings metadata (no key; User-Agent required).
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from agno.tools import tool

try:
    import yfinance as yf

    _YF_AVAILABLE = True
except ImportError:  # pragma: no cover
    _YF_AVAILABLE = False

# SEC EDGAR asks for a descriptive User-Agent: "<name/app> <contact-email>"
_EDGAR_USER_AGENT = os.getenv(
    "EDGAR_USER_AGENT", "yagno-finance-tools/1.0 research@example.com"
)
_EDGAR_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_EDGAR_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"

_COINGECKO_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"
_COINGECKO_SEARCH_URL = "https://api.coingecko.com/api/v3/search"


def _http_get(url: str, params: dict | None = None, user_agent: str | None = None) -> dict:
    """Minimal HTTP GET returning parsed JSON. Uses stdlib only."""
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    headers = {"User-Agent": user_agent or "yagno/1.0"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


# ---------------------------------------------------------------------------
# Stock quotes — Yahoo Finance (yfinance, free, no key)
# ---------------------------------------------------------------------------


@tool
def get_stock_quote(symbol: str) -> str:
    """Get the current stock price and key statistics for a ticker symbol.

    Data source: Yahoo Finance via yfinance (free, no API key required).
    Returns price, daily change, 52-week range, market cap, P/E ratio, and
    dividend yield when available.
    """
    if not _YF_AVAILABLE:
        return "yfinance not installed. Run: uv add yfinance"

    sym = symbol.upper().strip()
    try:
        ticker = yf.Ticker(sym)
        fi = ticker.fast_info

        last = fi.last_price
        prev = fi.previous_close
        if last is None or prev is None:
            return f"No price data found for '{sym}'. Verify the ticker symbol."

        change_pct = (last - prev) / prev * 100 if prev else 0.0

        parts = [
            f"Symbol : {sym}",
            f"Price  : ${last:,.2f}",
            f"Change : {change_pct:+.2f}%",
        ]
        if fi.market_cap:
            parts.append(f"Mkt Cap: ${fi.market_cap / 1e9:.2f}B")
        if fi.year_high and fi.year_low:
            parts.append(f"52W    : ${fi.year_low:,.2f} – ${fi.year_high:,.2f}")
        if fi.three_month_average_volume:
            parts.append(f"Avg Vol: {fi.three_month_average_volume:,.0f} (3M)")

        # Slower but richer fundamentals
        info = ticker.info
        if info.get("trailingPE"):
            parts.append(f"P/E    : {info['trailingPE']:.1f}")
        if info.get("dividendYield"):
            parts.append(f"Div Yld: {info['dividendYield'] * 100:.2f}%")
        if info.get("sector"):
            parts.append(f"Sector : {info['sector']}")

        return "\n".join(parts)
    except Exception as exc:
        return f"Error fetching quote for '{sym}': {exc}"


# ---------------------------------------------------------------------------
# Financial news — Yahoo Finance (yfinance, free, no key)
# ---------------------------------------------------------------------------


@tool
def get_financial_news(query: str) -> str:
    """Fetch the latest financial news for a ticker symbol or company name.

    Data source: Yahoo Finance via yfinance (free, no API key required).
    Accepts a ticker ('AAPL') or a company/topic name ('Apple', 'Fed rate').
    Returns up to 5 recent articles with title, source, date, and URL.
    """
    if not _YF_AVAILABLE:
        return "yfinance not installed. Run: uv add yfinance"

    def _parse_articles(raw: list) -> list[dict]:
        """Normalise article dicts across yfinance versions."""
        parsed = []
        for a in raw:
            # yfinance >=0.2.50 wraps everything under 'content'
            content = a.get("content", a)
            title = content.get("title") or a.get("title", "")
            provider = (
                content.get("provider", {}).get("displayName")
                or a.get("publisher", "")
            )
            pub_date = content.get("pubDate") or str(
                a.get("providerPublishTime", "")
            )
            url = (
                content.get("canonicalUrl", {}).get("url")
                or a.get("link", "")
            )
            if title:
                parsed.append(
                    {"title": title, "provider": provider, "date": pub_date, "url": url}
                )
        return parsed

    try:
        # Direct ticker lookup
        ticker = yf.Ticker(query.upper().strip())
        articles = _parse_articles(ticker.get_news(count=5) or [])

        if not articles:
            # Fallback: yfinance Search (available in yfinance >=0.2.x)
            try:
                result = yf.Search(query, news_count=5)
                articles = _parse_articles(result.news or [])
            except Exception:
                pass

        if not articles:
            return f"No recent news found for '{query}'."

        lines = [f"Latest news for '{query}' ({len(articles)} articles):"]
        for i, a in enumerate(articles, 1):
            lines.append(f"\n{i}. {a['title']}")
            meta = " | ".join(filter(None, [a["provider"], a["date"]]))
            if meta:
                lines.append(f"   {meta}")
            if a["url"]:
                lines.append(f"   {a['url']}")

        return "\n".join(lines)
    except Exception as exc:
        return f"Error fetching news for '{query}': {exc}"


# ---------------------------------------------------------------------------
# SEC EDGAR filings — EDGAR REST API (free, no key; User-Agent required)
# ---------------------------------------------------------------------------


@tool
def get_sec_filing(ticker: str, filing_type: str = "10-K") -> str:
    """Retrieve recent SEC filing metadata for a public company via EDGAR.

    Data source: SEC EDGAR REST API (free, no API key required).
    Common filing_type values: 10-K (annual), 10-Q (quarterly), 8-K (current),
    DEF 14A (proxy statement), S-1 (IPO registration).
    Returns filing dates and direct EDGAR document links.
    """
    sym = ticker.upper().strip()
    try:
        # Step 1 — resolve ticker → CIK via EDGAR's company_tickers.json
        tickers_data: dict = _http_get(_EDGAR_TICKERS_URL, user_agent=_EDGAR_USER_AGENT)
        cik_padded = None
        company_name = sym
        for entry in tickers_data.values():
            if entry.get("ticker", "").upper() == sym:
                cik_padded = str(entry["cik_str"]).zfill(10)
                company_name = entry.get("title", sym)
                break

        if not cik_padded:
            return (
                f"Ticker '{sym}' not found in SEC EDGAR. "
                "Verify the symbol or try the company's full name."
            )

        # Step 2 — fetch submissions (recent filings index)
        submissions = _http_get(
            _EDGAR_SUBMISSIONS_URL.format(cik=cik_padded),
            user_agent=_EDGAR_USER_AGENT,
        )
        recent = submissions.get("filings", {}).get("recent", {})

        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])

        matches = [
            (dates[i], accessions[i], primary_docs[i])
            for i, f in enumerate(forms)
            if f.upper() == filing_type.upper()
        ][:3]

        if not matches:
            return (
                f"No {filing_type} filings found for {company_name} "
                f"(CIK {cik_padded.lstrip('0')}) in EDGAR's recent index."
            )

        cik_int = int(cik_padded)
        lines = [
            f"{filing_type} filings for {company_name} "
            f"(CIK: {cik_int}, ticker: {sym}):"
        ]
        for date, accession, doc in matches:
            acc_clean = accession.replace("-", "")
            url = (
                f"https://www.sec.gov/Archives/edgar/data/"
                f"{cik_int}/{acc_clean}/{doc}"
            )
            lines.append(f"  • {date}  →  {url}")

        return "\n".join(lines)
    except Exception as exc:
        return f"Error fetching SEC filings for '{sym}': {exc}"


# ---------------------------------------------------------------------------
# Crypto prices — CoinGecko public API (free, no key, ~30 req/min)
# ---------------------------------------------------------------------------


@tool
def get_crypto_price(coin: str) -> str:
    """Get the current USD price and 24h stats for a cryptocurrency.

    Data source: CoinGecko public API (free, no API key required; ~30 req/min).
    Accepts a CoinGecko coin id ('bitcoin', 'ethereum') or a common symbol/name
    ('BTC', 'ETH', 'Solana'). Resolves the id automatically via CoinGecko search.
    Returns price, 24h change, market cap, and 24h trading volume.
    """
    raw = coin.lower().strip()
    try:
        # Resolve coin name/symbol → CoinGecko id via /search
        search_data = _http_get(_COINGECKO_SEARCH_URL, {"query": raw})
        coins_found = search_data.get("coins", [])
        if not coins_found:
            return (
                f"Coin '{coin}' not found on CoinGecko. "
                "Try the full name (e.g., 'bitcoin') or id."
            )
        coin_id: str = coins_found[0]["id"]
        coin_symbol: str = coins_found[0].get("symbol", "").upper()
        coin_name: str = coins_found[0].get("name", coin_id)

        # Fetch live price data
        price_data = _http_get(
            _COINGECKO_PRICE_URL,
            {
                "ids": coin_id,
                "vs_currencies": "usd",
                "include_24hr_change": "true",
                "include_market_cap": "true",
                "include_24hr_vol": "true",
            },
        )

        if coin_id not in price_data:
            return f"Price data unavailable for '{coin_name}' ({coin_id})."

        d = price_data[coin_id]
        price = d.get("usd", 0.0)
        change_24h = d.get("usd_24h_change") or 0.0
        mkt_cap = d.get("usd_market_cap") or 0.0
        vol_24h = d.get("usd_24h_vol") or 0.0

        return (
            f"{coin_name} ({coin_symbol}) | "
            f"Price: ${price:,.2f} | "
            f"24h: {change_24h:+.2f}% | "
            f"Mkt Cap: ${mkt_cap / 1e9:.2f}B | "
            f"Vol 24h: ${vol_24h / 1e6:.2f}M"
        )
    except Exception as exc:
        return f"Error fetching crypto price for '{coin}': {exc}"
