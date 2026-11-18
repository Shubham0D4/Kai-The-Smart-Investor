"""
News for Delta from several free providers fetched in parallel (DESIGN.md §1): Finnhub company
news (free key, FINNHUB_API_KEY; skipped when no key is set), Yahoo Finance RSS and Google News RSS.
yfinance news is a last resort: it is only asked when those return fewer than MIN_ARTICLES
articles (its endpoint failed in both live tests). Nasdaq RSS was removed: it answered 403 in
both live tests.

Articles are deduplicated by URL and then by normalised title. When providers disagree on a
field of the same article, the majority value wins; ties go to PROVIDER_ORDER. A provider
that fails is skipped and reported in `providers_failed`; if every provider fails the tool
returns success=False. Articles without a summary from any provider are flagged headline_only.
"""

import html
import os
import re
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Callable, Dict, List, Optional
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
import yfinance as yf

PROVIDER_ORDER = ["finnhub", "yahoo_rss", "google_rss", "yfinance"]
PRIMARY = ["finnhub", "yahoo_rss", "google_rss"]
MIN_ARTICLES = 5      # fewer articles than this from the primary providers: ask yfinance too
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Kai-Economist/1.0)"}
SUMMARY_MAX_CHARS = 800


def _now() -> datetime:
    """Current time (UTC). Patched in tests so recorded fixtures stay inside the window."""
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- helpers

_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    t = html.unescape(_TAG_RE.sub(" ", text))
    t = re.sub(r"\s+", " ", t).strip()
    return t[:SUMMARY_MAX_CHARS] or None


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_rfc822(s: Optional[str]) -> Optional[str]:
    try:
        return _iso(parsedate_to_datetime(s)) if s else None
    except (TypeError, ValueError):
        return None


def _parse_iso(s: Optional[str]) -> Optional[str]:
    try:
        return _iso(datetime.fromisoformat(s.replace("Z", "+00:00"))) if s else None
    except ValueError:
        return None


def _url_key(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    p = urlsplit(url)
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), "", ""))


def _title_key(title: Optional[str]) -> Optional[str]:
    if not title:
        return None
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip() or None


def _get(url: str) -> bytes:
    r = httpx.get(url, headers=HEADERS, timeout=10.0, follow_redirects=True)
    r.raise_for_status()
    return r.content


def _rss_items(content: bytes):
    root = ET.fromstring(content)
    return root.findall(".//item")


def _text(item, tag: str) -> Optional[str]:
    el = item.find(tag)
    return el.text.strip() if el is not None and el.text else None


# --------------------------------------------------------------------------- providers
# Each returns a list of {title, url, published_at, source, summary}; exceptions mean failure.

def _from_yfinance(ticker: str) -> List[Dict]:
    out = []
    for n in yf.Ticker(ticker).news or []:
        c = n.get("content", n)
        out.append({
            "title": _clean(c.get("title")),
            "url": (c.get("canonicalUrl") or {}).get("url") or c.get("link"),
            "published_at": _parse_iso(c.get("pubDate")),
            "source": (c.get("provider") or {}).get("displayName"),
            "summary": _clean(c.get("summary") or c.get("description")),
        })
    return out


def _from_yahoo_rss(ticker: str) -> List[Dict]:
    url = f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={quote(ticker)}&region=US&lang=en-US"
    return [{
        "title": _clean(_text(i, "title")),
        "url": _text(i, "link"),
        "published_at": _parse_rfc822(_text(i, "pubDate")),
        "source": None,  # feed does not name the publisher
        "summary": _clean(_text(i, "description")),
    } for i in _rss_items(_get(url))]


def _from_finnhub(ticker: str) -> List[Dict]:
    """Finnhub company news, last 30 days (free tier: 60 calls a minute). Needs FINNHUB_API_KEY."""
    key = os.getenv("FINNHUB_API_KEY") or os.getenv("VITE_FINNHUB_API_KEY")
    if not key:
        raise RuntimeError("FINNHUB_API_KEY not set")
    today = _now().date()
    # key goes in a header, not the URL, so it never shows up in an error message or the dashboard
    r = httpx.get("https://finnhub.io/api/v1/company-news", headers={**HEADERS, "X-Finnhub-Token": key},
                  timeout=10.0, params={"symbol": ticker.upper(), "from": (today - timedelta(days=30)).isoformat(),
                                        "to": today.isoformat()})
    r.raise_for_status()
    out = []
    for n in r.json():
        out.append({
            "title": _clean(n.get("headline")),
            "url": n.get("url"),
            "published_at": _iso(datetime.fromtimestamp(n["datetime"], timezone.utc)) if n.get("datetime") else None,
            "source": n.get("source"),
            "summary": _clean(n.get("summary")),
        })
    return out


def _from_google_rss(ticker: str) -> List[Dict]:
    url = f"https://news.google.com/rss/search?q={quote(ticker + ' stock')}&hl=en-US&gl=US&ceid=US:en"
    out = []
    for i in _rss_items(_get(url)):
        title, source = _text(i, "title"), _text(i, "source")
        if title and source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        out.append({
            "title": _clean(title),
            "url": _text(i, "link"),
            "published_at": _parse_rfc822(_text(i, "pubDate")),
            "source": source,
            "summary": None,  # Google's description only repeats the headline
        })
    return out


PROVIDERS: Dict[str, Callable[[str], List[Dict]]] = {
    "finnhub": _from_finnhub,
    "yahoo_rss": _from_yahoo_rss,
    "google_rss": _from_google_rss,
    "yfinance": _from_yfinance,
}


# --------------------------------------------------------------------------- merge

def _majority(values: List[Optional[str]]) -> Optional[str]:
    """Most common non-empty value; ties go to the earliest (highest-priority provider)."""
    present = [v for v in values if v]
    if not present:
        return None
    counts = Counter(present)
    best = max(counts.values())
    return next(v for v in present if counts[v] == best)


def merge_articles(by_provider: Dict[str, List[Dict]]) -> List[Dict]:
    """Group the same article across providers and resolve each field by majority."""
    groups: List[List[tuple]] = []
    index: Dict[str, int] = {}
    for provider in PROVIDER_ORDER:
        for art in by_provider.get(provider, []):
            if not art.get("title"):
                continue
            keys = [k for k in (f"u:{_url_key(art.get('url'))}" if art.get("url") else None,
                                f"t:{_title_key(art['title'])}") if k]
            gi = next((index[k] for k in keys if k in index), None)
            if gi is None:
                gi = len(groups)
                groups.append([])
            groups[gi].append((provider, art))
            for k in keys:
                index.setdefault(k, gi)

    merged = []
    for members in groups:
        members.sort(key=lambda m: PROVIDER_ORDER.index(m[0]))
        arts = [a for _, a in members]
        summary = _majority([a.get("summary") for a in arts])
        merged.append({
            "title": _majority([a.get("title") for a in arts]),
            "url": _majority([a.get("url") for a in arts]),
            "published_at": _majority([a.get("published_at") for a in arts]),
            "source": _majority([a.get("source") for a in arts]) or "Unknown",
            "summary": summary,
            "headline_only": summary is None,
            "providers": sorted({p for p, _ in members}, key=PROVIDER_ORDER.index),
        })
    return merged


# --------------------------------------------------------------------------- tool

def get_news_stream(ticker: str, days: int = 7, max_articles: int = 15) -> Dict:
    """
    MCP tool: recent news for a ticker, merged across free providers.

    Returns {success, ticker, articles[{id, title, url, published_at, source, summary,
    headline_only, providers}], count, time_range, sources, providers_ok, providers_failed,
    warnings} or {success: False, ticker, error, providers_failed}.
    """
    days = max(1, min(days, 30))
    max_articles = max(1, min(max_articles, 50))

    results: Dict[str, List[Dict]] = {}
    failed: Dict[str, str] = {}
    skipped: Dict[str, str] = {}

    def run(names: List[str]):
        with ThreadPoolExecutor(max_workers=len(names)) as pool:
            futures = {name: pool.submit(PROVIDERS[name], ticker) for name in names}
            for name, fut in futures.items():
                try:
                    results[name] = fut.result()
                except Exception as e:
                    failed[name] = str(e)[:200]

    primary = list(PRIMARY)
    if not (os.getenv("FINNHUB_API_KEY") or os.getenv("VITE_FINNHUB_API_KEY")):
        primary.remove("finnhub")
        skipped["finnhub"] = "no FINNHUB_API_KEY set"
    run(primary)
    if sum(len(v) for v in results.values()) < MIN_ARTICLES:
        run(["yfinance"])          # last resort
    else:
        skipped["yfinance"] = "not needed: the other feeds returned enough articles"

    if not results:
        return {"success": False, "ticker": ticker, "articles": [], "count": 0,
                "error": "all news providers failed", "providers_failed": failed}

    cutoff = _now() - timedelta(days=days)
    articles = []
    for a in merge_articles(results):
        when = a["published_at"]
        if when and datetime.fromisoformat(when.replace("Z", "+00:00")) < cutoff:
            continue
        articles.append(a)
    articles.sort(key=lambda a: a["published_at"] or "", reverse=True)
    articles = articles[:max_articles]
    for i, a in enumerate(articles, start=1):
        a["id"] = f"news:{i}"

    warnings = [f"news provider {p} failed: {msg}" for p, msg in failed.items()]
    headline_only = sum(a["headline_only"] for a in articles)
    if headline_only:
        warnings.append(f"{headline_only} of {len(articles)} articles have no summary (headline only)")

    if len(results) == 1:
        warnings.append(f"news coverage is thin: only {next(iter(results))} answered")

    return {
        "success": True,
        "ticker": ticker,
        "articles": articles,
        "count": len(articles),
        "time_range": f"Last {days} days",
        "sources": sorted({a["source"] for a in articles}),
        "providers_ok": [p for p in PROVIDER_ORDER if p in results],
        "providers_failed": failed,
        "providers_skipped": skipped,
        # per-feed coverage: articles each feed returned (before merging and the time window),
        # and how many of the articles kept come from it
        "provider_counts": {p: len(results[p]) for p in PROVIDER_ORDER if p in results},
        "articles_by_provider": {p: sum(p in a["providers"] for a in articles)
                                 for p in PROVIDER_ORDER if p in results},
        "warnings": warnings,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(get_news_stream("AAPL", days=7, max_articles=10), indent=2))
