"""Data & Analytics News: fetch, filter, normalize, cache.

This is the ONLY module that talks to the external news API. It runs
server-side only, the API key never reaches the browser (see
routes/news.py, which returns nothing but the already-normalized
JSON below).

Design goals (see the product brief this implements):
  - Never break the homepage. Every failure mode here is caught and
    turned into an "unavailable" status, never an exception that
    bubbles up into a 500.
  - Don't hammer the API. Results are cached in-memory for
    Config.NEWS_CACHE_TTL_SECONDS and only refreshed when stale.
  - Only show articles relevant to data/analytics/AI/health-data/
    tech, not general news. Two keyword passes: a relevance allowlist
    and a blocklist for topics we never want (politics, celebrity,
    sports, crime, entertainment).

This is intentionally a simple in-memory cache (a module-level dict
behind a lock), not Redis. That's enough for a single-process/
single-dyno MVP; if Omixa ever runs multiple instances without a
shared cache, each will refetch independently, which is still fine
since it's still bounded by the TTL per-instance.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

import requests

from config import Config

logger = logging.getLogger(__name__)

# Broad search terms sent to the news API itself. Kept fairly wide on
# purpose, RELEVANCE_KEYWORDS/BLOCKLIST_KEYWORDS below do the real
# filtering on whatever comes back.
SEARCH_TERMS = [
    "data quality",
    "data cleaning",
    "data analytics",
    "data science",
    "data engineering",
    "spreadsheet software",
    "database technology",
    "artificial intelligence data",
    "machine learning",
    "digital health data",
    "African technology",
]

# An article must mention at least one of these (case-insensitive, in
# its title or description) to be considered relevant at all.
RELEVANCE_KEYWORDS = [
    "data quality", "data cleaning", "data clean", "data analytics",
    "data science", "data engineering", "dataset", "database",
    "spreadsheet", "data pipeline", "data governance", "big data",
    "data infrastructure", "data platform", "data management",
    "artificial intelligence", "machine learning", "generative ai",
    " ai ", "ai model", "ai tool", "llm", "large language model",
    "health data", "digital health", "healthcare data", "health tech",
    "medical data", "fintech", "cloud computing", "cybersecurity",
    "tech startup", "african tech", "africa tech", "startup africa",
    "software", "developer tool",
]

# Anything mentioning these is dropped outright, even if it also
# matched a relevance keyword above. Keeps out things like "AI plot
# twist in the new season of [TV show]".
BLOCKLIST_KEYWORDS = [
    "celebrity", "kardashian", "royal family", "box office", "election",
    "senator", "congress", "parliament", "president trump",
    "president biden", "football", " nba ", " nfl ", " nhl ", "soccer",
    "olympics", "grammy", "oscars", "murder", "shooting", "homicide",
    "stabbing", "sex tape", "divorce", "dating rumor", "reality tv",
    "box-office",
]

# Homepage/news-page filter tabs, mapped to the keywords that tag an
# article as belonging to that tab. An article can belong to more
# than one.
CATEGORY_KEYWORDS = {
    "data": [
        "data quality", "data cleaning", "data clean", "dataset",
        "database", "spreadsheet", "data pipeline", "data governance",
        "data engineering", "big data", "data infrastructure",
        "data platform", "data management",
    ],
    "analytics": [
        "data analytics", "analytics", "data science",
        "business intelligence", "data visualization",
    ],
    "ai": [
        "artificial intelligence", "machine learning", " ai ",
        "ai model", "ai tool", "llm", "large language model",
        "generative ai",
    ],
    "health": [
        "health data", "digital health", "healthcare data",
        "health tech", "medical data",
    ],
    "technology": [
        "technology", "software", "cloud computing", "cybersecurity",
        "startup", "developer tool", "african tech", "africa tech",
    ],
}

FALLBACK_IMAGE = "/static/img/news-fallback.svg"

_cache_lock = threading.Lock()
_cache = {"articles": [], "fetched_at": 0.0, "ok": False}


def _pad(text: str) -> str:
    """Pads with spaces so word-boundary keywords like ' ai ' can
    match at the very start/end of a string too."""
    return f" {text.lower()} "


def _matches_any(haystack: str, keywords: list[str]) -> bool:
    padded = _pad(haystack)
    return any(kw in padded for kw in keywords)


def _categorize(title: str, description: str) -> list[str]:
    text = f"{title} {description}"
    tags = [cat for cat, kws in CATEGORY_KEYWORDS.items() if _matches_any(text, kws)]
    return tags or ["technology"]


def _normalize(raw: dict) -> dict | None:
    title = (raw.get("title") or "").strip()
    url = raw.get("url") or ""
    if not title or not url or title.lower() == "[removed]":
        return None

    description = (raw.get("description") or "").strip()
    source = ((raw.get("source") or {}).get("name") or "").strip() or "Unknown source"

    published_display = ""
    published_at = raw.get("publishedAt") or ""
    if published_at:
        try:
            dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            published_display = dt.strftime("%b %d, %Y")
        except ValueError:
            pass

    return {
        "title": title,
        "description": description[:220],
        "url": url,
        "image": raw.get("urlToImage") or FALLBACK_IMAGE,
        "source": source,
        "published_display": published_display,
        "categories": _categorize(title, description),
    }


def _fetch_from_api() -> list[dict]:
    if not Config.NEWS_API_KEY:
        raise RuntimeError("NEWS_API_KEY is not configured")

    query = " OR ".join(f'"{term}"' for term in SEARCH_TERMS)
    params = {
        "q": query,
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 50,
        "apiKey": Config.NEWS_API_KEY,
    }
    resp = requests.get(Config.NEWS_API_URL, params=params, timeout=6)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("status") != "ok":
        raise RuntimeError(f"news API returned status={payload.get('status')!r}")
    return payload.get("articles", [])


def _refresh_cache() -> None:
    """Fetches, filters, and normalizes fresh articles, then swaps
    them into the module-level cache. Never raises: any failure is
    logged and leaves the previous cache contents in place."""
    try:
        raw_articles = _fetch_from_api()
    except Exception:
        logger.exception("Data & Analytics news refresh failed; serving stale/empty cache")
        with _cache_lock:
            _cache["fetched_at"] = time.time()
        return

    seen_urls = set()
    filtered = []
    for raw in raw_articles:
        article = _normalize(raw)
        if not article or article["url"] in seen_urls:
            continue

        haystack = f"{article['title']} {article['description']}"
        if _matches_any(haystack, BLOCKLIST_KEYWORDS):
            continue
        if not _matches_any(haystack, RELEVANCE_KEYWORDS):
            continue

        seen_urls.add(article["url"])
        filtered.append(article)
        if len(filtered) >= Config.NEWS_MAX_ARTICLES:
            break

    with _cache_lock:
        _cache["articles"] = filtered
        _cache["fetched_at"] = time.time()
        _cache["ok"] = True


def get_articles(category: str | None = None) -> tuple[list[dict], str]:
    """Returns (articles, status) for a homepage/news-page request.

    status is "ok" once we have a usable cache (even if a filter
    narrows it to zero results, that's a normal "nothing in this
    category" case, not an outage) or "unavailable" if we have never
    successfully fetched anything. Never raises.
    """
    try:
        age = time.time() - _cache["fetched_at"]
        if _cache["fetched_at"] == 0 or age > Config.NEWS_CACHE_TTL_SECONDS:
            _refresh_cache()
    except Exception:
        logger.exception("Unexpected error refreshing news cache")

    with _cache_lock:
        articles = list(_cache["articles"])
        has_ever_succeeded = _cache["ok"]

    if not has_ever_succeeded:
        return [], "unavailable"

    if category and category != "all":
        articles = [a for a in articles if category in a["categories"]]

    return articles, "ok"
