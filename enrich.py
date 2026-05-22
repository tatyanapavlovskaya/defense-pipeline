"""
Enrich the Companies tab with data from allabolag.se.

For each company row that is missing an Org Nr (or --force to re-enrich all),
searches allabolag.se for the best name match, then fetches:
  - Org Nr
  - Address
  - Employees  (plain integer, header says "Employees")
  - Revenue    (plain integer in kSEK, header says "Revenue (kSEK)")

Writes back to the sheet in individual row updates.
"""

import re
import sys
import os
import time
import argparse
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()
from sheet import get_tabs, COMPANY_HEADERS, _format_header_row

BASE = "https://www.allabolag.se"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "sv-SE,sv;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
}

# Companies tab column indices (0-based), must match COMPANY_HEADERS order
COL_ORG_NR   = COMPANY_HEADERS.index("Org Nr")
COL_NAME     = COMPANY_HEADERS.index("Company Name")
COL_ADDRESS  = COMPANY_HEADERS.index("Address")
COL_EMPL     = COMPANY_HEADERS.index("Employees")
COL_REV      = COMPANY_HEADERS.index("Revenue (kSEK)")
NUM_COLS     = len(COMPANY_HEADERS)        # 8


def _search(name: str) -> list[str]:
    """Return up to 3 allabolag.se /foretag/ paths for a company name."""
    try:
        r = requests.get(
            f"{BASE}/what/{requests.utils.quote(name)}",
            headers=_HEADERS, timeout=12, allow_redirects=True,
        )
        soup = BeautifulSoup(r.text, "lxml")
        return [
            a["href"] for a in soup.find_all("a", href=True)
            if a["href"].startswith("/foretag/")
        ][:3]
    except Exception:
        return []


def _fetch_data(path: str) -> dict:
    """Fetch org nr, address, employees and revenue from a company profile page."""
    try:
        r = requests.get(f"{BASE}{path}", headers=_HEADERS, timeout=12)
        soup = BeautifulSoup(r.text, "lxml")
        text = soup.get_text(separator=" ", strip=True)

        # Org nr  e.g. 556036-0793
        org_m = re.search(r"(\d{6}-\d{4})", text)

        # Address: capture up to "NNN NN City" Swedish postcode pattern
        addr_m = re.search(
            r"Adress\s+(.+?,\s*\d{3}\s*\d{2}\s+[A-Za-zÅÄÖåäö\-]+)",
            text,
        )

        # Employees — plain integer
        emp_m = re.search(r"Antal anst[äa]llda\s*([\d\xa0 ]+)", text)
        emp_str = re.sub(r"[\xa0\s]", "", emp_m.group(1)).strip() if emp_m else ""
        employees = emp_str if emp_str and emp_str != "0" else ""

        # Revenue in kSEK — plain integer (page already shows amounts in 1 000-tals)
        rev_m = re.search(r"Omsättning\s+\d{4}\s+([\d\xa0 ]+)", text)
        rev_str = re.sub(r"[\xa0\s]", "", rev_m.group(1)).strip() if rev_m else ""
        revenue = rev_str if rev_str else ""

        return {
            "org_nr":   org_m.group(1) if org_m else "",
            "address":  addr_m.group(1).strip() if addr_m else "",
            "employees": employees,
            "revenue":   revenue,
        }
    except Exception:
        return {"org_nr": "", "address": "", "employees": "", "revenue": ""}


def _best_match(name: str, paths: list[str]) -> dict:
    """
    Pick the search result whose slug best overlaps with the company name.
    Falls back to the first result.
    """
    name_words = set(re.sub(r"[^a-z0-9]", " ", name.lower()).split()) - {
        "ab", "as", "ltd", "hb", "kb", "oy", "plc", "inc", "llc",
    }
    for path in paths:
        slug_words = set(re.sub(r"[^a-z0-9]", " ", path.lower()).split())
        if name_words & slug_words:
            return _fetch_data(path)
    return _fetch_data(paths[0]) if paths else {}


def run_enrichment(force: bool = False, limit: int = 0) -> None:
    print("=== Company Enrichment via allabolag.se ===\n")

    tabs = get_tabs()   # also ensures headers are up-to-date + formatted
    ws = tabs["Companies"]

    all_rows = ws.get_all_values()
    if len(all_rows) < 2:
        print("Companies tab is empty.")
        return

    data = all_rows[1:]   # skip header
    total = len(data)

    # Identify rows to enrich
    to_enrich = []
    for i, row in enumerate(data):
        org = row[COL_ORG_NR].strip() if len(row) > COL_ORG_NR else ""
        if force or not org:
            to_enrich.append(i)

    if limit:
        to_enrich = to_enrich[:limit]

    print(f"{total} companies total, {len(to_enrich)} queued "
          f"({'forced re-enrich' if force else 'missing Org Nr'}).\n")

    updated = 0
    for idx, row_i in enumerate(to_enrich):
        row = list(data[row_i])
        name = row[COL_NAME].strip() if len(row) > COL_NAME else ""
        if not name:
            continue

        print(f"[{idx+1}/{len(to_enrich)}] {name} … ", end="", flush=True)

        paths = _search(name)
        if not paths:
            print("no results")
            time.sleep(0.5)
            continue

        result = _best_match(name, paths)

        # Pad row to full column width
        while len(row) < NUM_COLS:
            row.append("")

        changed = False
        if result.get("org_nr") and not row[COL_ORG_NR]:
            row[COL_ORG_NR] = result["org_nr"]
            changed = True
        if result.get("address") and not row[COL_ADDRESS]:
            row[COL_ADDRESS] = result["address"]
            changed = True
        if result.get("employees"):
            row[COL_EMPL] = result["employees"]
            changed = True
        if result.get("revenue"):
            row[COL_REV] = result["revenue"]
            changed = True

        if changed:
            sheet_row = row_i + 2   # 1-indexed; row 1 = header
            col_letter = chr(ord("A") + NUM_COLS - 1)   # "H" for 8 cols
            ws.update(
                values=[row[:NUM_COLS]],
                range_name=f"A{sheet_row}:{col_letter}{sheet_row}",
            )
            updated += 1
            print(
                f"org={result['org_nr']}  "
                f"emp={result['employees'] or '—'}  "
                f"rev={result['revenue'] or '—'} kSEK"
            )
        else:
            print("no new data")

        time.sleep(0.6)   # polite rate limit (~100 req/min)

    print(f"\nDone. {updated}/{len(to_enrich)} rows updated.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Enrich Companies tab from allabolag.se")
    parser.add_argument("--force", action="store_true",
                        help="Re-enrich even rows that already have an Org Nr")
    parser.add_argument("--limit", type=int, default=0,
                        help="Only enrich this many companies (0 = all)")
    args = parser.parse_args()
    run_enrichment(force=args.force, limit=args.limit)
