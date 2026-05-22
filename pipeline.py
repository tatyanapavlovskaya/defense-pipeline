"""
Main pipeline runner.

Steps:
  1. Open Google Sheet tabs
  2. Sync company names from Companies → Keywords tab
  3. Load keywords from Keywords tab
  4. Fetch latest articles from nordicdefencesector.com
  5. Run 2-step keyword funnel (fetches full article text when possible)
  6. For each passing article, generate pitch points + draft email via Claude
  7. Append results to News & Drafts tab
"""

import os
import sys
import re
import anthropic
from dotenv import load_dotenv

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

from scraper import fetch_articles, fetch_all_articles
from filters import run_funnel, results_to_news_rows
from sheet import (
    get_tabs,
    sync_company_keywords,
    read_keywords,
    append_news_rows,
)

CLAUDE_MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 1200

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[a-z]{2,}", re.I)


def _last_known_contact_cached(news_rows: list[list], company_name: str) -> dict:
    """
    Search the already-fetched news rows (list of lists) for the most recent
    contact for this company. No extra Sheets API calls needed.
    NEWS_HEADERS indices: Company=1, Contact Name=9, Title=10, Email=11, Source=12
    """
    target = company_name.strip().lower()
    match = {"name": "", "title": "", "email": "", "source": ""}
    for row in news_rows[1:]:
        if len(row) < 13:
            continue
        if row[1].strip().lower() == target and any(row[9:13]):
            match = {
                "name":   row[9].strip(),
                "title":  row[10].strip(),
                "email":  row[11].strip(),
                "source": row[12].strip(),
            }
    return match


def _extract_contact_from_text(text: str) -> dict:
    """
    Very lightweight contact extraction: look for an email address in the
    article text and return it. Name and title are left empty (Claude could
    do this with a dedicated prompt but it's kept simple here).
    """
    if not text:
        return {"name": "", "title": "", "email": "", "source": ""}
    m = _EMAIL_RE.search(text)
    if m:
        return {"name": "", "title": "", "email": m.group(), "source": "article"}
    return {"name": "", "title": "", "email": "", "source": ""}


_PITCH_SYSTEM = (
    "You are a business development assistant at Civitta, a strategy consulting firm "
    "that specialises in defence-sector innovation, procurement advisory, and export "
    "market entry for Scandinavian and Baltic companies.\n"
    "Your task: given a news article about a Swedish defence company, produce:\n"
    "1. Three to five concise pitch points explaining why Civitta can help this company "
    "right now (1–2 sentences each, grounded in the article).\n"
    "2. A short outreach email (subject line + body, ≤180 words) addressed to "
    "the company's Head of Innovation or VP of Sales, referencing the news and "
    "offering a 30-minute exploratory call.\n"
    "Write in professional English. Output format:\n"
    "PITCH POINTS:\n- …\n\nDRAFT EMAIL:\nSubject: …\n\n<body>"
)


def _generate_content(
    client: anthropic.Anthropic,
    company: str,
    headline: str,
    article_text: str,
    matched_geo: list[str],
) -> tuple[str, str]:
    """
    Call Claude to generate pitch points and draft email.
    Returns (pitch_points, draft_email) strings.
    """
    geo_hint = ", ".join(matched_geo[:5]) if matched_geo else ""
    body_snippet = article_text[:2000] if article_text else headline

    prompt = (
        f"Company: {company}\n"
        f"Headline: {headline}\n"
        f"Relevant keywords: {geo_hint}\n\n"
        f"Article excerpt:\n{body_snippet}"
    )

    msg = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        system=_PITCH_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()

    pitch, email = "", raw
    if "DRAFT EMAIL:" in raw:
        parts = raw.split("DRAFT EMAIL:", 1)
        pitch = parts[0].replace("PITCH POINTS:", "").strip()
        email = parts[1].strip()
    elif "PITCH POINTS:" in raw:
        pitch = raw.replace("PITCH POINTS:", "").strip()

    return pitch, email


def run_pipeline(
    article_limit: int = 50,
    dry_run: bool = False,
    backlog: bool = False,
) -> None:
    print("=== Civitta Defense Pipeline ===\n")

    # 1. Open sheet tabs
    print("Opening Google Sheet...")
    tabs = get_tabs()
    companies_ws = tabs["Companies"]
    news_ws = tabs["News & Drafts"]
    keywords_ws = tabs["Keywords"]

    # 2. Sync company names to Keywords tab
    added = sync_company_keywords(companies_ws, keywords_ws)
    if added:
        print(f"Synced {added} new company name(s) to Keywords tab.")
    else:
        print("Keywords tab already up to date.")

    # 3. Load keywords
    kw = read_keywords(keywords_ws)
    company_names    = kw["company_names"]
    geo_sv           = kw["geo_commercial_sv"]
    geo_en           = kw["geo_commercial_en"]
    print(
        f"Loaded keywords: {len(company_names)} companies, "
        f"{len(geo_sv)} SV terms, {len(geo_en)} EN terms."
    )

    if not company_names:
        print("No company keywords — nothing to filter against. Exiting.")
        return

    # 4. Fetch articles
    if backlog:
        print("\nBacklog mode: fetching ALL Sweden articles (this may take ~2 minutes)...")
        articles = fetch_all_articles(progress=True)
    else:
        print(f"\nFetching up to {article_limit} latest articles from nordicdefencesector.com...")
        articles = fetch_articles(limit=article_limit)
    print(f"Fetched {len(articles)} article(s).")

    # 5. Read News & Drafts tab ONCE — used for both URL dedup and contact lookup cache
    print("Reading existing News & Drafts rows...")
    existing_news_rows = news_ws.get_all_values()  # single API call, cached in memory
    existing_urls = {r[5].strip() for r in existing_news_rows[1:] if len(r) > 5 and r[5].strip()}
    print(f"Skipping {len(existing_urls)} already-recorded URL(s).")

    # 6. Run funnel (fetches full text, applies 2-step filter)
    # For backlog runs, skip per-article HTTP fetches — headline+summary is
    # sufficient for the initial historical scan and avoids ~1000 extra requests.
    fetch_full = not backlog
    if backlog:
        print("Running keyword funnel on headline+summary (backlog mode, no full-text fetch)...")
    else:
        print("Running keyword funnel (fetching full article text where available)...")
    results = run_funnel(
        articles,
        company_names,
        geo_sv,
        geo_en,
        existing_urls=existing_urls,
        fetch_full=fetch_full,
    )
    print(f"{len(results)} article(s) passed both filters.")

    if not results:
        print("Nothing new to write.")
        return

    # 7. Enrich each result with Claude-generated content + contact
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
    use_claude = bool(anthropic_key)
    if use_claude:
        client = anthropic.Anthropic(api_key=anthropic_key)
        print(f"Generating content with {CLAUDE_MODEL}...")
    else:
        print("ANTHROPIC_API_KEY not set — skipping AI content generation.")

    rows = results_to_news_rows(results)  # 14-column rows, pitch/email empty

    for i, (result, row) in enumerate(zip(results, rows)):
        a = result["article"]
        company = row[1]  # Company Name (col index 1)
        headline = a.get("headline", "")
        article_text = result.get("full_text") or ""
        matched_geo = result["matched_geo"]

        # Contact: try article text, then search cached rows (no extra API calls)
        contact = _extract_contact_from_text(article_text)
        if not contact["email"]:
            contact = _last_known_contact_cached(existing_news_rows, company)

        row[9]  = contact.get("name", "")
        row[10] = contact.get("title", "")
        row[11] = contact.get("email", "")
        row[12] = contact.get("source", "")

        if use_claude:
            try:
                pitch, email_draft = _generate_content(
                    client, company, headline, article_text, matched_geo
                )
                row[7] = pitch        # Pitch Points
                row[8] = email_draft  # Draft Email
            except Exception as e:
                print(f"  Claude error for '{headline[:60]}': {e}")

        print(
            f"  [{i+1}/{len(results)}] {a['date']} | {company} | "
            f"{headline[:60]}{'...' if len(headline)>60 else ''}"
        )

    # 8. Write to sheet
    if dry_run:
        print(f"\n[dry-run] Would write {len(rows)} row(s) — skipping sheet write.")
        return

    written = append_news_rows(news_ws, rows)
    print(f"\nWrote {written} row(s) to News & Drafts tab.")
    print(f"Spreadsheet: {news_ws.spreadsheet.url}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Civitta Defense Pipeline")
    parser.add_argument("--limit", type=int, default=50,
                        help="Max articles to fetch for incremental run (default 50)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run funnel but don't write to sheet")
    parser.add_argument("--backlog", action="store_true",
                        help="Fetch ALL historical articles (full backlog, ~1284 for Sweden)")
    args = parser.parse_args()

    run_pipeline(article_limit=args.limit, dry_run=args.dry_run, backlog=args.backlog)
