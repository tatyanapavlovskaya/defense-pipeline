"""
Fetch article headlines, URLs, and dates from nordicdefencesector.com
for Sweden, using the site's internal JSON API.

Pagination: the API supports ?limit=N&offset=N.
  - Total Sweden articles: ~1 284 (checked 2026-05-22)
  - fetch_articles()     → newest N articles (for weekly incremental runs)
  - fetch_all_articles() → full backlog via paged requests, deduped by slug
                           (keeps English version when sv+en pair exists)
"""

import re
import sys
import os
import time
import requests
from bs4 import BeautifulSoup

# Ensure UTF-8 output on Windows so Swedish characters print correctly
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_URL = "https://nordicdefencesector.com"
API_URL = f"{BASE_URL}/api/articles"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": f"{BASE_URL}/?country=sweden",
}

_PAGE_SIZE = 100  # max batch for backlog fetching


def _parse_article(a: dict) -> dict:
    """Convert a raw API article dict to our internal format."""
    lang = a.get("language") or "sv"
    slug = a.get("slug", "")
    url = f"{BASE_URL}/{lang}/article/{slug}" if slug else ""
    raw_date = a.get("publishedDate", "")
    date = raw_date[:10] if raw_date else ""
    return {
        "date": date,
        "headline": (a.get("title") or "").strip(),
        "url": url,
        "summary": (a.get("summary") or a.get("excerpt") or "").strip(),
        "source": "nordicdefencesector.com",
        "_slug": slug,
        "_lang": lang,
    }


def _deduplicate_by_slug(articles: list[dict]) -> list[dict]:
    """
    When the same story exists in both Swedish and English (same slug),
    keep the English version. Otherwise keep the first occurrence.

    Returns articles in the same order, duplicates removed.
    """
    seen: dict[str, dict] = {}  # slug → article
    for a in articles:
        slug = a["_slug"]
        if slug not in seen:
            seen[slug] = a
        else:
            # Prefer English over Swedish
            if a["_lang"] == "en" and seen[slug]["_lang"] != "en":
                seen[slug] = a
    # Preserve insertion order of winning articles
    order = list(dict.fromkeys(a["_slug"] for a in articles))
    return [seen[s] for s in order]


def _strip_internal_fields(articles: list[dict]) -> list[dict]:
    """Remove _slug / _lang helper fields before returning to callers."""
    return [{k: v for k, v in a.items() if not k.startswith("_")} for a in articles]


def fetch_articles(limit: int = 50, offset: int = 0) -> list[dict]:
    """
    Return up to `limit` articles starting at `offset`, newest-first.
    Deduplicates translation pairs (keeps English).
    """
    params = {
        "country": "sweden",
        "sortBy": "desc",
        "limit": str(limit),
        "offset": str(offset),
        "articleType": "standard",
    }
    resp = requests.get(API_URL, params=params, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    articles = [_parse_article(a) for a in data.get("articles", [])]
    articles = _deduplicate_by_slug(articles)
    return _strip_internal_fields(articles)


def fetch_all_articles(
    page_size: int = _PAGE_SIZE,
    delay: float = 0.5,
    progress: bool = True,
) -> list[dict]:
    """
    Fetch every Sweden article from the API using offset pagination.
    Deduplicates translation pairs (keeps English).

    Args:
        page_size: Articles per API request (max 100).
        delay:     Seconds to sleep between requests (be polite).
        progress:  Print progress to stdout.

    Returns:
        All unique articles, sorted newest-first.
    """
    all_raw: list[dict] = []
    offset = 0

    # First request to learn total
    params_base = {
        "country": "sweden",
        "sortBy": "desc",
        "limit": str(page_size),
        "articleType": "standard",
    }

    while True:
        params = {**params_base, "offset": str(offset)}
        resp = requests.get(API_URL, params=params, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        batch = data.get("articles", [])
        pagination = data.get("pagination", {})
        total = pagination.get("total", 0)

        all_raw.extend(_parse_article(a) for a in batch)

        if progress:
            pct = min(100, int(len(all_raw) * 100 / total)) if total else 0
            print(
                f"  Fetched {len(all_raw)}/{total} articles … {pct}%",
                end="\r", flush=True,
            )

        if not batch or len(all_raw) >= total:
            break

        offset += len(batch)
        time.sleep(delay)

    if progress:
        print()  # newline after \r progress

    deduped = _deduplicate_by_slug(all_raw)
    removed = len(all_raw) - len(deduped)
    if removed and progress:
        print(f"  Removed {removed} translation duplicate(s) (kept English versions).")

    return _strip_internal_fields(deduped)


# ── Full-article fetching ──────────────────────────────────────────────────────

_ARTICLE_SELECTORS = [
    "article", "[class*='article-body']", "[class*='article-content']",
    "[class*='articleBody']", "[class*='post-body']", "[class*='story-body']",
    "[class*='entry-content']", "main",
]

_PAYWALL_PATTERNS = re.compile(
    r"prenumer|subscribe|paywall|subscriber.only|logga in för att|"
    r"sign in to read|unlock this article|become a member|register to read|"
    r"för att läsa|skapa konto|create account",
    re.I,
)

_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
}


def fetch_full_article(url: str, min_chars: int = 300) -> str | None:
    """
    Attempt to fetch the full article text from a URL.

    Returns the article body text if the page is freely accessible and
    contains enough content. Returns None if:
      - The request fails
      - A paywall / login wall is detected
      - The extracted text is shorter than min_chars (paywalled stub)
    """
    if not url:
        return None
    try:
        resp = requests.get(url, headers=_FETCH_HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "lxml")

        # Check for paywall signals in the raw HTML
        if _PAYWALL_PATTERNS.search(resp.text[:5000]):
            return None

        # Try each selector until we get enough text
        for selector in _ARTICLE_SELECTORS:
            el = soup.select_one(selector)
            if el:
                text = el.get_text(separator=" ", strip=True)
                if len(text) >= min_chars:
                    return text

        return None
    except Exception:
        return None


# ── CLI helpers ───────────────────────────────────────────────────────────────

def print_articles(articles: list[dict]) -> None:
    print(f"{'#':<4} {'Date':<12} {'Headline'}")
    print("-" * 110)
    for i, a in enumerate(articles, 1):
        headline = a["headline"]
        if len(headline) > 80:
            headline = headline[:77] + "..."
        print(f"{i:<4} {a['date']:<12} {headline}")
        print(f"     {a['url']}")
        if a["summary"]:
            summary = a["summary"][:100] + ("..." if len(a["summary"]) > 100 else "")
            print(f"     {summary}")
        print()


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="Fetch all articles (full backlog)")
    parser.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    print(f"Fetching Sweden articles from {API_URL} ...\n")
    try:
        if args.all:
            articles = fetch_all_articles()
        else:
            articles = fetch_articles(limit=args.limit)
    except requests.RequestException as e:
        sys.exit(f"Request failed: {e}")

    print(f"Found {len(articles)} article(s):\n")
    print_articles(articles)


if __name__ == "__main__":
    main()
