"""
2-step keyword funnel for filtering news articles.

Filter 1 — Company match (case-insensitive substring):
    Article text must mention at least one company from the Company Names column.
    Matching strips common legal suffixes so "Brokk Sverige AB" also matches
    plain "Brokk Sverige" in an article, and vice-versa.

Filter 2 — Geo & Commercial match (case-insensitive substring):
    Article text must mention at least one term from the Swedish or English
    Geo & Commercial column.

Text searched: full article body if freely available, else headline + summary.
"""

import re

# Legal entity suffixes to strip when building match variants
_LEGAL_SUFFIX_RE = re.compile(
    r"\s+(AB|ABP|HB|KB|EK|EF|SF|BRF|KF|"
    r"Ltd\.?|LLC|GmbH|AS|ASA|SA|NV|BV|PLC|Inc\.?|Corp\.?|Co\.?|"
    r"Oy|ApS|A/S|SIA|UAB|OÜ)$",
    re.IGNORECASE,
)


def _company_variants(name: str) -> list[str]:
    """
    Return lowercase match variants for a company name:
    - The full name as-is
    - The name with legal suffix stripped (if different)

    Example: "Brokk Sverige AB" → ["brokk sverige ab", "brokk sverige"]
    Example: "Saab"             → ["saab"]
    """
    lower = name.strip().lower()
    stripped = _LEGAL_SUFFIX_RE.sub("", name.strip()).strip().lower()
    variants = [lower]
    if stripped and stripped != lower and len(stripped) > 2:
        variants.append(stripped)
    return variants


def _matches_any(text_lower: str, keywords: list[str]) -> list[str]:
    """Return keywords that appear as substrings in text_lower."""
    return [kw for kw in keywords if kw and kw in text_lower]


def _matches_any_company(text_lower: str, company_names: list[str]) -> list[str]:
    """
    Match company names with legal-suffix-stripped variants.
    Returns matched keyword strings (the original form from the list).
    """
    matched = []
    for name in company_names:
        for variant in _company_variants(name):
            if variant in text_lower:
                matched.append(name)
                break
    return matched


def apply_filters(
    article: dict,
    company_names: list[str],
    geo_commercial_sv: list[str],
    geo_commercial_en: list[str],
    full_text: str | None = None,
) -> dict | None:
    """
    Run both filters on a single article dict.

    Searches full_text when provided; falls back to headline + summary.

    Returns a result dict if the article passes both filters, None otherwise:
        {
            "article":            original article dict,
            "full_text":          text that was searched (full or summary),
            "matched_companies":  list of matched company keyword strings,
            "matched_geo":        list of matched geo/commercial keyword strings,
            "text_source":        "full" | "summary",
        }
    """
    if full_text and len(full_text) >= 300:
        searchable = full_text.lower()
        text_source = "full"
    else:
        searchable = (
            (article.get("headline") or "") + " " +
            (article.get("summary") or "")
        ).lower()
        text_source = "summary"

    # Filter 1: company name mention
    matched_companies = _matches_any_company(searchable, company_names)
    if not matched_companies:
        return None

    # Filter 2: geo or commercial keyword (SV or EN)
    matched_geo = (
        _matches_any(searchable, geo_commercial_sv) +
        _matches_any(searchable, geo_commercial_en)
    )
    if not matched_geo:
        return None

    return {
        "article":           article,
        "full_text":         full_text if text_source == "full" else None,
        "matched_companies": matched_companies,
        "matched_geo":       matched_geo,
        "text_source":       text_source,
    }


def run_funnel(
    articles: list[dict],
    company_names: list[str],
    geo_commercial_sv: list[str],
    geo_commercial_en: list[str],
    existing_urls: set[str] | None = None,
    fetch_full: bool = True,
) -> list[dict]:
    """
    Apply both filters to a list of articles.
    Skips articles whose URL is already in existing_urls.
    When fetch_full=True, attempts to fetch the full article text first.
    Returns list of result dicts for articles that pass both filters.
    """
    from scraper import fetch_full_article

    existing_urls = existing_urls or set()
    passed = []

    for article in articles:
        url = article.get("url", "")
        if url in existing_urls:
            continue

        full_text = fetch_full_article(url) if fetch_full else None
        result = apply_filters(
            article, company_names, geo_commercial_sv, geo_commercial_en, full_text
        )
        if result:
            passed.append(result)

    return passed


def results_to_news_rows(results: list[dict]) -> list[list]:
    """
    Convert filter results to rows for the News & Drafts tab.
    Columns: Date | Company Name | Org Nr | News Source | Headline |
             Article URL | Trigger Keywords Matched | Pitch Points |
             Draft Email | Contact Name | Contact Title | Contact Email |
             Contact Source | Status
    """
    rows = []
    for r in results:
        a = r["article"]
        matched_companies = r["matched_companies"]
        matched_geo = r["matched_geo"]
        all_keywords = sorted(set(matched_companies + matched_geo))

        company_name = _recover_cased_name(
            matched_companies,
            (a.get("headline") or "") + " " + (a.get("summary") or ""),
            r.get("full_text") or "",
        )

        rows.append([
            a.get("date", ""),
            company_name,
            "",                          # Org Nr
            a.get("source", ""),
            a.get("headline", ""),
            a.get("url", ""),
            ", ".join(all_keywords),     # Trigger Keywords Matched
            "",                          # Pitch Points (Claude fills this)
            "",                          # Draft Email (Claude fills this)
            "",                          # Contact Name
            "",                          # Contact Title
            "",                          # Contact Email
            "",                          # Contact Source
            "New",                       # Status
        ])
    return rows


def _recover_cased_name(
    matched_keywords: list[str], text: str, full_text: str = ""
) -> str:
    """
    Recover the properly-cased company name by searching headline+summary
    first, then the full article body. Falls back to the keyword itself.
    """
    if not matched_keywords:
        return ""
    kw = matched_keywords[0]
    for search_text in (text, full_text):
        if not search_text:
            continue
        for variant in _company_variants(kw):
            m = re.search(re.escape(variant), search_text, re.IGNORECASE)
            if m:
                return search_text[m.start():m.end()]
    return kw
