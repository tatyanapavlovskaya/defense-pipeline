"""
Scrape member lists from three Swedish defence industry associations
and write them to the Companies tab in Google Sheets.

Sources:
  SOFF          - soff.se         (491 members via WordPress AJAX)
  SME-D         - sme-d.se        (5 pages, HTML)
  CivilSecurity - civilsecurity.se (JS-rendered logos — skipped for now)
"""

import os
import sys
import re
import requests
from bs4 import BeautifulSoup
from sheet import get_tabs, get_existing_company_names, append_companies

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


# ── SOFF ──────────────────────────────────────────────────────────────────────

def scrape_soff() -> list[dict]:
    """
    Fetch all SOFF members via the WordPress AJAX endpoint used by the
    interactive map on soff.se/en/members/.
    Returns name + SOFF member page URL for each company.
    """
    print("Scraping SOFF...")

    # The nonce rotates with each page load — fetch a fresh one
    nonce = _get_soff_nonce()

    resp = requests.post(
        "https://soff.se/wp/wp-admin/admin-ajax.php",
        data={"action": "get_map_markers", "security": nonce},
        headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()

    companies = []
    for m in data:
        name = (m.get("name") or "").strip()
        if not name:
            continue
        # Fetch the member's own website from their SOFF profile page
        website = _fetch_soff_member_website(m.get("url", ""))
        companies.append({
            "name": name,
            "source": "SOFF",
            "website": website,
        })

    print(f"  SOFF: {len(companies)} companies")
    return companies


def _get_soff_nonce() -> str:
    """Extract the current WordPress nonce from the SOFF members page."""
    r = requests.get("https://soff.se/en/members/", headers=HEADERS, timeout=15)
    m = re.search(r'"security"\s*:\s*"([a-f0-9]+)"', r.text)
    if m:
        return m.group(1)
    # Fallback — nonce from last known page load (may expire)
    return "5271f9430a"


def _fetch_soff_member_website(profile_url: str) -> str:
    """Fetch the company's external website from their SOFF member profile page."""
    if not profile_url:
        return ""
    try:
        r = requests.get(profile_url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        # SOFF member pages show the company website as an external link
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if (href.startswith("http") and
                    "soff.se" not in href and
                    "linkedin" not in href and
                    "facebook" not in href):
                return href
    except Exception:
        pass
    return ""


# ── SME-D ─────────────────────────────────────────────────────────────────────

def scrape_smed() -> list[dict]:
    """
    Scrape all SME-D member pages (paginated WordPress site).
    Company names are in <h3> tags; address + website also available.
    """
    print("Scraping SME-D...")
    companies = []
    page = 1

    # Use the no-language-prefix URL so company names are not auto-translated.
    # /en/medlemmar/ runs TranslatePress and turns "Stigen" into "The path", etc.
    while True:
        url = (
            "https://sme-d.se/medlemmar/"
            if page == 1
            else f"https://sme-d.se/medlemmar/page/{page}/"
        )
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 404:
            break
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        cards = _parse_smed_page(soup)
        if not cards:
            break
        companies.extend(cards)

        pag = soup.find("nav", class_=re.compile("paginat", re.I))
        has_next = False
        if pag:
            for a in pag.find_all("a", href=True):
                if f"page/{page + 1}/" in a["href"]:
                    has_next = True
                    break
        if not has_next:
            break
        page += 1

    print(f"  SME-D: {len(companies)} companies across {page} page(s)")
    return companies


def _parse_smed_page(soup: BeautifulSoup) -> list[dict]:
    companies = []
    # Each member is wrapped in an article or a div containing an <h3> name
    # The structure: h3 (name), then dt/dd or p tags with Address/ZIP/City/Website
    for h3 in soup.find_all("h3"):
        name = h3.get_text(strip=True)
        if not name or len(name) < 3:
            continue
        # Gather sibling text until the next h3
        parent = h3.parent
        address_parts = []
        website = ""
        for el in h3.find_next_siblings():
            if el.name == "h3":
                break
            if el.name == "a" and el.get("href", "").startswith("http"):
                website = el["href"]
            text = el.get_text(strip=True)
            if text and not any(
                label in text for label in ["Address:", "ZIP code:", "City:"]
            ):
                # Keep city / other address text
                pass
            if text:
                address_parts.append(text)

        # Reconstruct address from labeled fields
        full_text = " ".join(address_parts)
        street = re.search(r"Address:\s*(.+?)(?:ZIP|$)", full_text)
        city = re.search(r"City:\s*(\S+)", full_text)

        companies.append({
            "name": name,
            "source": "SME-D",
            "address": street.group(1).strip() if street else "",
            "city": city.group(1).strip() if city else "",
            "website": website,
        })
    return companies


# ── CivilSecurity ────────────────────────────────────────────────────────────

def scrape_civilsecurity() -> list[dict]:
    """
    civilsecurity.se renders member logos via JavaScript — static scraping
    returns no company names. Skipping for this release.
    TODO: implement with Playwright for full member list.
    """
    print("  CivilSecurity: JS-rendered member list — skipped (see TODO in code)")
    return []


# ── Google Sheets writer ──────────────────────────────────────────────────────

def companies_to_rows(companies: list[dict]) -> list[list]:
    """
    Convert company dicts to sheet rows matching Companies tab headers:
    Org Nr | Company Name | Source | Address | SNI Code |
    Website | Contact Name | Contact Email | Contact Found Date | Notes
    """
    rows = []
    for c in companies:
        address = c.get("address", "")
        if c.get("city"):
            address = f"{address}, {c['city']}".strip(", ")
        rows.append([
            "",               # Org Nr (to be filled by Bolagsverket later)
            c["name"],
            c.get("source", ""),
            address,
            "",               # SNI Code
            c.get("website", ""),
            "",               # Contact Name
            "",               # Contact Email
            "",               # Contact Found Date
            "",               # Notes
        ])
    return rows


def deduplicate(companies: list[dict]) -> list[dict]:
    """
    Merge companies with the same name (case-insensitive) that appear in
    multiple associations. The merged row gets:
    - Source: comma-joined list of all sources (e.g. "SOFF, SME-D")
    - Address/website: first non-empty value wins
    """
    seen: dict[str, dict] = {}
    for c in companies:
        key = c["name"].strip().lower()
        if key not in seen:
            seen[key] = c.copy()
        else:
            existing = seen[key]
            # Merge sources
            sources = set(existing["source"].split(", ")) | {c["source"]}
            existing["source"] = ", ".join(sorted(sources))
            # Fill in missing fields from the new record
            for field in ("address", "city", "website"):
                if not existing.get(field) and c.get(field):
                    existing[field] = c[field]
    merged = list(seen.values())
    cross = [c for c in merged if "," in c["source"]]
    if cross:
        print(f"  Cross-association duplicates merged: {len(cross)}")
        for c in cross:
            print(f"    {c['name']}  ({c['source']})")
    return merged


def run():
    print("=== Company scraper ===\n")

    # Scrape all sources
    soff = scrape_soff()
    smed = scrape_smed()
    civil = scrape_civilsecurity()

    # Deduplicate within the scraped batch before touching the sheet
    all_companies = deduplicate(soff + smed + civil)
    print(f"\nTotal after dedup: {len(all_companies)}")

    # Connect to sheet
    print("\nConnecting to Google Sheet...")
    tabs = get_tabs()
    ws = tabs["Companies"]

    # Filter out companies already in the sheet
    existing = get_existing_company_names(ws)
    new_companies = [
        c for c in all_companies
        if c["name"].strip().lower() not in existing
    ]
    print(f"New companies (not yet in sheet): {len(new_companies)}")

    if not new_companies:
        print("Nothing to add.")
        return

    rows = companies_to_rows(new_companies)
    added = append_companies(ws, rows)
    print(f"Written {added} rows to Companies tab.")
    print(f"\nSheet URL: {ws.spreadsheet.url}")


if __name__ == "__main__":
    run()
