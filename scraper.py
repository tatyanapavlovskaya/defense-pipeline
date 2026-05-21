"""
Fetch article headlines, URLs, and dates from nordicdefencesector.com
for Sweden, using the site's internal JSON API.
"""

import sys
import os
import requests

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


def fetch_articles(limit: int = 50) -> list[dict]:
    """Return a list of article dicts from the nordicdefencesector API."""
    params = {
        "country": "sweden",
        "sortBy": "desc",
        "limit": str(limit),
        "articleType": "standard",
    }
    resp = requests.get(API_URL, params=params, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    data = resp.json()

    articles = []
    for a in data.get("articles", []):
        lang = a.get("language") or "sv"
        slug = a.get("slug", "")
        url = f"{BASE_URL}/{lang}/article/{slug}" if slug else ""

        raw_date = a.get("publishedDate", "")
        date = raw_date[:10] if raw_date else ""

        articles.append({
            "date": date,
            "headline": (a.get("title") or "").strip(),
            "url": url,
            "summary": (a.get("summary") or a.get("excerpt") or "").strip(),
            "source": "nordicdefencesector.com",
        })

    return articles


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
    print(f"Fetching Sweden articles from {API_URL} ...\n")
    try:
        articles = fetch_articles(limit=50)
    except requests.RequestException as e:
        sys.exit(f"Request failed: {e}")

    print(f"Found {len(articles)} article(s):\n")
    print_articles(articles)


if __name__ == "__main__":
    main()
